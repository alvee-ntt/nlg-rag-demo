from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class PromptStudioSettings(BaseSettings):
    prompt_database_url: SecretStr | None = None
    prompt_studio_cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def database_url(self) -> str:
        if self.prompt_database_url is None:
            raise RuntimeError("PROMPT_DATABASE_URL is required for Prompt Studio")
        return self.prompt_database_url.get_secret_value()

    @property
    def allowed_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.prompt_studio_cors_origins.split(",")
            if origin.strip()
        ]


@lru_cache
def get_prompt_studio_settings() -> PromptStudioSettings:
    return PromptStudioSettings()
