from __future__ import annotations

from typing import Any

from .config import Settings
from .db import citation, connect, search_chunks
from .embeddings import (
    AzureOpenAIClient,
    answer_with_context,
    embed_texts,
    factcheck_claim,
    parse_verdict,
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
            "blob_name": row["blob_name"],
            "chunk_index": row["chunk_index"],
            "citation": citation(row),
            "page": (row.get("metadata") or {}).get("page"),
            "zone": (row.get("metadata") or {}).get("zone", "body"),
            "similarity": float(row["similarity"]),
            "preview": row["content"][:500],
        }
        for row in contexts
    ]


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
    return {
        "answer": answer_with_context(client, settings, question, contexts),
        "sources": format_sources(contexts),
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
