from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

_VENDOR = Path(__file__).resolve().parents[2] / ".vendor"
_VENDOR_PATH = str(_VENDOR)
_VENDOR_WAS_ON_PATH = _VENDOR_PATH in sys.path
if _VENDOR_WAS_ON_PATH:
    sys.path.remove(_VENDOR_PATH)

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

if _VENDOR_WAS_ON_PATH:
    sys.path.insert(0, _VENDOR_PATH)

from .auth import Auth
from .config import load_settings
from .db import (
    connect,
    create_mix,
    delete_mix,
    delete_roleplay_session,
    get_mix,
    get_mix_audio,
    get_roleplay_session,
    init_db,
    list_audio_mixes_without_audio,
    list_mixes,
    list_roleplay_sessions,
    mark_stale_mixes,
    roleplay_stats,
)
from .embeddings import get_openai_client
from .foundry import chat as foundry_chat, foundry_configured
from .curriculum import CURRICULUM, curriculum_outline
from .learn import (
    KINDS,
    LENGTH_SPECS,
    LENGTHS,
    NARRATED_KINDS,
    RECOMMENDED_MIXES,
    enqueue,
    run_audio_render,
    run_generation,
    serialize_mix,
)
from . import profile
from .roleplay import OUTCOME_LABELS, Roleplay
from .service import (
    answer,
    chat,
    check_transcript,
    corpus,
    document_chunks,
    fact_check,
    search,
)
from .speech import speech_configured


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = Field(default=8, ge=1, le=20)


class AnswerRequest(BaseModel):
    question: str = Field(..., min_length=1)
    limit: int = Field(default=8, ge=1, le=20)


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(..., max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=12)
    limit: int = Field(default=6, ge=1, le=20)


class FoundryChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)


class FoundryCitation(BaseModel):
    n: int
    title: str
    url: str


class FoundryChatResponse(BaseModel):
    answer: str
    citations: list[FoundryCitation]
    agent: str
    model: str | None = None
    response_id: str | None = None
    status: str | None = None


class FoundryStatusResponse(BaseModel):
    configured: bool
    agent: str
    endpoint: str


class FactCheckRequest(BaseModel):
    claim: str = Field(..., min_length=1)
    limit: int = Field(default=8, ge=1, le=20)


class TranscriptCheckRequest(BaseModel):
    transcript: str = Field(..., min_length=1)
    limit: int = Field(default=8, ge=1, le=20)
    speaker: str | None = Field(
        default=None,
        description="If set, only check statements attributed to this speaker label (e.g. 'Agent').",
    )
    max_statements: int = Field(default=50, ge=1, le=200)


class Source(BaseModel):
    blob_name: str
    chunk_index: int
    citation: str
    page: Any | None = None
    zone: str
    similarity: float
    preview: str


class SearchResponse(BaseModel):
    sources: list[Source]


class AnswerResponse(SearchResponse):
    answer: str


class ChatResponse(SearchResponse):
    answer: str
    follow_ups: list[str]


class FactCheckResponse(SearchResponse):
    verdict: Literal["SUPPORTED", "CONTRADICTED", "NOT ADDRESSED", "UNKNOWN"]
    report: str


class TranscriptStatementResult(BaseModel):
    index: int
    speaker: str | None = None
    statement: str
    verdict: Literal["SUPPORTED", "CONTRADICTED", "NOT ADDRESSED", "UNKNOWN"]
    report: str
    sources: list[Source]


class TranscriptCheckSummary(BaseModel):
    statements_checked: int
    counts: dict[str, int]
    supported_ratio: float
    flagged: list[int]
    truncated: bool


class TranscriptCheckResponse(BaseModel):
    summary: TranscriptCheckSummary
    statements: list[TranscriptStatementResult]


class DocumentSummary(BaseModel):
    id: int
    blob_name: str
    prefix: str
    size_bytes: int | None
    chunk_count: int
    words_total: int
    chars_avg: int
    chars_min: int
    chars_max: int
    page_count: int
    zones: list[str]
    updated_at: str | None


class CorpusTotals(BaseModel):
    documents: int
    chunks: int
    words: int


class CorpusResponse(BaseModel):
    totals: CorpusTotals
    chunk_size: int
    chunk_overlap: int
    documents: list[DocumentSummary]


class ChunkDetail(BaseModel):
    chunk_index: int
    chars: int
    words: int | None
    page: Any | None = None
    zone: str
    heading_path: str
    content: str


class DocumentHeader(BaseModel):
    id: int
    blob_name: str
    prefix: str
    size_bytes: int | None
    chunk_count: int
    updated_at: str | None


class DocumentChunksResponse(BaseModel):
    document: DocumentHeader
    chunks: list[ChunkDetail]


# --- Learn (salesDJ) models ---------------------------------------------------------


class MixCreateRequest(BaseModel):
    kind: Literal["audio", "article", "flashcards"]
    prompt: str = Field(..., min_length=3, max_length=400)
    length: Literal["short", "long"] = "short"


class MixSummary(BaseModel):
    id: int
    kind: str
    prompt: str
    length: str
    length_label: str
    status: Literal["queued", "generating", "ready", "failed"]
    title: str
    summary: str
    duration_seconds: int
    recommended: bool
    tier: int | None = None
    curriculum_key: str | None = None
    has_audio: bool
    audio_status: str | None = None
    error: str | None = None
    created_at: str | None
    updated_at: str | None


class MixDetail(MixSummary):
    content: dict[str, Any]
    sources: list[Source]


class MixListResponse(BaseModel):
    mixes: list[MixSummary]


class LearnStatusResponse(BaseModel):
    speech_configured: bool
    kinds: list[str]
    lengths: list[str]
    specs: dict[str, dict[str, dict[str, Any]]]
    recommended_prompts: list[dict[str, str]]


class SeedResponse(BaseModel):
    queued: list[int]
    skipped: int


class CurriculumTier(BaseModel):
    tier: int
    title: str
    items: list[dict[str, Any]]


class CurriculumResponse(BaseModel):
    tiers: list[CurriculumTier]


class RenderPendingResponse(BaseModel):
    queued: list[int]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    app.state.settings = settings
    app.state.openai_client = get_openai_client(settings)
    app.state.auth = Auth(settings)
    _ensure_schema(settings)
    with connect(settings) as conn:
        stale = mark_stale_mixes(conn)
    if stale:
        print(f"Marked {stale} interrupted learn mix(es) as failed.")
    # Prepare tab: personas, voice registry, in-memory sessions, keep-warm ping.
    roleplay = Roleplay(settings, app.state.openai_client)
    roleplay.start_keep_warm()
    app.state.roleplay = roleplay
    print(f"Roleplay ready: {len(roleplay.personas)} scenarios, {len(roleplay.voices.profiles)} voice profiles.")
    yield


def _ensure_schema(settings, attempts: int = 15, delay_seconds: float = 2.0) -> None:
    """Create the pgvector schema if missing. Idempotent (CREATE ... IF NOT EXISTS).

    Retries briefly so the API tolerates Postgres finishing its own startup.
    """
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            init_db(settings)
            print(f"Database schema ready (attempt {attempt}).")
            return
        except Exception as error:  # noqa: BLE001 - surface after retries exhaust
            last_error = error
            print(f"Waiting for database schema (attempt {attempt}/{attempts}): {error}")
            time.sleep(delay_seconds)
    raise RuntimeError(f"Could not initialize database schema: {last_error}")


app = FastAPI(
    title="NLG RAG API",
    version="1.0.0",
    description="Local RAG API for source-backed answers and fact checks.",
    lifespan=lifespan,
)


def _cors_origins() -> list[str]:
    raw = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://localhost:5173,http://localhost:8080,"
        "http://127.0.0.1:3000,http://127.0.0.1:5173,http://127.0.0.1:8080",
    )
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# The salesDJ web app is a static page; serving it from the API keeps one origin for
# the browser (audio streaming, no CORS) and one container to run.
_UI_DIR = Path(__file__).resolve().parents[2] / "ui"
if _UI_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=str(_UI_DIR), html=True), name="ui")


@app.middleware("http")
async def _sign_in_gate(request: Request, call_next):
    """Every API route and the docs need the demo sign-in cookie; see auth.py for the
    short list of open paths."""
    auth: Auth | None = getattr(request.app.state, "auth", None)
    if auth is not None:
        blocked = auth.gate(request)
        if blocked is not None:
            return blocked
    return await call_next(request)


@app.middleware("http")
async def _no_cache_ui(request: Request, call_next):
    """The UI is a single hand-edited page that changes often; make browsers revalidate
    it on every load instead of heuristically caching the old copy for hours."""
    response = await call_next(request)
    if request.url.path.startswith("/app/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "name": "NLG RAG API",
        "version": app.version,
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
def health(request: Request) -> dict[str, str]:
    settings = request.app.state.settings
    roleplay: Roleplay | None = getattr(request.app.state, "roleplay", None)
    return {
        "status": "ok",
        "postgres_host": settings.postgres_host,
        "model_provider": settings.model_provider,
        "scenarios": str(len(roleplay.personas)) if roleplay else "0",
        "model_warm": str(bool(roleplay and roleplay.model_warm.is_set())).lower(),
    }


@app.post("/v1/search", response_model=SearchResponse)
def search_endpoint(payload: SearchRequest, request: Request) -> dict[str, Any]:
    try:
        return search(
            settings=request.app.state.settings,
            client=request.app.state.openai_client,
            query=payload.query.strip(),
            limit=payload.limit,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.post("/v1/answer", response_model=AnswerResponse)
def answer_endpoint(payload: AnswerRequest, request: Request) -> dict[str, Any]:
    try:
        return answer(
            settings=request.app.state.settings,
            client=request.app.state.openai_client,
            question=payload.question.strip(),
            limit=payload.limit,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.post("/v1/chat", response_model=ChatResponse)
def chat_endpoint(payload: ChatRequest, request: Request) -> dict[str, Any]:
    """Conversational grounded Q&A for the app's Ask Navigator sheet (short replies, remembers the thread)."""
    try:
        return chat(
            settings=request.app.state.settings,
            client=request.app.state.openai_client,
            message=payload.message.strip(),
            history=[t.model_dump() for t in payload.history],
            limit=payload.limit,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.get("/v1/foundry/status", response_model=FoundryStatusResponse)
def foundry_status_endpoint(request: Request) -> dict[str, Any]:
    """Whether the hosted Foundry agent is wired up, for the Coach console's Foundry tab."""
    settings = request.app.state.settings
    return {
        "configured": foundry_configured(settings),
        "agent": settings.foundry_agent_name,
        "endpoint": settings.foundry_project_endpoint,
    }


@app.post("/v1/foundry/chat", response_model=FoundryChatResponse)
def foundry_chat_endpoint(payload: FoundryChatRequest, request: Request) -> dict[str, Any]:
    """Proxy one chat turn to the hosted Azure AI Foundry agent (its own knowledge base
    does the grounding server-side). Lets the console test that agent next to local RAG."""
    settings = request.app.state.settings
    if not foundry_configured(settings):
        raise HTTPException(
            status_code=503,
            detail="Foundry is not configured. Set FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_API_KEY in .env.",
        )
    try:
        return foundry_chat(
            settings=settings,
            message=payload.message.strip(),
            history=[t.model_dump() for t in payload.history],
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc


@app.post("/v1/fact-check", response_model=FactCheckResponse)
def fact_check_endpoint(payload: FactCheckRequest, request: Request) -> dict[str, Any]:
    try:
        return fact_check(
            settings=request.app.state.settings,
            client=request.app.state.openai_client,
            claim=payload.claim.strip(),
            limit=payload.limit,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.post("/v1/transcript-check", response_model=TranscriptCheckResponse)
def transcript_check_endpoint(payload: TranscriptCheckRequest, request: Request) -> dict[str, Any]:
    try:
        return check_transcript(
            settings=request.app.state.settings,
            client=request.app.state.openai_client,
            transcript=payload.transcript,
            limit=payload.limit,
            speaker=payload.speaker,
            max_statements=payload.max_statements,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.get("/v1/documents", response_model=CorpusResponse)
def documents_endpoint(request: Request) -> dict[str, Any]:
    try:
        return corpus(settings=request.app.state.settings)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.get("/v1/documents/{document_id}/chunks", response_model=DocumentChunksResponse)
def document_chunks_endpoint(document_id: int, request: Request) -> dict[str, Any]:
    try:
        result = document_chunks(settings=request.app.state.settings, document_id=document_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    if result is None:
        raise HTTPException(status_code=404, detail=f"No document with id {document_id}")
    return result


# --- Learn (salesDJ) routes ---------------------------------------------------------


@app.get("/learn", include_in_schema=False)
def learn_redirect() -> RedirectResponse:
    return RedirectResponse(url="/app/learn.html")


@app.get("/coach", include_in_schema=False)
def coach_redirect() -> RedirectResponse:
    """Public Coach entry point; the RAG console remains its implementation for now."""
    return RedirectResponse(url="/app/index.html")


@app.get("/v1/learn/status", response_model=LearnStatusResponse)
def learn_status(request: Request) -> dict[str, Any]:
    return {
        "speech_configured": speech_configured(request.app.state.settings),
        "kinds": list(KINDS),
        "lengths": list(LENGTHS),
        "specs": LENGTH_SPECS,
        "recommended_prompts": RECOMMENDED_MIXES,
    }


@app.get("/v1/learn/mixes", response_model=MixListResponse)
def list_mixes_endpoint(request: Request) -> dict[str, Any]:
    try:
        with connect(request.app.state.settings) as conn:
            mark_stale_mixes(conn)
            rows = list_mixes(conn)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    return {"mixes": [serialize_mix(row) for row in rows]}


@app.post("/v1/learn/mixes", response_model=MixSummary, status_code=202)
def create_mix_endpoint(payload: MixCreateRequest, request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    try:
        with connect(settings) as conn:
            mix_id = create_mix(conn, kind=payload.kind, prompt=payload.prompt.strip(), length=payload.length)
            row = get_mix(conn, mix_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    enqueue(run_generation, settings, request.app.state.openai_client, mix_id)
    return serialize_mix(row)


@app.post("/v1/learn/seed", response_model=SeedResponse, status_code=202)
def seed_mixes_endpoint(request: Request) -> dict[str, Any]:
    """Queue every curriculum mix that is not already in the library (tier 1 is recommended)."""
    settings = request.app.state.settings
    queued: list[int] = []
    skipped = 0
    try:
        with connect(settings) as conn:
            existing = {m["curriculum_key"] for m in list_mixes(conn) if m.get("curriculum_key")}
            for item in CURRICULUM:
                if item["key"] in existing:
                    skipped += 1
                    continue
                queued.append(
                    create_mix(
                        conn,
                        kind=item["kind"],
                        prompt=item["prompt"],
                        length=item["length"],
                        recommended=item["tier"] == 1,
                        tier=item["tier"],
                        curriculum_key=item["key"],
                    )
                )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    for mix_id in queued:
        enqueue(run_generation, settings, request.app.state.openai_client, mix_id)
    return {"queued": queued, "skipped": skipped}


@app.get("/v1/learn/curriculum", response_model=CurriculumResponse)
def curriculum_endpoint() -> dict[str, Any]:
    return {"tiers": curriculum_outline()}


@app.get("/v1/learn/mixes/{mix_id}", response_model=MixDetail)
def get_mix_endpoint(mix_id: int, request: Request) -> dict[str, Any]:
    try:
        with connect(request.app.state.settings) as conn:
            row = get_mix(conn, mix_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"No mix with id {mix_id}")
    return serialize_mix(row, include_content=True)


@app.get("/v1/learn/mixes/{mix_id}/audio", include_in_schema=False)
def get_mix_audio_endpoint(mix_id: int, request: Request) -> Response:
    try:
        with connect(request.app.state.settings) as conn:
            result = get_mix_audio(conn, mix_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="This mix has no rendered audio")
    audio, mime = result
    total = len(audio)
    base_headers = {"Cache-Control": "private, max-age=86400", "Accept-Ranges": "bytes"}

    # Honor HTTP Range requests so the browser treats the audio as seekable.
    # Without a 206 + Accept-Ranges, <audio> marks the stream non-seekable and
    # ignores currentTime, which breaks the skip-15 buttons and the scrub bar.
    range_header = request.headers.get("range") or request.headers.get("Range")
    if range_header and range_header.strip().lower().startswith("bytes="):
        spec = range_header.split("=", 1)[1].split(",", 1)[0].strip()
        start_s, _, end_s = spec.partition("-")
        try:
            if start_s == "":
                # suffix range: last N bytes
                length = int(end_s)
                start = max(0, total - length)
                end = total - 1
            else:
                start = int(start_s)
                end = int(end_s) if end_s else total - 1
        except ValueError:
            start, end = 0, total - 1
        if start > end or start >= total:
            return Response(
                status_code=416,
                headers={**base_headers, "Content-Range": f"bytes */{total}"},
            )
        end = min(end, total - 1)
        chunk = audio[start : end + 1]
        return Response(
            content=chunk,
            status_code=206,
            media_type=mime,
            headers={**base_headers, "Content-Range": f"bytes {start}-{end}/{total}"},
        )

    return Response(content=audio, media_type=mime, headers=base_headers)


@app.post("/v1/learn/mixes/{mix_id}/render-audio", response_model=MixSummary, status_code=202)
def render_audio_endpoint(mix_id: int, request: Request) -> dict[str, Any]:
    """Synthesize (or re-synthesize) the MP3 for a finished mix.

    Audio episodes re-render their two-host script; articles get their single-voice
    "Listen" narration, which is only ever produced on demand.
    """
    settings = request.app.state.settings
    try:
        with connect(settings) as conn:
            row = get_mix(conn, mix_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"No mix with id {mix_id}")
    if row["kind"] not in NARRATED_KINDS or row["status"] != "ready":
        raise HTTPException(status_code=409, detail="Only a finished audio episode or article can be rendered")
    # Already in flight: don't double-render (double the speech cost) on a repeat tap.
    # A render that has sat in "rendering" for ages was orphaned by a restart, so let it retry.
    updated_at = row.get("updated_at")
    fresh = bool(updated_at) and (datetime.now(timezone.utc) - updated_at) < timedelta(minutes=10)
    if (row.get("content") or {}).get("audio_status") == "rendering" and fresh:
        return serialize_mix(row)
    if not speech_configured(settings):
        raise HTTPException(status_code=409, detail="Azure Speech is not configured (AZURE_SPEECH_KEY / AZURE_SPEECH_REGION)")
    enqueue(run_audio_render, settings, mix_id)
    return serialize_mix(row)


@app.post("/v1/learn/audio/render-pending", response_model=RenderPendingResponse, status_code=202)
def render_pending_audio_endpoint(request: Request) -> dict[str, Any]:
    """After Azure Speech is configured: render every audio episode that has a script but no MP3."""
    settings = request.app.state.settings
    if not speech_configured(settings):
        raise HTTPException(status_code=409, detail="Azure Speech is not configured (AZURE_SPEECH_KEY / AZURE_SPEECH_REGION)")
    try:
        with connect(settings) as conn:
            ids = list_audio_mixes_without_audio(conn)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    for mix_id in ids:
        enqueue(run_audio_render, settings, mix_id)
    return {"queued": ids}


@app.post("/v1/learn/mixes/{mix_id}/retry", response_model=MixSummary, status_code=202)
def retry_mix_endpoint(mix_id: int, request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    try:
        with connect(settings) as conn:
            row = get_mix(conn, mix_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"No mix with id {mix_id}")
    if row["status"] in {"queued", "generating"}:
        raise HTTPException(status_code=409, detail="This mix is already being generated")
    enqueue(run_generation, settings, request.app.state.openai_client, mix_id)
    row["status"] = "queued"
    return serialize_mix(row)


@app.delete("/v1/learn/mixes/{mix_id}", status_code=204)
def delete_mix_endpoint(mix_id: int, request: Request) -> Response:
    try:
        with connect(request.app.state.settings) as conn:
            removed = delete_mix(conn, mix_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    if not removed:
        raise HTTPException(status_code=404, detail=f"No mix with id {mix_id}")
    return Response(status_code=204)


# --- Sign-in ------------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str = Field(default="", max_length=200)
    password: str = Field(default="", max_length=200)


@app.post("/v1/auth/login")
def login_endpoint(payload: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
    auth: Auth = request.app.state.auth
    if not auth.check(payload.username, payload.password):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    auth.set_cookie(response)
    return {"ok": True, "username": auth.username}


@app.post("/v1/auth/logout")
def logout_endpoint(request: Request, response: Response) -> dict[str, Any]:
    request.app.state.auth.clear_cookie(response)
    return {"ok": True}


@app.get("/v1/auth/me")
def me_endpoint(request: Request) -> dict[str, Any]:
    auth: Auth = request.app.state.auth
    return {"authenticated": auth.is_authed(request), "username": auth.username}


# --- Prepare (roleplay) routes ----------------------------------------------------------


@app.get("/prepare", include_in_schema=False)
def prepare_redirect() -> RedirectResponse:
    return RedirectResponse(url="/app/learn.html#/prepare")


class CustomScenarioRequest(BaseModel):
    notes: str = Field(..., min_length=3, max_length=2000)


class SessionStartRequest(BaseModel):
    persona_id: str = Field(..., min_length=1, max_length=120)
    custom_notes: str = Field(default="", max_length=2000)


class TurnRequest(BaseModel):
    agent_text: str = Field(..., min_length=1, max_length=4000)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)


class SpeechTokenRequest(BaseModel):
    profile_id: str = ""


def _roleplay(request: Request) -> Roleplay:
    roleplay: Roleplay | None = getattr(request.app.state, "roleplay", None)
    if roleplay is None:
        raise HTTPException(status_code=503, detail="Roleplay is not ready yet")
    return roleplay


def _session_or_404(roleplay: Roleplay, session_id: str):
    try:
        return roleplay.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown or expired session. Start a new call.")


@app.get("/v1/roleplay/status")
def roleplay_status(request: Request) -> dict[str, Any]:
    roleplay = _roleplay(request)
    settings = request.app.state.settings
    return {
        "speech_configured": bool(settings.azure_speech_key and settings.azure_speech_region),
        "model_warm": roleplay.model_warm.is_set(),
        "scenarios": len(roleplay.personas),
        "active_sessions": len(roleplay.sessions),
    }


@app.get("/v1/roleplay/scenarios")
def roleplay_scenarios(request: Request) -> dict[str, Any]:
    return _roleplay(request).scenarios()


@app.post("/v1/roleplay/scenarios/custom", status_code=201)
def roleplay_create_scenario(payload: CustomScenarioRequest, request: Request) -> dict[str, Any]:
    try:
        return _roleplay(request).create_custom(payload.notes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.get("/v1/roleplay/scenarios/{persona_id}")
def roleplay_inspect_scenario(persona_id: str, request: Request) -> dict[str, Any]:
    result = _roleplay(request).inspect_persona(persona_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Unknown scenario: {persona_id}")
    return result


@app.delete("/v1/roleplay/scenarios/{persona_id}", status_code=204)
def roleplay_delete_scenario(persona_id: str, request: Request) -> Response:
    if not _roleplay(request).delete_custom(persona_id):
        raise HTTPException(status_code=404, detail="Only custom scenarios can be removed")
    return Response(status_code=204)


@app.post("/v1/roleplay/sessions", status_code=201)
def roleplay_start(payload: SessionStartRequest, request: Request) -> dict[str, Any]:
    language = "en"
    try:
        with connect(request.app.state.settings) as conn:
            language = profile.current_settings(conn)["practice_language"]
    except Exception as exc:  # noqa: BLE001 - a settings read must not block a call
        print(f"[profile] could not read practice language, using English: {type(exc).__name__}: {exc}")
    try:
        return _roleplay(request).start(payload.persona_id, payload.custom_notes, language=language)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/roleplay/sessions/{session_id}/turn")
def roleplay_turn(session_id: str, payload: TurnRequest, request: Request) -> dict[str, Any]:
    roleplay = _roleplay(request)
    _session_or_404(roleplay, session_id)
    try:
        return roleplay.turn(session_id, payload.agent_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.post("/v1/roleplay/sessions/{session_id}/ask")
def roleplay_ask(session_id: str, payload: AskRequest, request: Request) -> dict[str, Any]:
    roleplay = _roleplay(request)
    _session_or_404(roleplay, session_id)
    try:
        return roleplay.ask(session_id, payload.question)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.post("/v1/roleplay/sessions/{session_id}/feedback")
def roleplay_feedback(session_id: str, request: Request) -> dict[str, Any]:
    roleplay = _roleplay(request)
    _session_or_404(roleplay, session_id)
    try:
        return roleplay.feedback(session_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.post("/v1/roleplay/sessions/{session_id}/metrics")
def roleplay_metrics(session_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    _roleplay(request).client_metrics({**payload, "session_id": session_id})
    return {"ok": True}


def _history_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for key in ("started_at", "ended_at"):
        if out.get(key) is not None and hasattr(out[key], "isoformat"):
            out[key] = out[key].isoformat()
    out["outcome_label"] = OUTCOME_LABELS.get(out.get("outcome", ""), out.get("outcome", ""))
    return out


@app.get("/v1/roleplay/history")
def roleplay_history(request: Request, limit: int = 30) -> dict[str, Any]:
    settings = request.app.state.settings
    with connect(settings) as conn:
        rows = list_roleplay_sessions(conn, limit=max(1, min(limit, 200)))
        stats = roleplay_stats(conn)
    return {"sessions": [_history_row(r) for r in rows], "stats": stats}


@app.get("/v1/roleplay/history/{history_id}")
def roleplay_history_detail(history_id: int, request: Request) -> dict[str, Any]:
    with connect(request.app.state.settings) as conn:
        row = get_roleplay_session(conn, history_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such session report")
    return _history_row(row)


@app.delete("/v1/roleplay/history/{history_id}", status_code=204)
def roleplay_history_delete(history_id: int, request: Request) -> Response:
    with connect(request.app.state.settings) as conn:
        ok = delete_roleplay_session(conn, history_id)
    if not ok:
        raise HTTPException(status_code=404, detail="No such session report")
    return Response(status_code=204)


# --- Profile ------------------------------------------------------------------------------


class ProfileSettingsRequest(BaseModel):
    app_language: str | None = Field(default=None, max_length=8)
    practice_language: str | None = Field(default=None, max_length=8)


@app.get("/v1/profile")
def profile_get(request: Request) -> dict[str, Any]:
    with connect(request.app.state.settings) as conn:
        return profile.build_profile(conn, request.app.state.auth.username)


@app.put("/v1/profile/settings")
def profile_settings_put(payload: ProfileSettingsRequest, request: Request) -> dict[str, Any]:
    try:
        with connect(request.app.state.settings) as conn:
            return profile.update_settings(conn, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- Speech (browser STT/TTS token) -------------------------------------------------


@app.post("/v1/speech/token")
def speech_token(payload: SpeechTokenRequest, request: Request) -> dict[str, Any]:
    try:
        return _roleplay(request).speech_token(payload.profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not get a speech token: {type(exc).__name__}: {exc}") from exc


@app.get("/v1/speech/profiles")
def speech_profiles(request: Request) -> dict[str, Any]:
    return _roleplay(request).voice_profiles()


def main() -> None:
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("src.rag_layer.server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
