"""The agent's Profile screen: level, stats, skills, and language preferences.

This is a demo with one shared login, so there is one profile for the whole deployment.
Everything numeric comes from finished roleplay calls (``roleplay_sessions``); the two
language preferences live in the ``app_settings`` key-value table. No per-user scoping
anywhere - adding a user id to the sessions and settings queries is the migration path
when real accounts arrive.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from .db import get_app_settings, profile_stats, set_app_settings

# --- Levels --------------------------------------------------------------------------
# Level is a function of finished calls only, so it is explainable and never goes down.
# (lower bound of calls, title). The next level's lower bound is the target.
LEVELS: tuple[tuple[int, str], ...] = (
    (0, "Getting started"),
    (5, "Building confidence"),
    (15, "Finding your rhythm"),
    (30, "Closing with ease"),
)


def level_for(calls: int) -> dict[str, Any]:
    calls = max(0, int(calls or 0))
    index = 0
    for i, (floor, _title) in enumerate(LEVELS):
        if calls >= floor:
            index = i
    number = index + 1
    floor, title = LEVELS[index]
    if index + 1 < len(LEVELS):
        target = LEVELS[index + 1][0]
        progress = (calls - floor) / (target - floor)
        criteria = f"Reach level {number + 1} when you finish {target} calls ({target - calls} to go)."
    else:
        target = None
        progress = 1.0
        criteria = "Top level. Keep your average score up to stay sharp."
    return {
        "number": number,
        "title": title,
        "calls": calls,
        "next_at": target,
        "progress": round(max(0.0, min(1.0, progress)), 3),
        "criteria": criteria,
    }


# --- Skills ----------------------------------------------------------------------------
# The coach grades every finished call on these three, 0-100 or null when the call did
# not exercise the skill. The profile shows the average across graded calls.
SKILLS: tuple[dict[str, str], ...] = (
    {"key": "living_benefits", "label": "Living benefits",
     "topic": "How FlexLife's living benefits work and how to explain them to a client"},
    {"key": "illustration_design", "label": "Illustration design",
     "topic": "Designing and walking a client through a FlexLife illustration"},
    {"key": "objection_handling", "label": "Objection handling",
     "topic": "Handling the most common FlexLife objections without losing trust"},
)
SKILL_KEYS = tuple(s["key"] for s in SKILLS)


def clamp_score(value: Any) -> int | None:
    """Coerce a model-produced score to an int in 0..100, or None when absent/garbage."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return int(round(max(0.0, min(100.0, number))))


def normalize_feedback_scores(feedback: dict[str, Any]) -> dict[str, Any]:
    """Make the score fields of a coaching report safe to store and average."""
    feedback["score"] = clamp_score(feedback.get("score"))
    raw = feedback.get("skills")
    raw = raw if isinstance(raw, dict) else {}
    feedback["skills"] = {key: clamp_score(raw.get(key)) for key in SKILL_KEYS}
    return feedback


# --- Languages -------------------------------------------------------------------------
# ``stt`` is the Azure speech-to-text locale used while practising in that language;
# empty means Azure has no recogniser for it, so the call falls back to typing.
LANGUAGES: tuple[dict[str, str], ...] = (
    {"code": "en", "name": "English", "native": "", "stt": "en-US", "flag": "us"},
    {"code": "es", "name": "Spanish", "native": "Español", "stt": "es-US", "flag": "es"},
    {"code": "zh", "name": "Chinese", "native": "中文", "stt": "zh-CN", "flag": "cn"},
    {"code": "tl", "name": "Tagalog", "native": "", "stt": "fil-PH", "flag": "ph"},
    {"code": "vi", "name": "Vietnamese", "native": "Tiếng Việt", "stt": "vi-VN", "flag": "vn"},
    {"code": "ko", "name": "Korean", "native": "한국어", "stt": "ko-KR", "flag": "kr"},
    {"code": "fr", "name": "French", "native": "Français", "stt": "fr-FR", "flag": "fr"},
    {"code": "pt", "name": "Portuguese", "native": "Português", "stt": "pt-BR", "flag": "br"},
    {"code": "ht", "name": "Haitian Creole", "native": "Kreyòl", "stt": "", "flag": "ht"},
    {"code": "ru", "name": "Russian", "native": "Русский", "stt": "ru-RU", "flag": "ru"},
    {"code": "ar", "name": "Arabic", "native": "العربية", "stt": "ar-SA", "flag": "sa"},
)
LANGUAGE_BY_CODE = {lang["code"]: lang for lang in LANGUAGES}
DEFAULT_LANGUAGE = "en"

SETTING_KEYS = ("app_language", "practice_language")


def language(code: str | None) -> dict[str, str]:
    return LANGUAGE_BY_CODE.get((code or "").strip().lower(), LANGUAGE_BY_CODE[DEFAULT_LANGUAGE])


def validate_settings(payload: dict[str, Any]) -> dict[str, str]:
    """Keep only known keys with known language codes; raise on an unknown code."""
    out: dict[str, str] = {}
    for key in SETTING_KEYS:
        if key not in payload or payload[key] is None:
            continue
        code = str(payload[key]).strip().lower()
        if code not in LANGUAGE_BY_CODE:
            raise ValueError(f"Unknown language code for {key}: {code!r}")
        out[key] = code
    return out


def current_settings(conn) -> dict[str, str]:
    stored = get_app_settings(conn)
    return {key: language(stored.get(key))["code"] for key in SETTING_KEYS}


def update_settings(conn, payload: dict[str, Any]) -> dict[str, str]:
    changes = validate_settings(payload)
    if changes:
        set_app_settings(conn, changes)
    return current_settings(conn)


# --- The profile payload ---------------------------------------------------------------


def _days_ago(when: _dt.datetime | None) -> int | None:
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    today = _dt.datetime.now(_dt.timezone.utc).date()
    return max(0, (today - when.astimezone(_dt.timezone.utc).date()).days)


def build_profile(conn, username: str) -> dict[str, Any]:
    stats = profile_stats(conn)
    last = stats.pop("last_practice_at", None)
    skills = [
        {**skill, "score": stats["skills"].get(skill["key"]), "graded_calls": stats["skill_counts"].get(skill["key"], 0)}
        for skill in SKILLS
    ]
    stats.pop("skills", None)
    stats.pop("skill_counts", None)
    return {
        "username": username,
        "level": level_for(stats["sessions"]),
        "stats": {
            **stats,
            "last_practice_at": last.isoformat() if hasattr(last, "isoformat") else last,
            "last_practice_days_ago": _days_ago(last),
        },
        "skills": skills,
        "settings": current_settings(conn),
        "languages": list(LANGUAGES),
    }
