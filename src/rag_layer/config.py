from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    azure_storage_account: str
    azure_storage_container: str
    azure_storage_connection_string: str
    azure_storage_sas_token: str
    azure_storage_verify_ssl: bool
    azure_blob_prefixes: list[str]

    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str
    postgres_sslmode: str

    model_provider: str
    azure_openai_endpoint: str
    azure_openai_api_key: str
    azure_openai_embedding_deployment: str
    azure_openai_chat_deployment: str
    azure_speech_key: str
    azure_speech_region: str
    azure_speech_endpoint: str
    azure_speech_voice_ava: str
    azure_speech_voice_andrew: str

    embedding_dimensions: int
    chunk_size: int
    chunk_overlap: int
    batch_size: int
    force_reindex: bool

    @property
    def postgres_dsn(self) -> str:
        return (
            f"host={self.postgres_host} port={self.postgres_port} "
            f"dbname={self.postgres_db} user={self.postgres_user} "
            f"password={self.postgres_password} sslmode={self.postgres_sslmode}"
        )


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y"}


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def _csv(name: str) -> list[str]:
    value = os.getenv(name, "")
    return [part.strip().strip("/") for part in value.split(",") if part.strip()]


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env", override=True)
    return Settings(
        azure_storage_account=os.getenv("AZURE_STORAGE_ACCOUNT", ""),
        azure_storage_container=os.getenv("AZURE_STORAGE_CONTAINER", ""),
        azure_storage_connection_string=os.getenv("AZURE_STORAGE_CONNECTION_STRING", ""),
        azure_storage_sas_token=os.getenv("AZURE_STORAGE_SAS_TOKEN", ""),
        azure_storage_verify_ssl=_bool("AZURE_STORAGE_VERIFY_SSL", True),
        azure_blob_prefixes=_csv("AZURE_BLOB_PREFIXES"),
        postgres_host=os.getenv("POSTGRES_HOST", "localhost"),
        postgres_port=_int("POSTGRES_PORT", 5432),
        postgres_db=os.getenv("POSTGRES_DB", "postgres"),
        postgres_user=os.getenv("POSTGRES_USER", "postgres"),
        postgres_password=os.getenv("POSTGRES_PASSWORD", "postgres"),
        postgres_sslmode=os.getenv("POSTGRES_SSLMODE", "disable"),
        model_provider=os.getenv("MODEL_PROVIDER", "azure_openai"),
        azure_openai_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/"),
        azure_openai_api_key=os.getenv("AZURE_OPENAI_API_KEY", ""),
        azure_openai_embedding_deployment=os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", ""),
        azure_openai_chat_deployment=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT", ""),
        azure_speech_key=os.getenv("AZURE_SPEECH_KEY", ""),
        azure_speech_region=os.getenv("AZURE_SPEECH_REGION", "eastus"),
        azure_speech_endpoint=os.getenv("AZURE_SPEECH_ENDPOINT", "https://eastus.api.cognitive.microsoft.com/").rstrip("/"),
        azure_speech_voice_ava=os.getenv("AZURE_SPEECH_VOICE_AVA", "en-US-Ava:DragonHDLatestNeural"),
        azure_speech_voice_andrew=os.getenv("AZURE_SPEECH_VOICE_ANDREW", "en-US-Andrew:DragonHDLatestNeural"),
        embedding_dimensions=_int("EMBEDDING_DIMENSIONS", 1536),
        chunk_size=_int("CHUNK_SIZE", 1000),
        chunk_overlap=_int("CHUNK_OVERLAP", 150),
        batch_size=_int("BATCH_SIZE", 50),
        force_reindex=_bool("FORCE_REINDEX", False),
    )


