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
    events = []

    class Recorder:
        invocation_id = "invocation-1"

        def __init__(self, **values):
            events.append(("create", values))

        def rendered(self, prompt):
            events.append(("rendered", prompt))

        def begin_attempt(self, **values):
            events.append(("begin_attempt", values))
            return 1

        def complete_attempt(self, attempt_number, **values):
            events.append(("complete_attempt", attempt_number, values))

        def complete(self, **values):
            events.append(("complete", values))

        def fail(self, exc):
            events.append(("fail", exc))

    monkeypatch.setattr(foundry, "PromptInvocationRecorder", Recorder)
    trace = _RequestTrace(
        settings=object(),
        session_id="session-1",
        recipe={
            "ask.navigator": 1,
            "ask.about_me": 1,
            "ask.memories": 1,
        },
        inputs={
            "question": "How do caps work?",
            "history": [],
            "preferences": {"tone": "warm"},
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
    response = {
        "id": "response-1",
        "model": "gpt-5",
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "Answer"}]}],
    }

    trace.provider_request(payload)
    attempt_number = trace.begin_attempt(
        reason="primary",
        provider="azure_ai_foundry",
        requested_model="KnowledgeBase",
        provider_request=payload,
    )
    trace.attempt_response(attempt_number, response)
    trace.response(response, agent="KnowledgeBase")

    creation = events[0][1]
    assert trace.request_id == "invocation-1"
    assert creation["feature_key"] == "ask"
    assert creation["trace_session_id"] == "session-1"
    assert creation["prompt_recipe"]["ask.memories"] == 1
    assert creation["runtime_inputs"]["question"] == "How do caps work?"
    assert events[1][1] == (
        "===== MESSAGE 1: SYSTEM =====\nSystem instructions\n\n"
        "===== MESSAGE 2: USER =====\nFinal prompt\n"
    )
    assert events[2][1]["provider_request"] == payload
    assert events[3][1] == 1
    assert events[3][2]["provider_response"] == response
    assert events[4][1]["model_output"] == "Answer"
    assert events[4][1]["provider_metadata"]["agent"] == "KnowledgeBase"


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


def test_foundry_transport_retry_is_recorded_as_a_second_attempt(monkeypatch):
    class Response:
        def __init__(self, status_code, data=None):
            self.status_code = status_code
            self.reason = "throttled" if status_code == 429 else "ok"
            self.headers = {}
            self.text = "retry later" if status_code == 429 else ""
            self._data = data

        def raise_for_status(self):
            return None

        def json(self):
            return self._data

    responses = iter([
        Response(429),
        Response(200, {"id": "response-1", "model": "gpt-5", "status": "completed"}),
    ])
    client = foundry.FoundryAgentClient.__new__(foundry.FoundryAgentClient)
    client.agent = "KnowledgeBase"
    client.url = "https://example.invalid/responses"
    client.headers = {}
    client.verify_ssl = True
    client.session = SimpleNamespace(post=lambda *args, **kwargs: next(responses))
    events = []
    trace = SimpleNamespace(
        begin_attempt=lambda **values: events.append(("begin", values)) or len(events),
        attempt_error=lambda number, error: events.append(("error", number, error)),
        attempt_response=lambda number, data: events.append(("response", number, data)),
    )
    monkeypatch.setattr(foundry.time, "sleep", lambda delay: None)

    result = client.respond({"input": "hello"}, trace=trace)

    begins = [event for event in events if event[0] == "begin"]
    assert [event[1]["reason"] for event in begins] == ["primary", "transport_retry"]
    assert any(event[0] == "error" for event in events)
    assert result["id"] == "response-1"


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

        def respond(self, payload, trace=None):
            sent.update(payload)
            return {"id": "response-1", "status": "completed", "output_text": "Answer"}

    monkeypatch.setattr(foundry, "FoundryAgentClient", Client)
    monkeypatch.setattr(foundry, "_extract_answer", lambda data: (
        "Answer",
        [{"n": 1, "title": "guide.pdf", "url": "https://example/guide.pdf"}],
    ))
    monkeypatch.setattr(foundry, "_RequestTrace", lambda **kwargs: SimpleNamespace(
        provider_request=lambda payload: None,
        response=lambda data, **kwargs: None,
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
