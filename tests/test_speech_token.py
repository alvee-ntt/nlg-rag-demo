"""The browser speech token: Ask Navigator (no roleplay persona) also gets its voice."""

from __future__ import annotations

from types import SimpleNamespace

from rag_layer import roleplay as roleplay_module
from rag_layer.roleplay import Roleplay


def test_speech_token_without_profile_returns_default_voice(monkeypatch) -> None:
    settings = SimpleNamespace(
        azure_speech_key="key",
        azure_speech_region="eastus",
        azure_speech_endpoint="",
        azure_speech_voice_ava="en-US-Ava:DragonHDLatestNeural",
        azure_storage_verify_ssl=True,
    )
    posted: list[str] = []

    def fake_post(url, **kwargs):
        posted.append(url)
        return SimpleNamespace(status_code=200, text="tok", raise_for_status=lambda: None)

    monkeypatch.setattr(roleplay_module.requests, "post", fake_post)
    fake_self = SimpleNamespace(settings=settings, voices=SimpleNamespace(by_id=lambda _id: None))

    result = Roleplay.speech_token(fake_self, "")

    assert result == {"token": "tok", "region": "eastus", "voice": "en-US-Ava:DragonHDLatestNeural"}
    assert posted == ["https://eastus.api.cognitive.microsoft.com/sts/v1.0/issueToken"]
