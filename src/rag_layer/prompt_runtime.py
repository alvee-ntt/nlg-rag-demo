from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from .db import (
    complete_prompt_provider_attempt,
    connect,
    create_prompt_invocation_trace,
    create_prompt_provider_attempt,
    update_prompt_invocation_trace,
)


_PLACEHOLDER = re.compile(r"<<([a-z][a-z0-9_]*)>>")
_logger = logging.getLogger(__name__)


class PromptTemplateError(ValueError):
    """A prompt template does not satisfy its declared placeholder contract."""


class PromptTracePersistenceError(RuntimeError):
    """Prompt trace persistence failed while strict behavior was active."""


def template_placeholders(template: str) -> set[str]:
    """Return placeholders after rejecting unmatched template delimiters."""
    placeholders = set(_PLACEHOLDER.findall(template))
    without_valid = _PLACEHOLDER.sub("", template)
    if "<<" in without_valid or ">>" in without_valid:
        raise PromptTemplateError("Prompt template contains a malformed placeholder")
    return placeholders


def validate_prompt_template(
    template: str,
    *,
    required: set[str] | frozenset[str] = frozenset(),
    allowed: set[str] | frozenset[str] = frozenset(),
) -> set[str]:
    if not template.strip():
        raise PromptTemplateError("Prompt template cannot be empty")
    required_set = set(required)
    allowed_set = set(allowed)
    if not required_set <= allowed_set:
        undeclared = sorted(required_set - allowed_set)
        raise PromptTemplateError(f"Required placeholders are not allowed: {undeclared}")
    present = template_placeholders(template)
    missing = sorted(required_set - present)
    if missing:
        raise PromptTemplateError(f"Prompt template is missing required placeholders: {missing}")
    unknown = sorted(present - allowed_set)
    if unknown:
        raise PromptTemplateError(f"Prompt template contains unknown placeholders: {unknown}")
    return present


def render_prompt_template(
    template: str,
    values: dict[str, Any],
    *,
    required: set[str] | frozenset[str] = frozenset(),
    allowed: set[str] | frozenset[str] = frozenset(),
) -> str:
    present = validate_prompt_template(template, required=required, allowed=allowed)
    missing_values = sorted(name for name in present if name not in values)
    if missing_values:
        raise PromptTemplateError(f"Runtime inputs are missing placeholder values: {missing_values}")
    # re.sub visits placeholders from the original template only. Placeholder-like text in a
    # runtime value is inserted literally and is never recursively interpreted.
    return _PLACEHOLDER.sub(lambda match: str(values[match.group(1)]), template)


def _error_payload(exc: Exception) -> dict[str, str]:
    return {
        "error_type": type(exc).__name__,
        "message": str(exc),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }


class PromptInvocationRecorder:
    """Persist one logical prompt invocation and its ordered provider attempts.

    The recorder deliberately knows nothing about application workflows or response
    processing. In best-effort mode, the first persistence failure disables later writes
    for this invocation because child attempts cannot exist without their parent trace.
    """

    def __init__(
        self,
        *,
        settings: Any,
        feature_key: str,
        origin: str,
        prompt_recipe: dict[str, int],
        runtime_inputs: dict[str, Any],
        trace_session_id: str | None = None,
        correlation_id: str | None = None,
        invocation_id: str | None = None,
        connect_fn: Callable[[Any], Any] = connect,
    ) -> None:
        self.settings = settings
        self.feature_key = feature_key
        self.origin = origin
        self.invocation_id = invocation_id or f"inv_{uuid.uuid4().hex}"
        self._connect = connect_fn
        self._attempt_number = 0
        self._available = True
        configured_mode = getattr(settings, "prompt_trace_failure_mode", "strict")
        self._strict = origin == "replay" or configured_mode == "strict"
        started_at = datetime.now(timezone.utc)
        self._write(
            "create invocation",
            lambda conn: create_prompt_invocation_trace(
                conn,
                invocation_id=self.invocation_id,
                feature_key=feature_key,
                origin=origin,
                trace_session_id=trace_session_id,
                correlation_id=correlation_id,
                started_at=started_at,
                prompt_recipe=prompt_recipe,
                runtime_inputs=runtime_inputs,
            ),
        )

    def _write(self, operation: str, action: Callable[[Any], None]) -> bool:
        if not self._available:
            return False
        try:
            with self._connect(self.settings) as conn:
                action(conn)
            return True
        except Exception as exc:  # noqa: BLE001 - configured trace failure boundary
            self._available = False
            _logger.exception(
                "Prompt tracing failed feature=%s invocation=%s operation=%s",
                self.feature_key,
                self.invocation_id,
                operation,
            )
            if self._strict:
                raise PromptTracePersistenceError(
                    f"Prompt tracing failed during {operation}: {exc}"
                ) from exc
            return False

    def rendered(self, flattened_prompt: str) -> None:
        self._write(
            "save rendered prompt",
            lambda conn: update_prompt_invocation_trace(
                conn, self.invocation_id, rendered_prompt=flattened_prompt
            ),
        )

    def begin_attempt(
        self,
        *,
        reason: str,
        provider: str,
        requested_model: str | None,
        provider_request: dict[str, Any],
    ) -> int:
        self._attempt_number += 1
        number = self._attempt_number
        started_at = datetime.now(timezone.utc)
        self._write(
            f"create provider attempt {number}",
            lambda conn: create_prompt_provider_attempt(
                conn,
                invocation_id=self.invocation_id,
                attempt_number=number,
                reason=reason,
                provider=provider,
                requested_model=requested_model,
                started_at=started_at,
                provider_request=provider_request,
            ),
        )
        return number

    def complete_attempt(
        self,
        attempt_number: int,
        *,
        provider_response: dict[str, Any] | None = None,
        error: Exception | dict[str, Any] | None = None,
        actual_model_metadata: dict[str, Any] | None = None,
    ) -> None:
        error_data = _error_payload(error) if isinstance(error, Exception) else error
        self._write(
            f"complete provider attempt {attempt_number}",
            lambda conn: complete_prompt_provider_attempt(
                conn,
                invocation_id=self.invocation_id,
                attempt_number=attempt_number,
                completed_at=datetime.now(timezone.utc),
                actual_model_metadata=actual_model_metadata,
                provider_response=provider_response,
                error=error_data,
            ),
        )

    def complete(
        self,
        *,
        model_output: str,
        provider_metadata: dict[str, Any] | None = None,
    ) -> None:
        self._write(
            "complete invocation",
            lambda conn: update_prompt_invocation_trace(
                conn,
                self.invocation_id,
                completed_at=datetime.now(timezone.utc),
                status="completed",
                model_output=model_output,
                provider_metadata=provider_metadata or {},
                error=None,
            ),
        )

    def fail(self, exc: Exception) -> None:
        self._write(
            "fail invocation",
            lambda conn: update_prompt_invocation_trace(
                conn,
                self.invocation_id,
                completed_at=datetime.now(timezone.utc),
                status="failed",
                error=_error_payload(exc),
            ),
        )
