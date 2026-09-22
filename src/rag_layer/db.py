from __future__ import annotations

import argparse
import datetime as _dt
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import Settings, load_settings

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS rag_documents (
    id BIGSERIAL PRIMARY KEY,
    blob_name TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL,
    etag TEXT,
    last_modified TIMESTAMPTZ,
    size_bytes BIGINT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rag_chunks (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES rag_documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    embedding vector(__EMBEDDING_DIMENSIONS__) NOT NULL,
    token_count INTEGER,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS rag_chunks_embedding_hnsw_idx
ON rag_chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS rag_documents_blob_name_idx ON rag_documents(blob_name);
CREATE INDEX IF NOT EXISTS rag_documents_content_hash_idx ON rag_documents(content_hash);
CREATE INDEX IF NOT EXISTS rag_chunks_document_id_idx ON rag_chunks(document_id);

CREATE TABLE IF NOT EXISTS learn_mixes (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL,
    prompt TEXT NOT NULL,
    length TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    title TEXT,
    summary TEXT,
    duration_seconds INTEGER,
    content JSONB NOT NULL DEFAULT '{}'::jsonb,
    sources JSONB NOT NULL DEFAULT '[]'::jsonb,
    audio BYTEA,
    audio_mime TEXT,
    error TEXT,
    recommended BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS learn_mixes_created_idx ON learn_mixes(created_at DESC);
ALTER TABLE learn_mixes ADD COLUMN IF NOT EXISTS tier INTEGER;
ALTER TABLE learn_mixes ADD COLUMN IF NOT EXISTS curriculum_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS learn_mixes_curriculum_key_idx
    ON learn_mixes(curriculum_key) WHERE curriculum_key IS NOT NULL;

-- Prepare tab (roleplay). Custom scenarios an agent generated from a description
-- live here so they survive a container rebuild; the built-in personas ship as JSON
-- in the source tree.
CREATE TABLE IF NOT EXISTS roleplay_personas (
    id TEXT PRIMARY KEY,
    persona JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per finished roleplay call: the transcript, how it ended, and the coaching
-- report. In-flight sessions stay in process memory (the voice path is latency-bound);
-- only completed ones are written, so the history list and stats are durable.
CREATE TABLE IF NOT EXISTS roleplay_sessions (
    id SERIAL PRIMARY KEY,
    session_key TEXT NOT NULL UNIQUE,
    persona_id TEXT NOT NULL,
    persona_name TEXT NOT NULL DEFAULT '',
    scenario_title TEXT NOT NULL DEFAULT '',
    custom BOOLEAN NOT NULL DEFAULT FALSE,
    outcome TEXT NOT NULL DEFAULT '',
    elapsed_seconds INTEGER NOT NULL DEFAULT 0,
    turn_count INTEGER NOT NULL DEFAULT 0,
    turns JSONB NOT NULL DEFAULT '[]'::jsonb,
    feedback JSONB NOT NULL DEFAULT '{}'::jsonb,
    verification_events JSONB NOT NULL DEFAULT '[]'::jsonb,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS roleplay_sessions_ended_idx ON roleplay_sessions(ended_at DESC);
ALTER TABLE roleplay_sessions DROP COLUMN IF EXISTS duration;

-- Deployment-wide preferences (one shared login, so one profile): a JSON value per key.
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def connect(settings: Settings):
    return psycopg.connect(settings.postgres_dsn, row_factory=dict_row)


def init_db(settings: Settings) -> None:
    with connect(settings) as conn:
        conn.execute(SCHEMA_SQL.replace("__EMBEDDING_DIMENSIONS__", str(settings.embedding_dimensions)))
        conn.commit()


def count_documents(settings: Settings) -> int:
    """Number of ingested documents. Returns 0 if the schema is not present yet."""
    try:
        with connect(settings) as conn:
            row = conn.execute("SELECT count(*) AS n FROM rag_documents").fetchone()
            return int(row["n"]) if row else 0
    except psycopg.errors.UndefinedTable:
        return 0


def get_document_by_blob(conn, blob_name: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM rag_documents WHERE blob_name = %s",
        (blob_name,),
    ).fetchone()
    return dict(row) if row else None


def get_document_by_hash(conn, content_hash: str, exclude_blob: str) -> dict[str, Any] | None:
    """First-ingested document whose bytes match this hash, under a *different* blob.

    The container holds the same files copied across folders under different names
    (e.g. a FlexLife brochure lives in Data for DJ, FlexLife Product Information and
    From NLG). Dedup by blob_name alone would embed each copy, so ingest also skips a
    blob whose exact content is already indexed under another path. First writer wins;
    ``created_at, id`` keeps the winner stable across re-runs."""
    row = conn.execute(
        """
        SELECT blob_name FROM rag_documents
        WHERE content_hash = %s AND blob_name <> %s
        ORDER BY created_at, id
        LIMIT 1
        """,
        (content_hash, exclude_blob),
    ).fetchone()
    return dict(row) if row else None


def upsert_document(
    conn,
    *,
    blob_name: str,
    content_hash: str,
    etag: str | None,
    last_modified,
    size_bytes: int | None,
    metadata: dict[str, Any],
) -> int:
    row = conn.execute(
        """
        INSERT INTO rag_documents (blob_name, content_hash, etag, last_modified, size_bytes, metadata)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (blob_name) DO UPDATE SET
            content_hash = EXCLUDED.content_hash,
            etag = EXCLUDED.etag,
            last_modified = EXCLUDED.last_modified,
            size_bytes = EXCLUDED.size_bytes,
            metadata = EXCLUDED.metadata,
            updated_at = now()
        RETURNING id
        """,
        (blob_name, content_hash, etag, last_modified, size_bytes, Jsonb(metadata)),
    ).fetchone()
    return int(row["id"])


def replace_chunks(conn, document_id: int, chunks: list, embeddings: list[list[float]]) -> None:
    """Replace a document's chunks. Accepts ``Chunk`` objects or bare strings."""
    conn.execute("DELETE FROM rag_chunks WHERE document_id = %s", (document_id,))
    rows = []
    for index, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
        content = chunk if isinstance(chunk, str) else chunk.content
        metadata = {} if isinstance(chunk, str) else dict(chunk.metadata)
        rows.append(
            (
                document_id,
                index,
                content,
                _vector_literal(embedding),
                len(content.split()),
                Jsonb(metadata),
            )
        )
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO rag_chunks (document_id, chunk_index, content, embedding, token_count, metadata)
            VALUES (%s, %s, %s, %s::vector, %s, %s)
            """,
            rows,
        )


def search_chunks(conn, query_embedding: list[float], limit: int = 8) -> list[dict[str, Any]]:
    return list(
        conn.execute(
            """
            SELECT
                c.content,
                c.chunk_index,
                c.metadata,
                d.blob_name,
                1 - (c.embedding <=> %s::vector) AS similarity
            FROM rag_chunks c
            JOIN rag_documents d ON d.id = c.document_id
            ORDER BY c.embedding <=> %s::vector
            LIMIT %s
            """,
            (_vector_literal(query_embedding), _vector_literal(query_embedding), limit),
        ).fetchall()
    )


def list_documents(conn) -> list[dict[str, Any]]:
    """Every ingested document with per-document chunk statistics.

    Used by the corpus inspector to sanity-check the chunking strategy: chunk count,
    character size spread against the configured target, and which structure metadata
    (page / zone) actually made it through extraction.
    """
    return list(
        conn.execute(
            """
            SELECT
                d.id,
                d.blob_name,
                d.size_bytes,
                d.updated_at,
                count(c.id) AS chunk_count,
                coalesce(sum(c.token_count), 0) AS words_total,
                coalesce(round(avg(char_length(c.content)))::int, 0) AS chars_avg,
                coalesce(min(char_length(c.content)), 0) AS chars_min,
                coalesce(max(char_length(c.content)), 0) AS chars_max,
                count(DISTINCT c.metadata->>'page')
                    FILTER (WHERE c.metadata->>'page' IS NOT NULL) AS page_count,
                coalesce(
                    array_agg(DISTINCT c.metadata->>'zone')
                        FILTER (WHERE c.metadata->>'zone' IS NOT NULL),
                    ARRAY[]::text[]
                ) AS zones
            FROM rag_documents d
            LEFT JOIN rag_chunks c ON c.document_id = d.id
            GROUP BY d.id
            ORDER BY d.blob_name
            """
        ).fetchall()
    )


def get_document_chunks(conn, document_id: int) -> dict[str, Any] | None:
    """A single document's header plus all of its chunks in order."""
    doc = conn.execute(
        "SELECT id, blob_name, size_bytes, updated_at FROM rag_documents WHERE id = %s",
        (document_id,),
    ).fetchone()
    if not doc:
        return None
    chunks = conn.execute(
        """
        SELECT
            chunk_index,
            content,
            token_count AS words,
            char_length(content) AS chars,
            metadata
        FROM rag_chunks
        WHERE document_id = %s
        ORDER BY chunk_index
        """,
        (document_id,),
    ).fetchall()
    return {"document": dict(doc), "chunks": [dict(row) for row in chunks]}


def citation(row: dict[str, Any]) -> str:
    """Human-readable source locator: page and heading when the chunk carries them."""
    metadata = row.get("metadata") or {}
    parts = [row["blob_name"]]
    page = metadata.get("page")
    if page:
        parts.append(f"p.{page}")
    heading = metadata.get("heading_path")
    if heading:
        parts.append(heading)
    if len(parts) == 1:
        parts.append(f"chunk-{row['chunk_index']}")
    return " | ".join(parts)


# --- Learn mixes (salesDJ) -------------------------------------------------------

_MIX_LIST_COLUMNS = """
    id, kind, prompt, length, status, title, summary, duration_seconds,
    sources, error, recommended, tier, curriculum_key, created_at, updated_at,
    (audio IS NOT NULL) AS has_audio
"""


def create_mix(
    conn,
    *,
    kind: str,
    prompt: str,
    length: str,
    recommended: bool = False,
    tier: int | None = None,
    curriculum_key: str | None = None,
) -> int:
    row = conn.execute(
        """
        INSERT INTO learn_mixes (kind, prompt, length, recommended, tier, curriculum_key)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (kind, prompt, length, recommended, tier, curriculum_key),
    ).fetchone()
    conn.commit()
    return int(row["id"])


def list_mixes(conn) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            f"SELECT {_MIX_LIST_COLUMNS} FROM learn_mixes ORDER BY created_at DESC"
        ).fetchall()
    ]


def get_mix(conn, mix_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        f"SELECT {_MIX_LIST_COLUMNS}, content FROM learn_mixes WHERE id = %s",
        (mix_id,),
    ).fetchone()
    return dict(row) if row else None


def get_mix_audio(conn, mix_id: int) -> tuple[bytes, str] | None:
    row = conn.execute(
        "SELECT audio, audio_mime FROM learn_mixes WHERE id = %s AND audio IS NOT NULL",
        (mix_id,),
    ).fetchone()
    if not row:
        return None
    return bytes(row["audio"]), row["audio_mime"] or "audio/mpeg"


def update_mix(conn, mix_id: int, **fields: Any) -> None:
    """Set any subset of mutable columns; JSON-typed columns are wrapped automatically."""
    if not fields:
        return
    json_columns = {"content", "sources"}
    assignments = []
    values: list[Any] = []
    for column, value in fields.items():
        assignments.append(f"{column} = %s")
        values.append(Jsonb(value) if column in json_columns else value)
    values.append(mix_id)
    conn.execute(
        f"UPDATE learn_mixes SET {', '.join(assignments)}, updated_at = now() WHERE id = %s",
        values,
    )
    conn.commit()


def delete_mix(conn, mix_id: int) -> bool:
    cur = conn.execute("DELETE FROM learn_mixes WHERE id = %s", (mix_id,))
    conn.commit()
    return cur.rowcount > 0


def mark_stale_mixes(conn, *, older_than_minutes: int = 12) -> int:
    """Mixes stuck in queued/generating well past any realistic generation time are
    marked failed so the UI stops spinning. A 10-minute audio episode finishes in a
    few minutes, so anything older was orphaned by a process restart."""
    cur = conn.execute(
        """
        UPDATE learn_mixes
        SET status = 'failed', error = 'Generation was interrupted. Try again.',
            updated_at = now()
        WHERE status IN ('queued', 'generating')
          AND updated_at < now() - make_interval(mins => %s)
        """,
        (older_than_minutes,),
    )
    conn.commit()
    return cur.rowcount


def get_chunks_for_source(
    conn,
    *,
    doc_like: str,
    pages: list[int] | None = None,
    chunk_range: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Chunks of one document in reading order, optionally limited to PDF pages or a
    chunk_index range. Rows carry the same keys as ``search_chunks`` so the generators
    can treat pinned and retrieved context alike."""
    clauses = ["d.blob_name ILIKE %s"]
    params: list[Any] = [f"%{doc_like}%"]
    if pages:
        clauses.append("(c.metadata->>'page') ~ '^[0-9]+$' AND (c.metadata->>'page')::int = ANY(%s)")
        params.append(list(pages))
    if chunk_range and len(chunk_range) == 2:
        clauses.append("c.chunk_index BETWEEN %s AND %s")
        params.extend(chunk_range)
    return [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT c.content, c.chunk_index, c.metadata, d.blob_name, 1.0::float AS similarity
            FROM rag_chunks c
            JOIN rag_documents d ON d.id = c.document_id
            WHERE {' AND '.join(clauses)}
            ORDER BY d.blob_name, c.chunk_index
            """,
            params,
        ).fetchall()
    ]


def list_audio_mixes_without_audio(conn) -> list[int]:
    return [
        int(r["id"])
        for r in conn.execute(
            """
            SELECT id FROM learn_mixes
            WHERE kind = 'audio' AND status = 'ready' AND audio IS NULL
            ORDER BY created_at
            """
        ).fetchall()
    ]


# --- Roleplay (Prepare) -----------------------------------------------------------------


def list_roleplay_personas(conn) -> list[dict[str, Any]]:
    """Custom personas in creation order (oldest first, so ids never shadow built-ins
    that load before them)."""
    rows = conn.execute(
        "SELECT persona FROM roleplay_personas ORDER BY created_at ASC, id ASC"
    ).fetchall()
    return [dict(r)["persona"] for r in rows]


def upsert_roleplay_persona(conn, persona: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO roleplay_personas (id, persona) VALUES (%s, %s)
        ON CONFLICT (id) DO UPDATE SET persona = EXCLUDED.persona
        """,
        (persona["id"], Jsonb(persona)),
    )
    conn.commit()


def delete_roleplay_persona(conn, persona_id: str) -> bool:
    cur = conn.execute("DELETE FROM roleplay_personas WHERE id = %s", (persona_id,))
    conn.commit()
    return cur.rowcount > 0


_SESSION_COLUMNS = (
    "id, session_key, persona_id, persona_name, scenario_title, custom, outcome, "
    "elapsed_seconds, turn_count, started_at, ended_at"
)


def insert_roleplay_session(conn, **fields: Any) -> int:
    for key in ("turns", "feedback", "verification_events"):
        if key in fields and not isinstance(fields[key], Jsonb):
            fields[key] = Jsonb(fields[key])
    columns = ", ".join(fields)
    placeholders = ", ".join(["%s"] * len(fields))
    row = conn.execute(
        f"""
        INSERT INTO roleplay_sessions ({columns}) VALUES ({placeholders})
        ON CONFLICT (session_key) DO UPDATE SET feedback = EXCLUDED.feedback,
            turns = EXCLUDED.turns, outcome = EXCLUDED.outcome,
            verification_events = EXCLUDED.verification_events,
            elapsed_seconds = EXCLUDED.elapsed_seconds, turn_count = EXCLUDED.turn_count,
            ended_at = EXCLUDED.ended_at
        RETURNING id
        """,
        tuple(fields.values()),
    ).fetchone()
    conn.commit()
    return int(row["id"])


def list_roleplay_sessions(conn, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT {_SESSION_COLUMNS} FROM roleplay_sessions ORDER BY ended_at DESC LIMIT %s",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_roleplay_session(conn, session_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        f"SELECT {_SESSION_COLUMNS}, turns, feedback, verification_events "
        "FROM roleplay_sessions WHERE id = %s",
        (session_id,),
    ).fetchone()
    return dict(row) if row else None


def delete_roleplay_session(conn, session_id: int) -> bool:
    cur = conn.execute("DELETE FROM roleplay_sessions WHERE id = %s", (session_id,))
    conn.commit()
    return cur.rowcount > 0


def roleplay_stats(conn) -> dict[str, Any]:
    """Totals for the Prepare home and report: sessions, minutes, and the current
    streak of consecutive days (ending today or yesterday) with at least one call."""
    totals = dict(conn.execute(
        "SELECT count(*) AS n, coalesce(sum(elapsed_seconds), 0) AS s FROM roleplay_sessions"
    ).fetchone())
    sessions, seconds = totals["n"], totals["s"]
    days = [dict(r)["d"] for r in conn.execute(
        "SELECT DISTINCT (ended_at AT TIME ZONE 'UTC')::date AS d FROM roleplay_sessions "
        "ORDER BY d DESC LIMIT 400"
    ).fetchall()]

    streak = 0
    if days:
        today = _dt.datetime.now(_dt.timezone.utc).date()
        cursor = today if days[0] == today else today - _dt.timedelta(days=1)
        for day in days:
            if day != cursor:
                break
            streak += 1
            cursor -= _dt.timedelta(days=1)
    return {
        "sessions": int(sessions),
        "minutes": int(round(int(seconds) / 60)),
        "streak_days": streak,
    }


def _avg_score(conn, since: _dt.datetime | None, until: _dt.datetime | None) -> float | None:
    """Mean overall score of graded calls that ended in [since, until)."""
    clauses, params = ["(feedback->>'score') IS NOT NULL"], []
    if since is not None:
        clauses.append("ended_at >= %s"); params.append(since)
    if until is not None:
        clauses.append("ended_at < %s"); params.append(until)
    row = conn.execute(
        f"SELECT avg((feedback->>'score')::numeric) AS a FROM roleplay_sessions WHERE {' AND '.join(clauses)}",
        tuple(params),
    ).fetchone()
    value = dict(row)["a"]
    return float(value) if value is not None else None


def profile_stats(conn) -> dict[str, Any]:
    """Everything the Profile screen shows on top of ``roleplay_stats``: the average
    overall score with a week-over-week delta, a week-over-week delta of call count,
    when the agent last practised, and the per-skill averages the coach has graded."""
    base = roleplay_stats(conn)
    now = _dt.datetime.now(_dt.timezone.utc)
    week_ago, two_weeks_ago = now - _dt.timedelta(days=7), now - _dt.timedelta(days=14)

    avg_all = _avg_score(conn, None, None)
    avg_this, avg_prev = _avg_score(conn, week_ago, None), _avg_score(conn, two_weeks_ago, week_ago)
    calls = dict(conn.execute(
        "SELECT count(*) FILTER (WHERE ended_at >= %s) AS this_week, "
        "count(*) FILTER (WHERE ended_at >= %s AND ended_at < %s) AS prev_week, "
        "max(ended_at) AS last_at FROM roleplay_sessions",
        (week_ago, two_weeks_ago, week_ago),
    ).fetchone())

    skills: dict[str, int | None] = {}
    counts: dict[str, int] = {}
    for row in conn.execute(
        "SELECT s.key AS key, avg(s.value::numeric) AS a, count(*) AS n "
        "FROM roleplay_sessions, jsonb_each_text(coalesce(feedback->'skills', '{}'::jsonb)) AS s "
        "WHERE s.value IS NOT NULL AND s.value <> 'null' GROUP BY s.key"
    ).fetchall():
        row = dict(row)
        skills[row["key"]] = int(round(float(row["a"])))
        counts[row["key"]] = int(row["n"])

    return {
        **base,
        "avg_score": int(round(avg_all)) if avg_all is not None else None,
        "score_delta": int(round(avg_this - avg_prev)) if avg_this is not None and avg_prev is not None else None,
        "calls_delta": int(calls["this_week"]) - int(calls["prev_week"]),
        "last_practice_at": calls["last_at"],
        "skills": skills,
        "skill_counts": counts,
    }


def get_app_settings(conn) -> dict[str, Any]:
    rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    return {dict(r)["key"]: dict(r)["value"] for r in rows}


def set_app_settings(conn, values: dict[str, Any]) -> None:
    for key, value in values.items():
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (%s, %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
            (key, Jsonb(value)),
        )
    conn.commit()


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in values) + "]"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["init", "count"])
    args = parser.parse_args()
    settings = load_settings()
    if args.command == "init":
        init_db(settings)
        print("Database initialized")
    elif args.command == "count":
        # Prints just the integer so launchers can parse it directly.
        print(count_documents(settings))


if __name__ == "__main__":
    main()
