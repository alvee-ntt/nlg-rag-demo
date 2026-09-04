from __future__ import annotations

from .config import load_settings


def main() -> None:
    settings = load_settings()
    missing = []
    if not settings.azure_storage_connection_string and not settings.azure_storage_sas_token:
        missing.append("AZURE_STORAGE_CONNECTION_STRING or AZURE_STORAGE_SAS_TOKEN")
    if not settings.azure_openai_endpoint:
        missing.append("AZURE_OPENAI_ENDPOINT")
    if not settings.azure_openai_api_key:
        missing.append("AZURE_OPENAI_API_KEY")
    if not settings.azure_openai_embedding_deployment:
        missing.append("AZURE_OPENAI_EMBEDDING_DEPLOYMENT")
    if not settings.azure_openai_chat_deployment:
        missing.append("AZURE_OPENAI_CHAT_DEPLOYMENT")

    if missing:
        print("Missing required settings:")
        for item in missing:
            print(f"- {item}")
        raise SystemExit(1)

    print("Config looks complete")
    print(f"Container: {settings.azure_storage_container}")
    print(f"Prefixes: {', '.join(settings.azure_blob_prefixes) or '(all)'}")
    print(f"Postgres: {settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}")
    print(f"Embedding deployment: {settings.azure_openai_embedding_deployment}")
    print(f"Chat deployment: {settings.azure_openai_chat_deployment}")


if __name__ == "__main__":
    main()
