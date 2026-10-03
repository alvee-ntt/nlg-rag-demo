"""M03 — citations & abstention across both tracks.

Foundry track: max_sources trim + abstention/escalate post-processing in foundry.chat.
Local track: select_citations (filter/aggregate/cap), format_sources shape, and the
insufficient-support abstention in service.answer / service.chat.

All network/LLM calls are mocked; no Azure, no DB.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rag_layer import embeddings, foundry, service  # noqa: E402
from src.rag_layer.config import NLG_SUPPORT_MESSAGE  # noqa: E402


# --- Foundry track ---------------------------------------------------------------

class _FakeClient:
    agent = "KnowledgeBase"

    def __init__(self, settings):
        pass

    def respond(self, payload):
        return {"model": "gpt-5", "id": "resp_1", "status": "completed"}


def _foundry_env(monkeypatch, *, answer, citations):
    monkeypatch.setattr(foundry, "FoundryAgentClient", _FakeClient)
    monkeypatch.setattr(foundry, "_extract_answer", lambda data: (answer, citations))
    monkeypatch.setattr(foundry, "_RequestTrace", lambda **kwargs: SimpleNamespace(
        request_id="request-1",
        provider_request=lambda payload: None,
        response=lambda data: None,
        error=lambda exc: None,
    ))


def _cites(n):
    return [{"n": i + 1, "title": f"doc{i}.pdf", "url": f"https://x/doc{i}.pdf"} for i in range(n)]


def test_foundry_trims_to_max_sources(monkeypatch):
    _foundry_env(monkeypatch, answer="FlexLife has a floor [1][2][3][4][5].", citations=_cites(5))
    settings = SimpleNamespace(max_sources=3, foundry_agent_name="KnowledgeBase")
    out = foundry.chat(settings=settings, message="floor?", history=[])
    assert len(out["citations"]) == 3
    assert [c["n"] for c in out["citations"]] == [1, 2, 3]  # order/numbering preserved
    assert out["escalate"] is False
    assert out["escalate_reason"] is None


def test_foundry_abstains_on_zero_citations(monkeypatch):
    _foundry_env(monkeypatch, answer="Here is some ungrounded prose.", citations=[])
    settings = SimpleNamespace(max_sources=4, foundry_agent_name="KnowledgeBase")
    out = foundry.chat(settings=settings, message="unanswerable?", history=[])
    assert out["answer"] == NLG_SUPPORT_MESSAGE
    assert out["citations"] == []
    assert out["escalate"] is True
    assert out["escalate_reason"] == "no_citations"


def test_foundry_abstains_on_empty_answer_marker(monkeypatch):
    _foundry_env(monkeypatch, answer="(the agent returned no text)", citations=_cites(2))
    settings = SimpleNamespace(max_sources=4, foundry_agent_name="KnowledgeBase")
    out = foundry.chat(settings=settings, message="?", history=[])
    assert out["escalate"] is True
    assert out["escalate_reason"] == "empty_answer"
    assert out["citations"] == []


def test_foundry_passes_through_grounded_answer(monkeypatch):
    _foundry_env(monkeypatch, answer="FlexLife floor is 0% [1].", citations=_cites(1))
    settings = SimpleNamespace(max_sources=4, foundry_agent_name="KnowledgeBase")
    out = foundry.chat(settings=settings, message="floor?", history=[])
    assert out["answer"] == "FlexLife floor is 0% [1]."
    assert out["escalate"] is False
    assert len(out["citations"]) == 1


# --- Local track: select_citations ----------------------------------------------

_LOCAL = SimpleNamespace(min_similarity=0.30, max_sources=2)


def _row(doc_id, sim, idx=0, blob=None, content="body text"):
    return {
        "document_id": doc_id,
        "blob_name": blob or f"doc{doc_id}.pdf",
        "chunk_index": idx,
        "content": content,
        "metadata": {"page": 3, "heading_path": "Caps"},
        "similarity": sim,
    }


def test_select_filters_below_floor():
    ctx = [_row(1, 0.9), _row(2, 0.10)]
    out = service.select_citations(ctx, settings=_LOCAL)
    assert [r["document_id"] for r in out] == [1]


def test_select_aggregates_chunks_per_document():
    # Two chunks of doc 1 (best 0.9) + one chunk of doc 2 (0.5).
    ctx = [_row(1, 0.9, idx=0), _row(1, 0.6, idx=1), _row(2, 0.5, idx=0)]
    out = service.select_citations(ctx, settings=_LOCAL)
    assert [r["document_id"] for r in out] == [1, 2]        # best-first
    doc1 = next(r for r in out if r["document_id"] == 1)
    assert doc1["chunk_count"] == 2                          # both doc-1 chunks counted
    assert doc1["similarity"] == 0.9                         # best chunk retained


def test_select_caps_at_max_sources():
    ctx = [_row(1, 0.9), _row(2, 0.8), _row(3, 0.7)]
    out = service.select_citations(ctx, settings=_LOCAL)     # max_sources=2
    assert len(out) == 2
    assert [r["document_id"] for r in out] == [1, 2]


def test_select_returns_empty_when_all_below_floor():
    ctx = [_row(1, 0.2), _row(2, 0.05)]
    assert service.select_citations(ctx, settings=_LOCAL) == []


def test_format_sources_shape():
    ctx = [{**_row(7, 0.83, idx=2), "chunk_count": 3, "content": "x" * 600}]
    out = service.format_sources(ctx)
    s = out[0]
    assert s["document_id"] == 7
    assert s["chunk_count"] == 3
    assert len(s["preview"]) == 500                          # truncated to 500 chars
    assert s["page"] == 3 and s["zone"] == "body"
    assert "doc7.pdf" in s["citation"]


def test_format_sources_missing_metadata_falls_back_to_chunk_label():
    row = {"document_id": 1, "blob_name": "d.pdf", "chunk_index": 4,
           "content": "c", "metadata": None, "similarity": 0.5}
    s = service.format_sources([row])[0]
    assert s["citation"].endswith("chunk-4")                 # no page/heading -> chunk label
    assert s["zone"] == "body"


# --- Local track: abstention in service.answer / service.chat --------------------

def test_answer_abstains_without_calling_llm(monkeypatch):
    monkeypatch.setattr(service, "retrieve_contexts", lambda **k: [_row(1, 0.10)])  # below floor
    called = {"llm": False}

    def guard(*a, **k):
        called["llm"] = True
        raise AssertionError("LLM must not run when abstaining")

    monkeypatch.setattr(service, "answer_with_context", guard)
    res = service.answer(settings=_LOCAL, client=None, question="q", limit=6)
    assert res["insufficient_support"] is True
    assert res["sources"] == []
    assert res["answer"] == NLG_SUPPORT_MESSAGE
    assert called["llm"] is False


def test_answer_success_cites_selected_documents(monkeypatch):
    ctx = [_row(1, 0.9, idx=0), _row(1, 0.6, idx=1), _row(2, 0.7, idx=0)]
    monkeypatch.setattr(service, "retrieve_contexts", lambda **k: ctx)
    monkeypatch.setattr(service, "answer_with_context",
                        lambda client, settings, q, chunks: f"answer over {len(chunks)} chunks")
    res = service.answer(settings=_LOCAL, client=None, question="q", limit=6)
    assert res["insufficient_support"] is False
    assert len(res["sources"]) == 2                          # two distinct docs
    assert "3 chunks" in res["answer"]                       # generated from all kept chunks


def test_chat_abstains_without_calling_llm(monkeypatch):
    monkeypatch.setattr(service, "retrieve_contexts", lambda **k: [_row(1, 0.1)])

    def guard(*a, **k):
        raise AssertionError("LLM must not run when abstaining")

    monkeypatch.setattr(service, "chat_with_context", guard)
    res = service.chat(settings=_LOCAL, client=None, message="hi", history=[], limit=6)
    assert res["insufficient_support"] is True
    assert res["sources"] == []
    assert res["follow_ups"] == []
    assert res["answer"] == NLG_SUPPORT_MESSAGE


def test_chat_success_returns_sources_and_flag(monkeypatch):
    ctx = [_row(1, 0.9), _row(2, 0.7)]
    monkeypatch.setattr(service, "retrieve_contexts", lambda **k: ctx)
    monkeypatch.setattr(service, "chat_with_context",
                        lambda client, settings, msg, hist, chunks: {"answer": "ok", "follow_ups": ["next?"], "grounded": True})
    res = service.chat(settings=_LOCAL, client=None, message="floor?", history=[], limit=6)
    assert res["insufficient_support"] is False
    assert res["answer"] == "ok"
    assert res["follow_ups"] == ["next?"]
    assert len(res["sources"]) == 2


def test_chat_abstains_when_model_reports_not_grounded(monkeypatch):
    # Chunks clear the similarity floor, but the model says they don't answer the question.
    ctx = [_row(1, 0.9), _row(2, 0.7)]
    monkeypatch.setattr(service, "retrieve_contexts", lambda **k: ctx)
    monkeypatch.setattr(service, "chat_with_context", lambda client, settings, msg, hist, chunks: {
        "answer": "The approved FlexLife material doesn't cover that — contact NLG support.",
        "follow_ups": ["x?"], "grounded": False,
    })
    res = service.chat(settings=_LOCAL, client=None, message="crypto premiums?", history=[], limit=6)
    assert res["insufficient_support"] is True   # local endpoint reports abstention
    assert res["sources"] == []                  # non-supporting chunks are dropped
    assert res["follow_ups"] == []
    assert "NLG support" in res["answer"]         # keeps the model's helpful decline


def test_chat_with_context_parses_grounded_false(monkeypatch):
    monkeypatch.setattr(embeddings, "_generate",
                        lambda *a, **k: '{"answer": "not covered", "grounded": false, "follow_ups": []}')
    out = embeddings.chat_with_context(None, None, "q", [], [])
    assert out["grounded"] is False
    assert out["answer"] == "not covered"


def test_chat_with_context_grounded_defaults_true_on_bad_json(monkeypatch):
    monkeypatch.setattr(embeddings, "_generate", lambda *a, **k: "plain prose, not json")
    out = embeddings.chat_with_context(None, None, "q", [], [])
    assert out["grounded"] is True   # fail-open: don't over-escalate on a parse hiccup
