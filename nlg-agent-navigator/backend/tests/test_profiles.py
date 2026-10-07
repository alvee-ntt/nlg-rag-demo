from fastapi.testclient import TestClient

from app.main import create_app
from app.navigator_schema import NAVIGATOR_SCHEMA_NAME, NAVIGATOR_SCHEMA_SQL
from tests.test_chat_api import FakeRunner


PROFILE = {
    "user_id": "nlg-user",
    "language": "English",
    "length": "Brief",
    "format": "Prose",
    "tone": "Warm",
    "how_you_ask": "Type",
    "how_answers_reach_you": "Type",
    "wording": "Plain Language",
}


def test_navigator_profile_uses_its_own_schema() -> None:
    assert NAVIGATOR_SCHEMA_NAME == "NLG-Agent-Navigator"
    assert 'CREATE SCHEMA IF NOT EXISTS "NLG-Agent-Navigator"' in NAVIGATOR_SCHEMA_SQL
    assert "prompt_library" not in NAVIGATOR_SCHEMA_SQL
    assert "prompt_studio" not in NAVIGATOR_SCHEMA_SQL
    assert '"NLG-Agent-Navigator".user_profiles' in NAVIGATOR_SCHEMA_SQL


def test_next_saves_settings_for_the_signed_in_user() -> None:
    with TestClient(create_app(FakeRunner())) as client:
        first = client.post("/api/profiles", json=PROFILE)
        assert first.status_code == 200
        saved = first.json()
        assert saved["user_id"] == "nlg-user"
        assert saved["length"] == "Brief"
        assert saved["format"] == "Prose"

        updated = client.post("/api/profiles", json={**PROFILE, "length": "Detailed", "tone": "Formal"})
        assert updated.status_code == 200
        body = updated.json()
        assert body["user_id"] == "nlg-user"
        assert body["length"] == "Detailed"
        assert body["tone"] == "Formal"
        assert body["created_at"] == saved["created_at"]
        assert body["updated_at"] >= saved["updated_at"]

        loaded = client.get("/api/profiles/nlg-user")
        assert loaded.status_code == 200
        assert loaded.json()["length"] == "Detailed"
        missing = client.get("/api/profiles/missing-user")
        assert missing.status_code == 404


def test_reset_deletes_the_signed_in_users_profile() -> None:
    with TestClient(create_app(FakeRunner())) as client:
        saved = client.post("/api/profiles", json=PROFILE)
        assert saved.status_code == 200

        deleted = client.delete("/api/profiles/nlg-user")
        assert deleted.status_code == 200
        assert deleted.json() == {"deleted": True}
        assert client.get("/api/profiles/nlg-user").status_code == 404

        again = client.delete("/api/profiles/nlg-user")
        assert again.status_code == 200
        assert again.json() == {"deleted": False}


def test_profile_rejects_unknown_setting_values() -> None:
    with TestClient(create_app(FakeRunner())) as client:
        response = client.post("/api/profiles", json={**PROFILE, "format": "Essay"})

    assert response.status_code == 422
