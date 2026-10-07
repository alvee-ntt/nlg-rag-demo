# Prompt library

This package was initially imported from `nlg-rag-demo/src/prompt_library`.
It owns reusable prompt versioning and all database objects in the dedicated
`prompt_library` PostgreSQL schema. It contains no HTTP API. Host applications
supply connections; provider calls, request traces, replay behavior, authentication,
and UI remain host concerns.

The schema name is deliberately library-owned and fixed. Applications distinguish
their prompts with namespaced keys such as `nlgagent.orchestrator`.
