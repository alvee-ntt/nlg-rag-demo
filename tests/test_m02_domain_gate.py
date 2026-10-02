"""M02 — out-of-domain guard on the Foundry chat path.

Covers the two new pieces added for M02:
- ``embeddings.classify_domain`` — the IN_DOMAIN / OUT_OF_DOMAIN router, incl. its
  fail-open behavior so a classifier hiccup never blocks a real question.
- ``service.chat_foundry`` — the wrapper that declines off-domain turns *without*
  calling the hosted agent and tags every reply with a ``domain`` flag.

Plus a light, offline integration check that ``POST /v1/foundry/chat`` routes through
the wrapper and its response validates against ``FoundryChatResponse`` (with ``domain``).
All LLM / Foundry calls are mocked; no network, no DB, no Azure.
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


# --- classify_domain -------------------------------------------------------------

def test_classify_domain_out_of_domain(monkeypatch):
    monkeypatch.setattr(embeddings, "_generate", lambda *a, **k: "OUT_OF_DOMAIN")
    assert embeddings.classify_domain(None, None, "write me a poem", []) == "OUT_OF_DOMAIN"


def test_classify_domain_in_domain(monkeypatch):
    monkeypatch.setattr(embeddings, "_generate", lambda *a, **k: "IN_DOMAIN")
    assert embeddings.classify_domain(None, None, "what is the FlexLife floor?", []) == "IN_DOMAIN"


def test_classify_domain_tolerates_extra_text(monkeypatch):
    # The token may arrive wrapped in prose or lower-cased; substring + upper() handles it.
    monkeypatch.setattr(embeddings, "_generate", lambda *a, **k: "Verdict: out_of_domain.")
    assert embeddings.classify_domain(None, None, "capital of France?", []) == "OUT_OF_DOMAIN"


def test_classify_domain_fails_open_on_exception(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("azure hiccup")

    monkeypatch.setattr(embeddings, "_generate", boom)
    # A classifier error must never block a legitimate question -> default IN_DOMAIN.
    assert embeddings.classify_domain(None, None, "FlexLife caps?", []) == "IN_DOMAIN"


def test_classify_domain_fails_open_on_garbage(monkeypatch):
    monkeypatch.setattr(embeddings, "_generate", lambda *a, **k: "¯\\_(ツ)_/¯")
    assert embeddings.classify_domain(None, None, "FlexLife caps?", []) == "IN_DOMAIN"


# --- service.chat_foundry --------------------------------------------------------

_SETTINGS = SimpleNamespace(foundry_agent_name="KnowledgeBase")


def test_chat_foundry_declines_out_of_domain_without_calling_agent(monkeypatch):
    monkeypatch.setattr(service, "classify_domain", lambda *a, **k: "OUT_OF_DOMAIN")

    called = {"foundry": False}

    def must_not_run(*a, **k):
        called["foundry"] = True
        raise AssertionError("foundry.chat must not be called on an out-of-domain turn")

    monkeypatch.setattr(service.foundry, "chat", must_not_run)

    result = service.chat_foundry(
        settings=_SETTINGS, client=None, message="recommend a pasta recipe", history=[]
    )

    assert called["foundry"] is False
    assert result["domain"] == "out_of_domain"
    assert result["status"] == "declined"
    assert result["citations"] == []
    assert result["answer"] == service.DOMAIN_DECLINE
    assert result["agent"] == "KnowledgeBase"


def test_chat_foundry_proxies_in_domain_and_tags_domain(monkeypatch):
    monkeypatch.setattr(service, "classify_domain", lambda *a, **k: "IN_DOMAIN")

    agent_reply = {
        "answer": "FlexLife has a 0% floor [1].",
        "citations": [{"n": 1, "title": "brochure.pdf", "url": "https://x/brochure.pdf"}],
        "agent": "KnowledgeBase",
        "model": "gpt-5",
        "response_id": "resp_123",
        "status": "completed",
    }
    monkeypatch.setattr(service.foundry, "chat", lambda **k: dict(agent_reply))

    result = service.chat_foundry(
        settings=_SETTINGS, client=None, message="what's the FlexLife floor?", history=[]
    )

    assert result["domain"] == "in_domain"
    assert result["citations"] == agent_reply["citations"]  # passed through unchanged
    assert result["answer"] == agent_reply["answer"]
    assert result["status"] == "completed"


def test_chat_foundry_forwards_message_and_history(monkeypatch):
    monkeypatch.setattr(service, "classify_domain", lambda *a, **k: "IN_DOMAIN")

    seen = {}

    def capture(**kwargs):
        seen.update(kwargs)
        return {"answer": "ok", "citations": [], "agent": "KnowledgeBase",
                "model": None, "response_id": None, "status": "completed"}

    monkeypatch.setattr(service.foundry, "chat", capture)

    history = [{"role": "user", "text": "Tell me about FlexLife."}]
    service.chat_foundry(settings=_SETTINGS, client=None, message="and the cap?", history=history)

    assert seen["message"] == "and the cap?"
    assert seen["history"] == history
    assert seen["settings"] is _SETTINGS


def _boom(**kwargs):
    raise RuntimeError("Foundry 400: OBO auth not supported with API key")


def test_chat_foundry_falls_back_to_local_when_foundry_errors(monkeypatch):
    monkeypatch.setattr(service, "classify_domain", lambda *a, **k: "IN_DOMAIN")
    monkeypatch.setattr(service.foundry, "chat", _boom)
    monkeypatch.setattr(service, "chat", lambda **k: {
        "answer": "local grounded answer",
        "follow_ups": ["next?"],
        "sources": [{"document_id": 1, "blob_name": "d.pdf", "citation": "d.pdf | p.1",
                     "chunk_index": 0, "page": 1, "zone": "body", "similarity": 0.8,
                     "preview": "...", "chunk_count": 1}],
        "insufficient_support": False,
    })
    s = SimpleNamespace(foundry_agent_name="KnowledgeBase", rag_search_limit=6)
    out = service.chat_foundry(settings=s, client=None, message="FlexLife floor?", history=[])
    assert out["source_engine"] == "local"
    assert out["answer"] == "local grounded answer"
    assert out["citations"] == [] and len(out["sources"]) == 1
    assert out["domain"] == "in_domain"
    assert out["escalate"] is False


def test_chat_foundry_fallback_maps_insufficient_support_to_escalate(monkeypatch):
    monkeypatch.setattr(service, "classify_domain", lambda *a, **k: "IN_DOMAIN")
    monkeypatch.setattr(service.foundry, "chat", _boom)
    monkeypatch.setattr(service, "chat", lambda **k: {
        "answer": service.NLG_SUPPORT_MESSAGE, "follow_ups": [], "sources": [],
        "insufficient_support": True,
    })
    s = SimpleNamespace(foundry_agent_name="KnowledgeBase", rag_search_limit=6)
    out = service.chat_foundry(settings=s, client=None, message="x", history=[])
    assert out["source_engine"] == "local"
    assert out["escalate"] is True
    assert out["escalate_reason"] == "insufficient_support"


# --- endpoint routing (offline; app.state set by hand, lifespan not run) ----------

@pytest.fixture()
def client(monkeypatch):
    from starlette.testclient import TestClient

    from src.rag_layer import server

    app = server.app
    # No sign-in gate, no DB, no Azure: set just the state the endpoint reads.
    app.state.auth = None
    app.state.openai_client = object()
    app.state.settings = SimpleNamespace(
        foundry_agent_name="KnowledgeBase",
        foundry_project_endpoint="https://foundry.example/project",
        foundry_api_key="key",
    )
    # Not entering the context manager means lifespan (init_db / model warm) never runs.
    return TestClient(app)


def test_endpoint_out_of_domain(client, monkeypatch):
    from src.rag_layer import server

    monkeypatch.setattr(server, "chat_foundry", lambda **k: {
        "answer": service.DOMAIN_DECLINE, "citations": [], "agent": "KnowledgeBase",
        "model": None, "response_id": None, "status": "declined", "domain": "out_of_domain",
    })

    resp = client.post("/v1/foundry/chat", json={"message": "write a haiku", "history": []})
    assert resp.status_code == 200
    body = resp.json()
    assert body["domain"] == "out_of_domain"
    assert body["citations"] == []
    assert body["status"] == "declined"


def test_endpoint_in_domain(client, monkeypatch):
    from src.rag_layer import server

    monkeypatch.setattr(server, "chat_foundry", lambda **k: {
        "answer": "FlexLife floor is 0% [1].",
        "citations": [{"n": 1, "title": "brochure.pdf", "url": "https://x/brochure.pdf"}],
        "agent": "KnowledgeBase", "model": "gpt-5", "response_id": "resp_1",
        "status": "completed", "domain": "in_domain",
    })

    resp = client.post("/v1/foundry/chat", json={"message": "FlexLife floor?", "history": []})
    assert resp.status_code == 200
    body = resp.json()
    assert body["domain"] == "in_domain"
    assert body["answer"].startswith("FlexLife floor")
    assert len(body["citations"]) == 1


def test_endpoint_503_when_foundry_unconfigured(client):
    from src.rag_layer import server

    server.app.state.settings = SimpleNamespace(
        foundry_agent_name="KnowledgeBase",
        foundry_project_endpoint="",
        foundry_api_key="",
    )
    resp = client.post("/v1/foundry/chat", json={"message": "FlexLife floor?", "history": []})
    assert resp.status_code == 503
