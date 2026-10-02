"""Standalone, demo-only prompt-management API.

The main application only resolves selected prompts. This router owns the isolated
authoring workflow: browse definitions, inspect immutable versions, create a version,
and explicitly select the version the application should use.

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
