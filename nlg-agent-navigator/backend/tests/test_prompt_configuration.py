import pytest

from app.prompt_configuration import PROMPT_KEYS, load_agent_prompts
from app.settings import Settings
from prompt_library import (
    PROMPT_SCHEMA_NAME,
    PROMPT_SCHEMA_SQL,
    PromptConfigurationError,
)


def test_prompt_library_owns_a_dedicated_postgres_schema() -> None:
    assert PROMPT_SCHEMA_NAME == "prompt_library"
    assert "CREATE SCHEMA IF NOT EXISTS prompt_library" in PROMPT_SCHEMA_SQL
    assert "prompt_library.prompt_definitions" in PROMPT_SCHEMA_SQL
    assert "prompt_library.prompt_versions" in PROMPT_SCHEMA_SQL


def test_prompt_keys_are_namespaced_for_the_application() -> None:
    assert set(PROMPT_KEYS) == {
        "nlgagent.knowledge",
        "nlgagent.orchestrator",
        "nlgagent.small",
        "nlgagent.underwriting",
        "nlgagent.language",
        "nlgagent.compose",
        "nlgagent.revise",
    }


def test_no_database_configuration_fails_loudly() -> None:
    with pytest.raises(PromptConfigurationError, match="PROMPT_DATABASE_URL is required"):
        load_agent_prompts(Settings(prompt_database_url=None))
