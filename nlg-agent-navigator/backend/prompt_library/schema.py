"""PostgreSQL schema owned by the reusable prompt library."""

PROMPT_SCHEMA_NAME = "prompt_library"

PROMPT_SCHEMA_SQL = r"""
CREATE SCHEMA IF NOT EXISTS prompt_library;

-- Prompt definitions identify an application function. Versions are immutable
-- snapshots, and selected_version identifies the version used at runtime.
CREATE TABLE IF NOT EXISTS prompt_library.prompt_definitions (
    key TEXT PRIMARY KEY,
    purpose TEXT NOT NULL,
    selected_version INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT prompt_definitions_key_format
        CHECK (key ~ '^[a-z][a-z0-9]*(\.[a-z][a-z0-9_-]*)+$')
);

CREATE TABLE IF NOT EXISTS prompt_library.prompt_versions (
    prompt_key TEXT NOT NULL
        REFERENCES prompt_library.prompt_definitions(key) ON DELETE RESTRICT,
    version INTEGER NOT NULL CHECK (version > 0),
    instructions TEXT NOT NULL CHECK (length(instructions) > 0),
    change_notes TEXT NOT NULL DEFAULT '',
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (prompt_key, version)
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'prompt_definitions_selected_version_fk'
          AND conrelid = 'prompt_library.prompt_definitions'::regclass
    ) THEN
        ALTER TABLE prompt_library.prompt_definitions
        ADD CONSTRAINT prompt_definitions_selected_version_fk
        FOREIGN KEY (key, selected_version)
        REFERENCES prompt_library.prompt_versions(prompt_key, version)
        DEFERRABLE INITIALLY DEFERRED;
    END IF;
END $$;
"""
