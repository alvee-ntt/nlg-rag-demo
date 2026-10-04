"""Standalone, demo-only prompt-management and trace-inspection API.

The main application only resolves selected prompts. This router owns the isolated
authoring workflow: browse definitions, inspect immutable versions, create a version,
and explicitly select the version the application should use. It also provides the
read-only session/request drill-down used by Trace Explorer.

Prompt authoring, trace inspection, and test replay are intentionally open in this
demo workspace. Revisit that boundary before using it with production data.
"""

from __future__ import annotations

from datetime import datetime
import secrets
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb

from .db import connect
from .foundry import foundry_configured, replay_ask_prompt
from .prompt_features import (
    features_using_component,
    get_prompt_component,
    get_prompt_feature,
    validate_component_template,
)

router = APIRouter(prefix="/v1/prompt-admin", tags=["prompt-admin"])


class PromptVersionCreate(BaseModel):
    instructions: str = Field(..., min_length=1, max_length=200_000)
    change_notes: str = Field(default="", max_length=2_000)


class PromptVersionSelect(BaseModel):
    version: int = Field(..., ge=1)


class PromptTestRunCreate(BaseModel):
    feature: str = Field(..., min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_-]*$")
    source_invocation_id: str = Field(..., min_length=1, max_length=240)
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
    component = get_prompt_component(key)
    item["placeholder_contract"] = {
        "required": sorted(component.required_placeholders),
        "allowed": sorted(component.allowed_placeholders),
    } if component else None
    item["registered_features"] = [
        {"key": feature_key, "name": feature.name}
        for feature_key in features_using_component(key)
        if (feature := get_prompt_feature(feature_key)) is not None
    ]
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
    validate_component_template(key, instructions)
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
    selected_version = conn.execute(
        "SELECT instructions FROM prompt_versions WHERE prompt_key = %s AND version = %s",
        (key, version),
    ).fetchone()
    if not selected_version:
        return None
    validate_component_template(key, str(selected_version["instructions"]))
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


def list_prompt_invocations(
    conn,
    *,
    feature: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            invocation_id, feature_key, origin, trace_session_id, correlation_id,
            started_at, completed_at, status, prompt_recipe, runtime_inputs,
            provider_metadata, model_output, error
        FROM prompt_invocation_traces
        WHERE (%s IS NULL OR feature_key = %s)
        ORDER BY started_at DESC, invocation_id DESC
        LIMIT %s
        """,
        (feature, feature, limit),
    ).fetchall()
    invocations = []
    for row in rows:
        item = dict(row)
        item["started_at"] = _iso(item.get("started_at"))
        item["completed_at"] = _iso(item.get("completed_at"))
        inputs = item.get("runtime_inputs") or {}
        item["question"] = inputs.get("question") or inputs.get("topic") or ""
        item["history_turns"] = len(inputs.get("history") or [])
        invocations.append(item)
    return invocations


def get_prompt_invocation(conn, invocation_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT invocation_id, feature_key, origin, trace_session_id, correlation_id,
               started_at, completed_at, status, prompt_recipe, runtime_inputs,
               rendered_prompt, provider_metadata, model_output, error, updated_at
        FROM prompt_invocation_traces
        WHERE invocation_id = %s
        """,
        (invocation_id,),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    for key in ("started_at", "completed_at", "updated_at"):
        item[key] = _iso(item.get(key))
    attempts = conn.execute(
        """
        SELECT attempt_number, reason, provider, requested_model,
               actual_model_metadata, started_at, completed_at,
               provider_request, provider_response, error
        FROM prompt_provider_attempts
        WHERE invocation_id = %s
        ORDER BY attempt_number
        """,
        (invocation_id,),
    ).fetchall()
    item["attempts"] = []
    for attempt in attempts:
        attempt_item = dict(attempt)
        attempt_item["started_at"] = _iso(attempt_item.get("started_at"))
        attempt_item["completed_at"] = _iso(attempt_item.get("completed_at"))
        item["attempts"].append(attempt_item)
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


def list_feature_invocations(conn, feature: str, *, limit: int = 100) -> list[dict[str, Any]]:
    config = TEST_FEATURES.get(feature)
    if not config:
        return []
    rows = conn.execute(
        """
        SELECT invocation_id, trace_session_id, started_at, runtime_inputs,
               prompt_recipe, status, model_output, error
        FROM prompt_invocation_traces AS i
        WHERE feature_key = %s AND origin = 'live'
          AND NOT EXISTS (
              SELECT 1 FROM prompt_test_cases AS c
              WHERE c.replay_invocation_id = i.invocation_id
          )
        ORDER BY started_at DESC, invocation_id DESC
        LIMIT %s
        """,
        (feature, limit),
    ).fetchall()
    traces = []
    for row in rows:
        item = dict(row)
        inputs = item.get("runtime_inputs") or {}
        traces.append({
            "invocation_id": item["invocation_id"],
            "trace_session_id": item.get("trace_session_id"),
            "started_at": _iso(item.get("started_at")),
            "question": inputs.get("question") or "",
            "history_turns": len(inputs.get("history") or []),
            "prompt_versions": item.get("prompt_recipe") or {},
            "status": item.get("status"),
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
        SELECT r.run_id, r.feature, r.source_invocation_id, r.status, r.case_count,
               r.created_at, r.started_at, r.completed_at, r.error,
               count(c.case_id) FILTER (WHERE c.status = 'completed')::int AS completed_count,
               count(c.case_id) FILTER (WHERE c.status = 'failed')::int AS failed_count,
               t.runtime_inputs->>'question' AS question
        FROM prompt_test_runs AS r
        JOIN prompt_invocation_traces AS t ON t.invocation_id = r.source_invocation_id
        LEFT JOIN prompt_test_cases AS c ON c.run_id = r.run_id
        GROUP BY r.run_id, t.invocation_id
        ORDER BY r.created_at DESC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
    return [_test_run_dict(row) for row in rows]


def get_test_run(conn, run_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT r.run_id, r.feature, r.source_invocation_id, r.status, r.case_count,
               r.created_at, r.started_at, r.completed_at, r.error,
               t.runtime_inputs->>'question' AS question
        FROM prompt_test_runs AS r
        JOIN prompt_invocation_traces AS t ON t.invocation_id = r.source_invocation_id
        WHERE r.run_id = %s
        """,
        (run_id,),
    ).fetchone()
    if not row:
        return None
    run = _test_run_dict(row)
    cases = conn.execute(
        """
        SELECT c.case_id, c.position, c.prompt_versions, c.status, c.result, c.error,
               c.replay_invocation_id, c.started_at, c.completed_at,
               i.model_output, i.provider_metadata
        FROM prompt_test_cases
        AS c LEFT JOIN prompt_invocation_traces AS i
          ON i.invocation_id = c.replay_invocation_id
        WHERE run_id = %s
        ORDER BY position
        """,
        (run_id,),
    ).fetchall()
    run["cases"] = []
    for case in cases:
        case_item = _test_run_dict(case)
        replay_invocation_id = case_item.get("replay_invocation_id")
        if replay_invocation_id:
            replay = get_prompt_invocation(conn, replay_invocation_id)
            case_item["attempts"] = (replay or {}).get("attempts", [])
        run["cases"].append(case_item)
    return run


def _normalize_test_configuration(
    conn,
    *,
    feature: str,
    source_invocation_id: str,
    combinations: list[dict[str, int]],
) -> list[dict[str, int]]:
    config = TEST_FEATURES.get(feature)
    if not config:
        raise ValueError(f"Unknown test feature: {feature}")
    source = conn.execute(
        "SELECT feature_key, prompt_recipe FROM prompt_invocation_traces WHERE invocation_id = %s",
        (source_invocation_id,),
    ).fetchone()
    if not source or source.get("feature_key") != feature:
        raise ValueError("The selected trace does not belong to this feature")

    expected = set(source.get("prompt_recipe") or {})
    allowed = {config["primary"], *config["augmentations"]}
    if not expected or config["primary"] not in expected or not expected <= allowed:
        raise ValueError("The source invocation has an invalid prompt recipe")
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
        SELECT c.configuration_id, c.name, c.feature, c.source_invocation_id,
               c.combinations, c.created_at, c.updated_at, c.last_run_at,
               t.runtime_inputs->>'question' AS question
        FROM prompt_test_configurations AS c
        JOIN prompt_invocation_traces AS t ON t.invocation_id = c.source_invocation_id
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
    source_invocation_id: str,
    combinations: list[dict[str, int]],
) -> dict[str, Any]:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Configuration name cannot be blank")
    normalized = _normalize_test_configuration(
        conn,
        feature=feature,
        source_invocation_id=source_invocation_id,
        combinations=combinations,
    )
    now = datetime.now().astimezone()
    configuration_id = f"setup-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}"
    conn.execute(
        """
        INSERT INTO prompt_test_configurations
            (configuration_id, name, feature, source_invocation_id, combinations,
             created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            configuration_id, clean_name, feature, source_invocation_id,
            Jsonb(normalized), now, now,
        ),
    )
    conn.commit()
    return {
        "configuration_id": configuration_id,
        "name": clean_name,
        "feature": feature,
        "source_invocation_id": source_invocation_id,
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
    source_invocation_id: str,
    combinations: list[dict[str, int]],
) -> dict[str, Any]:
    normalized = _normalize_test_configuration(
        conn,
        feature=feature,
        source_invocation_id=source_invocation_id,
        combinations=combinations,
    )

    now = datetime.now().astimezone()
    run_id = f"test-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}"
    conn.execute(
        """
        INSERT INTO prompt_test_runs
            (run_id, feature, source_invocation_id, status, case_count, created_at)
        VALUES (%s, %s, %s, 'queued', %s, %s)
        """,
        (run_id, feature, source_invocation_id, len(normalized), now),
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
        SELECT configuration_id, feature, source_invocation_id, combinations
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
        source_invocation_id=configuration["source_invocation_id"],
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
            source = get_prompt_invocation(conn, run["source_invocation_id"])
            conn.execute(
                "UPDATE prompt_test_runs SET status = 'running', started_at = now() WHERE run_id = %s",
                (run_id,),
            )
            conn.commit()
        assert source is not None
        inputs = source.get("runtime_inputs") or {}
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
                result = replay_ask_prompt(
                    settings=settings,
                    instructions=prompts[config["primary"]]["instructions"],
                    prompt_key=config["primary"],
                    prompt_version=versions[config["primary"]],
                    question=str(inputs.get("question") or ""),
                    history=inputs.get("history") or [],
                    preferences=inputs.get("preferences") or {},
                    prompt_augmentations=[
                        prompts[key] for key in config["augmentations"] if key in versions
                    ],
                    about_me=str(inputs.get("about_me") or ""),
                    memories=inputs.get("memories") or [],
                    user_id=inputs.get("user_id"),
                    trace_session_id=run_id,
                    correlation_id=case_id,
                )
                replay_invocation_id = result.pop("invocation_id")
                with connect(settings) as conn:
                    conn.execute(
                        """
                        UPDATE prompt_test_cases
                        SET status = 'completed', result = %s, replay_invocation_id = %s,
                            completed_at = now()
                        WHERE case_id = %s
                        """,
                        (Jsonb(result), replay_invocation_id, case_id),
                    )
                    conn.commit()
            except Exception as exc:  # noqa: BLE001 - one failed variant must not stop siblings
                error = {"error_type": type(exc).__name__, "message": str(exc)}
                replay_invocation_id = getattr(exc, "prompt_invocation_id", None)
                with connect(settings) as conn:
                    conn.execute(
                        """
                        UPDATE prompt_test_cases
                        SET status = 'failed', error = %s, replay_invocation_id = %s,
                            completed_at = now()
                        WHERE case_id = %s
                        """,
                        (Jsonb(error), replay_invocation_id, case_id),
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


@router.get("/invocations")
def prompt_invocations_list(request: Request, feature: str | None = None) -> dict[str, Any]:
    with connect(request.app.state.settings) as conn:
        return {"invocations": list_prompt_invocations(conn, feature=feature)}


@router.get("/invocations/{invocation_id}")
def prompt_invocation_detail(invocation_id: str, request: Request) -> dict[str, Any]:
    with connect(request.app.state.settings) as conn:
        invocation = get_prompt_invocation(conn, invocation_id)
    if invocation is None:
        raise HTTPException(status_code=404, detail=f"Unknown invocation: {invocation_id}")
    return invocation


@router.get("/test-features")
def test_features_list(request: Request) -> dict[str, Any]:
    with connect(request.app.state.settings) as conn:
        return {"features": list_test_features(conn)}


@router.get("/test-features/{feature}/traces")
def test_feature_traces(feature: str, request: Request) -> dict[str, Any]:
    if feature not in TEST_FEATURES:
        raise HTTPException(status_code=404, detail=f"Unknown test feature: {feature}")
    with connect(request.app.state.settings) as conn:
        return {"traces": list_feature_invocations(conn, feature)}


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
                source_invocation_id=payload.source_invocation_id,
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
                source_invocation_id=payload.source_invocation_id,
                combinations=payload.combinations,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    background_tasks.add_task(execute_test_run, request.app.state.settings, run["run_id"])
    return run
