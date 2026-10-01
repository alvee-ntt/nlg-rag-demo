from __future__ import annotations

import re
from typing import Any

from . import foundry
from .config import NLG_SUPPORT_MESSAGE, Settings
from .db import (
    citation,
    connect,
    get_document_chunks,
    list_documents,
    search_chunks,
)
from .embeddings import (
    AzureOpenAIClient,
    answer_with_context,
    chat_with_context,
    classify_domain,
    embed_texts,
    factcheck_claim,
    generate_support_email,
    parse_verdict,
)

# Shown when the out-of-domain guard declines a turn (M02). Kept short and redirecting.
DOMAIN_DECLINE = (
    "I'm the FlexLife Navigator, so I can only help with FlexLife products, "
    "riders, and approved wording. Ask me anything about that."
)


def retrieve_contexts(
    *,
    settings: Settings,
    client: AzureOpenAIClient,
    text: str,
    limit: int,
) -> list[dict[str, Any]]:
    query_embedding = embed_texts(client, settings, [text])[0]
    with connect(settings) as conn:
        return search_chunks(conn, query_embedding, limit=limit)


def format_sources(contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "document_id": row.get("document_id"),
            "blob_name": row["blob_name"],
            "chunk_index": row["chunk_index"],
            "citation": citation(row),
            "page": (row.get("metadata") or {}).get("page"),
            "zone": (row.get("metadata") or {}).get("zone", "body"),
            "similarity": float(row["similarity"]),
            "preview": row["content"][:500],
            "chunk_count": row.get("chunk_count"),
        }
        for row in contexts
    ]


def select_citations(
    contexts: list[dict[str, Any]], *, settings: Settings
) -> list[dict[str, Any]]:
    """Choose the citations for a grounded answer (M03, local track).

    Filters chunks below ``min_similarity``, aggregates the survivors to distinct
    documents (keeping each document's best-matching chunk for the locator/preview and
    counting how many chunks contributed), then returns the top ``max_sources``
    documents best-similarity first. Contexts arrive best-first from ``search_chunks``.
    Returns ``[]`` when nothing clears the floor — the caller's abstention signal.
    """
    by_doc: dict[Any, dict[str, Any]] = {}
    for row in contexts:
        if float(row["similarity"]) < settings.min_similarity:
            continue
        doc_id = row.get("document_id")
        best = by_doc.get(doc_id)
        if best is None:
            by_doc[doc_id] = {**row, "chunk_count": 1}
        else:
            best["chunk_count"] += 1
            if float(row["similarity"]) > float(best["similarity"]):
                by_doc[doc_id] = {**row, "chunk_count": best["chunk_count"]}
    ranked = sorted(by_doc.values(), key=lambda r: float(r["similarity"]), reverse=True)
    return ranked[: settings.max_sources]


def search(
    *,
    settings: Settings,
    client: AzureOpenAIClient,
    query: str,
    limit: int,
) -> dict[str, Any]:
    contexts = retrieve_contexts(settings=settings, client=client, text=query, limit=limit)
    return {"sources": format_sources(contexts)}


def answer(
    *,
    settings: Settings,
    client: AzureOpenAIClient,
    question: str,
    limit: int,
) -> dict[str, Any]:
    contexts = retrieve_contexts(settings=settings, client=client, text=question, limit=limit)
    selected = select_citations(contexts, settings=settings)
    if not selected:
        return {"answer": NLG_SUPPORT_MESSAGE, "sources": [], "insufficient_support": True}
    kept = [c for c in contexts if float(c["similarity"]) >= settings.min_similarity]
    return {
        "answer": answer_with_context(client, settings, question, kept),
        "sources": format_sources(selected),
        "insufficient_support": False,
    }


def chat(
    *,
    settings: Settings,
    client: AzureOpenAIClient,
    message: str,
    history: list[dict[str, Any]],
    limit: int,
) -> dict[str, Any]:
    """Chat-sized grounded reply for the in-app Ask Navigator sheet.

    Retrieval uses the latest message plus the previous agent message so short
    follow-ups like "and the floor?" still land on the right chunks.
    """
    prior_user = [t["text"] for t in history if t.get("role") == "user" and t.get("text")]
    query = f"{prior_user[-1]}\n{message}" if prior_user else message
    contexts = retrieve_contexts(settings=settings, client=client, text=query, limit=limit)
    selected = select_citations(contexts, settings=settings)
    if not selected:
        return {
            "answer": NLG_SUPPORT_MESSAGE,
            "follow_ups": [],
            "sources": [],
            "insufficient_support": True,
        }
    kept = [c for c in contexts if float(c["similarity"]) >= settings.min_similarity]
    reply = chat_with_context(client, settings, message, history, kept)
    return {**reply, "sources": format_sources(selected), "insufficient_support": False}


def chat_foundry(
    *,
    settings: Settings,
    client: AzureOpenAIClient,
    message: str,
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    """Foundry chat with an in-repo out-of-domain guard (M02).

    The turn is classified first; a clearly non-FlexLife request is declined here
    without ever calling the hosted agent, so the app does not behave as a
    general-purpose chatbot. In-domain turns proxy to the agent unchanged. Either
    way the reply carries a ``domain`` flag so the UI and tests can tell an
    answered turn from a decline.
    """
    if classify_domain(client, settings, message, history) == "OUT_OF_DOMAIN":
        return {
            "answer": DOMAIN_DECLINE,
            "citations": [],
            "sources": [],
            "agent": settings.foundry_agent_name,
            "model": None,
            "response_id": None,
            "status": "declined",
            "domain": "out_of_domain",
            "escalate": False,
            "escalate_reason": None,
            "source_engine": "gate",
        }
    try:
        result = foundry.chat(settings=settings, message=message, history=history)
        return {**result, "sources": [], "domain": "in_domain", "source_engine": "foundry"}
    except Exception:  # noqa: BLE001
        # Foundry is unavailable or misconfigured (e.g. the hosted agent's OBO-auth
        # setting rejects API-key calls). Rather than fail the turn, serve the local
        # grounded pipeline — same corpus-grounded, cited, abstaining behavior — and map
        # it into the Foundry response shape so the UI renders it unchanged.
        local = chat(
            settings=settings,
            client=client,
            message=message,
            history=history,
            limit=settings.rag_search_limit,
        )
        insufficient = bool(local.get("insufficient_support"))
        return {
            "answer": local["answer"],
            "citations": [],
            "sources": local.get("sources", []),
            "agent": settings.foundry_agent_name,
            "model": None,
            "response_id": None,
            "status": "local_fallback",
            "domain": "in_domain",
            "escalate": insufficient,
            "escalate_reason": "insufficient_support" if insufficient else None,
            "source_engine": "local",
        }


def draft_support_email(
    *,
    settings: Settings,
    client: AzureOpenAIClient,
    question: str,
    history: list[dict[str, Any]],
    reason: str,
    limit: int,
) -> dict[str, Any]:
    """Prepare (not send) a draft NLG Support email from the conversation (M09).

    Retrieval is used only to let the draft describe what the corpus could and could
    not confirm; the email itself is a one-shot local completion. Nothing is sent.
    """
    contexts = retrieve_contexts(settings=settings, client=client, text=question, limit=limit)
    draft = generate_support_email(client, settings, question, history, contexts, reason)
    return {
        "to": settings.nlg_support_email,
        "subject": draft["subject"],
        "body": draft["body"],
        "reason": reason,
    }


def _prefix(blob_name: str) -> str:
    return blob_name.split("/", 1)[0] if "/" in blob_name else ""


def corpus(*, settings: Settings) -> dict[str, Any]:
    """List every document with chunk statistics, for the corpus inspector."""
    with connect(settings) as conn:
        rows = list_documents(conn)
    documents = [
        {
            "id": row["id"],
            "blob_name": row["blob_name"],
            "prefix": _prefix(row["blob_name"]),
            "size_bytes": row["size_bytes"],
            "chunk_count": row["chunk_count"],
            "words_total": row["words_total"],
            "chars_avg": row["chars_avg"],
            "chars_min": row["chars_min"],
            "chars_max": row["chars_max"],
            "page_count": row["page_count"],
            "zones": row["zones"] or [],
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }
        for row in rows
    ]
    return {
        "totals": {
            "documents": len(documents),
            "chunks": sum(d["chunk_count"] for d in documents),
            "words": sum(d["words_total"] for d in documents),
        },
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "documents": documents,
    }


def document_chunks(*, settings: Settings, document_id: int) -> dict[str, Any] | None:
    """A single document's chunks in order, with size and structure metadata."""
    with connect(settings) as conn:
        result = get_document_chunks(conn, document_id)
    if result is None:
        return None
    doc = result["document"]
    chunks = [
        {
            "chunk_index": c["chunk_index"],
            "chars": c["chars"],
            "words": c["words"],
            "page": (c["metadata"] or {}).get("page"),
            "zone": (c["metadata"] or {}).get("zone", "body"),
            "heading_path": (c["metadata"] or {}).get("heading_path", ""),
            "content": c["content"],
        }
        for c in result["chunks"]
    ]
    return {
        "document": {
            "id": doc["id"],
            "blob_name": doc["blob_name"],
            "prefix": _prefix(doc["blob_name"]),
            "size_bytes": doc["size_bytes"],
            "chunk_count": len(chunks),
            "updated_at": doc["updated_at"].isoformat() if doc["updated_at"] else None,
        },
        "chunks": chunks,
    }


def fact_check(
    *,
    settings: Settings,
    client: AzureOpenAIClient,
    claim: str,
    limit: int,
) -> dict[str, Any]:
    contexts = retrieve_contexts(settings=settings, client=client, text=claim, limit=limit)
    report = factcheck_claim(client, settings, claim, contexts)
    return {
        "verdict": parse_verdict(report),
        "report": report,
        "sources": format_sources(contexts),
    }


# A speaker label is a short name/role at the start of a line, ending in a colon:
# "Agent: ...", "Claims Rep: ...", "John Doe: ...". Kept deliberately narrow so it
# does not eat real sentences that happen to contain a colon ("Here's the deal: ...").
_SPEAKER_LABEL = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 ._-]{0,39}?):\s+(.*\S)\s*$")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_HAS_LETTER = re.compile(r"[A-Za-z]")


def split_statements(
    transcript: str, *, speaker: str | None = None
) -> list[dict[str, str | None]]:
    """Break a transcript into individually checkable statements.

    Splits on lines first; a line with no newline siblings is further split into
    sentences so a pasted paragraph still yields separate claims. A leading speaker
    label ("Agent: ...") is captured as ``speaker`` and stripped from the statement.
    When ``speaker`` is given, only statements attributed to that speaker are kept
    (case-insensitive); unattributed lines are dropped in that mode.
    """
    lines = [line for line in transcript.splitlines() if line.strip()]
    if len(lines) <= 1:
        lines = [s for s in _SENTENCE_SPLIT.split(transcript.strip()) if s.strip()]

    wanted = speaker.strip().lower() if speaker and speaker.strip() else None
    statements: list[dict[str, str | None]] = []
    current_speaker: str | None = None
    for line in lines:
        match = _SPEAKER_LABEL.match(line)
        if match:
            current_speaker = match.group(1).strip()
            text = match.group(2).strip()
        else:
            text = line.strip()
        if not _HAS_LETTER.search(text):
            continue  # skip pure numbers/punctuation/filler
        if wanted is not None and (current_speaker or "").lower() != wanted:
            continue
        statements.append({"speaker": current_speaker, "statement": text})
    return statements


def check_transcript(
    *,
    settings: Settings,
    client: AzureOpenAIClient,
    transcript: str,
    limit: int,
    speaker: str | None = None,
    max_statements: int = 50,
) -> dict[str, Any]:
    """Fact-check each statement in a transcript against the corpus, line by line."""
    statements = split_statements(transcript, speaker=speaker)
    truncated = len(statements) > max_statements
    statements = statements[:max_statements]

    counts = {"SUPPORTED": 0, "CONTRADICTED": 0, "NOT ADDRESSED": 0, "UNKNOWN": 0}
    results: list[dict[str, Any]] = []
    for index, item in enumerate(statements):
        claim = item["statement"]
        contexts = retrieve_contexts(settings=settings, client=client, text=claim, limit=limit)
        report = factcheck_claim(client, settings, claim, contexts)
        verdict = parse_verdict(report)
        counts[verdict] = counts.get(verdict, 0) + 1
        results.append(
            {
                "index": index,
                "speaker": item["speaker"],
                "statement": claim,
                "verdict": verdict,
                "report": report,
                "sources": format_sources(contexts),
            }
        )

    checked = len(results)
    return {
        "summary": {
            "statements_checked": checked,
            "counts": counts,
            "supported_ratio": (counts["SUPPORTED"] / checked) if checked else 0.0,
            "flagged": [r["index"] for r in results if r["verdict"] == "CONTRADICTED"],
            "truncated": truncated,
        },
        "statements": results,
    }
