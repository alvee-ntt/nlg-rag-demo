"""Application schema for Navigator data, separate from prompt storage."""

NAVIGATOR_SCHEMA_NAME = "NLG-Agent-Navigator"

NAVIGATOR_SCHEMA_SQL = r"""
CREATE SCHEMA IF NOT EXISTS "NLG-Agent-Navigator";

CREATE TABLE IF NOT EXISTS "NLG-Agent-Navigator".user_profiles (
    user_id TEXT PRIMARY KEY CHECK (length(btrim(user_id)) > 0),
    language TEXT NOT NULL CHECK (language IN ('English')),
    answer_length TEXT NOT NULL CHECK (answer_length IN ('Brief', 'Balanced', 'Detailed')),
    format TEXT NOT NULL CHECK (format IN ('Auto', 'Bullets', 'Prose')),
    tone TEXT NOT NULL CHECK (tone IN ('Warm', 'Neutral', 'Formal')),
    how_you_ask TEXT NOT NULL CHECK (how_you_ask IN ('Type', 'Voice')),
    how_answers_reach_you TEXT NOT NULL CHECK (how_answers_reach_you IN ('Type', 'Voice')),
    wording TEXT NOT NULL CHECK (wording IN ('Plain Language', 'Industry terms')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""
