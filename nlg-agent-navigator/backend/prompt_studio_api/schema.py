"""Prompt Studio-owned trace and replay schema."""

PROMPT_STUDIO_SCHEMA_NAME = "prompt_studio"

PROMPT_STUDIO_SCHEMA_SQL = r"""
CREATE SCHEMA IF NOT EXISTS prompt_studio;

CREATE TABLE IF NOT EXISTS prompt_studio.trace_sessions (
    session_id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    framework_session JSONB,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS prompt_studio.prompt_invocations (
    invocation_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL
        REFERENCES prompt_studio.trace_sessions(session_id) ON DELETE CASCADE,
    turn_id TEXT NOT NULL,
    turn_number INTEGER NOT NULL CHECK (turn_number > 0),
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    feature_key TEXT NOT NULL,
    actor TEXT NOT NULL,
    target TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    duration_ms DOUBLE PRECISION,
    prompt_recipe JSONB NOT NULL DEFAULT '{}'::jsonb,
    runtime_inputs JSONB NOT NULL DEFAULT '{}'::jsonb,
    logical_request JSONB,
    flattened_prompt TEXT,
    response JSONB,
    error JSONB,
    UNIQUE (turn_id, sequence)
);
CREATE INDEX IF NOT EXISTS prompt_invocations_session_idx
    ON prompt_studio.prompt_invocations(session_id, turn_number, sequence);
CREATE INDEX IF NOT EXISTS prompt_invocations_feature_idx
    ON prompt_studio.prompt_invocations(feature_key, started_at DESC);

CREATE TABLE IF NOT EXISTS prompt_studio.test_configurations (
    configuration_id TEXT PRIMARY KEY,
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    feature_key TEXT NOT NULL,
    source_invocation_id TEXT NOT NULL
        REFERENCES prompt_studio.prompt_invocations(invocation_id) ON DELETE RESTRICT,
    combinations JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_run_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS prompt_studio.test_runs (
    run_id TEXT PRIMARY KEY,
    feature_key TEXT NOT NULL,
    source_invocation_id TEXT NOT NULL
        REFERENCES prompt_studio.prompt_invocations(invocation_id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    case_count INTEGER NOT NULL CHECK (case_count > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    error TEXT
);

CREATE TABLE IF NOT EXISTS prompt_studio.test_cases (
    case_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES prompt_studio.test_runs(run_id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position > 0),
    prompt_recipe JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    result JSONB,
    error JSONB,
    replay_invocation_id TEXT
        REFERENCES prompt_studio.prompt_invocations(invocation_id) ON DELETE SET NULL,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    UNIQUE (run_id, position)
);
"""
