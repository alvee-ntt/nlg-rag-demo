from types import SimpleNamespace

import pytest

from src.rag_layer import prompt_runtime
from src.rag_layer.prompt_runtime import (
    PromptInvocationRecorder,
    PromptTemplateError,
    PromptTracePersistenceError,
    render_prompt_template,
    validate_prompt_template,
)


def test_template_validation_requires_declared_placeholders():
    with pytest.raises(PromptTemplateError, match="missing required"):
        validate_prompt_template(
            "Write an article about <<topic>>",
            required={"topic", "source_context"},
            allowed={"topic", "source_context"},
        )


def test_template_validation_rejects_unknown_and_malformed_placeholders():
    with pytest.raises(PromptTemplateError, match="unknown"):
        validate_prompt_template(
            "Hello <<unexpected>>", required=set(), allowed={"name"}
        )
    with pytest.raises(PromptTemplateError, match="malformed"):
        validate_prompt_template("Hello <<name>", required=set(), allowed={"name"})


def test_template_rendering_is_literal_and_non_recursive():
    rendered = render_prompt_template(
        "Topic: <<topic>>\nSources:\n<<source_context>>",
        {"topic": "Caps <<not_a_template>>", "source_context": "Excerpt"},
        required={"topic", "source_context"},
        allowed={"topic", "source_context"},
    )
    assert rendered == "Topic: Caps <<not_a_template>>\nSources:\nExcerpt"


def test_strict_trace_mode_surfaces_persistence_failure():
    def broken_connect(settings):
        raise RuntimeError("database unavailable")

    with pytest.raises(PromptTracePersistenceError, match="create invocation"):
        PromptInvocationRecorder(
            settings=SimpleNamespace(prompt_trace_failure_mode="strict"),
            feature_key="learn-article",
            origin="live",
            prompt_recipe={"learn.article": 1},
            runtime_inputs={"topic": "caps"},
            connect_fn=broken_connect,
        )


def test_best_effort_trace_mode_allows_live_invocation_to_continue(caplog):
    def broken_connect(settings):
        raise RuntimeError("database unavailable")

    recorder = PromptInvocationRecorder(
        settings=SimpleNamespace(prompt_trace_failure_mode="best_effort"),
        feature_key="learn-article",
        origin="live",
        prompt_recipe={"learn.article": 1},
        runtime_inputs={"topic": "caps"},
        connect_fn=broken_connect,
    )
    recorder.rendered("Rendered")
    assert "Prompt tracing failed" in caplog.text


def test_replay_is_strict_even_when_live_mode_is_best_effort():
    def broken_connect(settings):
        raise RuntimeError("database unavailable")

    with pytest.raises(PromptTracePersistenceError):
        PromptInvocationRecorder(
            settings=SimpleNamespace(prompt_trace_failure_mode="best_effort"),
            feature_key="learn-article",
            origin="replay",
            prompt_recipe={"learn.article": 1},
            runtime_inputs={"topic": "caps"},
            connect_fn=broken_connect,
        )


def test_recorder_writes_parent_then_ordered_attempt(monkeypatch):
    calls = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(
        prompt_runtime,
        "create_prompt_invocation_trace",
        lambda conn, **values: calls.append(("invocation", values)),
    )
    monkeypatch.setattr(
        prompt_runtime,
        "create_prompt_provider_attempt",
        lambda conn, **values: calls.append(("attempt", values)),
    )
    monkeypatch.setattr(
        prompt_runtime,
        "complete_prompt_provider_attempt",
        lambda conn, **values: calls.append(("attempt-complete", values)),
    )

    recorder = PromptInvocationRecorder(
        settings=SimpleNamespace(prompt_trace_failure_mode="strict"),
        feature_key="ask-relevance",
        origin="live",
        prompt_recipe={"ask.relevance": 1},
        runtime_inputs={"message": "Hello"},
        invocation_id="inv-1",
        connect_fn=lambda settings: Connection(),
    )
    attempt = recorder.begin_attempt(
        reason="primary",
        provider="azure_openai",
        requested_model="gpt-test",
        provider_request={"input": "Hello"},
    )
    recorder.complete_attempt(attempt, provider_response={"output_text": "IN_DOMAIN"})

    assert [name for name, _ in calls] == ["invocation", "attempt", "attempt-complete"]
    assert calls[1][1]["attempt_number"] == 1
    assert calls[2][1]["provider_response"]["output_text"] == "IN_DOMAIN"
