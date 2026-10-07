"""User profile storage in the Navigator application schema."""

from datetime import datetime, timezone
from typing import Literal

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel, Field, field_validator

from app.navigator_schema import NAVIGATOR_SCHEMA_SQL


class UserProfile(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    language: Literal["English"] = "English"
    length: Literal["Brief", "Balanced", "Detailed"]
    format: Literal["Auto", "Bullets", "Prose"]
    tone: Literal["Warm", "Neutral", "Formal"]
    how_you_ask: Literal["Type", "Voice"]
    how_answers_reach_you: Literal["Type", "Voice"]
    wording: Literal["Plain Language", "Industry terms"]

    @field_validator("user_id")
    @classmethod
    def strip_user_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("user_id is required")
        return cleaned


class SavedUserProfile(UserProfile):
    created_at: datetime
    updated_at: datetime


_UPSERT_SQL = """
INSERT INTO "NLG-Agent-Navigator".user_profiles (
    user_id, language, answer_length, format, tone,
    how_you_ask, how_answers_reach_you, wording
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (user_id) DO UPDATE SET
    language = EXCLUDED.language,
    answer_length = EXCLUDED.answer_length,
    format = EXCLUDED.format,
    tone = EXCLUDED.tone,
    how_you_ask = EXCLUDED.how_you_ask,
    how_answers_reach_you = EXCLUDED.how_answers_reach_you,
    wording = EXCLUDED.wording,
    updated_at = now()
RETURNING user_id, language, answer_length, format, tone,
    how_you_ask, how_answers_reach_you, wording, created_at, updated_at
"""


def _saved_from_row(row: dict) -> SavedUserProfile:
    return SavedUserProfile(
        user_id=row["user_id"],
        language=row["language"],
        length=row["answer_length"],
        format=row["format"],
        tone=row["tone"],
        how_you_ask=row["how_you_ask"],
        how_answers_reach_you=row["how_answers_reach_you"],
        wording=row["wording"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class MemoryProfileStore:
    def __init__(self) -> None:
        self.rows: dict[str, SavedUserProfile] = {}

    def get(self, user_id: str) -> SavedUserProfile | None:
        return self.rows.get(user_id.strip())

    def save(self, profile: UserProfile) -> SavedUserProfile:
        now = datetime.now(timezone.utc)
        existing = self.rows.get(profile.user_id)
        saved = SavedUserProfile(
            **profile.model_dump(),
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        self.rows[profile.user_id] = saved
        return saved

    def delete(self, user_id: str) -> bool:
        return self.rows.pop(user_id.strip(), None) is not None


class PostgresProfileStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def initialize(self) -> None:
        with psycopg.connect(self.database_url) as conn:
            conn.execute(NAVIGATOR_SCHEMA_SQL)
            conn.commit()

    def get(self, user_id: str) -> SavedUserProfile | None:
        with psycopg.connect(self.database_url, row_factory=dict_row) as conn:
            row = conn.execute(
                """
                SELECT user_id, language, answer_length, format, tone,
                       how_you_ask, how_answers_reach_you, wording,
                       created_at, updated_at
                FROM "NLG-Agent-Navigator".user_profiles
                WHERE user_id = %s
                """,
                (user_id.strip(),),
            ).fetchone()
        return _saved_from_row(row) if row else None

    def save(self, profile: UserProfile) -> SavedUserProfile:
        with psycopg.connect(self.database_url, row_factory=dict_row) as conn:
            row = conn.execute(
                _UPSERT_SQL,
                (
                    profile.user_id,
                    profile.language,
                    profile.length,
                    profile.format,
                    profile.tone,
                    profile.how_you_ask,
                    profile.how_answers_reach_you,
                    profile.wording,
                ),
            ).fetchone()
            conn.commit()
        if row is None:
            raise RuntimeError("profile save did not return a row")
        return _saved_from_row(row)

    def delete(self, user_id: str) -> bool:
        with psycopg.connect(self.database_url) as conn:
            row = conn.execute(
                'DELETE FROM "NLG-Agent-Navigator".user_profiles WHERE user_id = %s RETURNING user_id',
                (user_id.strip(),),
            ).fetchone()
            conn.commit()
        return row is not None
