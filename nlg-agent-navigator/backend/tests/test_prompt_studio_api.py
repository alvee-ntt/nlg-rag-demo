from fastapi import FastAPI

from prompt_studio_api.routes import router
from prompt_studio_api.schema import PROMPT_STUDIO_SCHEMA_NAME, PROMPT_STUDIO_SCHEMA_SQL


def test_prompt_studio_routes_are_not_mounted_in_agent_api() -> None:
    from app.main import create_app as create_agent_app

    assert not any(
        path.startswith("/api/prompts") for path in create_agent_app().openapi()["paths"]
    )


def test_prompt_studio_owns_prompt_crud_routes() -> None:
    app = FastAPI()
    app.include_router(router)

    assert set(app.openapi()["paths"]) == {
        "/api/prompts",
        "/api/prompts/{key}",
        "/api/prompts/{key}/versions",
        "/api/prompts/{key}/selected-version",
        "/api/traces",
        "/api/traces/{session_id}",
    }


def test_prompt_studio_schema_owns_trace_and_replay_tables() -> None:
    assert PROMPT_STUDIO_SCHEMA_NAME == "prompt_studio"
    for table in (
        "trace_sessions",
        "prompt_invocations",
        "test_configurations",
        "test_runs",
        "test_cases",
    ):
        assert f"prompt_studio.{table}" in PROMPT_STUDIO_SCHEMA_SQL
