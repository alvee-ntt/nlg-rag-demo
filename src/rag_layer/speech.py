"""Azure Speech text-to-speech over REST.

Renders a two-host dialogue (audio episodes) or a single-voice narration (the
"Listen" option on articles) into one MP3. Azure's REST synthesis endpoint caps
each request at roughly ten minutes of audio, so long scripts are split into
batches and the resulting MP3 frames are concatenated (same codec, bitrate and
sample rate on every batch, so players treat the result as one file).

Configuration comes from ``Settings``: ``AZURE_SPEECH_KEY`` and
``AZURE_SPEECH_REGION`` switch the feature on; the two voices are overridable
with ``AZURE_SPEECH_VOICE_AVA`` / ``AZURE_SPEECH_VOICE_ANDREW``. When no key is
present, ``speech_configured`` is False and callers fall back to transcript-only.
"""

from __future__ import annotations

import time
from typing import Any
from xml.sax.saxutils import escape

import requests
import urllib3

from .config import Settings

OUTPUT_FORMAT = "audio-24khz-48kbitrate-mono-mp3"
OUTPUT_MIME = "audio/mpeg"
BITRATE_BPS = 48_000

# Turns per synthesis request. ~8 turns of a 2-host script is ~1 minute of audio,
# comfortably under Azure's per-request ceiling while keeping request count low.
_TURNS_PER_REQUEST = 8
# Pauses inside a narrated article: between paragraphs, and after each section.
_PARAGRAPH_BREAK_MS = 450
_SEGMENT_BREAK_MS = 800
_SSML_OPEN = (
    '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
    'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="en-US">'
)
_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 4

HOSTS = ("Ava", "Andrew")


def speech_configured(settings: Settings) -> bool:
    return bool(settings.azure_speech_key and settings.azure_speech_region)


def tts_url(settings: Settings) -> str:
    """Where to POST SSML.

    A plain Speech resource is addressed by region (``{region}.tts.speech.microsoft.com``).
    An Azure AI Services / Foundry resource (which bundles Speech) only accepts its key on
    its own custom domain, e.g. ``https://<name>.cognitiveservices.azure.com``; set that as
    ``AZURE_SPEECH_ENDPOINT`` and the TTS path is ``/tts/cognitiveservices/v1`` on it. The
    generic regional endpoint (``{region}.api.cognitive.microsoft.com``) is not a custom
    domain, so it falls back to the regional TTS host.
    """
    endpoint = (settings.azure_speech_endpoint or "").rstrip("/")
    host = endpoint.split("//", 1)[-1].lower()
    if endpoint and not host.endswith(".api.cognitive.microsoft.com"):
        return f"{endpoint}/tts/cognitiveservices/v1"
    return f"https://{settings.azure_speech_region}.tts.speech.microsoft.com/cognitiveservices/v1"


def voice_for(settings: Settings, speaker: str) -> str:
    name = (speaker or "").strip().lower()
    if name.startswith("andrew"):
        return settings.azure_speech_voice_andrew
    return settings.azure_speech_voice_ava


def estimate_seconds(mp3_bytes: bytes) -> int:
    """Constant-bitrate MP3: duration follows directly from size."""
    return int(round(len(mp3_bytes) * 8 / BITRATE_BPS))


class AzureSpeechClient:
    def __init__(self, settings: Settings) -> None:
        if not speech_configured(settings):
            raise ValueError("AZURE_SPEECH_KEY and AZURE_SPEECH_REGION are required for audio mixes")
        self.settings = settings
        self.url = tts_url(settings)
        self.session = requests.Session()
        self.session.trust_env = False
        self.headers = {
            "Ocp-Apim-Subscription-Key": settings.azure_speech_key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": OUTPUT_FORMAT,
            "User-Agent": "nlg-rag-salesdj",
        }
        self.verify_ssl = settings.azure_storage_verify_ssl
        if not self.verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def synthesize_ssml(self, ssml: str) -> bytes:
        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            response = self.session.post(
                self.url,
                headers=self.headers,
                data=ssml.encode("utf-8"),
                timeout=(10, 180),
                verify=self.verify_ssl,
            )
            if response.status_code not in _RETRY_STATUS:
                if response.status_code >= 400:
                    detail = response.text[:300].strip()
                    raise requests.HTTPError(
                        f"Azure Speech {response.status_code} {response.reason}: {detail}",
                        response=response,
                    )
                return response.content
            last_error = requests.HTTPError(
                f"Azure Speech {response.status_code} {response.reason}", response=response
            )
            if attempt == _MAX_RETRIES:
                break
            retry_after = response.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else 2.0 * (2**attempt)
            time.sleep(min(delay, 30.0))
        assert last_error is not None
        raise last_error

    def synthesize_dialogue(self, turns: list[dict[str, str]]) -> bytes:
        """Render ``[{"speaker": "Ava", "text": "..."}, ...]`` to one MP3 byte string."""
        turns = [t for t in turns if (t.get("text") or "").strip()]
        if not turns:
            raise ValueError("No dialogue turns to synthesize")
        audio = bytearray()
        for start in range(0, len(turns), _TURNS_PER_REQUEST):
            batch = turns[start : start + _TURNS_PER_REQUEST]
            audio.extend(self.synthesize_ssml(self.build_ssml(batch)))
        return bytes(audio)

    def build_ssml(self, turns: list[dict[str, str]]) -> str:
        parts = [_SSML_OPEN]
        for turn in turns:
            voice = voice_for(self.settings, turn.get("speaker", ""))
            text = escape(turn["text"].strip())
            parts.append(f'<voice name="{voice}">{text}<break time="350ms"/></voice>')
        parts.append("</speak>")
        return "".join(parts)

    def synthesize_narration(self, segments: list[dict[str, Any]]) -> tuple[bytes, list[int]]:
        """Render an article read-aloud: one narrator, one request per segment.

        ``segments`` is ``[{"label": "...", "paragraphs": ["...", ...]}, ...]`` (see
        ``learn.article_narration``). Each segment is synthesized separately so its
        start offset in the joined MP3 is known, which lets the reader highlight the
        section being read. Returns ``(mp3_bytes, start_seconds_per_segment)``.
        """
        segments = [s for s in segments if any((p or "").strip() for p in s.get("paragraphs", []))]
        if not segments:
            raise ValueError("No narration segments to synthesize")
        audio = bytearray()
        starts: list[int] = []
        for segment in segments:
            starts.append(estimate_seconds(bytes(audio)))
            audio.extend(self.synthesize_ssml(self.build_narration_ssml(segment)))
        return bytes(audio), starts

    def build_narration_ssml(self, segment: dict[str, Any]) -> str:
        voice = voice_for(self.settings, HOSTS[0])
        body = "".join(
            f"<p>{escape(p.strip())}</p><break time=\"{_PARAGRAPH_BREAK_MS}ms\"/>"
            for p in segment.get("paragraphs", [])
            if (p or "").strip()
        )
        return f'{_SSML_OPEN}<voice name="{voice}">{body}<break time="{_SEGMENT_BREAK_MS}ms"/></voice></speak>'


def get_speech_client(settings: Settings) -> AzureSpeechClient:
    return AzureSpeechClient(settings)
