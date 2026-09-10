from __future__ import annotations

import os
import sys
import time
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
from pydantic import BaseModel, Field

if _VENDOR_WAS_ON_PATH:
    sys.path.insert(0, _VENDOR_PATH)

from .config import load_settings
from .db import init_db
from .embeddings import get_openai_client
from .service import (
    answer,
    check_transcript,
    corpus,
    document_chunks,
    fact_check,
    search,
)


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = Field(default=8, ge=1, le=20)


class AnswerRequest(BaseModel):
    question: str = Field(..., min_length=1)
    limit: int = Field(default=8, ge=1, le=20)


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    app.state.settings = settings
    app.state.openai_client = get_openai_client(settings)
    _ensure_schema(settings)
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
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


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
    return {
        "status": "ok",
        "postgres_host": settings.postgres_host,
        "model_provider": settings.model_provider,
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


def main() -> None:
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("src.rag_layer.server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
