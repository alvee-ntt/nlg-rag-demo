import json

from src.rag_layer.auth import Auth
from src.rag_layer.foundry import _RequestTrace, _current_user_content


def test_foundry_inputs_remain_separate_until_provider_serialization():
    content = _current_user_content(
        question="How do caps work?",
        preferences={
            "length": "detailed",
            "format": "bullets",
            "tone": "formal",
            "plain": True,
            "always_sources": True,
        },
        about_me="I am a new agent.",
        memories=["My market is California.", "Define insurance terms."],
    )

    assert "Give a thorough, detailed answer." in content
    assert "Prefer bullet points." in content
    assert "Use a formal, professional tone." in content
    assert "Explain simply, so a brand-new agent can follow." in content
    assert "Always cite the source documents." in content
    assert "About me: I am a new agent." in content
    assert "Remember: My market is California.; Define insurance terms." in content
    assert content.endswith("\n\nHow do caps work?")


def test_foundry_defaults_match_the_existing_warm_style():
    content = _current_user_content(question="Hello")

    assert "Answer style: Use a warm, encouraging tone." in content
    assert content.endswith("\n\nHello")


def test_login_creates_a_unique_human_readable_trace_session(tmp_path):
    auth = Auth.__new__(Auth)
    auth.username = "demo.user"
    auth.trace_root = tmp_path

    first = auth._create_trace_session()
    second = auth._create_trace_session()

    assert first != second
    assert first.startswith("20")
    assert "demo.user" in first
    metadata = json.loads((tmp_path / first / "session.json").read_text(encoding="utf-8"))
    assert metadata["session_id"] == first
    assert metadata["username"] == "demo.user"


def test_request_trace_writes_inputs_prompt_payload_and_response(tmp_path):
    trace = _RequestTrace(
        root=str(tmp_path),
        session_id="session-1",
        inputs={
            "question": "How do caps work?",
            "history": [],
            "preferences": {"tone": "warm"},
            "about_me": "New agent",
            "memories": ["California market"],
        },
    )
    payload = {"input": [{"role": "user", "content": "Final prompt"}]}
    response = {"id": "response-1", "output": [{"type": "message"}]}

    trace.provider_request(payload)
    trace.response(response)

    request_dir = next((tmp_path / "session-1").iterdir())
    inputs = json.loads((request_dir / "inputs.json").read_text(encoding="utf-8"))
    assert inputs["question"] == "How do caps work?"
    assert json.loads((request_dir / "provider-request.json").read_text(encoding="utf-8")) == payload
    assert (request_dir / "prompt.txt").read_text(encoding="utf-8") == (
        "===== MESSAGE 1: USER =====\nFinal prompt\n"
    )
    assert json.loads((request_dir / "response.json").read_text(encoding="utf-8")) == response
