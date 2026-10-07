from typing import Any
from pathlib import Path
import json

from fastapi.testclient import TestClient

from app.main import create_app
from app.knowledge import extract_citations
from app.models import AgentReply, ResponseType
from app.orchestrator import (
    AgentRoute,
    MicrosoftAgentFrameworkRunner,
    parse_language_review,
    requires_language_review,
    route_message,
    underwriting_policy_violations,
)
from app.prompt_configuration import AgentPrompts
from app.settings import Settings
from app.tracing import TurnTrace


TEST_PROMPTS = AgentPrompts(
    knowledge="knowledge instructions",
    orchestrator="orchestrator instructions",
    small="small instructions",
    underwriting="underwriting instructions",
    language="language instructions",
    compose="composition instructions",
    revise="revision instructions",
)


class FakeRunner:
    def create_session(self) -> dict[str, Any]:
        return {"turns": 0}

    async def run(self, message: str, session: dict[str, Any], trace: TurnTrace) -> AgentReply:
        session["turns"] += 1
        return AgentReply(content=f"Turn {session['turns']}: {message}")


def test_chat_reuses_agent_framework_session() -> None:
    with TestClient(create_app(FakeRunner())) as client:
        first = client.post("/api/chat", json={"message": "Hello"})
        assert first.status_code == 200
        session_id = first.json()["session_id"]
        assert first.json()["reply"]["content"] == "Turn 1: Hello"

        second = client.post(
            "/api/chat",
            json={"message": "Continue", "session_id": session_id},
        )

        assert second.status_code == 200
        assert second.json()["session_id"] == session_id
        assert second.json()["reply"]["content"] == "Turn 2: Continue"


def test_chat_stream_emits_reply_as_ordered_ndjson_events() -> None:
    with TestClient(create_app(FakeRunner())) as client:
        response = client.post("/api/chat/stream", json={"message": "Hello"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[0]["type"] == "start"
    assert events[0]["session_id"]
    assert events[0]["reply"]["content"] == ""
    assert "".join(event["delta"] for event in events[1:-1]) == "Turn 1: Hello"
    assert events[-1] == {"type": "done"}


def test_session_exposes_explicit_workflow_state() -> None:
    class EscalatingRunner(FakeRunner):
        async def run(self, message: str, session: dict[str, Any], trace: TurnTrace) -> AgentReply:
            return AgentReply(response_type=ResponseType.ESCALATION, content="Escalating")

    with TestClient(create_app(EscalatingRunner())) as client:
        chat = client.post("/api/chat", json={"message": "Unknown product question"})
        session_id = chat.json()["session_id"]

        summary = client.get(f"/api/sessions/{session_id}")

        assert summary.status_code == 200
        assert summary.json()["workflow_status"] == "escalation"


def test_chat_rejects_empty_messages() -> None:
    with TestClient(create_app(FakeRunner())) as client:
        response = client.post("/api/chat", json={"message": ""})

    assert response.status_code == 422


def test_chat_groups_numbered_turn_traces_by_session(tmp_path: Path) -> None:
    with TestClient(create_app(FakeRunner(), trace_path=tmp_path)) as client:
        first = client.post("/api/chat", json={"message": "First"})
        session_id = first.json()["session_id"]
        second = client.post("/api/chat", json={"message": "Second", "session_id": session_id})

    assert second.status_code == 200
    session_directories = list(tmp_path.glob("session-*"))
    assert len(session_directories) == 1
    trace_files = sorted(session_directories[0].glob("turn-*.md"))
    assert [path.name.split("-", 2)[1] for path in trace_files] == ["0001", "0002"]
    assert "First" in trace_files[0].read_text(encoding="utf-8")
    assert "Second" in trace_files[1].read_text(encoding="utf-8")


def test_foundry_citation_annotations_are_structured() -> None:
    class Content:
        annotations = [
            {
                "type": "citation",
                "title": "FlexLife Product Guide",
                "additional_properties": {
                    "get_url": "https://example.test/flexlife.pdf",
                    "document_id": "DOC-123",
                    "annotation_index": 0,
                },
                "annotated_regions": [{"type": "text_span", "start_index": 40, "end_index": 52}],
            },
            {
                "type": "citation",
                "title": "FlexLife Product Guide",
                "additional_properties": {
                    "get_url": "https://example.test/flexlife.pdf",
                    "annotation_index": 0,
                },
                "annotated_regions": [{"type": "text_span", "start_index": 80, "end_index": 92}],
            },
            {
                "type": "citation",
                "title": "Underwriting Guide",
                "additional_properties": {
                    "get_url": "https://example.test/underwriting.pdf",
                    "annotation_index": 2,
                },
                "annotated_regions": [{"type": "text_span", "start_index": 52, "end_index": 64}],
            },
        ]

    class Message:
        contents = [Content()]

    class Response:
        messages = [Message()]

    citations = extract_citations(Response())

    assert len(citations) == 2
    assert citations[0].citation_id == "C1"
    assert citations[0].source_title == "FlexLife Product Guide"
    assert citations[0].uri == "https://example.test/flexlife.pdf"
    assert citations[0].document_id == "DOC-123"
    assert citations[0].annotation_indexes == [0]
    assert [(span.start_index, span.end_index) for span in citations[0].spans] == [(40, 52), (80, 92)]
    assert citations[1].citation_id == "C2"
    assert citations[1].annotation_indexes == [2]
    assert citations[1].spans[0].start_index == 52


def test_shared_foundry_settings_enable_runtime() -> None:
    settings = Settings(
        foundry_project_endpoint="https://example.test/api/projects/example",
        foundry_api_key="secret",
        foundry_agent_kb="KnowledgeBase",
        foundry_model_large="NLG-Large",
        foundry_model_small="NLG-Small",
        underwriting_agent="NLG-Underwriting",
        language_agent="NLG-Language",
        foundry_api_version="v1",
        log_path=Path("C:/TraceRoot"),
    )

    assert settings.foundry_is_configured
    assert settings.foundry_agent_kb == "KnowledgeBase"
    assert settings.foundry_model_large == "NLG-Large"
    assert settings.foundry_model_small == "NLG-Small"
    assert settings.underwriting_agent == "NLG-Underwriting"
    assert settings.language_agent == "NLG-Language"
    assert settings.log_path == Path("C:/TraceRoot")
    assert settings.foundry_api_key is not None
    assert settings.foundry_api_key.get_secret_value() == "secret"


def test_agent_routing_uses_knowledge_for_flexlife() -> None:
    assert route_message("What are the FlexLife product rules?") == AgentRoute.KNOWLEDGE


def test_agent_routing_uses_underwriting_specialist_for_sensitive_questions() -> None:
    assert route_message("What are the FlexLife underwriting rules?") == AgentRoute.UNDERWRITING
    assert route_message("Will my medical history affect my rate class?") == AgentRoute.UNDERWRITING


def test_agent_routing_keeps_unmatched_requests_inside_flexlife_scope() -> None:
    assert route_message("Hello") == AgentRoute.SMALL
    assert route_message("Rewrite this sentence") == AgentRoute.KNOWLEDGE
    assert route_message("Analyze these trade-offs and propose a detailed plan") == AgentRoute.KNOWLEDGE


def test_agent_routing_keeps_follow_up_with_active_agent() -> None:
    assert route_message("Tell me more", AgentRoute.KNOWLEDGE) == AgentRoute.KNOWLEDGE


def test_language_review_is_adaptive() -> None:
    assert requires_language_review("Reply with a greeting", AgentRoute.SMALL) is False
    assert requires_language_review("What should I tell the customer?", AgentRoute.KNOWLEDGE) is True
    assert requires_language_review("Explain this condition", AgentRoute.UNDERWRITING) is True


def test_underwriting_policy_gate_detects_outcome_predictions() -> None:
    assert underwriting_policy_violations("You will be approved for coverage.")
    assert underwriting_policy_violations("You will receive a preferred rating.")
    assert underwriting_policy_violations("Underwriting will review the application.") == []


def test_language_review_contract_parses_strict_json_and_fences() -> None:
    raw = '{"passed": true, "violations": [], "required_changes": []}'
    assert parse_language_review(raw) is not None
    assert parse_language_review(f"```json\n{raw}\n```") is not None
    assert parse_language_review(f"Review result:\n{raw}\n[Language Guide]") is not None
    assert parse_language_review("Looks good") is None


def test_named_foundry_agent_prompts_carry_database_instructions_in_the_input() -> None:
    runner = object.__new__(MicrosoftAgentFrameworkRunner)
    runner._prompts = TEST_PROMPTS
    knowledge_prompt = runner._knowledge_prompt("question", "context")
    underwriting_prompt = runner._underwriting_prompt("question", "context")
    language_prompt = runner._language_review_prompt("question", "candidate")

    assert knowledge_prompt.startswith(
        "LOCAL APPLICATION INSTRUCTIONS:\nknowledge instructions"
    )
    assert underwriting_prompt.startswith(
        "LOCAL APPLICATION INSTRUCTIONS:\nunderwriting instructions"
    )
    assert "CURRENT USER REQUEST:\nquestion" in underwriting_prompt
    assert language_prompt.startswith(
        "LOCAL APPLICATION INSTRUCTIONS:\nlanguage instructions"
    )
    assert "CANDIDATE RESPONSE:\ncandidate" in language_prompt
