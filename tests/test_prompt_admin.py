from datetime import datetime, timezone
from pathlib import Path

from src.rag_layer.auth import Auth
from src.rag_layer.prompt_admin import (
    create_test_configuration,
    create_test_run,
    create_test_run_from_configuration,
    create_prompt_version,
    get_prompt_invocation,
    get_prompt_definition,
    list_feature_invocations,
    list_prompt_definitions,
    list_test_configurations,
    list_test_features,
    list_prompt_invocations,
    select_prompt_version,
)


class Result:
    def __init__(self, *, one=None, all_rows=None):
        self.one = one
        self.all_rows = all_rows or []

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.all_rows


def test_prompt_studio_routes_are_deliberately_open_for_the_demo():
    assert Auth.is_open("/prompts")
    assert Auth.is_open("/traces")
    assert Auth.is_open("/v1/prompt-admin/prompts")
    assert Auth.is_open("/v1/prompt-admin/invocations")
    assert Auth.is_open("/tests")
    assert Auth.is_open("/v1/prompt-admin/test-runs")


def test_list_prompt_definitions_serializes_dates():
    now = datetime(2026, 10, 1, 12, 30, tzinfo=timezone.utc)

    class Connection:
        def execute(self, query):
            return Result(all_rows=[{
                "key": "ask.navigator",
                "purpose": "Answer questions",
                "selected_version": 2,
                "created_at": now,
                "updated_at": now,
                "version_count": 2,
            }])

    rows = list_prompt_definitions(Connection())

    assert rows[0]["key"] == "ask.navigator"
    assert rows[0]["created_at"] == "2026-10-01T12:30:00+00:00"
    assert rows[0]["version_count"] == 2


def test_prompt_detail_marks_the_selected_version():
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)

    class Connection:
        calls = 0

        def execute(self, query, params):
            self.calls += 1
            if self.calls == 1:
                return Result(one={
                    "key": "ask.navigator",
                    "purpose": "Answer questions",
                    "selected_version": 2,
                    "created_at": now,
                    "updated_at": now,
                })
            return Result(all_rows=[
                {"version": 2, "instructions": "Two", "change_notes": "", "created_by": "a", "created_at": now},
                {"version": 1, "instructions": "One", "change_notes": "", "created_by": "a", "created_at": now},
            ])

    prompt = get_prompt_definition(Connection(), "ask.navigator")

    assert prompt["versions"][0]["selected"] is True
    assert prompt["versions"][1]["selected"] is False


def test_create_prompt_version_preserves_prompt_text_and_commits():
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)

    class Connection:
        committed = False
        insert_params = None

        def execute(self, query, params):
            if "FOR UPDATE" in query:
                return Result(one={"key": "ask.navigator"})
            if "INSERT INTO prompt_versions" in query:
                self.insert_params = params
                return Result(one={
                    "version": 4,
                    "instructions": params[1],
                    "change_notes": params[2],
                    "created_by": params[3],
                    "created_at": now,
                })
            return Result()

        def commit(self):
            self.committed = True

    conn = Connection()
    created = create_prompt_version(
        conn,
        key="ask.navigator",
        instructions="  Preserve meaningful whitespace\n",
        change_notes="  Clearer opening  ",
        created_by="prompt-studio",
    )

    assert created["version"] == 4
    assert created["instructions"] == "  Preserve meaningful whitespace\n"
    assert created["change_notes"] == "Clearer opening"
    assert conn.committed is True


def test_select_prompt_version_requires_an_existing_version():
    class Connection:
        committed = False

        def execute(self, query, params):
            return Result(one=None)

        def commit(self):
            self.committed = True

    conn = Connection()

    assert select_prompt_version(conn, key="ask.navigator", version=99) is None
    assert conn.committed is False


def test_select_prompt_version_updates_definition_and_commits():
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)

    class Connection:
        committed = False
        calls = 0

        def execute(self, query, params):
            self.calls += 1
            if self.calls == 1:
                return Result(one={"instructions": "Current instructions"})
            return Result(one={
                "key": "ask.navigator",
                "purpose": "Answer questions",
                "selected_version": params[0],
                "created_at": now,
                "updated_at": now,
            })

        def commit(self):
            self.committed = True

    conn = Connection()
    selected = select_prompt_version(conn, key="ask.navigator", version=3)

    assert selected["selected_version"] == 3
    assert selected["updated_at"] == "2026-10-01T00:00:00+00:00"
    assert conn.committed is True


def test_list_prompt_invocations_serializes_summary_dates():
    now = datetime(2026, 10, 2, 15, 45, tzinfo=timezone.utc)

    class Connection:
        def execute(self, query, params):
            assert "prompt_invocation_traces" in query
            assert params == (None, None, 100)
            return Result(all_rows=[{
                "invocation_id": "invocation-1", "feature_key": "ask", "origin": "live",
                "started_at": now, "completed_at": now, "status": "completed",
                "runtime_inputs": {"question": "How do caps work?", "history": []},
                "prompt_recipe": {"ask.navigator": 1},
            }])

    invocations = list_prompt_invocations(Connection())

    assert invocations[0]["question"] == "How do caps work?"
    assert invocations[0]["started_at"] == "2026-10-02T15:45:00+00:00"


def test_prompt_invocation_returns_attempts_and_saved_content():
    now = datetime(2026, 10, 2, 15, 45, tzinfo=timezone.utc)

    class Connection:
        calls = 0

        def execute(self, query, params):
            self.calls += 1
            assert params == ("invocation-1",)
            if self.calls == 1:
                return Result(one={
                    "invocation_id": "invocation-1", "feature_key": "ask", "origin": "live",
                    "started_at": now, "completed_at": now, "updated_at": now,
                    "status": "completed", "prompt_recipe": {"ask.navigator": 2},
                    "runtime_inputs": {"question": "How do caps work?"},
                    "rendered_prompt": "Rendered prompt", "model_output": "Answer",
                })
            return Result(all_rows=[{
                "attempt_number": 1, "reason": "primary", "provider": "azure_ai_foundry",
                "requested_model": "KnowledgeBase", "started_at": now, "completed_at": now,
                "provider_request": {"input": []}, "provider_response": {"id": "response-1"},
                "error": None,
            }])

    invocation = get_prompt_invocation(Connection(), "invocation-1")

    assert invocation["runtime_inputs"]["question"] == "How do caps work?"
    assert invocation["rendered_prompt"] == "Rendered prompt"
    assert invocation["model_output"] == "Answer"
    assert invocation["attempts"][0]["provider_response"]["id"] == "response-1"


def test_test_features_group_versions_into_friendly_executable_flows():
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)

    class Connection:
        def execute(self, query):
            return Result(all_rows=[
                {"key": key, "purpose": key, "selected_version": 2, "version": version,
                 "change_notes": f"notes-{version}", "created_at": now}
                for key in ("ask.about_me", "ask.memories", "ask.navigator")
                for version in (2, 1)
            ])

    features = list_test_features(Connection())

    assert [feature["key"] for feature in features] == ["ask"]
    assert [prompt["key"] for prompt in features[0]["prompts"]] == [
        "ask.navigator", "ask.about_me", "ask.memories",
    ]
    assert features[0]["prompts"][0]["versions"][0]["version"] == 2


def test_feature_invocations_include_the_original_prompt_recipe():
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)

    class Connection:
        def execute(self, query, params):
            assert params == ("ask", 100)
            assert "prompt_test_cases" in query
            return Result(all_rows=[{
                "invocation_id": "invocation-1", "trace_session_id": "session-1",
                "started_at": now,
                "runtime_inputs": {
                    "question": "What should I do?", "history": [{"role": "user"}],
                },
                "prompt_recipe": {
                    "ask.navigator": 3, "ask.about_me": 1, "ask.memories": 2,
                },
                "model_output": "Answer", "status": "completed", "error": None,
            }])

    traces = list_feature_invocations(Connection(), "ask")

    assert traces[0]["prompt_versions"] == {
        "ask.navigator": 3, "ask.about_me": 1, "ask.memories": 2,
    }
    assert traces[0]["history_turns"] == 1


def test_create_test_run_persists_each_complete_unique_combination():
    class Connection:
        committed = False
        inserts = []

        def execute(self, query, params):
            if "SELECT feature_key, prompt_recipe" in query:
                return Result(one={"feature_key": "ask", "prompt_recipe": {
                    "ask.navigator": 1, "ask.about_me": 1, "ask.memories": 1,
                }})
            if "SELECT 1 FROM prompt_versions" in query:
                return Result(one={"exists": 1})
            self.inserts.append((query, params))
            return Result()

        def commit(self):
            self.committed = True

    conn = Connection()
    created = create_test_run(
        conn,
        feature="ask",
        source_invocation_id="invocation-1",
        combinations=[
            {"ask.navigator": 1, "ask.about_me": 2, "ask.memories": 3},
            {"ask.navigator": 3, "ask.about_me": 1, "ask.memories": 2},
        ],
    )

    case_inserts = [params for query, params in conn.inserts if "INSERT INTO prompt_test_cases" in query]
    assert created["case_count"] == 2
    assert len(case_inserts) == 2
    assert conn.committed is True


def test_create_test_run_rejects_incomplete_or_duplicate_recipes():
    class Connection:
        def execute(self, query, params):
            if "SELECT feature_key, prompt_recipe" in query:
                return Result(one={"feature_key": "ask", "prompt_recipe": {
                    "ask.navigator": 1, "ask.about_me": 1, "ask.memories": 1,
                }})
            return Result(one={"exists": 1})

    try:
        create_test_run(
            Connection(), feature="ask", source_invocation_id="invocation-1",
            combinations=[{"ask.navigator": 1}],
        )
    except ValueError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("Incomplete combination should be rejected")


def test_create_test_run_requires_only_components_used_by_source_invocation():
    class Connection:
        committed = False

        def execute(self, query, params):
            if "SELECT feature_key, prompt_recipe" in query:
                return Result(one={
                    "feature_key": "ask",
                    "prompt_recipe": {"ask.navigator": 1},
                })
            if "SELECT 1 FROM prompt_versions" in query:
                return Result(one={"exists": 1})
            return Result()

        def commit(self):
            self.committed = True

    created = create_test_run(
        Connection(),
        feature="ask",
        source_invocation_id="invocation-1",
        combinations=[{"ask.navigator": 2}],
    )

    assert created["case_count"] == 1


def test_save_test_configuration_keeps_trace_and_prompt_recipe():
    class Connection:
        committed = False
        insert_params = None

        def execute(self, query, params):
            if "SELECT feature_key, prompt_recipe" in query:
                return Result(one={"feature_key": "ask", "prompt_recipe": {
                    "ask.navigator": 1, "ask.about_me": 1, "ask.memories": 1,
                }})
            if "SELECT 1 FROM prompt_versions" in query:
                return Result(one={"exists": 1})
            if "INSERT INTO prompt_test_configurations" in query:
                self.insert_params = params
            return Result()

        def commit(self):
            self.committed = True

    conn = Connection()
    saved = create_test_configuration(
        conn,
        name="  Ask profile comparison  ",
        feature="ask",
        source_invocation_id="invocation-1",
        combinations=[
            {"ask.navigator": 1, "ask.about_me": 2, "ask.memories": 3},
        ],
    )

    assert saved["name"] == "Ask profile comparison"
    assert saved["source_invocation_id"] == "invocation-1"
    assert saved["combinations"][0]["ask.memories"] == 3
    assert conn.insert_params[1] == "Ask profile comparison"
    assert conn.committed is True


def test_list_saved_test_configurations_adds_case_count_and_serializes_dates():
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)

    class Connection:
        def execute(self, query, params):
            assert params == (100,)
            return Result(all_rows=[{
                "configuration_id": "setup-1", "name": "Regression", "feature": "ask",
                "source_invocation_id": "invocation-1", "combinations": [{}, {}],
                "created_at": now, "updated_at": now, "last_run_at": None,
                "question": "How do caps work?",
            }])

    configurations = list_test_configurations(Connection())

    assert configurations[0]["case_count"] == 2
    assert configurations[0]["updated_at"] == "2026-10-02T00:00:00+00:00"


def test_replay_saved_configuration_creates_a_fresh_run_and_marks_last_used():
    class Connection:
        updated = False

        def execute(self, query, params):
            if "FROM prompt_test_configurations" in query:
                return Result(one={
                    "configuration_id": "setup-1", "feature": "ask",
                    "source_invocation_id": "invocation-1",
                    "combinations": [{
                        "ask.navigator": 1, "ask.about_me": 2, "ask.memories": 3,
                    }],
                })
            if "SELECT feature_key, prompt_recipe" in query:
                return Result(one={"feature_key": "ask", "prompt_recipe": {
                    "ask.navigator": 1, "ask.about_me": 1, "ask.memories": 1,
                }})
            if "SELECT 1 FROM prompt_versions" in query:
                return Result(one={"exists": 1})
            if "UPDATE prompt_test_configurations" in query:
                self.updated = True
            return Result()

        def commit(self):
            pass

    conn = Connection()
    run = create_test_run_from_configuration(conn, "setup-1")

    assert run["configuration_id"] == "setup-1"
    assert run["case_count"] == 1
    assert conn.updated is True


def test_prompt_studio_page_is_standalone_and_uses_prompt_admin_api():
    page = (Path(__file__).resolve().parents[1] / "ui" / "prompts.html").read_text(encoding="utf-8")

    assert "Prompt Studio" in page
    assert "/v1/prompt-admin/prompts" in page
    assert "Create a new version" in page
    assert "Use this version" in page
    assert 'href="/traces"' in page
    assert 'href="/tests"' in page


def test_trace_explorer_page_drills_from_sessions_into_saved_request_content():
    page = (Path(__file__).resolve().parents[1] / "ui" / "traces.html").read_text(encoding="utf-8")

    assert "Trace Explorer" in page
    assert "/v1/prompt-admin/invocations" in page
    assert "Flattened prompt" in page
    assert "Model output" in page
    assert "provider attempts" in page
    assert 'href="/tests"' in page


def test_prompt_tests_page_builds_combinations_and_compares_saved_results():
    page = (Path(__file__).resolve().parents[1] / "ui" / "tests.html").read_text(encoding="utf-8")

    assert "Prompt combinations" in page
    assert "/v1/prompt-admin/test-features" in page
    assert "/v1/prompt-admin/test-runs" in page
    assert "/v1/prompt-admin/test-configurations" in page
    assert "Run test" in page
    assert "Saved setups" in page
    assert "Run now" in page
    assert "Recent runs" in page
