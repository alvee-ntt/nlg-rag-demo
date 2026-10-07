from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from prompt_library import (
    create_prompt_definition,
    create_prompt_version,
    get_prompt_definition,
    list_prompt_definitions,
    select_prompt_version,
)
from prompt_studio_api.database import connect
from prompt_studio_api.models import PromptDefinitionCreate, PromptVersionCreate, PromptVersionSelect
from prompt_studio_api.settings import PromptStudioSettings
from prompt_studio_api.trace_store import get_trace_session, list_trace_sessions


router = APIRouter(prefix="/api", tags=["prompt-studio"])


def _settings(request: Request) -> PromptStudioSettings:
    return request.app.state.settings


@router.get("/prompts")
def prompts_list(request: Request) -> dict[str, Any]:
    with connect(_settings(request)) as conn:
        return {"prompts": list_prompt_definitions(conn)}


@router.get("/traces")
def traces_list(request: Request) -> dict[str, Any]:
    with connect(_settings(request)) as conn:
        return {"sessions": list_trace_sessions(conn)}


@router.get("/traces/{session_id}")
def trace_detail(session_id: str, request: Request) -> dict[str, Any]:
    with connect(_settings(request)) as conn:
        trace = get_trace_session(conn, session_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Trace session not found")
    return trace


@router.post("/prompts", status_code=status.HTTP_201_CREATED)
def prompt_definition_create(
    payload: PromptDefinitionCreate, request: Request
) -> dict[str, Any]:
    try:
        with connect(_settings(request)) as conn:
            return create_prompt_definition(
                conn,
                key=payload.key,
                purpose=payload.purpose,
                instructions=payload.instructions,
                created_by="prompt-studio",
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/prompts/{key}")
def prompt_detail(key: str, request: Request) -> dict[str, Any]:
    with connect(_settings(request)) as conn:
        prompt = get_prompt_definition(conn, key)
    if prompt is None:
        raise HTTPException(status_code=404, detail=f"Unknown prompt: {key}")
    return prompt


@router.post("/prompts/{key}/versions", status_code=status.HTTP_201_CREATED)
def prompt_version_create(
    key: str, payload: PromptVersionCreate, request: Request
) -> dict[str, Any]:
    try:
        with connect(_settings(request)) as conn:
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
    key: str, payload: PromptVersionSelect, request: Request
) -> dict[str, Any]:
    with connect(_settings(request)) as conn:
        prompt = select_prompt_version(conn, key=key, version=payload.version)
    if prompt is None:
        raise HTTPException(
            status_code=404,
            detail=f"Prompt {key!r} has no version {payload.version}",
        )
    return prompt
