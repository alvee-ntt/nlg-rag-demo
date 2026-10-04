from pathlib import Path
from types import SimpleNamespace

import pytest

from src.rag_layer import embeddings
from src.rag_layer.prompt_features import get_prompt_component
from src.rag_layer.prompt_runtime import PromptTracePersistenceError, render_prompt_template


ROOT = Path(__file__).resolve().parents[1]


def _render_seed(name: str, values: dict[str, str]) -> str:
    key = name.removesuffix(".prompt.md")
    component = get_prompt_component(key)
    assert component is not None
    template = (ROOT / "Prompts" / name).read_text(encoding="utf-8")
    return render_prompt_template(
        template,
        values,
        required=component.required_placeholders,
        allowed=component.allowed_placeholders,
    )


def test_relevance_seed_preserves_the_existing_provider_prompt():
    rendered = _render_seed(
        "ask.relevance.prompt.md",
        {"history": "Agent: Tell me about FlexLife.", "message": "And the cap?"},
    )

    assert rendered.startswith("You are a router for a FlexLife life-insurance sales-support assistant.")
    assert "Reply with exactly one token: IN_DOMAIN or OUT_OF_DOMAIN." in rendered
    assert "Conversation so far:\nAgent: Tell me about FlexLife." in rendered
    assert rendered.rstrip().endswith("Latest message: And the cap?")


def test_support_email_seed_preserves_json_request_and_captured_context():
    rendered = _render_seed(
        "ask.support_email.prompt.md",
        {
            "history": "Agent: Is reinstatement allowed?",
            "question": "Can it be back-dated?",
            "reason_note": "This needs an authoritative decision.",
            "source_context": "Source: guide.pdf, page 3\nReinstatement text",
        },
    )

    assert 'Return ONLY a JSON object: {"subject":' in rendered
    assert "Agent: Is reinstatement allowed?" in rendered
    assert "Source: guide.pdf, page 3" in rendered
    assert "Can it be back-dated?" in rendered


def test_versioned_azure_invocation_records_raw_output_and_attempt(monkeypatch):
    events = []

    class Recorder:
        invocation_id = "invocation-1"

        def __init__(self, **values):
            events.append(("create", values))

        def rendered(self, prompt):
            events.append(("rendered", prompt))

        def begin_attempt(self, **values):
            events.append(("begin", values))
            return 1

        def complete_attempt(self, number, **values):
            events.append(("attempt", number, values))

        def complete(self, **values):
            events.append(("complete", values))

        def fail(self, exc):
            events.append(("fail", exc))

    class Client:
        def post(self, path, payload, trace=None):
            number = trace.begin_attempt(reason="primary", provider_request=payload)
            data = {"id": "response-1", "model": "gpt-5", "output_text": "OUT_OF_DOMAIN"}
            trace.attempt_response(number, data)
            return data

    monkeypatch.setattr(embeddings, "PromptInvocationRecorder", Recorder)
    result = embeddings._invoke_versioned_prompt(
        Client(),
        SimpleNamespace(azure_openai_chat_deployment="configured-model"),
        feature_key="ask-relevance",
        component_key="ask.relevance",
        prompt_version=2,
        instructions="History: {{history}}\nMessage: {{message}}",
        inputs={"history": "Earlier", "message": "Latest"},
    )

    assert events[0][1]["prompt_recipe"] == {"ask.relevance": 2}
    assert events[1][1] == "History: Earlier\nMessage: Latest"
    assert events[2][1]["requested_model"] == "configured-model"
    assert events[3][2]["provider_response"]["output_text"] == "OUT_OF_DOMAIN"
    assert events[4][1]["model_output"] == "OUT_OF_DOMAIN"
    assert result["model_output"] == "OUT_OF_DOMAIN"


def test_azure_transport_retry_is_recorded_as_a_second_attempt(monkeypatch):
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
        Response(200, {"id": "response-1", "model": "gpt-5", "output_text": "ok"}),
    ])
    client = embeddings.AzureOpenAIClient.__new__(embeddings.AzureOpenAIClient)
    client.base_url = "https://example.invalid"
    client.headers = {}
    client.verify_ssl = True
    client.session = SimpleNamespace(post=lambda *args, **kwargs: next(responses))
    events = []
    trace = SimpleNamespace(
        begin_attempt=lambda **values: events.append(("begin", values)) or len(events),
        attempt_error=lambda number, error: events.append(("error", number, error)),
        attempt_response=lambda number, data: events.append(("response", number, data)),
    )
    monkeypatch.setattr(embeddings.time, "sleep", lambda delay: None)

    result = client.post("/responses", {"model": "configured", "input": "hello"}, trace=trace)

    begins = [event for event in events if event[0] == "begin"]
    assert [event[1]["reason"] for event in begins] == ["primary", "transport_retry"]
    assert any(event[0] == "error" for event in events)
    assert result["id"] == "response-1"


def test_relevance_does_not_hide_strict_trace_storage_failure(monkeypatch):
    def fail(*args, **kwargs):
        raise PromptTracePersistenceError("trace database unavailable")

    monkeypatch.setattr(embeddings, "_invoke_versioned_prompt", fail)

    with pytest.raises(PromptTracePersistenceError, match="trace database unavailable"):
        embeddings.classify_domain(
            None,
            None,
            "FlexLife floor?",
            [],
            instructions="x",
            prompt_version=1,
        )


@pytest.mark.parametrize(
    ("replay", "feature"),
    [
        (embeddings.replay_relevance_prompt, "ask-relevance"),
        (embeddings.replay_support_email_prompt, "ask-support-email"),
    ],
)
def test_replay_wrappers_force_replay_origin_without_processing(monkeypatch, replay, feature):
    seen = {}
    monkeypatch.setattr(
        embeddings,
        "_invoke_versioned_prompt",
        lambda **values: seen.update(values) or {"model_output": "raw model text"},
    )

    result = replay(client=None, settings=None, prompt_version=1, instructions="x", inputs={})

    assert seen["feature_key"] == feature
    assert seen["origin"] == "replay"
    assert result == {"model_output": "raw model text"}
