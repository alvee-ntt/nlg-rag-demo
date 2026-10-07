from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from app.models import AgentReply, SessionSummary
from app.orchestrator import AgentRunner
from app.tracing import TurnTrace


logger = logging.getLogger(__name__)


class TraceWriter(Protocol):
    def write(self, trace: TurnTrace) -> None: ...


@dataclass
class Conversation:
    agent_session: Any
    turn_number: int = 0
    topic: str | None = None
    workflow_status: str = "normal"
    known_facts: dict[str, str] = field(default_factory=dict)
    pending_action: dict[str, Any] | None = None


class ChatService:
    def __init__(
        self,
        runner: AgentRunner,
        trace_path: Path | None = None,
        trace_writer: TraceWriter | None = None,
    ) -> None:
        self._runner = runner
        self._trace_path = trace_path
        self._trace_writer = trace_writer
        self._conversations: dict[str, Conversation] = {}

    async def chat(self, message: str, session_id: str | None = None) -> tuple[str, AgentReply]:
        conversation_id = session_id or str(uuid4())
        conversation = self._conversations.get(conversation_id)
        if conversation is None:
            conversation = Conversation(agent_session=self._runner.create_session())
            self._conversations[conversation_id] = conversation

        conversation.turn_number += 1
        trace = TurnTrace.start(
            self._trace_path,
            conversation_id,
            conversation.turn_number,
            capture=self._trace_writer is not None,
        )
        trace.record(
            "UI -> Application Orchestrator",
            actor="UI",
            kind="user_input",
            input_text=message,
            details={"session_id": conversation_id, "turn_number": conversation.turn_number},
        )
        try:
            reply = await self._runner.run(message, conversation.agent_session, trace)
        except Exception as error:
            trace.finalize(error=error)
            self._persist_trace(trace)
            raise
        conversation.workflow_status = reply.response_type.value
        trace.finalize(reply=reply)
        self._persist_trace(trace)
        return conversation_id, reply

    def _persist_trace(self, trace: TurnTrace) -> None:
        if self._trace_writer is None:
            return
        try:
            self._trace_writer.write(trace)
        except Exception:
            logger.exception("Unable to persist agent turn trace %s", trace.context.turn_id)

    def summary(self, session_id: str) -> SessionSummary | None:
        conversation = self._conversations.get(session_id)
        if conversation is None:
            return None
        return SessionSummary(
            session_id=session_id,
            topic=conversation.topic,
            workflow_status=conversation.workflow_status,
        )
