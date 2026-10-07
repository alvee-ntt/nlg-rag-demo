import psycopg
from psycopg.rows import dict_row

from prompt_library import PROMPT_SCHEMA_SQL
from prompt_studio_api.schema import PROMPT_STUDIO_SCHEMA_SQL
from prompt_studio_api.settings import PromptStudioSettings


def connect(settings: PromptStudioSettings):
    return psycopg.connect(settings.database_url, row_factory=dict_row)


def initialize_database(settings: PromptStudioSettings) -> None:
    with connect(settings) as conn:
        conn.execute(PROMPT_SCHEMA_SQL)
        conn.execute(PROMPT_STUDIO_SCHEMA_SQL)
        conn.commit()
