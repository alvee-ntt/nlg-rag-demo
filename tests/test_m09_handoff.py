"""M09 — NLG Support handoff (draft email).

Covers generate_support_email (prompt framing + JSON parse/fallback),
draft_support_email (shape + configured recipient), and the POST /v1/handoff/draft
endpoint (routing, validation, error mapping). No network/LLM/DB — all mocked.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rag_layer import embeddings, service  # noqa: E402


# --- generate_support_email ------------------------------------------------------

def test_generate_support_email_parses_json(monkeypatch):
    monkeypatch.setattr(embeddings, "_invoke_versioned_prompt",
                        lambda *a, **k: {"model_output": '{"subject": "Reinstatement", "body": "Hello NLG, I need..."}'})
    out = embeddings.generate_support_email(None, None, "q?", [], [], "manual", instructions="x", prompt_version=1)
    assert out["subject"] == "Reinstatement"
    assert out["body"].startswith("Hello NLG")


def test_generate_support_email_falls_back_on_bad_json(monkeypatch):
    monkeypatch.setattr(embeddings, "_invoke_versioned_prompt", lambda *a, **k: {"model_output": "not json, just prose"})
    out = embeddings.generate_support_email(None, None, "q?", [], [], "manual", instructions="x", prompt_version=1)
    assert out["body"] == "not json, just prose"
    assert out["subject"]  # a non-empty fallback subject


def test_generate_support_email_reason_shapes_prompt(monkeypatch):
    seen = {}
    monkeypatch.setattr(embeddings, "_invoke_versioned_prompt",
                        lambda *args, **kwargs: seen.update(inputs=kwargs["inputs"]) or {"model_output": '{"subject":"s","body":"b"}'})

    embeddings.generate_support_email(None, None, "q?", [], [], "case_specific", instructions="x", prompt_version=1)
    assert "authoritative, case-specific decision" in seen["inputs"]["reason_note"]

    embeddings.generate_support_email(None, None, "q?", [], [], "insufficient", instructions="x", prompt_version=1)
    assert "could not find this in the Knowledge Foundation" in seen["inputs"]["reason_note"]


def test_generate_support_email_prompt_includes_history(monkeypatch):
    seen = {}
    monkeypatch.setattr(embeddings, "_invoke_versioned_prompt",
                        lambda *args, **kwargs: seen.update(inputs=kwargs["inputs"]) or {"model_output": '{"subject":"s","body":"b"}'})
    history = [{"role": "user", "text": "Does FlexLife allow reinstatement?"}]
    embeddings.generate_support_email(None, None, "back-dating?", history, [], "manual", instructions="x", prompt_version=1)
    # _history_text renders user turns as "Agent:" lines
    assert "Agent: Does FlexLife allow reinstatement?" in seen["inputs"]["history"]
    assert seen["inputs"]["question"] == "back-dating?"


# --- draft_support_email ---------------------------------------------------------

def test_draft_support_email_shape_and_recipient(monkeypatch):
    monkeypatch.setattr(service, "retrieve_contexts", lambda **k: [])
    monkeypatch.setattr(service, "generate_support_email",
                        lambda *a, **k: {"subject": "Subj", "body": "Body"})
    settings = SimpleNamespace(nlg_support_email="flexlife-support@example.com")
    out = service.draft_support_email(
        settings=settings, client=None, question="q?", history=[], reason="manual", limit=6
    )
    assert out == {"to": "flexlife-support@example.com", "subject": "Subj",
                   "body": "Body", "reason": "manual"}


# --- endpoint (offline; app.state set by hand, lifespan not run) ------------------

@pytest.fixture()
def client(monkeypatch):
    from starlette.testclient import TestClient
    from src.rag_layer import server

    app = server.app
    app.state.auth = None
    app.state.openai_client = object()
    app.state.settings = SimpleNamespace(nlg_support_email="flexlife-support@example.com")

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(server, "connect", lambda settings: Connection())
    monkeypatch.setattr(server, "get_selected_prompt", lambda conn, key: {
        "key": key, "version": 1, "instructions": "Support template",
    })
    return TestClient(app)


def test_endpoint_returns_draft(client, monkeypatch):
    from src.rag_layer import server

    monkeypatch.setattr(server, "draft_support_email", lambda **k: {
        "to": "flexlife-support@example.com", "subject": "S", "body": "B", "reason": k["reason"],
    })
    resp = client.post("/v1/handoff/draft", json={
        "question": "Can premiums be back-dated on reinstatement?",
        "reason": "case_specific",
        "history": [{"role": "user", "text": "Does FlexLife allow reinstatement?"}],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["to"] == "flexlife-support@example.com"
    assert body["reason"] == "case_specific"


def test_endpoint_rejects_empty_question(client):
    resp = client.post("/v1/handoff/draft", json={"question": "", "history": []})
    assert resp.status_code == 422


def test_endpoint_rejects_too_much_history(client):
    history = [{"role": "user", "text": f"q{i}"} for i in range(13)]  # max_length=12
    resp = client.post("/v1/handoff/draft", json={"question": "q?", "history": history})
    assert resp.status_code == 422


def test_endpoint_maps_provider_error_to_500(client, monkeypatch):
    from src.rag_layer import server

    def boom(**k):
        raise RuntimeError("azure down")

    monkeypatch.setattr(server, "draft_support_email", boom)
    resp = client.post("/v1/handoff/draft", json={"question": "q?", "history": []})
    assert resp.status_code == 500
    assert "azure down" in resp.json()["detail"]
