"""Azure Speech text-to-speech for spoken replies.

Renders one short answer into a single MP3 via Azure's REST synthesis endpoint.
This mirrors the nlg-rag ``speech.py`` synthesis path (same DragonHD voices and
output format) but stays self-contained to this app so the two stacks don't
couple: it reads its own ``AZURE_SPEECH_*`` settings and makes one async httpx
call. When no key is configured, ``speech_is_configured`` is False and the route
returns 503.
"""

from __future__ import annotations

import asyncio
from xml.sax.saxutils import escape

import httpx

from app.settings import Settings

OUTPUT_FORMAT = "audio-24khz-48kbitrate-mono-mp3"
_SSML_OPEN = (
    '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
    'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="en-US">'
)
_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3


class SpeechError(RuntimeError):
    """Synthesis failed after retries."""


def tts_url(settings: Settings) -> str:
    """Where to POST SSML.

    A plain Speech resource is addressed by region (``{region}.tts.speech.microsoft.com``).
    An Azure AI Services resource that bundles Speech only accepts its key on its own
    custom domain (``https://<name>.cognitiveservices.azure.com``); set that as
    ``AZURE_SPEECH_ENDPOINT`` and the TTS path is ``/tts/cognitiveservices/v1``. The
    generic regional endpoint (``{region}.api.cognitive.microsoft.com``) is not a custom
    domain, so it falls back to the regional TTS host.
    """
    endpoint = (settings.azure_speech_endpoint or "").rstrip("/")
    host = endpoint.split("//", 1)[-1].lower()
    if endpoint and not host.endswith(".api.cognitive.microsoft.com"):
        return f"{endpoint}/tts/cognitiveservices/v1"
    return f"https://{settings.azure_speech_region}.tts.speech.microsoft.com/cognitiveservices/v1"


def build_ssml(text: str, voice: str) -> str:
    return f'{_SSML_OPEN}<voice name="{voice}">{escape(text)}</voice></speak>'


async def synthesize_speech(settings: Settings, text: str, voice: str | None = None) -> bytes:
    """Render ``text`` to one MP3 byte string using the configured DragonHD voice."""
    cleaned = (text or "").strip()
    if not cleaned:
        raise ValueError("No text to synthesize")
    if settings.azure_speech_key is None:
        raise SpeechError("Azure Speech is not configured")

    voice_name = voice or settings.azure_speech_voice_ava
    ssml = build_ssml(cleaned, voice_name).encode("utf-8")
    headers = {
        "Ocp-Apim-Subscription-Key": settings.azure_speech_key.get_secret_value(),
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": OUTPUT_FORMAT,
        "User-Agent": "nlg-agent-navigator",
    }
    url = tts_url(settings)

    last_detail = ""
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        for attempt in range(_MAX_RETRIES + 1):
            response = await client.post(url, headers=headers, content=ssml)
            if response.status_code not in _RETRY_STATUS:
                if response.status_code >= 400:
                    raise SpeechError(
                        f"Azure Speech {response.status_code}: {response.text[:200].strip()}"
                    )
                return response.content
            last_detail = f"Azure Speech {response.status_code}"
            if attempt == _MAX_RETRIES:
                break
            retry_after = response.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else 2.0 * (2**attempt)
            await asyncio.sleep(min(delay, 10.0))
    raise SpeechError(last_detail or "Azure Speech request failed")
