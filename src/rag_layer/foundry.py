"""Call the hosted Azure AI Foundry agent ("KnowledgeBase") over the OpenAI Responses
protocol.

Unlike the rest of the app (pgvector + Azure OpenAI), this talks to a Foundry Agent
Service agent whose own retrieval tools (an Azure AI Search knowledge base, exposed to
the agent as MCP) do the grounding server-side. It exists so the Coach console has a
"Foundry" tab to test that agent's chat side by side with the local RAG stack.

Wire protocol (confirmed against the live endpoint):
  POST {project_endpoint}/agents/{agent}/endpoint/protocols/openai/responses?api-version=v1
  Header: api-key: <project key>
  Body:   {"input": "<text>"}  or  {"input": [{"role","content"}, ...]}  (OpenAI Responses)
  Reply:  an OpenAI Responses object; the final answer is the last `message` output item's
          output_text, and its `annotations` carry url_citation entries for the sources.
"""

from __future__ import annotations

import json
import time
from urllib.parse import unquote, urlparse

import requests
import urllib3

from .config import NLG_SUPPORT_MESSAGE, Settings
from .prompt_features import prompt_recipe
from .prompt_runtime import PromptInvocationRecorder

# Same transient-failure handling as embeddings.py: Foundry / the model behind it can
# throttle (429) or blip (5xx), so back off and retry rather than failing the chat turn.
_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 4
_BACKOFF_BASE = 2.0
_BACKOFF_CAP = 30.0


def foundry_configured(settings: Settings) -> bool:
    return bool(settings.foundry_project_endpoint and settings.foundry_api_key)


class FoundryAgentClient:
    """Thin client for one hosted Foundry agent's Responses endpoint."""

    def __init__(self, settings: Settings) -> None:
        if not foundry_configured(settings):
            raise ValueError(
                "Foundry is not configured (FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_API_KEY are required)"
            )
        self.settings = settings
        self.agent = settings.foundry_agent_name
        self.url = (
            f"{settings.foundry_project_endpoint}/agents/{self.agent}"
            f"/endpoint/protocols/openai/responses?api-version={settings.foundry_api_version}"
        )
        self.session = requests.Session()
        self.session.trust_env = False
        self.headers = {
            "api-key": settings.foundry_api_key,
            "Authorization": f"Bearer {settings.foundry_api_key}",
            "Content-Type": "application/json",
        }
        self.verify_ssl = settings.azure_storage_verify_ssl
        if not self.verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def respond(
        self,
        payload: dict,
        timeout: tuple[int, int] = (10, 180),
        trace: "_RequestTrace | None" = None,
    ) -> dict:
        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            attempt_number = trace.begin_attempt(
                reason="primary" if attempt == 0 else "transport_retry",
                provider="azure_ai_foundry",
                requested_model=self.agent,
                provider_request=payload,
            ) if trace else None
            try:
                response = self.session.post(
                    self.url,
                    headers=self.headers,
                    json=payload,
                    timeout=timeout,
                    verify=self.verify_ssl,
                )
            except Exception as exc:
                if trace and attempt_number is not None:
                    trace.attempt_error(attempt_number, exc)
                raise
            if response.status_code not in _RETRY_STATUS:
                try:
                    response.raise_for_status()
                    data = response.json()
                except Exception as exc:
                    if trace and attempt_number is not None:
                        trace.attempt_error(attempt_number, exc)
                    raise
                if trace and attempt_number is not None:
                    trace.attempt_response(attempt_number, data)
                return data
            last_error = requests.HTTPError(
                f"{response.status_code} {response.reason} for url: {self.url}", response=response
            )
            if trace and attempt_number is not None:
                trace.attempt_error(attempt_number, last_error)
            if attempt == _MAX_RETRIES:
                break
            time.sleep(self._retry_delay(response, attempt))
        assert last_error is not None
        raise last_error

    @staticmethod
    def _retry_delay(response: "requests.Response", attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), _BACKOFF_CAP)
            except ValueError:
                pass
        return min(_BACKOFF_BASE * (2 ** attempt), _BACKOFF_CAP)


def _blob_filename(url: str) -> str:
    """Turn a source URL into a short human label (the file's name, unescaped)."""
    try:
        path = urlparse(url).path
    except Exception:  # noqa: BLE001 - a malformed URL should still show something
        path = url
    name = unquote(path.rsplit("/", 1)[-1]) if path else url
    return name or url


def _final_message(data: dict) -> dict | None:
    """The last `message` output item holds the agent's spoken answer."""
    message = None
    for item in data.get("output", []) or []:
        if item.get("type") == "message":
            message = item
    return message


def _extract_answer(data: dict) -> tuple[str, list[dict]]:
    """Pull the answer text out of a Responses object and rewrite its inline citation
    markers (e.g. ``【6:0†source】``) into ``[1] [2]`` numbered against a deduped source
    list, so the UI can show clean prose plus a footnote list."""
    message = _final_message(data)

    # Fall back to the SDK convenience field, or an empty answer, if there's no message.
    if message is None:
        text = (data.get("output_text") or "").strip()
        return text, []

    parts = message.get("content", []) or []
    text = ""
    annotations: list[dict] = []
    for content in parts:
        if content.get("type") in {"output_text", "text"} and content.get("text"):
            text = content["text"]
            annotations = content.get("annotations", []) or []
            break

    # Map each cited URL to a stable footnote number (first mention wins).
    order: dict[str, int] = {}
    citations: list[dict] = []
    for ann in annotations:
        if ann.get("type") != "url_citation":
            continue
        url = ann.get("url") or ""
        if url and url not in order:
            n = len(order) + 1
            order[url] = n
            # The agent's `title` is usually just the URL again; a filename reads better.
            title = ann.get("title") or ""
            if not title or title.startswith(("http://", "https://")):
                title = _blob_filename(url)
            citations.append({"n": n, "title": title, "url": url})

    # Replace the marker spans right-to-left so earlier indices stay valid.
    spans = [
        a for a in annotations
        if a.get("type") == "url_citation"
        and isinstance(a.get("start_index"), int)
        and isinstance(a.get("end_index"), int)
    ]
    for ann in sorted(spans, key=lambda a: a["start_index"], reverse=True):
        s, e = ann["start_index"], ann["end_index"]
        n = order.get(ann.get("url") or "")
        if n is None or not (0 <= s <= e <= len(text)):
            continue
        text = text[:s] + f" [{n}]" + text[e:]

    return text.strip(), citations


def _extract_model_output(data: dict) -> str:
    """Extract model-authored text without applying application citation processing."""
    message = _final_message(data)
    if message is None:
        return str(data.get("output_text") or "").strip()
    parts: list[str] = []
    for content in message.get("content", []) or []:
        if content.get("type") in {"output_text", "text"} and content.get("text"):
            parts.append(str(content["text"]))
    return "\n".join(parts).strip()


def _current_user_content(
    *,
    question: str,
    preferences: dict | None = None,
    prompt_augmentations: list[dict] | None = None,
    about_me: str = "",
    memories: list[str] | None = None,
) -> str:
    """Serialize the separately sourced Ask inputs only at the provider boundary."""
    prefs = preferences or {}
    style: list[str] = []
    length = {
        "brief": "Be brief and to the point.",
        "balanced": "",
        "detailed": "Give a thorough, detailed answer.",
    }
    output_format = {
        "bullets": "Prefer bullet points.",
        "prose": "Answer in prose paragraphs, not lists.",
        "auto": "",
    }
    tone = {
        "plain": "Use a neutral, plain tone.",
        "warm": "Use a warm, encouraging tone.",
        "formal": "Use a formal, professional tone.",
    }
    if length.get(prefs.get("length", "balanced")):
        style.append(length[prefs.get("length", "balanced")])
    if output_format.get(prefs.get("format", "auto")):
        style.append(output_format[prefs.get("format", "auto")])
    if tone.get(prefs.get("tone", "warm")):
        style.append(tone[prefs.get("tone", "warm")])
    if prefs.get("plain"):
        style.append("Explain simply, so a brand-new agent can follow.")
    if prefs.get("always_sources"):
        style.append("Always cite the source documents.")

    lines: list[str] = []
    if style:
        lines.append("Answer style: " + " ".join(style))
    clean_about_me = about_me.strip()
    clean_memories = [text.strip() for text in (memories or []) if text.strip()]
    augmentation_values = {
        "ask.about_me": ("{{about_me}}", clean_about_me),
        "ask.memories": ("{{memories}}", "; ".join(clean_memories)),
    }
    for augmentation in prompt_augmentations or []:
        key = str(augmentation.get("key", ""))
        if key not in augmentation_values:
            raise ValueError(f"Unsupported prompt augmentation key: {key!r}")
        placeholder, value = augmentation_values[key]
        if not value:
            continue
        template = str(augmentation.get("instructions", ""))
        if placeholder not in template:
            raise ValueError(
                f"Prompt augmentation {key!r} is missing required placeholder {placeholder}"
            )
        lines.append(template.replace(placeholder, value).strip())

    preamble = (
        "[Context for how to answer — do not repeat this back to me:\n"
        + "\n".join(lines)
        + "]\n\n"
        if lines
        else ""
    )
    return preamble + question


def _flatten_provider_request(payload: dict) -> str:
    """Render every prompt-bearing field; provider-request.json remains authoritative."""
    sections: list[str] = []
    if "instructions" in payload:
        instructions = payload.get("instructions", "")
        if not isinstance(instructions, str):
            instructions = json.dumps(instructions, ensure_ascii=False, indent=2)
        sections.append(f"===== INSTRUCTIONS =====\n{instructions}")

    request_input = payload.get("input", [])
    if isinstance(request_input, str):
        sections.append(f"===== INPUT =====\n{request_input}")
    else:
        for index, item in enumerate(request_input or [], start=1):
            role = str(item.get("role", "unknown")).upper()
            content = item.get("content", "")
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False, indent=2)
            sections.append(f"===== MESSAGE {index}: {role} =====\n{content}")

    # Render every remaining request field generically. This deliberately favors a
    # complete trace over guessing which future Responses fields affect model context.
    for field, field_value in payload.items():
        if field in {"instructions", "input"}:
            continue
        value = json.dumps(field_value, ensure_ascii=False, indent=2)
        sections.append(f"===== {field.upper()} =====\n{value}")
    return "\n\n".join(sections) + "\n"


class _RequestTrace:
    def __init__(
        self,
        *,
        settings: Settings,
        session_id: str | None,
        recipe: dict[str, int],
        inputs: dict,
        origin: str = "live",
        correlation_id: str | None = None,
    ) -> None:
        self._recorder = PromptInvocationRecorder(
            settings=settings,
            feature_key="ask",
            origin=origin,
            prompt_recipe=recipe,
            runtime_inputs=inputs,
            trace_session_id=session_id,
            correlation_id=correlation_id,
        )
        self.request_id = self._recorder.invocation_id

    def provider_request(self, payload: dict) -> None:
        self._recorder.rendered(_flatten_provider_request(payload))

    def begin_attempt(self, **values) -> int:
        return self._recorder.begin_attempt(**values)

    def attempt_response(self, attempt_number: int, data: dict) -> None:
        self._recorder.complete_attempt(
            attempt_number,
            provider_response=data,
            actual_model_metadata={
                key: data.get(key) for key in ("model", "id", "status") if data.get(key) is not None
            },
        )

    def attempt_error(self, attempt_number: int, exc: Exception) -> None:
        self._recorder.complete_attempt(attempt_number, error=exc)

    def response(self, data: dict, *, agent: str) -> None:
        self._recorder.complete(
            model_output=_extract_model_output(data),
            provider_metadata={
                "provider": "azure_ai_foundry",
                "agent": agent,
                "model": data.get("model"),
                "response_id": data.get("id"),
                "status": data.get("status"),
            },
        )

    def error(self, exc: Exception) -> None:
        self._recorder.fail(exc)


def _invoke_ask_prompt(
    *,
    settings: Settings,
    history: list[dict],
    question: str | None = None,
    message: str | None = None,
    instructions: str = "",
    prompt_key: str = "ask.navigator",
    prompt_version: int = 1,
    preferences: dict | None = None,
    prompt_augmentations: list[dict] | None = None,
    about_me: str = "",
    memories: list[str] | None = None,
    user_id: str | None = None,
    trace_session_id: str | None = None,
    trace_origin: str = "live",
    correlation_id: str | None = None,
) -> tuple[dict, FoundryAgentClient, _RequestTrace]:
    """Render and invoke Ask without applying application response handling."""
    resolved_question = question if question is not None else message
    if resolved_question is None:
        raise ValueError("question is required")
    resolved_prompts = [
        {"key": prompt_key, "version": prompt_version},
        *[
            {"key": item.get("key"), "version": item.get("version")}
            for item in (prompt_augmentations or [])
        ],
    ]
    recipe = prompt_recipe("ask", resolved_prompts)
    trace = _RequestTrace(
        settings=settings,
        session_id=trace_session_id,
        recipe=recipe,
        origin=trace_origin,
        correlation_id=correlation_id,
        inputs={
            "question": resolved_question,
            "history": history,
            "preferences": preferences or {},
            "about_me": about_me,
            "memories": memories or [],
            "user_id": user_id,
        },
    )
    try:
        client = FoundryAgentClient(settings)
        # Agent-scoped Foundry endpoints reject the top-level Responses
        # ``instructions`` field ("Not allowed when agent is specified"). Put the
        # application-managed prompt in the input as a system message instead.
        conversation: list[dict] = [
            {"type": "message", "role": "system", "content": instructions}
        ]
        for turn in history:
            role = turn.get("role")
            text = str(turn.get("text", "")).strip()
            if role in {"user", "assistant"} and text:
                conversation.append({"type": "message", "role": role, "content": text})
        conversation.append({
            "type": "message",
            "role": "user",
            "content": _current_user_content(
                question=resolved_question,
                preferences=preferences,
                prompt_augmentations=prompt_augmentations,
                about_me=about_me,
                memories=memories,
            ),
        })

        provider_payload = {"input": conversation}
        trace.provider_request(provider_payload)
        data = client.respond(provider_payload, trace=trace)
        trace.response(data, agent=client.agent)
    except Exception as exc:
        # Replay runners use this to link a failed test case to the failed invocation
        # and its provider attempts without changing the exception's visible behavior.
        setattr(exc, "prompt_invocation_id", trace.request_id)
        trace.error(exc)
        raise
    return data, client, trace


def replay_ask_prompt(**values) -> dict:
    """Run one Ask prompt-unit replay and return only provider-boundary output."""
    data, client, trace = _invoke_ask_prompt(trace_origin="replay", **values)
    return {
        "invocation_id": trace.request_id,
        "model_output": _extract_model_output(data),
        "provider_metadata": {
            "provider": "azure_ai_foundry",
            "agent": client.agent,
            "model": data.get("model"),
            "response_id": data.get("id"),
            "status": data.get("status"),
        },
    }


def chat(
    *,
    settings: Settings,
    history: list[dict],
    question: str | None = None,
    message: str | None = None,
    instructions: str = "",
    prompt_key: str = "ask.navigator",
    prompt_version: int = 1,
    preferences: dict | None = None,
    prompt_augmentations: list[dict] | None = None,
    about_me: str = "",
    memories: list[str] | None = None,
    user_id: str | None = None,
    trace_session_id: str | None = None,
    trace_origin: str = "live",
    correlation_id: str | None = None,
) -> dict:
    """One live Ask turn, including application citation and abstention handling."""
    data, client, trace = _invoke_ask_prompt(
        settings=settings,
        history=history,
        question=question,
        message=message,
        instructions=instructions,
        prompt_key=prompt_key,
        prompt_version=prompt_version,
        preferences=preferences,
        prompt_augmentations=prompt_augmentations,
        about_me=about_me,
        memories=memories,
        user_id=user_id,
        trace_session_id=trace_session_id,
        trace_origin=trace_origin,
        correlation_id=correlation_id,
    )
    answer, citations = _extract_answer(data)

    # M03 — cap the agent's already-deduped, already-numbered citation list. Order and the
    # existing [n] numbering are preserved; this is a simple length cap.
    max_sources = getattr(settings, "max_sources", 0)
    if max_sources and len(citations) > max_sources:
        citations = citations[:max_sources]

    meta = {
        "agent": client.agent,
        "model": data.get("model"),
        "response_id": data.get("id"),
        "status": data.get("status"),
        "trace_request_id": getattr(trace, "request_id", None),
    }

    # M03 abstention — the practical low-confidence signal on this track is the agent
    # returning no grounded sources (min_similarity can't be used: no scores are exposed).
    # Rather than surface ungrounded prose, hand off to NLG support and raise a discrete
    # escalate flag that M09 can branch on.
    non_answer = (not answer) or answer.strip() in {"", "(the agent returned no text)"}
    if not citations or non_answer:
        return {
            "answer": NLG_SUPPORT_MESSAGE,
            "citations": [],
            "escalate": True,
            "escalate_reason": "no_citations" if not citations else "empty_answer",
            **meta,
        }

    return {
        "answer": answer,
        "citations": citations,
        "escalate": False,
        "escalate_reason": None,
        **meta,
    }
