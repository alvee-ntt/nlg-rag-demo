"""Standalone, demo-only prompt-management and trace-inspection API.

The main application only resolves selected prompts. This router owns the isolated
authoring workflow: browse definitions, inspect immutable versions, create a version,
and explicitly select the version the application should use. It also provides the
read-only session/request drill-down used by Trace Explorer.

Prompt authoring and trace inspection remain open in this demo. Test replay endpoints
use the app's sign-in because they can create billable provider requests.
"""

from __future__ import annotations

from datetime import datetime
import secrets
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb

from .db import connect, create_foundry_trace_session
from .foundry import chat as foundry_chat, foundry_configured

router = APIRouter(prefix="/v1/prompt-admin", tags=["prompt-admin"])


class PromptVersionCreate(BaseModel):
    instructions: str = Field(..., min_length=1, max_length=200_000)
    change_notes: str = Field(default="", max_length=2_000)


class PromptVersionSelect(BaseModel):
    version: int = Field(..., ge=1)


class PromptTestRunCreate(BaseModel):
    feature: str = Field(..., min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_-]*$")
    source_trace_request_id: str = Field(..., min_length=1, max_length=240)
    combinations: list[dict[str, int]] = Field(..., min_length=1, max_length=25)


class PromptTestConfigurationCreate(PromptTestRunCreate):
    name: str = Field(..., min_length=1, max_length=120)


# A feature describes the prompts that participate in one executable application flow.
# Keeping execution adapters explicit prevents the UI from offering combinations the
# backend cannot actually replay yet, while leaving room for more flows later.
TEST_FEATURES: dict[str, dict[str, Any]] = {
    "ask": {
        "name": "Ask",
        "description": "Replay an Ask request with different navigator and context prompt versions.",
        "primary": "ask.navigator",
        "augmentations": ["ask.about_me", "ask.memories"],
    },
}


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


def list_test_features(conn) -> list[dict[str, Any]]:
    """Return only feature adapters whose complete prompt set exists."""
    rows = conn.execute(
        """
        SELECT d.key, d.purpose, d.selected_version, v.version, v.change_notes, v.created_at
        FROM prompt_definitions AS d
        JOIN prompt_versions AS v ON v.prompt_key = d.key
        ORDER BY d.key, v.version DESC
        """
    ).fetchall()
    by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        key = item["key"]
        prompt = by_key.setdefault(key, {
            "key": key,
            "purpose": item["purpose"],
            "selected_version": item["selected_version"],
            "versions": [],
        })
        prompt["versions"].append({
            "version": item["version"],
            "change_notes": item.get("change_notes") or "",
            "created_at": _iso(item.get("created_at")),
        })

    features: list[dict[str, Any]] = []
    for feature_key, config in TEST_FEATURES.items():
        prompt_keys = [config["primary"], *config["augmentations"]]
        if not all(key in by_key for key in prompt_keys):
            continue
        features.append({
            "key": feature_key,
            "name": config["name"],
            "description": config["description"],
            "prompts": [by_key[key] for key in prompt_keys],
        })
    return features


def list_feature_trace_requests(conn, feature: str, *, limit: int = 100) -> list[dict[str, Any]]:
    config = TEST_FEATURES.get(feature)
    if not config:
        return []
    rows = conn.execute(
        """
        SELECT request_id, session_id, received_at, inputs, response, error
        FROM foundry_request_traces AS r
        WHERE inputs->>'prompt_key' = %s
          AND NOT EXISTS (
              SELECT 1 FROM prompt_test_cases AS c WHERE c.trace_request_id = r.request_id
          )
        ORDER BY received_at DESC, request_id DESC
        LIMIT %s
        """,
        (config["primary"], limit),
    ).fetchall()
    traces = []
    for row in rows:
        item = dict(row)
        inputs = item.get("inputs") or {}
        versions = {inputs.get("prompt_key"): inputs.get("prompt_version")}
        for augmentation in inputs.get("prompt_augmentations") or []:
            if augmentation.get("key"):
                versions[augmentation["key"]] = augmentation.get("version")
        traces.append({
            "request_id": item["request_id"],
            "session_id": item["session_id"],
            "received_at": _iso(item.get("received_at")),
            "question": inputs.get("question") or "",
            "history_turns": len(inputs.get("history") or []),
            "prompt_versions": versions,
            "status": "failed" if item.get("error") else (
                "completed" if item.get("response") else "incomplete"
            ),
        })
    return traces


def _test_run_dict(row: Any) -> dict[str, Any]:
    item = dict(row)
    for key in ("created_at", "started_at", "completed_at"):
        item[key] = _iso(item.get(key))
    return item


def list_test_runs(conn, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT r.run_id, r.feature, r.source_trace_request_id, r.status, r.case_count,
               r.created_at, r.started_at, r.completed_at, r.error,
               count(c.case_id) FILTER (WHERE c.status = 'completed')::int AS completed_count,
               count(c.case_id) FILTER (WHERE c.status = 'failed')::int AS failed_count,
               t.inputs->>'question' AS question
        FROM prompt_test_runs AS r
        JOIN foundry_request_traces AS t ON t.request_id = r.source_trace_request_id
        LEFT JOIN prompt_test_cases AS c ON c.run_id = r.run_id
        GROUP BY r.run_id, t.request_id
        ORDER BY r.created_at DESC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
    return [_test_run_dict(row) for row in rows]


def get_test_run(conn, run_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT r.run_id, r.feature, r.source_trace_request_id, r.status, r.case_count,
               r.created_at, r.started_at, r.completed_at, r.error,
               t.inputs->>'question' AS question
        FROM prompt_test_runs AS r
        JOIN foundry_request_traces AS t ON t.request_id = r.source_trace_request_id
        WHERE r.run_id = %s
        """,
        (run_id,),
    ).fetchone()
    if not row:
        return None
    run = _test_run_dict(row)
    cases = conn.execute(
        """
        SELECT case_id, position, prompt_versions, status, result, error,
               trace_request_id, started_at, completed_at
        FROM prompt_test_cases
        WHERE run_id = %s
        ORDER BY position
        """,
        (run_id,),
    ).fetchall()
    run["cases"] = [_test_run_dict(case) for case in cases]
    return run


def _normalize_test_configuration(
    conn,
    *,
    feature: str,
    source_trace_request_id: str,
    combinations: list[dict[str, int]],
) -> list[dict[str, int]]:
    config = TEST_FEATURES.get(feature)
    if not config:
        raise ValueError(f"Unknown test feature: {feature}")
    source = conn.execute(
        "SELECT inputs FROM foundry_request_traces WHERE request_id = %s",
        (source_trace_request_id,),
    ).fetchone()
    if not source or (source.get("inputs") or {}).get("prompt_key") != config["primary"]:
        raise ValueError("The selected trace does not belong to this feature")

    expected = {config["primary"], *config["augmentations"]}
    normalized: list[dict[str, int]] = []
    seen: set[tuple[tuple[str, int], ...]] = set()
    for index, combination in enumerate(combinations, start=1):
        if set(combination) != expected:
            missing = sorted(expected - set(combination))
            extra = sorted(set(combination) - expected)
            raise ValueError(
                f"Combination {index} must choose every feature prompt"
                + (f"; missing {', '.join(missing)}" if missing else "")
                + (f"; unexpected {', '.join(extra)}" if extra else "")
            )
        clean = {key: int(combination[key]) for key in sorted(expected)}
        signature = tuple(clean.items())
        if signature in seen:
            raise ValueError(f"Combination {index} duplicates an earlier combination")
        seen.add(signature)
        for key, version in clean.items():
            exists = conn.execute(
                "SELECT 1 FROM prompt_versions WHERE prompt_key = %s AND version = %s",
                (key, version),
            ).fetchone()
            if not exists:
                raise ValueError(f"Prompt {key!r} has no version {version}")
        normalized.append(clean)
    return normalized


def list_test_configurations(conn, *, limit: int = 100) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT c.configuration_id, c.name, c.feature, c.source_trace_request_id,
               c.combinations, c.created_at, c.updated_at, c.last_run_at,
               t.inputs->>'question' AS question
        FROM prompt_test_configurations AS c
        JOIN foundry_request_traces AS t ON t.request_id = c.source_trace_request_id
        ORDER BY c.updated_at DESC, c.configuration_id DESC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
    configurations = []
    for row in rows:
        item = dict(row)
        for key in ("created_at", "updated_at", "last_run_at"):
            item[key] = _iso(item.get(key))
        item["case_count"] = len(item.get("combinations") or [])
        configurations.append(item)
    return configurations


def create_test_configuration(
    conn,
    *,
    name: str,
    feature: str,
    source_trace_request_id: str,
    combinations: list[dict[str, int]],
) -> dict[str, Any]:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Configuration name cannot be blank")
    normalized = _normalize_test_configuration(
        conn,
        feature=feature,
        source_trace_request_id=source_trace_request_id,
        combinations=combinations,
    )
    now = datetime.now().astimezone()
    configuration_id = f"setup-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}"
    conn.execute(
        """
        INSERT INTO prompt_test_configurations
            (configuration_id, name, feature, source_trace_request_id, combinations,
             created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            configuration_id, clean_name, feature, source_trace_request_id,
            Jsonb(normalized), now, now,
        ),
    )
    conn.commit()
    return {
        "configuration_id": configuration_id,
        "name": clean_name,
        "feature": feature,
        "source_trace_request_id": source_trace_request_id,
        "combinations": normalized,
        "case_count": len(normalized),
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "last_run_at": None,
    }


def create_test_run(
    conn,
    *,
    feature: str,
    source_trace_request_id: str,
    combinations: list[dict[str, int]],
) -> dict[str, Any]:
    normalized = _normalize_test_configuration(
        conn,
        feature=feature,
        source_trace_request_id=source_trace_request_id,
        combinations=combinations,
    )

    now = datetime.now().astimezone()
    run_id = f"test-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}"
    conn.execute(
        """
        INSERT INTO prompt_test_runs
            (run_id, feature, source_trace_request_id, status, case_count, created_at)
        VALUES (%s, %s, %s, 'queued', %s, %s)
        """,
        (run_id, feature, source_trace_request_id, len(normalized), now),
    )
    for position, combination in enumerate(normalized, start=1):
        conn.execute(
            """
            INSERT INTO prompt_test_cases
                (case_id, run_id, position, prompt_versions, status)
            VALUES (%s, %s, %s, %s, 'queued')
            """,
            (f"{run_id}-case-{position}", run_id, position, Jsonb(combination)),
        )
    conn.commit()
    return {"run_id": run_id, "status": "queued", "case_count": len(normalized)}


def create_test_run_from_configuration(conn, configuration_id: str) -> dict[str, Any] | None:
    configuration = conn.execute(
        """
        SELECT configuration_id, feature, source_trace_request_id, combinations
        FROM prompt_test_configurations
        WHERE configuration_id = %s
        """,
        (configuration_id,),
    ).fetchone()
    if not configuration:
        return None
    run = create_test_run(
        conn,
        feature=configuration["feature"],
        source_trace_request_id=configuration["source_trace_request_id"],
        combinations=configuration["combinations"],
    )
    conn.execute(
        """
        UPDATE prompt_test_configurations
        SET last_run_at = now(), updated_at = now()
        WHERE configuration_id = %s
        """,
        (configuration_id,),
    )
    conn.commit()
    run["configuration_id"] = configuration_id
    return run


def execute_test_run(settings: Any, run_id: str) -> None:
    """Replay each case serially so one run cannot unexpectedly fan out model load."""
    try:
        with connect(settings) as conn:
            run = get_test_run(conn, run_id)
            if not run:
                return
            source = get_request_trace(conn, run["source_trace_request_id"])
            conn.execute(
                "UPDATE prompt_test_runs SET status = 'running', started_at = now() WHERE run_id = %s",
                (run_id,),
            )
            create_foundry_trace_session(
                conn,
                session_id=run_id,
                username="prompt-test-runner",
                created_at=datetime.now().astimezone(),
            )
            conn.commit()
        assert source is not None
        inputs = source.get("inputs") or {}
        config = TEST_FEATURES[run["feature"]]

        for case in run["cases"]:
            case_id = case["case_id"]
            with connect(settings) as conn:
                conn.execute(
                    "UPDATE prompt_test_cases SET status = 'running', started_at = now() WHERE case_id = %s",
                    (case_id,),
                )
                versions = case["prompt_versions"]
                prompts: dict[str, dict[str, Any]] = {}
                for key, version in versions.items():
                    row = conn.execute(
                        """
                        SELECT prompt_key AS key, version, instructions
                        FROM prompt_versions WHERE prompt_key = %s AND version = %s
                        """,
                        (key, version),
                    ).fetchone()
                    if not row:
                        raise ValueError(f"Prompt {key!r} version {version} no longer exists")
                    prompts[key] = dict(row)
                conn.commit()
            try:
                result = foundry_chat(
                    settings=settings,
                    instructions=prompts[config["primary"]]["instructions"],
                    prompt_key=config["primary"],
                    prompt_version=versions[config["primary"]],
                    question=str(inputs.get("question") or ""),
                    history=inputs.get("history") or [],
                    preferences=inputs.get("preferences") or {},
                    prompt_augmentations=[prompts[key] for key in config["augmentations"]],
                    about_me=str(inputs.get("about_me") or ""),
                    memories=inputs.get("memories") or [],
                    user_id=inputs.get("user_id"),
                    trace_session_id=run_id,
                )
                trace_request_id = result.pop("trace_request_id", None)
                with connect(settings) as conn:
                    conn.execute(
                        """
                        UPDATE prompt_test_cases
                        SET status = 'completed', result = %s, trace_request_id = %s,
                            completed_at = now()
                        WHERE case_id = %s
                        """,
                        (Jsonb(result), trace_request_id, case_id),
                    )
                    conn.commit()
            except Exception as exc:  # noqa: BLE001 - one failed variant must not stop siblings
                error = {"error_type": type(exc).__name__, "message": str(exc)}
                with connect(settings) as conn:
                    conn.execute(
                        """
                        UPDATE prompt_test_cases
                        SET status = 'failed', error = %s, completed_at = now()
                        WHERE case_id = %s
                        """,
                        (Jsonb(error), case_id),
                    )
                    conn.commit()

        with connect(settings) as conn:
            conn.execute(
                "UPDATE prompt_test_runs SET status = 'completed', completed_at = now() WHERE run_id = %s",
                (run_id,),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001 - persist a runner-level failure for the UI
        with connect(settings) as conn:
            conn.execute(
                """
                UPDATE prompt_test_runs
                SET status = 'failed', error = %s, completed_at = now()
                WHERE run_id = %s
                """,
                (f"{type(exc).__name__}: {exc}", run_id),
            )
            conn.commit()


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


@router.get("/test-features")
def test_features_list(request: Request) -> dict[str, Any]:
    with connect(request.app.state.settings) as conn:
        return {"features": list_test_features(conn)}


@router.get("/test-features/{feature}/traces")
def test_feature_traces(feature: str, request: Request) -> dict[str, Any]:
    if feature not in TEST_FEATURES:
        raise HTTPException(status_code=404, detail=f"Unknown test feature: {feature}")
    with connect(request.app.state.settings) as conn:
        return {"traces": list_feature_trace_requests(conn, feature)}


@router.get("/test-runs")
def test_runs_list(request: Request) -> dict[str, Any]:
    with connect(request.app.state.settings) as conn:
        return {"runs": list_test_runs(conn)}


@router.get("/test-configurations")
def test_configurations_list(request: Request) -> dict[str, Any]:
    with connect(request.app.state.settings) as conn:
        return {"configurations": list_test_configurations(conn)}


@router.post("/test-configurations", status_code=status.HTTP_201_CREATED)
def test_configuration_create(
    payload: PromptTestConfigurationCreate,
    request: Request,
) -> dict[str, Any]:
    try:
        with connect(request.app.state.settings) as conn:
            return create_test_configuration(
                conn,
                name=payload.name,
                feature=payload.feature,
                source_trace_request_id=payload.source_trace_request_id,
                combinations=payload.combinations,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/test-configurations/{configuration_id}/runs", status_code=status.HTTP_202_ACCEPTED)
def test_configuration_run(
    configuration_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    if not foundry_configured(request.app.state.settings):
        raise HTTPException(status_code=503, detail="Foundry is not configured")
    try:
        with connect(request.app.state.settings) as conn:
            run = create_test_run_from_configuration(conn, configuration_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if run is None:
        raise HTTPException(status_code=404, detail="Unknown saved test configuration")
    background_tasks.add_task(execute_test_run, request.app.state.settings, run["run_id"])
    return run


@router.get("/test-runs/{run_id}")
def test_run_detail(run_id: str, request: Request) -> dict[str, Any]:
    with connect(request.app.state.settings) as conn:
        run = get_test_run(conn, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Unknown test run: {run_id}")
    return run


@router.post("/test-runs", status_code=status.HTTP_202_ACCEPTED)
def test_run_create(
    payload: PromptTestRunCreate,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    if not foundry_configured(request.app.state.settings):
        raise HTTPException(status_code=503, detail="Foundry is not configured")
    try:
        with connect(request.app.state.settings) as conn:
            run = create_test_run(
                conn,
                feature=payload.feature,
                source_trace_request_id=payload.source_trace_request_id,
                combinations=payload.combinations,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    background_tasks.add_task(execute_test_run, request.app.state.settings, run["run_id"])
    return run
