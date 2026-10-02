"""Standalone, demo-only prompt-management and trace-inspection API.

The main application only resolves selected prompts. This router owns the isolated
authoring workflow: browse definitions, inspect immutable versions, create a version,
and explicitly select the version the application should use. It also provides the
read-only session/request drill-down used by Trace Explorer.

Authentication is intentionally not required for this demo router. Revisit that
boundary before production use.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from .db import connect

router = APIRouter(prefix="/v1/prompt-admin", tags=["prompt-admin"])


class PromptVersionCreate(BaseModel):
    instructions: str = Field(..., min_length=1, max_length=200_000)
    change_notes: str = Field(default="", max_length=2_000)


class PromptVersionSelect(BaseModel):
    version: int = Field(..., ge=1)


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else value


def _version_dict(row: Any, selected_version: int | None = None) -> dict[str, Any]:
    item = dict(row)
    item["created_at"] = _iso(item.get("created_at"))
    if selected_version is not None:
        item["selected"] = item.get("version") == selected_version
    return item


def list_prompt_definitions(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            d.key,
            d.purpose,
            d.selected_version,
            d.created_at,
            d.updated_at,
            count(v.version)::int AS version_count
        FROM prompt_definitions AS d
        LEFT JOIN prompt_versions AS v ON v.prompt_key = d.key
        GROUP BY d.key
        ORDER BY d.key
        """
    ).fetchall()
    prompts = []
    for row in rows:
        item = dict(row)
        item["created_at"] = _iso(item.get("created_at"))
        item["updated_at"] = _iso(item.get("updated_at"))
        prompts.append(item)
    return prompts


def get_prompt_definition(conn, key: str) -> dict[str, Any] | None:
    definition = conn.execute(
        """
        SELECT key, purpose, selected_version, created_at, updated_at
        FROM prompt_definitions
        WHERE key = %s
        """,
        (key,),
    ).fetchone()
    if not definition:
        return None
    item = dict(definition)
    selected_version = item.get("selected_version")
    item["created_at"] = _iso(item.get("created_at"))
    item["updated_at"] = _iso(item.get("updated_at"))
    versions = conn.execute(
        """
        SELECT version, instructions, change_notes, created_by, created_at
        FROM prompt_versions
        WHERE prompt_key = %s
        ORDER BY version DESC
        """,
        (key,),
    ).fetchall()
    item["versions"] = [_version_dict(row, selected_version) for row in versions]
    return item


def create_prompt_version(
    conn,
    *,
    key: str,
    instructions: str,
    change_notes: str,
    created_by: str,
) -> dict[str, Any] | None:
    """Create the next immutable version while locking its definition."""
    definition = conn.execute(
        "SELECT key FROM prompt_definitions WHERE key = %s FOR UPDATE",
        (key,),
    ).fetchone()
    if not definition:
        return None
    if not instructions.strip():
        raise ValueError("Prompt instructions cannot be blank")
    row = conn.execute(
        """
        INSERT INTO prompt_versions
            (prompt_key, version, instructions, change_notes, created_by)
        SELECT %s, coalesce(max(version), 0) + 1, %s, %s, %s
        FROM prompt_versions
        WHERE prompt_key = %s
        RETURNING version, instructions, change_notes, created_by, created_at
        """,
        (key, instructions, change_notes.strip(), created_by, key),
    ).fetchone()
    conn.execute(
        "UPDATE prompt_definitions SET updated_at = now() WHERE key = %s",
        (key,),
    )
    conn.commit()
    return _version_dict(row)


def select_prompt_version(conn, *, key: str, version: int) -> dict[str, Any] | None:
    """Select an existing version; the composite FK is an additional safety net."""
    exists = conn.execute(
        "SELECT 1 FROM prompt_versions WHERE prompt_key = %s AND version = %s",
        (key, version),
    ).fetchone()
    if not exists:
        return None
    row = conn.execute(
        """
        UPDATE prompt_definitions
        SET selected_version = %s, updated_at = now()
        WHERE key = %s
        RETURNING key, purpose, selected_version, created_at, updated_at
        """,
        (version, key),
    ).fetchone()
    conn.commit()
    item = dict(row)
    item["created_at"] = _iso(item.get("created_at"))
    item["updated_at"] = _iso(item.get("updated_at"))
    return item


def list_trace_sessions(conn, *, limit: int = 100) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            s.session_id,
            s.username,
            s.created_at,
            count(r.request_id)::int AS request_count,
            (count(r.request_id) FILTER (WHERE r.error IS NOT NULL))::int AS error_count,
            max(r.received_at) AS last_request_at
        FROM foundry_trace_sessions AS s
        LEFT JOIN foundry_request_traces AS r ON r.session_id = s.session_id
        GROUP BY s.session_id
        ORDER BY coalesce(max(r.received_at), s.created_at) DESC, s.session_id DESC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
    sessions = []
    for row in rows:
        item = dict(row)
        item["created_at"] = _iso(item.get("created_at"))
        item["last_request_at"] = _iso(item.get("last_request_at"))
        sessions.append(item)
    return sessions


def get_trace_session(conn, session_id: str) -> dict[str, Any] | None:
    session = conn.execute(
        """
        SELECT session_id, username, created_at
        FROM foundry_trace_sessions
        WHERE session_id = %s
        """,
        (session_id,),
    ).fetchone()
    if not session:
        return None
    item = dict(session)
    item["created_at"] = _iso(item.get("created_at"))
    rows = conn.execute(
        """
        SELECT
            request_id,
            received_at,
            updated_at,
            inputs->>'prompt_key' AS prompt_key,
            nullif(inputs->>'prompt_version', '')::int AS prompt_version,
            inputs->>'question' AS question,
            response->>'id' AS response_id,
            (provider_request IS NOT NULL) AS has_provider_request,
            (response IS NOT NULL) AS has_response,
            (error IS NOT NULL) AS has_error
        FROM foundry_request_traces
        WHERE session_id = %s
        ORDER BY received_at DESC, request_id DESC
        """,
        (session_id,),
    ).fetchall()
    requests = []
    for row in rows:
        request_item = dict(row)
        request_item["received_at"] = _iso(request_item.get("received_at"))
        request_item["updated_at"] = _iso(request_item.get("updated_at"))
        requests.append(request_item)
    item["requests"] = requests
    return item


def get_request_trace(conn, request_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT
            request_id,
            session_id,
            received_at,
            updated_at,
            inputs,
            provider_request,
            prompt,
            response,
            error
        FROM foundry_request_traces
        WHERE request_id = %s
        """,
        (request_id,),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["received_at"] = _iso(item.get("received_at"))
    item["updated_at"] = _iso(item.get("updated_at"))
    return item


@router.get("/prompts")
def prompts_list(request: Request) -> dict[str, Any]:
    with connect(request.app.state.settings) as conn:
        return {"prompts": list_prompt_definitions(conn)}


@router.get("/prompts/{key}")
def prompt_detail(key: str, request: Request) -> dict[str, Any]:
    with connect(request.app.state.settings) as conn:
        prompt = get_prompt_definition(conn, key)
    if prompt is None:
        raise HTTPException(status_code=404, detail=f"Unknown prompt: {key}")
    return prompt


@router.post("/prompts/{key}/versions", status_code=status.HTTP_201_CREATED)
def prompt_version_create(
    key: str,
    payload: PromptVersionCreate,
    request: Request,
) -> dict[str, Any]:
    try:
        with connect(request.app.state.settings) as conn:
            version = create_prompt_version(
                conn,
                key=key,
                instructions=payload.instructions,
                change_notes=payload.change_notes,
                created_by="prompt-studio",
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if version is None:
        raise HTTPException(status_code=404, detail=f"Unknown prompt: {key}")
    return version


@router.post("/prompts/{key}/selected-version")
def prompt_version_select(
    key: str,
    payload: PromptVersionSelect,
    request: Request,
) -> dict[str, Any]:
    with connect(request.app.state.settings) as conn:
        prompt = select_prompt_version(conn, key=key, version=payload.version)
    if prompt is None:
        raise HTTPException(
            status_code=404,
            detail=f"Prompt {key!r} has no version {payload.version}",
        )
    return prompt


@router.get("/trace-sessions")
def trace_sessions_list(request: Request) -> dict[str, Any]:
    with connect(request.app.state.settings) as conn:
        return {"sessions": list_trace_sessions(conn)}


@router.get("/trace-sessions/{session_id}")
def trace_session_detail(session_id: str, request: Request) -> dict[str, Any]:
    with connect(request.app.state.settings) as conn:
        session = get_trace_session(conn, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Unknown trace session: {session_id}")
    return session


@router.get("/trace-requests/{request_id}")
def trace_request_detail(request_id: str, request: Request) -> dict[str, Any]:
    with connect(request.app.state.settings) as conn:
        trace = get_request_trace(conn, request_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Unknown request trace: {request_id}")
    return trace
