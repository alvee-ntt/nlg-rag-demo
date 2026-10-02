from datetime import datetime, timezone
from pathlib import Path

from src.rag_layer.auth import Auth
from src.rag_layer.prompt_admin import (
    create_prompt_version,
    get_prompt_definition,
    list_prompt_definitions,
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
    assert Auth.is_open("/v1/prompt-admin/prompts")


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
                return Result(one={"exists": 1})
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


def test_prompt_studio_page_is_standalone_and_uses_prompt_admin_api():
    page = (Path(__file__).resolve().parents[1] / "ui" / "prompts.html").read_text(encoding="utf-8")

    assert "Prompt Studio" in page
    assert "/v1/prompt-admin/prompts" in page
    assert "Create a new version" in page
    assert "Use this version" in page
