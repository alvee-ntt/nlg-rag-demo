from dataclasses import dataclass

from app.settings import Settings
from prompt_library import PROMPT_SCHEMA_SQL, get_selected_prompt
from prompt_library import PromptConfigurationError


@dataclass(frozen=True)
class AgentPrompts:
    knowledge: str
    orchestrator: str
    small: str
    underwriting: str
    language: str
    compose: str
    revise: str


PROMPT_KEYS = (
    "nlgagent.knowledge",
    "nlgagent.orchestrator",
    "nlgagent.small",
    "nlgagent.underwriting",
    "nlgagent.language",
    "nlgagent.compose",
    "nlgagent.revise",
)


def load_agent_prompts(settings: Settings) -> AgentPrompts:
    """Initialize and resolve the required startup prompt snapshot."""
    database_url = settings.prompt_database_url
    if database_url is None:
        raise PromptConfigurationError(
            "PROMPT_DATABASE_URL is required; the application will not fall back "
            "to repository prompts at runtime"
        )

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(database_url.get_secret_value(), row_factory=dict_row) as conn:
        conn.execute(PROMPT_SCHEMA_SQL)
        conn.commit()
        selected = {
            key: get_selected_prompt(conn, key)["instructions"] for key in PROMPT_KEYS
        }

    return AgentPrompts(
        knowledge=selected["nlgagent.knowledge"],
        orchestrator=selected["nlgagent.orchestrator"],
        small=selected["nlgagent.small"],
        underwriting=selected["nlgagent.underwriting"],
        language=selected["nlgagent.language"],
        compose=selected["nlgagent.compose"],
        revise=selected["nlgagent.revise"],
    )
