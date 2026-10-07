from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import AgentReply
from app.orchestrator import AgentRoute, MicrosoftAgentFrameworkRunner
from app.prompt_configuration import AgentPrompts
from app.tracing import TurnTrace
from prompt_studio_api.trace_store import persist_trace


TEST_PROMPTS = AgentPrompts(
    knowledge="knowledge instructions",
    orchestrator="orchestrator instructions",
    small="small instructions",
    underwriting="underwriting instructions",
    language="language instructions",
    compose="composition instructions",
    revise="revision instructions",
)


class StubAgent:
    def __init__(self, *results: Any) -> None:
        self.results = list(results)

    async def run(self, prompt: str) -> Any:
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.committed = False

    def execute(self, query: str, parameters: tuple[Any, ...]) -> None:
        self.calls.append((query, parameters))

    def commit(self) -> None:
        self.committed = True


def response(text: str, *, cited: bool = False) -> SimpleNamespace:
    annotations = []
    if cited:
        annotations.append(
            {
                "type": "citation",
                "title": "FlexLife Guide",
                "additional_properties": {
                    "get_url": "https://example.test/guide.pdf",
                    "document_id": "DOC-1",
                },
            }
        )
    content = SimpleNamespace(annotations=annotations)
    message = SimpleNamespace(contents=[content])
    return SimpleNamespace(text=text, messages=[message])


def runner_with_agents(
    *,
    knowledge: StubAgent | None = None,
    underwriting: StubAgent | None = None,
    language: StubAgent | None = None,
    large: StubAgent | None = None,
    small: StubAgent | None = None,
) -> MicrosoftAgentFrameworkRunner:
    runner = MicrosoftAgentFrameworkRunner.__new__(MicrosoftAgentFrameworkRunner)
    runner._prompts = TEST_PROMPTS
    runner._route_targets = {
        AgentRoute.UNDERWRITING: "NLG-Underwriting",
        AgentRoute.KNOWLEDGE: "KnowledgeBase",
        AgentRoute.LARGE: "NLG-Large",
        AgentRoute.SMALL: "NLG-Small",
    }
    runner._language_target = "NLG-Language"
    runner._large_target = "NLG-Large"
    runner._small_target = "NLG-Small"
    runner._knowledge_agent = knowledge or StubAgent()
    runner._underwriting_agent = underwriting or StubAgent()
    runner._language_agent = language or StubAgent()
    large_agent = large or StubAgent()
    runner._large_model = large_agent
    runner._composition_model = large_agent
    runner._revision_model = large_agent
    runner._small_model = small or StubAgent()
    return runner


def test_trace_writes_ordered_exact_content_to_safe_session_folder(tmp_path: Path) -> None:
    trace = TurnTrace.start(tmp_path, "../unsafe\\session", 2)
    trace.record(
        "UI -> Application Orchestrator",
        actor="UI",
        kind="input",
        input_text="A Unicode request: café\n```embedded```",
    )
    trace.record(
        "Specialist call",
        actor="FlexLifeLanguageAgent",
        kind="agent_call",
        input_text="exact prompt",
        output_text='{"passed": true}',
        duration_ms=12.5,
    )

    destination = trace.finalize(AgentReply(content="exact final answer"))

    assert destination is not None
    assert destination.parent.parent == tmp_path
    assert destination.parent.name.startswith("session-")
    assert "unsafe" not in destination.parent.name
    content = destination.read_text(encoding="utf-8")
    assert 'session_id: "../unsafe\\\\session"' in content
    assert content.index("## 01. UI -> Application Orchestrator") < content.index("## 02. Specialist call")
    assert content.index("## 02. Specialist call") < content.index("## 03. Application Orchestrator -> UI")
    assert "A Unicode request: café\n```embedded```" in content
    assert "exact prompt" in content
    assert "exact final answer" in content
    assert not list(destination.parent.glob("*.tmp"))


def test_trace_records_partial_failure(tmp_path: Path) -> None:
    trace = TurnTrace.start(tmp_path, "session-1", 1)
    trace.record("Started", actor="Application Orchestrator (deterministic)", kind="routing")

    try:
        raise RuntimeError("specialist unavailable")
    except RuntimeError as error:
        destination = trace.finalize(error=error)

    assert destination is not None
    content = destination.read_text(encoding="utf-8")
    assert "status: failed" in content
    assert '"exception_type": "RuntimeError"' in content
    assert '"message": "specialist unavailable"' in content
    assert "Traceback (most recent call last)" in content


def test_trace_write_error_does_not_escape(tmp_path: Path) -> None:
    invalid_root = tmp_path / "trace-file"
    invalid_root.write_text("not a directory", encoding="utf-8")
    trace = TurnTrace.start(invalid_root, "session-1", 1)

    assert trace.finalize(AgentReply(content="still returned")) is None


def test_trace_persistence_keeps_only_agent_invocations() -> None:
    trace = TurnTrace.start(None, "session-1", 3, capture=True)
    trace.record("Route", actor="Orchestrator", kind="routing")
    trace.record(
        "Knowledge call",
        actor="FlexLifeKnowledgeAgent",
        kind="agent_call",
        input_text="exact prompt",
        output_text="exact response",
        details={
            "feature_key": "nlgagent.knowledge",
            "target": "KnowledgeBase",
            "purpose": "Retrieve evidence",
            "application_instructions": "Use approved sources.",
            "citations": [{"document_id": "DOC-1"}],
        },
        duration_ms=12.5,
    )
    trace.finalize(AgentReply(content="answer"))
    connection = RecordingConnection()

    persist_trace(connection, trace)

    assert connection.committed
    assert len(connection.calls) == 2
    invocation_parameters = connection.calls[1][1]
    assert invocation_parameters[5] == "nlgagent.knowledge"
    assert invocation_parameters[7] == "KnowledgeBase"
    assert invocation_parameters[14] == "exact prompt"


@pytest.mark.asyncio
async def test_underwriting_trace_preserves_specialist_composition_and_review_order(tmp_path: Path) -> None:
    runner = runner_with_agents(
        underwriting=StubAgent(response("specialist evidence", cited=True)),
        large=StubAgent(response("composed candidate")),
        language=StubAgent(response('{"passed": true, "violations": [], "required_changes": []}')),
    )
    trace = TurnTrace.start(tmp_path, "underwriting-session", 1)

    reply = await runner.run(
        "Will my medical history affect my rate class?",
        runner.create_session(),
        trace,
    )
    destination = trace.finalize(reply)

    assert destination is not None
    content = destination.read_text(encoding="utf-8")
    underwriting_position = content.index("FlexLifeUnderwritingAgent: Retrieve underwriting evidence")
    composition_position = content.index("FlexLifeOrchestrator: Compose a user-facing answer")
    review_position = content.index("FlexLifeLanguageAgent: Validate the candidate")
    assert underwriting_position < composition_position < review_position
    assert "specialist evidence" in content
    assert "composed candidate" in content
    assert '"feature_key": "nlgagent.underwriting"' in content
    assert '"feature_key": "nlgagent.compose"' in content
    assert '"feature_key": "nlgagent.language"' in content
    assert '"document_id": "DOC-1"' in content


@pytest.mark.asyncio
async def test_language_failure_trace_shows_revision_and_second_review(tmp_path: Path) -> None:
    runner = runner_with_agents(
        knowledge=StubAgent(response("grounded candidate", cited=True)),
        language=StubAgent(
            response(
                '{"passed": false, "violations": ["risky wording"], '
                '"required_changes": ["qualify it"]}',
                cited=True,
            ),
            response('{"passed": true, "violations": [], "required_changes": []}'),
        ),
        large=StubAgent(response("revised candidate")),
    )
    trace = TurnTrace.start(tmp_path, "language-session", 1)

    reply = await runner.run(
        "What should I tell the customer about FlexLife coverage?",
        runner.create_session(),
        trace,
    )
    destination = trace.finalize(reply)

    assert destination is not None
    content = destination.read_text(encoding="utf-8")
    first_review = content.index("FlexLifeLanguageAgent: Validate the candidate")
    revision = content.index("FlexLifeOrchestrator: Revise the candidate")
    second_review = content.index("FlexLifeLanguageAgent: Validate the revised candidate")
    assert first_review < revision < second_review
    assert "risky wording" in content
    assert "revised candidate" in content
    assert reply.content == "revised candidate"


@pytest.mark.asyncio
async def test_failed_specialist_trace_includes_attempted_input_and_exception(tmp_path: Path) -> None:
    runner = runner_with_agents(knowledge=StubAgent(RuntimeError("Foundry unavailable")))
    trace = TurnTrace.start(tmp_path, "failure-session", 1)

    with pytest.raises(RuntimeError, match="Foundry unavailable") as caught:
        await runner.run("Explain FlexLife coverage", runner.create_session(), trace)
    destination = trace.finalize(error=caught.value)

    assert destination is not None
    content = destination.read_text(encoding="utf-8")
    assert "CURRENT USER REQUEST:\nExplain FlexLife coverage" in content
    assert "Foundry unavailable" in content
    assert "FlexLifeKnowledgeAgent: Retrieve grounded product evidence" in content
    assert "status: failed" in content
