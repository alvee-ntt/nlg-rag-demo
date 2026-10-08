from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    foundry_project_endpoint: str | None = None
    foundry_api_key: SecretStr | None = None
    foundry_agent_kb: str | None = None
    foundry_model_large: str | None = None
    foundry_model_small: str | None = None
    underwriting_agent: str | None = None
    language_agent: str | None = None
    foundry_api_version: str = "v1"
    log_path: Path | None = None
    cors_origins: str = "http://localhost:5173"
    prompt_database_url: SecretStr | None = None

    # Azure Speech (text-to-speech for spoken replies). Shares the nlg-rag Speech
    # resource; the DragonHD voices match the Learn narration and roleplay.
    azure_speech_key: SecretStr | None = None
    azure_speech_region: str = "eastus"
    azure_speech_endpoint: str = "https://eastus.api.cognitive.microsoft.com"
    azure_speech_voice_ava: str = "en-US-Ava:DragonHDLatestNeural"
    azure_speech_voice_andrew: str = "en-US-Andrew:DragonHDLatestNeural"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def speech_is_configured(self) -> bool:
        return bool(self.azure_speech_key and self.azure_speech_region)

    @property
    def foundry_is_configured(self) -> bool:
        return all(
            (
                self.foundry_project_endpoint,
                self.foundry_api_key,
                self.foundry_agent_kb,
                self.foundry_model_large,
                self.foundry_model_small,
                self.underwriting_agent,
                self.language_agent,
            )
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
