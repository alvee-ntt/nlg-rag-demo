"""Question tracker — category classifier, outcome mapping, and endpoint wiring.

Covers classify_category (parsing + fallback), outcome_of / source_titles (the mapping
from a chat result to a tracked row), track_question's never-raise contract, and that
POST /v1/foundry/chat and the handoff endpoints call into the tracker. No network/LLM/DB.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rag_layer import question_tracker as qt  # noqa: E402


# --- classify_category -----------------------------------------------------------

def test_classify_category_matches_known_name(monkeypatch):
    monkeypatch.setattr(qt, "_generate", lambda *a, **k: "Category: riders & living benefits.")
    assert qt.classify_category(None, None, "What does the chronic illness rider pay?", []) == "Riders & living benefits"


def test_classify_category_falls_back_to_other(monkeypatch):
    monkeypatch.setattr(qt, "_generate", lambda *a, **k: "no idea")
    assert qt.classify_category(None, None, "hmm", []) == qt.OTHER_CATEGORY


def test_classify_category_prompt_lists_every_category(monkeypatch):
    seen = {}
    monkeypatch.setattr(qt, "_generate", lambda client, settings, prompt: seen.update(prompt=prompt) or "Other")
    qt.classify_category(None, None, "and the floor?", [{"role": "user", "text": "FlexLife caps?"}])
    for name in qt.CATEGORIES:
        assert name in seen["prompt"]
    assert "Agent: FlexLife caps?" in seen["prompt"]


# --- result -> tracked row -------------------------------------------------------

@pytest.mark.parametrize("result,expected", [
    ({"domain": "out_of_domain", "escalate": False}, "out_of_domain"),
    ({"domain": "in_domain", "escalate": True}, "unanswered"),
    ({"domain": "in_domain", "escalate": False}, "answered"),
])
def test_outcome_of(result, expected):
    assert qt.outcome_of(result) == expected


def test_source_titles_merges_and_dedupes_both_engines():
    result = {
        "citations": [{"n": 1, "title": "guide.pdf", "url": "u1"}, {"n": 2, "title": "guide.pdf", "url": "u2"}],
        "sources": [{"blob_name": "Riders Information/abr.pdf"}],
    }
    assert qt.source_titles(result) == ["guide.pdf", "abr.pdf"]


class _Conn:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return SimpleNamespace(fetchone=lambda: {"id": 7})


def test_track_question_records_and_queues_categorization(monkeypatch):
    conn = _Conn()
    monkeypatch.setattr(qt, "connect", lambda settings: conn)
    tasks = BackgroundTasks()
    question_id = qt.track_question(
        settings=None, client=None, background_tasks=tasks, user_id="demo-user",
        question="FlexLife floor?", history=[],
        result={"domain": "in_domain", "escalate": True, "citations": [], "trace_request_id": "req-1"},
    )
    assert question_id == 7
    params = conn.calls[0][1]
    assert params[:4] == ("demo-user", "FlexLife floor?", None, "unanswered")
    assert len(tasks.tasks) == 1


def test_track_question_skips_classifier_for_off_topic(monkeypatch):
    conn = _Conn()
    monkeypatch.setattr(qt, "connect", lambda settings: conn)
    tasks = BackgroundTasks()
    qt.track_question(
        settings=None, client=None, background_tasks=tasks, user_id="demo-user",
        question="write a haiku", history=[], result={"domain": "out_of_domain"},
    )
    assert conn.calls[0][1][2:4] == (qt.OFF_TOPIC_CATEGORY, "out_of_domain")
    assert tasks.tasks == []


def test_track_question_never_raises(monkeypatch):
    def boom(settings):
        raise RuntimeError("db down")

    monkeypatch.setattr(qt, "connect", boom)
    assert qt.track_question(
        settings=None, client=None, background_tasks=BackgroundTasks(), user_id="u",
        question="q", history=[], result={},
    ) is None


def test_mark_support_rejects_unknown_status():
    with pytest.raises(ValueError):
        qt.mark_support(_Conn(), 1, "resolved")


# --- answer feedback (thumbs) ----------------------------------------------------

def test_record_feedback_down_keeps_and_filters_reasons():
    conn = _Conn()
    qt.record_feedback(conn, question_id=7, user_id="demo-user", vote="down",
                       reasons=["Incorrect", "made up", "Outdated"], comment="  too vague  ")
    sql, params = conn.calls[0]
    assert "ON CONFLICT (question_id) DO UPDATE" in sql
    assert params[0] == 7 and params[1] == "demo-user" and params[2] == "down"
    assert params[3].obj == ["Incorrect", "Outdated"]  # unknown reason dropped
    assert params[4] == "too vague"


def test_record_feedback_up_drops_reasons_and_comment():
    conn = _Conn()
    qt.record_feedback(conn, question_id=7, user_id="u", vote="up",
                       reasons=["Incorrect"], comment="nice")
    params = conn.calls[0][1]
    assert params[2] == "up" and params[3].obj == [] and params[4] is None


def test_record_feedback_rejects_bad_vote():
    with pytest.raises(ValueError):
        qt.record_feedback(_Conn(), question_id=1, user_id="u", vote="meh")


def test_clear_feedback_deletes_row():
    conn = _Conn()
    qt.clear_feedback(conn, 7)
    sql, params = conn.calls[0]
    assert "DELETE FROM ask_answer_feedback" in sql and params == (7,)


# --- endpoint wiring (offline; app.state set by hand, lifespan not run) -----------

@pytest.fixture()
def client(monkeypatch):
    from starlette.testclient import TestClient

    from src.rag_layer import server

    app = server.app
    app.state.openai_client = object()
    app.state.settings = SimpleNamespace(
        foundry_agent_name="KnowledgeBase",
        foundry_project_endpoint="https://foundry.example/project",
        foundry_api_key="key",
    )
    app.state.auth = SimpleNamespace(
        gate=lambda request: None,
        trace_session_id=lambda request: "session-1",
    )
    monkeypatch.setattr(server, "connect", lambda settings: _Conn())
    monkeypatch.setattr(server, "get_ask_user_context", lambda conn, user_id: {"about_me": "", "memories": []})
    monkeypatch.setattr(server, "get_selected_prompt", lambda conn, key: {
        "key": key, "version": 1, "instructions": "x",
    })
    return TestClient(app)


def test_chat_endpoint_returns_question_id(client, monkeypatch):
    from src.rag_layer import server

    seen = {}
    monkeypatch.setattr(server, "chat_foundry", lambda **k: {
        "answer": "0% [1].", "citations": [{"n": 1, "title": "b.pdf", "url": "https://x/b.pdf"}],
        "agent": "KnowledgeBase", "domain": "in_domain", "escalate": False,
    })
    monkeypatch.setattr(server, "track_question", lambda **k: seen.update(k) or 42)
    resp = client.post("/v1/foundry/chat", json={"message": " FlexLife floor? ", "history": []})
    assert resp.status_code == 200
    assert resp.json()["question_id"] == 42
    assert seen["question"] == "FlexLife floor?"
    assert seen["result"]["domain"] == "in_domain"


def test_handoff_endpoints_mark_support(client, monkeypatch):
    from src.rag_layer import server

    marks = []
    monkeypatch.setattr(server, "mark_support_safely", lambda settings, qid, status: marks.append((qid, status)))
    monkeypatch.setattr(server, "draft_support_email", lambda **k: {
        "to": "s@example.com", "subject": "S", "body": "B", "reason": k["reason"],
    })
    assert client.post("/v1/handoff/draft", json={"question": "q?", "question_id": 42}).status_code == 200
    assert client.post("/v1/handoff/sent", json={"question_id": 42}).status_code == 204
    assert marks == [(42, "drafted"), (42, "sent")]


def test_feedback_endpoint_records_a_vote(client, monkeypatch):
    from src.rag_layer import server

    seen = {}
    monkeypatch.setattr(server, "question_exists", lambda conn, qid: True)
    monkeypatch.setattr(server, "record_feedback", lambda conn, **k: seen.update(k))
    resp = client.post("/v1/ask/feedback", json={
        "question_id": 7, "vote": "down", "reasons": ["Incorrect"], "comment": "x"})
    assert resp.status_code == 204
    assert seen["question_id"] == 7 and seen["vote"] == "down" and seen["reasons"] == ["Incorrect"]


def test_feedback_endpoint_rejects_unknown_question(client, monkeypatch):
    from src.rag_layer import server

    monkeypatch.setattr(server, "question_exists", lambda conn, qid: False)
    resp = client.post("/v1/ask/feedback", json={"question_id": 999, "vote": "up"})
    assert resp.status_code == 404


def test_feedback_clear_endpoint(client, monkeypatch):
    from src.rag_layer import server

    cleared = []
    monkeypatch.setattr(server, "clear_feedback", lambda conn, qid: cleared.append(qid))
    resp = client.delete("/v1/ask/feedback/7")
    assert resp.status_code == 204
    assert cleared == [7]
