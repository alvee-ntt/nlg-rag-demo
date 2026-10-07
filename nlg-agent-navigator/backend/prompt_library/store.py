"""Persistence operations for immutable, selectable prompt versions.

The functions accept any psycopg-compatible connection instead of importing the
host application's settings or connection factory. The library owns all objects in
the ``prompt_library`` PostgreSQL schema.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


class PromptConfigurationError(RuntimeError):
    """A prompt key has no usable selected version."""


def create_prompt_definition(
    conn,
    *,
    key: str,
    purpose: str,
    instructions: str,
    created_by: str,
) -> dict[str, Any]:
    """Create a definition with its selected immutable version 1."""
    if not purpose.strip():
        raise ValueError("Prompt purpose cannot be blank")
    if not instructions.strip():
        raise ValueError("Prompt instructions cannot be blank")
    row = conn.execute(
        """
        INSERT INTO prompt_library.prompt_definitions (key, purpose)
        VALUES (%s, %s)
        RETURNING key, purpose, created_at, updated_at
        """,
        (key, purpose.strip()),
    ).fetchone()
    conn.execute(
        """
        INSERT INTO prompt_library.prompt_versions
            (prompt_key, version, instructions, change_notes, created_by)
        VALUES (%s, 1, %s, %s, %s)
        """,
        (key, instructions.strip(), "Initial prompt", created_by),
    )
    conn.execute(
        """
        UPDATE prompt_library.prompt_definitions
        SET selected_version = 1, updated_at = now()
        WHERE key = %s
        """,
        (key,),
    )
    conn.commit()
    item = dict(row)
    item["selected_version"] = 1
    item["created_at"] = _iso(item.get("created_at"))
    item["updated_at"] = _iso(item.get("updated_at"))
    return item


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else value


def _version_dict(row: Any, selected_version: int | None = None) -> dict[str, Any]:
    item = dict(row)
    item["created_at"] = _iso(item.get("created_at"))
    if selected_version is not None:
        item["selected"] = item.get("version") == selected_version
    return item


def get_selected_prompt(conn, key: str) -> dict[str, Any]:
    """Resolve the immutable prompt version currently selected for a key."""
    row = conn.execute(
        """
        SELECT d.key, d.purpose, v.version, v.instructions
        FROM prompt_library.prompt_definitions AS d
        JOIN prompt_library.prompt_versions AS v
          ON v.prompt_key = d.key
         AND v.version = d.selected_version
        WHERE d.key = %s
        """,
        (key,),
    ).fetchone()
    if not row:
        raise PromptConfigurationError(
            f"No selected prompt version is configured for prompt key {key!r}"
        )
    return dict(row)


def list_prompt_definitions(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT d.key, d.purpose, d.selected_version, d.created_at, d.updated_at,
               count(v.version)::int AS version_count
        FROM prompt_library.prompt_definitions AS d
        LEFT JOIN prompt_library.prompt_versions AS v ON v.prompt_key = d.key
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
        FROM prompt_library.prompt_definitions
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
        FROM prompt_library.prompt_versions
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
        "SELECT key FROM prompt_library.prompt_definitions WHERE key = %s FOR UPDATE",
        (key,),
    ).fetchone()
    if not definition:
        return None
    if not instructions.strip():
        raise ValueError("Prompt instructions cannot be blank")
    row = conn.execute(
        """
        INSERT INTO prompt_library.prompt_versions
            (prompt_key, version, instructions, change_notes, created_by)
        SELECT %s, coalesce(max(version), 0) + 1, %s, %s, %s
        FROM prompt_library.prompt_versions
        WHERE prompt_key = %s
        RETURNING version, instructions, change_notes, created_by, created_at
        """,
        (key, instructions, change_notes.strip(), created_by, key),
    ).fetchone()
    conn.execute(
        "UPDATE prompt_library.prompt_definitions SET updated_at = now() WHERE key = %s",
        (key,),
    )
    conn.commit()
    return _version_dict(row)


def select_prompt_version(conn, *, key: str, version: int) -> dict[str, Any] | None:
    """Select an existing version; the composite foreign key is a safety net."""
    exists = conn.execute(
        "SELECT 1 FROM prompt_library.prompt_versions WHERE prompt_key = %s AND version = %s",
        (key, version),
    ).fetchone()
    if not exists:
        return None
    row = conn.execute(
        """
        UPDATE prompt_library.prompt_definitions
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
