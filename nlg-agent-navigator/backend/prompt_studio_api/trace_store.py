from __future__ import annotations

from datetime import timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from prompt_studio_api.schema import PROMPT_STUDIO_SCHEMA_SQL


def persist_trace(conn, trace: Any) -> None:
    completed_at = trace.completed_at or trace.context.started_at
    conn.execute(
        """
        INSERT INTO prompt_studio.trace_sessions
            (session_id, created_at, updated_at, metadata)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (session_id) DO UPDATE
        SET updated_at = EXCLUDED.updated_at,
            metadata = prompt_studio.trace_sessions.metadata || EXCLUDED.metadata
        """,
        (
            trace.context.session_id,
            trace.context.started_at,
            completed_at,
            Jsonb({"latest_status": trace.status}),
        ),
    )
    for event in trace.events:
        if event.kind != "agent_call":
            continue
        details = event.details if isinstance(event.details, dict) else {}
        duration_ms = event.duration_ms or 0
        started_at = event.timestamp - timedelta(milliseconds=duration_ms)
        response = None
        error = None
        if event.status == "failed":
            error = {
                key: details[key]
                for key in ("exception_type", "message", "traceback")
                if key in details
            }
        elif event.output_text is not None:
            response = {
                "text": event.output_text,
                "citations": details.get("citations", []),
            }
        conn.execute(
            """
            INSERT INTO prompt_studio.prompt_invocations
                (invocation_id, session_id, turn_id, turn_number, sequence,
                 feature_key, actor, target, status, started_at, completed_at,
                 duration_ms, prompt_recipe, runtime_inputs, flattened_prompt,
                 response, error)
            VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                 %s, %s, %s, %s, %s, %s)
            ON CONFLICT (invocation_id) DO NOTHING
            """,
            (
                f"{trace.context.turn_id}:{event.sequence}",
                trace.context.session_id,
                trace.context.turn_id,
                trace.context.turn_number,
                event.sequence,
                details.get("feature_key", "unknown"),
                event.actor,
                details.get("target"),
                event.status,
                started_at,
                event.timestamp,
                event.duration_ms,
                Jsonb(
                    {
                        "purpose": details.get("purpose"),
                        "instructions": details.get("application_instructions"),
                    }
                ),
                Jsonb({}),
                event.input_text,
                Jsonb(response) if response is not None else None,
                Jsonb(error) if error is not None else None,
            ),
        )
    conn.commit()


def list_trace_sessions(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT s.session_id, s.created_at, s.updated_at, s.metadata,
               count(DISTINCT i.turn_id)::int AS turn_count,
               count(i.invocation_id)::int AS invocation_count
        FROM prompt_studio.trace_sessions AS s
        LEFT JOIN prompt_studio.prompt_invocations AS i USING (session_id)
        GROUP BY s.session_id
        ORDER BY s.updated_at DESC
        """
    ).fetchall()
    return [_json_ready(dict(row)) for row in rows]


def get_trace_session(conn, session_id: str) -> dict[str, Any] | None:
    session = conn.execute(
        """
        SELECT session_id, created_at, updated_at, metadata
        FROM prompt_studio.trace_sessions
        WHERE session_id = %s
        """,
        (session_id,),
    ).fetchone()
    if session is None:
        return None
    rows = conn.execute(
        """
        SELECT invocation_id, turn_id, turn_number, sequence, feature_key,
               actor, target, status, started_at, completed_at, duration_ms,
               prompt_recipe, runtime_inputs, logical_request,
               flattened_prompt, response, error
        FROM prompt_studio.prompt_invocations
        WHERE session_id = %s
        ORDER BY turn_number, sequence
        """,
        (session_id,),
    ).fetchall()
    turns: list[dict[str, Any]] = []
    by_turn: dict[str, dict[str, Any]] = {}
    for row in rows:
        invocation = _json_ready(dict(row))
        turn_id = invocation["turn_id"]
        turn = by_turn.get(turn_id)
        if turn is None:
            turn = {
                "turn_id": turn_id,
                "turn_number": invocation["turn_number"],
                "started_at": invocation["started_at"],
                "invocations": [],
            }
            by_turn[turn_id] = turn
            turns.append(turn)
        turn["invocations"].append(invocation)
    result = _json_ready(dict(session))
    result["turns"] = turns
    return result


def _json_ready(item: dict[str, Any]) -> dict[str, Any]:
    for key, value in item.items():
        if hasattr(value, "isoformat"):
            item[key] = value.isoformat()
    return item


class PostgresTraceWriter:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def initialize(self) -> None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as conn:
            conn.execute(PROMPT_STUDIO_SCHEMA_SQL)
            conn.commit()

    def write(self, trace: Any) -> None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as conn:
            persist_trace(conn, trace)