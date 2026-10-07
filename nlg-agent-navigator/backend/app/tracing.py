import hashlib
import json
import logging
import re
import traceback
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from app.models import AgentReply


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnContext:
    session_id: str
    turn_id: str
    turn_number: int
    started_at: datetime


@dataclass
class TraceEvent:
    sequence: int
    title: str
    actor: str
    kind: str
    timestamp: datetime
    status: str = "completed"
    input_text: str | None = None
    output_text: str | None = None
    details: Any = None
    duration_ms: float | None = None


@dataclass
class TurnTrace:
    root: Path | None
    context: TurnContext
    capture: bool = False
    events: list[TraceEvent] = field(default_factory=list)
    status: str = "running"
    completed_at: datetime | None = None

    @classmethod
    def start(
        cls, root: Path | None, session_id: str, turn_number: int, *, capture: bool = False
    ) -> "TurnTrace":
        return cls(
            root=root,
            context=TurnContext(
                session_id=session_id,
                turn_id=uuid4().hex,
                turn_number=turn_number,
                started_at=datetime.now(UTC),
            ),
            capture=capture,
        )

    @property
    def enabled(self) -> bool:
        return self.root is not None or self.capture

    def record(
        self,
        title: str,
        *,
        actor: str,
        kind: str,
        status: str = "completed",
        input_text: str | None = None,
        output_text: str | None = None,
        details: Any = None,
        duration_ms: float | None = None,
    ) -> None:
        if not self.enabled:
            return
        self.events.append(
            TraceEvent(
                sequence=len(self.events) + 1,
                title=title,
                actor=actor,
                kind=kind,
                timestamp=datetime.now(UTC),
                status=status,
                input_text=input_text,
                output_text=output_text,
                details=details,
                duration_ms=duration_ms,
            )
        )

    def record_exception(self, title: str, error: BaseException, *, actor: str) -> None:
        self.record(
            title,
            actor=actor,
            kind="exception",
            status="failed",
            details={
                "exception_type": type(error).__name__,
                "message": str(error),
                "traceback": "".join(traceback.format_exception(error)),
            },
        )

    def finalize(self, reply: AgentReply | None = None, error: BaseException | None = None) -> Path | None:
        if not self.enabled:
            return None
        if reply is not None:
            self.record(
                "Application Orchestrator -> UI",
                actor="Application Orchestrator (deterministic)",
                kind="final_response",
                output_text=reply.content,
                details=reply.model_dump(mode="json"),
            )
        if error is not None:
            self.record_exception("Turn failed", error, actor="Application Orchestrator (deterministic)")
            self.status = "failed"
        else:
            self.status = "completed"
        self.completed_at = datetime.now(UTC)
        if self.root is not None:
            try:
                return self._write()
            except OSError:
                logger.exception("Unable to write agent turn trace %s", self.context.turn_id)
        return None

    def _write(self) -> Path:
        assert self.root is not None
        session_key = hashlib.sha256(self.context.session_id.encode("utf-8")).hexdigest()[:20]
        session_directory = self.root / f"session-{session_key}"
        session_directory.mkdir(parents=True, exist_ok=True)
        timestamp = self.context.started_at.strftime("%Y%m%dT%H%M%S.%fZ")
        filename = f"turn-{self.context.turn_number:04d}-{timestamp}-{self.context.turn_id}.md"
        destination = session_directory / filename
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(self.render(), encoding="utf-8")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination

    def render(self) -> str:
        completed_at = self.completed_at or datetime.now(UTC)
        duration_ms = (completed_at - self.context.started_at).total_seconds() * 1000
        lines = [
            "---",
            f"trace_format: 1",
            f"session_id: {json.dumps(self.context.session_id, ensure_ascii=False)}",
            f"turn_id: {json.dumps(self.context.turn_id)}",
            f"turn_number: {self.context.turn_number}",
            f"status: {self.status}",
            f"started_at: {self.context.started_at.isoformat()}",
            f"completed_at: {completed_at.isoformat()}",
            f"duration_ms: {duration_ms:.3f}",
            "---",
            "",
            f"# Agent Flow: Turn {self.context.turn_number}",
            "",
        ]
        for event in self.events:
            lines.extend(self._render_event(event))
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _render_event(event: TraceEvent) -> list[str]:
        lines = [
            f"## {event.sequence:02d}. {event.title}",
            "",
            f"- **Actor:** {event.actor}",
            f"- **Kind:** {event.kind}",
            f"- **Status:** {event.status}",
            f"- **Timestamp:** {event.timestamp.isoformat()}",
        ]
        if event.duration_ms is not None:
            lines.append(f"- **Duration:** {event.duration_ms:.3f} ms")
        if event.input_text is not None:
            lines.extend(("", "### Exact Input", "", _fenced(event.input_text, "text")))
        if event.output_text is not None:
            lines.extend(("", "### Exact Output", "", _fenced(event.output_text, "text")))
        if event.details is not None:
            details = json.dumps(event.details, ensure_ascii=False, indent=2, default=str)
            lines.extend(("", "### Details", "", _fenced(details, "json")))
        lines.append("")
        return lines


def timed_milliseconds(started: float) -> float:
    return (perf_counter() - started) * 1000


def _fenced(content: str, language: str) -> str:
    longest_run = max((len(match.group(0)) for match in re.finditer(r"`+", content)), default=0)
    fence = "`" * max(3, longest_run + 1)
    return f"{fence}{language}\n{content}\n{fence}"