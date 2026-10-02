from types import SimpleNamespace

from src.rag_layer import auth as auth_module
from src.rag_layer.auth import Auth
from src.rag_layer.db import PromptConfigurationError, get_selected_prompt
from src.rag_layer import foundry
from src.rag_layer.foundry import _RequestTrace, _current_user_content, _flatten_provider_request


PROMPT_AUGMENTATIONS = [
    {"key": "ask.about_me", "version": 1, "instructions": "About me: {{about_me}}"},
    {"key": "ask.memories", "version": 1, "instructions": "Remember: {{memories}}"},
]


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
        prompt_augmentations=PROMPT_AUGMENTATIONS,
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


def test_login_creates_a_unique_human_readable_trace_session(monkeypatch):
    sessions = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(auth_module, "connect", lambda settings: Connection())
    monkeypatch.setattr(
        auth_module,
        "create_foundry_trace_session",
        lambda conn, **values: sessions.append(values),
    )
    auth = Auth.__new__(Auth)
    auth.username = "demo.user"
    auth.settings = object()

    first = auth._create_trace_session()
    second = auth._create_trace_session()

    assert first != second
    assert first.startswith("20")
    assert "demo.user" in first
    assert sessions[0]["session_id"] == first
    assert sessions[0]["username"] == "demo.user"
    assert sessions[0]["created_at"].tzinfo is not None


def test_request_trace_writes_inputs_prompt_payload_and_response(monkeypatch):
    created = []
    updates = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(foundry, "connect", lambda settings: Connection())
    monkeypatch.setattr(
        foundry,
        "create_foundry_request_trace",
        lambda conn, **values: created.append(values),
    )
    monkeypatch.setattr(
        foundry,
        "update_foundry_request_trace",
        lambda conn, request_id, **values: updates.append((request_id, values)),
    )
    trace = _RequestTrace(
        settings=object(),
        session_id="session-1",
        inputs={
            "prompt_key": "ask.navigator",
            "prompt_version": 1,
            "question": "How do caps work?",
            "history": [],
            "preferences": {"tone": "warm"},
            "prompt_augmentations": [
                {"key": "ask.about_me", "version": 1},
                {"key": "ask.memories", "version": 1},
            ],
            "about_me": "New agent",
            "memories": ["California market"],
        },
    )
    payload = {
        "input": [
            {"type": "message", "role": "system", "content": "System instructions"},
            {"type": "message", "role": "user", "content": "Final prompt"},
        ],
    }
    response = {"id": "response-1", "output": [{"type": "message"}]}

    trace.provider_request(payload)
    trace.response(response)

    assert created[0]["session_id"] == "session-1"
    inputs = created[0]["inputs"]
    assert inputs["question"] == "How do caps work?"
    assert inputs["prompt_key"] == "ask.navigator"
    assert inputs["prompt_version"] == 1
    assert inputs["prompt_augmentations"][1]["key"] == "ask.memories"
    assert updates[0][0] == created[0]["request_id"]
    assert updates[0][1]["provider_request"] == payload
    assert updates[0][1]["prompt"] == (
        "===== MESSAGE 1: SYSTEM =====\nSystem instructions\n\n"
        "===== MESSAGE 2: USER =====\nFinal prompt\n"
    )
    assert updates[1] == (created[0]["request_id"], {"response": response})


def test_flatten_provider_request_includes_all_prompt_bearing_fields():
    payload = {
        "instructions": "Base instructions",
        "input": [
            {"role": "developer", "content": [{"type": "input_text", "text": "Be concise"}]},
            {"role": "user", "content": "Question"},
        ],
        "structured_inputs": {"audience": "new agent"},
    }

    rendered = _flatten_provider_request(payload)

    assert "===== INSTRUCTIONS =====\nBase instructions" in rendered
    assert '"text": "Be concise"' in rendered
    assert "===== MESSAGE 2: USER =====\nQuestion" in rendered
    assert '===== STRUCTURED_INPUTS =====\n{\n  "audience": "new agent"\n}' in rendered


def test_get_selected_prompt_resolves_definition_and_version():
    expected = {
        "key": "ask.navigator",
        "purpose": "Answer questions",
        "version": 3,
        "instructions": "Current instructions",
    }

    class Result:
        def fetchone(self):
            return expected

    class Connection:
        def execute(self, query, params):
            assert "d.selected_version" in query
            assert params == ("ask.navigator",)
            return Result()

    assert get_selected_prompt(Connection(), "ask.navigator") == expected


def test_get_selected_prompt_fails_when_no_version_is_selected():
    class Result:
        def fetchone(self):
            return None

    class Connection:
        def execute(self, query, params):
            return Result()

    try:
        get_selected_prompt(Connection(), "ask.navigator")
    except PromptConfigurationError as exc:
        assert "ask.navigator" in str(exc)
    else:
        raise AssertionError("Expected missing selected prompt to fail")


def test_chat_sends_resolved_instructions_as_system_input_message(monkeypatch):
    sent = {}

    class Client:
        agent = "KnowledgeBase"

        def __init__(self, settings):
            pass

        def respond(self, payload):
            sent.update(payload)
            return {"id": "response-1", "status": "completed", "output_text": "Answer"}

    monkeypatch.setattr(foundry, "FoundryAgentClient", Client)
    monkeypatch.setattr(foundry, "_RequestTrace", lambda **kwargs: SimpleNamespace(
        provider_request=lambda payload: None,
        response=lambda data: None,
        error=lambda exc: None,
    ))
    settings = SimpleNamespace()

    result = foundry.chat(
        settings=settings,
        instructions="Versioned application instructions",
        prompt_key="ask.navigator",
        prompt_version=1,
        question="How do caps work?",
        history=[],
        prompt_augmentations=PROMPT_AUGMENTATIONS,
        about_me="New agent",
        memories=["California market"],
    )

    assert "instructions" not in sent
    assert sent["input"][0] == {
        "type": "message",
        "role": "system",
        "content": "Versioned application instructions",
    }
    assert all(item["type"] == "message" for item in sent["input"])
    assert sent["input"][-1]["role"] == "user"
    assert "About me: New agent" in sent["input"][-1]["content"]
    assert "Remember: California market" in sent["input"][-1]["content"]
    assert sent["input"][-1]["content"].endswith("How do caps work?")
    assert result["answer"] == "Answer"


def test_prompt_augmentation_is_omitted_when_its_user_value_is_empty():
    content = _current_user_content(
        question="Hello",
        prompt_augmentations=PROMPT_AUGMENTATIONS,
    )

    assert "About me:" not in content
    assert "Remember:" not in content


def test_prompt_augmentation_requires_its_expected_placeholder():
    try:
        _current_user_content(
            question="Hello",
            prompt_augmentations=[
                {"key": "ask.about_me", "version": 2, "instructions": "Profile follows"}
            ],
            about_me="New agent",
        )
    except ValueError as exc:
        assert "{{about_me}}" in str(exc)
    else:
        raise AssertionError("Expected an invalid augmentation template to fail")
