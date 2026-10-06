from __future__ import annotations

import re
from typing import Any

from . import foundry, underwriting
from .blob_store import get_blob_store
from .config import NLG_SUPPORT_MESSAGE, UNDERWRITING_DISCLAIMER, Settings
from .db import (
    citation,
    connect,
    find_document_id,
    get_document_blob_name,
    get_document_chunks,
    list_documents,
    search_chunks,
)
from .embeddings import (
    AzureOpenAIClient,
    answer_with_context,
    chat_with_context,
    classify_turn,
    embed_texts,
    extract_case_facts,
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
            # Same-origin, authed link to open the original document (M10). Relative so the
            # browser sends the sign-in cookie; None when the id is somehow absent.
            "url": f"/v1/documents/{row['document_id']}/open" if row.get("document_id") is not None else None,
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
    # The chunks cleared the similarity floor, but the model may still report that they
    # don't actually answer the question. Treat that as an abstention too (M03): keep the
    # model's helpful decline, drop the non-supporting sources, and raise the flag M09 uses.
    if not reply.get("grounded", True):
        return {
            "answer": reply.get("answer") or NLG_SUPPORT_MESSAGE,
            "follow_ups": [],
            "sources": [],
            "insufficient_support": True,
        }
    return {
        "answer": reply["answer"],
        "follow_ups": reply.get("follow_ups", []),
        "sources": format_sources(selected),
        "insufficient_support": False,
    }


# M16 - fixed wording for the client-facts flow. Kept here, next to DOMAIN_DECLINE, so the
# copy is in one place and never left to the model.
PRIVACY_NOTE = "I only keep the facts listed here, in this chat. No names, SSNs or banking details."
REPLACEMENT_BANNER = "Replacement rules can vary by state."
REPLACEMENT_FALLBACK = (
    "Using an existing policy to pay for new coverage is a replacement, so I won't give a "
    "talk track for it. NLG needs to look at what the current policy pays, any surrender "
    "charges and what the client would give up."
)
RULEBOOK_ANSWER = "Here is what the underwriting guide lays out for the facts so far."
REPLACEMENT_GUARDRAIL = (
    "This turn involves replacing, surrendering or borrowing from an existing policy to fund "
    "new coverage. Do NOT give a sales talk track or recommend the replacement. Say plainly "
    "that it is a replacement, and explain only what the approved material says must be "
    "reviewed or submitted for a replacement. Keep it to a few sentences and do not restate "
    "the client's underwriting requirements. NLG decides."
)

_guide_url_cache: dict[str, str | None] = {}


def _guide_document_url(settings: Settings, rulebook: dict[str, Any]) -> str | None:
    """Same-origin link that opens the underwriting guide (M10), or None if it is not
    indexed. Looked up once per process; findings add ``#page=N`` to it."""
    filename = rulebook["source"]["blob"].rsplit("/", 1)[-1]
    if filename not in _guide_url_cache:
        try:
            with connect(settings) as conn:
                document_id = find_document_id(conn, filename)
            _guide_url_cache[filename] = f"/v1/documents/{document_id}/open" if document_id else None
        except Exception:  # noqa: BLE001 - a missing link must never fail the turn
            return None
    return _guide_url_cache[filename]


def _local_fallback(
    *, settings: Settings, client: AzureOpenAIClient, message: str, history: list[dict[str, Any]]
) -> dict[str, Any]:
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


def chat_foundry(
    *,
    settings: Settings,
    client: AzureOpenAIClient,
    message: str,
    history: list[dict[str, Any]],
    preferences: dict[str, Any] | None = None,
    instructions: str = "",
    prompt_key: str = "ask.navigator",
    prompt_version: int = 0,
    prompt_augmentations: list[dict[str, Any]] | None = None,
    about_me: str = "",
    memories: list[str] | None = None,
    user_id: str | None = None,
    trace_session_id: str | None = None,
    case: dict[str, Any] | None = None,
    case_action: str | None = None,
) -> dict[str, Any]:
    """Foundry chat with an in-repo out-of-domain guard (M02) and the client-facts flow (M16).

    The turn is classified first; a clearly non-FlexLife request is declined here
    without ever calling the hosted agent, so the app does not behave as a
    general-purpose chatbot. In-domain turns proxy to the agent unchanged. Either
    way the reply carries a ``domain`` flag so the UI and tests can tell an
    answered turn from a decline.

    When the agent is describing a specific client, the facts are kept on a case sheet
    (``case``, held by the browser). The first time facts the underwriting guide treats
    as material are missing, the turn returns a facts card instead of calling the agent;
    after that it answers with what it has, alongside what the guide lays out for those
    facts. ``case_action`` is set by the UI: "answers" when the sheet was just filled in
    by hand, "answer_now" to skip the questions, "show" to bring the facts card back.
    """
    if case_action == "show":
        # A button in the UI, not something the agent typed: nothing to classify.
        turn = {"domain": "IN_DOMAIN", "client": True, "replacement": False}
    else:
        turn = classify_turn(client, settings, message, history)
    if turn["domain"] == "OUT_OF_DOMAIN":
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

    rulebook = underwriting.load_rulebook()
    sheet = underwriting.clean_case(case, rulebook)
    on_file = (sheet["conditions"], sheet["facts"])
    if turn["client"] and not case_action:
        found = extract_case_facts(client, settings, message, history, sheet, rulebook)
        sheet = underwriting.merge_extracted(
            sheet, conditions=found["conditions"], facts=found["facts"], rulebook=rulebook
        )
    about_client = underwriting.has_content(sheet) and bool(turn["client"] or case_action)
    replacement = bool(turn["replacement"]) and not case_action
    # The guide lookup is shown when the sheet changed or the agent asked for it, not
    # repeated under every follow-up question about the same client.
    sheet_changed = bool(case_action) or (sheet["conditions"], sheet["facts"]) != on_file

    def ask_agent(case_context: str = "") -> dict[str, Any]:
        try:
            result = foundry.chat(
                settings=settings,
                instructions=instructions,
                prompt_key=prompt_key,
                prompt_version=prompt_version,
                message=message,
                history=history,
                preferences=preferences,
                prompt_augmentations=prompt_augmentations,
                about_me=about_me,
                memories=memories,
                user_id=user_id,
                trace_session_id=trace_session_id,
                **({"case_context": case_context} if case_context else {}),
            )
            return {**result, "sources": [], "domain": "in_domain", "source_engine": "foundry"}
        except Exception:  # noqa: BLE001
            return _local_fallback(settings=settings, client=client, message=message, history=history)

    if not about_client and not replacement:
        return ask_agent()

    def card(show: bool) -> dict[str, Any]:
        missing = underwriting.missing_keys(sheet, rulebook)
        return {
            "show": show,
            "rows": underwriting.card_rows(sheet, rulebook),
            "questions": underwriting.questions_for(sheet, missing, rulebook),
            "edit_questions": underwriting.questions_for(
                sheet, underwriting.editable_keys(sheet, rulebook), rulebook
            ),
            "note": PRIVACY_NOTE,
        }

    guide_url = _guide_document_url(settings, rulebook)

    def cited(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {**f, "url": f"{guide_url}#page={f['page']}" if guide_url else None} for f in findings
        ]

    disclaimer = getattr(settings, "underwriting_disclaimer", UNDERWRITING_DISCLAIMER)

    if replacement:
        out = ask_agent(REPLACEMENT_GUARDRAIL)
        if out["escalate"]:
            out["answer"] = REPLACEMENT_FALLBACK
        rule = next(r for r in rulebook["rules"] if r["id"] == "replacement_question")
        out.update(
            escalate=True,
            escalate_reason="case_specific",
            underwriting="case",
            banner=REPLACEMENT_BANNER,
            disclaimer=disclaimer,
            findings=cited([{"kind": "replacement", "text": rule["text"], "page": rule["page"]}]),
            actions=[{"id": "handoff", "label": "Ask NLG support"}]
            + ([{"id": "back", "label": "Back to the client"}] if underwriting.has_content(sheet) else []),
        )
        if underwriting.has_content(sheet):
            out.update(case=sheet, case_card=card(False))
        return out

    # Facts the guide treats as material are missing and have not been asked for yet:
    # show the facts card and stop. Skipping the agent here also keeps M03's
    # zero-citation abstention from replacing the card with the NLG-support message.
    if case_action == "show" or (not case_action and underwriting.unasked_missing(sheet, rulebook)):
        sheet = underwriting.mark_asked(sheet, underwriting.missing_keys(sheet, rulebook))
        has_missing = bool(underwriting.missing_keys(sheet, rulebook))
        return {
            "answer": underwriting.summary_text(sheet, rulebook),
            "citations": [],
            "sources": [],
            "agent": settings.foundry_agent_name,
            "model": None,
            "response_id": None,
            "status": "guiding",
            "domain": "in_domain",
            "escalate": False,
            "escalate_reason": None,
            "source_engine": "rulebook",
            "underwriting": "rule",
            "case": sheet,
            "case_card": card(True),
            "actions": ([{"id": "walk", "label": "Walk through questions with client"}] if has_missing else [])
            + [
                {"id": "edit", "label": "Edit facts"},
                {"id": "answer_now", "label": "Answer with what I have" if has_missing else "What does the guide say?"},
            ],
        }

    findings = underwriting.findings_for(sheet, rulebook)
    out = ask_agent(underwriting.context_text(sheet, findings, rulebook))
    if out["escalate"] and findings and sheet_changed:
        # The agent had nothing grounded to add, but the guide lookup did: show that
        # rather than a bare referral, cited to the guide itself.
        out.update(
            answer=RULEBOOK_ANSWER,
            escalate=False,
            escalate_reason=None,
            status="rulebook",
            source_engine="rulebook",
            citations=[{"n": 1, "title": rulebook["source"]["blob"].rsplit("/", 1)[-1], "url": guide_url}]
            if guide_url
            else [],
        )
    still_missing = underwriting.missing_keys(sheet, rulebook)
    out.update(
        underwriting="rule",
        case=sheet,
        case_card=card(False),
        findings=cited(findings) if sheet_changed else [],
        fit_signals=underwriting.fit_signals_for(sheet) if sheet_changed else [],
        disclaimer=disclaimer,
        actions=([{"id": "walk", "label": "Add the missing facts"}] if still_missing else [])
        + [{"id": "edit", "label": "Edit facts"}],
    )
    return out


def open_document(*, settings: Settings, document_id: int) -> tuple[str, bytes] | None:
    """Resolve a document id to its blob and download the original bytes (M10).

    Returns (blob_name, data), or None if the id is unknown. Raises on a blob-fetch
    failure (the endpoint maps that to 502). The container SAS token stays server-side.
    """
    with connect(settings) as conn:
        blob_name = get_document_blob_name(conn, document_id)
    if blob_name is None:
        return None
    data = get_blob_store(settings).download_blob(blob_name)
    return blob_name, data


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
