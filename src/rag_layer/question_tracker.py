"""Ask Navigator question tracker.

Records one row per question asked through Ask Navigator: what it was about (a
category from a short fixed list), whether the Knowledge Foundation answered it, and
whether it ended up routed to NLG Support. The Question Insights admin page reads the
aggregates to show where the document library is and is not covering what agents ask.

"Answered" here means the reply came back grounded in at least one source document.
It does not judge whether the answer was correct or helpful.

Tracking is best-effort by design: nothing in this module may fail a chat turn.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Query, Request
from psycopg.types.json import Jsonb

from .config import Settings
from .db import connect
from .embeddings import AzureOpenAIClient, _generate, _history_text

router = APIRouter(prefix="/v1/question-insights", tags=["question-insights"])

# The fixed topic list the classifier picks from. Keep it short: stable buckets are
# what make week-over-week trends comparable. Edit freely; existing rows keep the
# category they were given.
CATEGORIES: dict[str, str] = {
    "Product basics": "what FlexLife / IUL is, how the policy works, features, premiums, death benefit, who it suits",
    "Indexing & crediting": "index options, caps, floors, participation rates, interest crediting, volatility-controlled indexes",
    "Riders & living benefits": "accelerated benefit riders, chronic/critical/terminal illness, LIBR, chronic care, Homethrive",
    "Underwriting & eligibility": "underwriting classes, EZ underwriting, health or age requirements, issue limits, state availability",
    "Illustrations & applications": "reading or running an illustration, the application, forms, signatures, new-business process",
    "Sales conversations": "what to say or ask, discovery questions, objections, presenting, closing, follow-up, approved wording",
    "Marketing & compliance": "advertising rules, social media, marketing materials, what an agent may or may not claim",
}
OTHER_CATEGORY = "Other"
# Turns the M02 domain gate declined never reach the classifier.
OFF_TOPIC_CATEGORY = "Off-topic"

OUTCOMES = ("answered", "unanswered", "out_of_domain")


def classify_category(
    client: AzureOpenAIClient,
    settings: Settings,
    question: str,
    history: list[dict],
) -> str:
    """Pick the one category a question belongs to. Raises on a provider failure."""
    options = "\n".join(f"- {name}: {hint}" for name, hint in CATEGORIES.items())
    prompt = f"""You label questions asked by life-insurance sales agents so an admin can see which topics come up.
Pick the single best category for the agent's latest message. If it is a follow-up
("make that shorter", "and the floor?"), label the topic of the thread it continues.

Categories:
{options}
- {OTHER_CATEGORY}: anything that fits none of the above

Reply with exactly the category name and nothing else.

Conversation so far:
{_history_text(history)}
Latest message: {question}
"""
    raw = _generate(client, settings, prompt).strip().lower()
    for name in CATEGORIES:
        if name.lower() in raw:
            return name
    return OTHER_CATEGORY


def outcome_of(result: dict[str, Any]) -> str:
    """Map a /v1/foundry/chat result onto the tracker's three outcomes."""
    if result.get("domain") == "out_of_domain":
        return "out_of_domain"
    return "unanswered" if result.get("escalate") else "answered"


def source_titles(result: dict[str, Any]) -> list[str]:
    """The documents an answer was grounded in, as deduped file names."""
    titles: list[str] = []
    for citation in result.get("citations") or []:
        titles.append(str(citation.get("title") or citation.get("url") or ""))
    for source in result.get("sources") or []:
        titles.append(str(source.get("blob_name") or "").rsplit("/", 1)[-1])
    return list(dict.fromkeys(title for title in titles if title))


def record_question(
    conn,
    *,
    user_id: str,
    question: str,
    result: dict[str, Any],
) -> int:
    outcome = outcome_of(result)
    row = conn.execute(
        """
        INSERT INTO ask_question_log
            (user_id, question, category, outcome, sources, trace_request_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            user_id,
            question,
            OFF_TOPIC_CATEGORY if outcome == "out_of_domain" else None,
            outcome,
            Jsonb(source_titles(result)),
            result.get("trace_request_id"),
        ),
    ).fetchone()
    return int(row["id"])


def mark_support(conn, question_id: int, status: str) -> None:
    """Note that a question was routed to NLG Support. 'sent' is never downgraded."""
    if status not in {"drafted", "sent"}:
        raise ValueError(f"Unsupported support status: {status!r}")
    conn.execute(
        """
        UPDATE ask_question_log
        SET support_status = %s
        WHERE id = %s AND support_status IS DISTINCT FROM 'sent'
        """,
        (status, question_id),
    )


# The fixed reasons a thumbs-down can cite. Kept short and stable so the "Top issues"
# aggregate stays comparable over time; the UI sends these exact strings.
FEEDBACK_REASONS: tuple[str, ...] = (
    "Incorrect",
    "Incomplete",
    "Not in the sources / wrong source",
    "Outdated",
    "Hard to understand",
    "Wrong wording or tone",
)


def record_feedback(
    conn,
    *,
    question_id: int,
    user_id: str,
    vote: str,
    reasons: list[str] | None = None,
    comment: str | None = None,
) -> None:
    """Upsert the thumbs vote for one answered turn. A later vote replaces the earlier one."""
    if vote not in {"up", "down"}:
        raise ValueError(f"Unsupported vote: {vote!r}")
    # Only a thumbs-down carries why; drop reasons/comment on an up so the row stays clean.
    kept_reasons = [r for r in (reasons or []) if r in FEEDBACK_REASONS] if vote == "down" else []
    kept_comment = (comment or "").strip() or None if vote == "down" else None
    conn.execute(
        """
        INSERT INTO ask_answer_feedback (question_id, user_id, vote, reasons, comment, updated_at)
        VALUES (%s, %s, %s, %s, %s, now())
        ON CONFLICT (question_id) DO UPDATE
        SET vote = EXCLUDED.vote,
            reasons = EXCLUDED.reasons,
            comment = EXCLUDED.comment,
            user_id = EXCLUDED.user_id,
            updated_at = now()
        """,
        (question_id, user_id, vote, Jsonb(kept_reasons), kept_comment),
    )


def clear_feedback(conn, question_id: int) -> None:
    """Remove the vote for a turn (agent un-clicked the thumb)."""
    conn.execute("DELETE FROM ask_answer_feedback WHERE question_id = %s", (question_id,))


def question_exists(conn, question_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM ask_question_log WHERE id = %s", (question_id,)
    ).fetchone()
    return row is not None


def mark_support_safely(settings: Settings, question_id: int | None, status: str) -> None:
    if question_id is None:
        return
    try:
        with connect(settings) as conn:
            mark_support(conn, question_id, status)
    except Exception as exc:  # noqa: BLE001 - tracking must never fail the handoff
        print(f"Question tracker: could not mark question {question_id} {status}: {exc}")


def _categorize(
    settings: Settings,
    client: AzureOpenAIClient,
    question_id: int,
    question: str,
    history: list[dict],
) -> None:
    """Background step: label a recorded question. A failure leaves it uncategorized."""
    try:
        category = classify_category(client, settings, question, history)
        with connect(settings) as conn:
            conn.execute(
                "UPDATE ask_question_log SET category = %s WHERE id = %s",
                (category, question_id),
            )
    except Exception as exc:  # noqa: BLE001
        print(f"Question tracker: could not categorize question {question_id}: {exc}")


def track_question(
    *,
    settings: Settings,
    client: AzureOpenAIClient,
    background_tasks: BackgroundTasks,
    user_id: str,
    question: str,
    history: list[dict],
    result: dict[str, Any],
) -> int | None:
    """Record one answered chat turn and queue its categorization.

    Returns the row id (the UI sends it back if the question is later handed to NLG
    Support), or None when tracking failed. Never raises. The category is filled in
    after the response is sent so the classifier adds no latency to the chat turn.
    """
    try:
        with connect(settings) as conn:
            question_id = record_question(
                conn, user_id=user_id, question=question, result=result
            )
    except Exception as exc:  # noqa: BLE001 - tracking must never fail the chat turn
        print(f"Question tracker: could not record question: {exc}")
        return None
    if outcome_of(result) != "out_of_domain":
        background_tasks.add_task(_categorize, settings, client, question_id, question, history)
    return question_id


def question_insights(conn, *, days: int = 0, limit: int = 500) -> dict[str, Any]:
    """Aggregates plus the recent question log for the Question Insights page.

    ``days`` limits everything to the trailing window; 0 means all time.
    """
    where = "WHERE asked_at >= now() - make_interval(days => %s)" if days else ""
    params: tuple[Any, ...] = (days,) if days else ()
    counts = """
        count(*) AS total,
        count(*) FILTER (WHERE outcome = 'answered') AS answered,
        count(*) FILTER (WHERE outcome = 'unanswered') AS unanswered,
        count(*) FILTER (WHERE outcome = 'out_of_domain') AS out_of_domain,
        count(*) FILTER (WHERE support_status = 'drafted') AS support_drafted,
        count(*) FILTER (WHERE support_status = 'sent') AS support_sent
    """
    totals = conn.execute(
        f"""
        SELECT {counts},
               count(f.question_id) AS rated,
               count(*) FILTER (WHERE f.vote = 'up') AS thumbs_up,
               count(*) FILTER (WHERE f.vote = 'down') AS thumbs_down
        FROM ask_question_log
        LEFT JOIN ask_answer_feedback f ON f.question_id = ask_question_log.id
        {where}
        """,
        params,
    ).fetchone()
    issues = conn.execute(
        f"""
        SELECT reason, count(*) AS count
        FROM ask_question_log
        JOIN ask_answer_feedback f ON f.question_id = ask_question_log.id,
             jsonb_array_elements_text(f.reasons) AS reason
        {where + (' AND' if where else 'WHERE')} f.vote = 'down'
        GROUP BY reason
        ORDER BY count DESC, reason
        """,
        params,
    ).fetchall()
    categories = conn.execute(
        f"""
        SELECT coalesce(category, 'Uncategorized') AS category, {counts}
        FROM ask_question_log {where}
        GROUP BY 1
        ORDER BY unanswered DESC, total DESC, category
        """,
        params,
    ).fetchall()
    documents = conn.execute(
        f"""
        SELECT title, count(*) AS citations
        FROM ask_question_log, jsonb_array_elements_text(sources) AS title
        {where}
        GROUP BY title
        ORDER BY citations DESC, title
        LIMIT 25
        """,
        params,
    ).fetchall()
    questions = conn.execute(
        f"""
        SELECT q.id, q.user_id, q.question, coalesce(q.category, 'Uncategorized') AS category,
               q.outcome, q.support_status, q.sources, q.trace_request_id, q.asked_at,
               f.vote, coalesce(f.reasons, '[]'::jsonb) AS feedback_reasons, f.comment
        FROM ask_question_log q
        LEFT JOIN ask_answer_feedback f ON f.question_id = q.id
        {where.replace('asked_at', 'q.asked_at')}
        ORDER BY q.asked_at DESC, q.id DESC
        LIMIT %s
        """,
        (*params, limit),
    ).fetchall()
    return {
        "days": days,
        "totals": dict(totals),
        "categories": [dict(row) for row in categories],
        "documents": [dict(row) for row in documents],
        "issues": [dict(row) for row in issues],
        "questions": [
            {**dict(row), "asked_at": row["asked_at"].isoformat()} for row in questions
        ],
    }


@router.get("")
def question_insights_endpoint(
    request: Request,
    days: int = Query(default=30, ge=0, le=3650),
) -> dict[str, Any]:
    with connect(request.app.state.settings) as conn:
        return question_insights(conn, days=days)
