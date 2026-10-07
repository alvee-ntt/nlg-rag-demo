import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.models import ChatRequest, ChatResponse, SessionSummary
from app.orchestrator import AgentRunner, DemoAgentRunner, MicrosoftAgentFrameworkRunner
from app.profiles import MemoryProfileStore, PostgresProfileStore, SavedUserProfile, UserProfile
from app.prompt_configuration import load_agent_prompts
from app.service import ChatService
from app.settings import get_settings
from prompt_studio_api.trace_store import PostgresTraceWriter


def create_app(
    runner: AgentRunner | None = None,
    trace_path: Path | None = None,
    profiles: MemoryProfileStore | PostgresProfileStore | None = None,
) -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        selected_runner = runner
        if selected_runner is None:
            prompts = load_agent_prompts(settings)
            selected_runner = (
                MicrosoftAgentFrameworkRunner(settings, prompts)
                if settings.foundry_is_configured
                else DemoAgentRunner()
            )
        selected_trace_path = settings.log_path if runner is None else trace_path
        trace_writer = None
        if runner is None and settings.prompt_database_url is not None:
            trace_writer = PostgresTraceWriter(settings.prompt_database_url.get_secret_value())
            trace_writer.initialize()
        app.state.chat_service = ChatService(selected_runner, selected_trace_path, trace_writer)
        if profiles is not None:
            app.state.profiles = profiles
        elif runner is None and settings.prompt_database_url is not None:
            profile_store = PostgresProfileStore(settings.prompt_database_url.get_secret_value())
            profile_store.initialize()
            app.state.profiles = profile_store
        else:
            app.state.profiles = MemoryProfileStore()
        yield
        close = getattr(selected_runner, "close", None)
        if close is not None:
            await close()

    app = FastAPI(title="FlexLife Agent API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/profiles/{user_id}", response_model=SavedUserProfile)
    def get_profile(user_id: str, request: Request) -> SavedUserProfile:
        found = request.app.state.profiles.get(user_id)
        if found is None:
            raise HTTPException(status_code=404, detail="Profile not found")
        return found

    @app.post("/api/profiles", response_model=SavedUserProfile)
    def save_profile(payload: UserProfile, request: Request) -> SavedUserProfile:
        return request.app.state.profiles.save(payload)

    @app.delete("/api/profiles/{user_id}")
    def delete_profile(user_id: str, request: Request) -> dict[str, bool]:
        deleted = request.app.state.profiles.delete(user_id)
        return {"deleted": deleted}

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
        service: ChatService = request.app.state.chat_service
        session_id, reply = await service.chat(payload.message, payload.session_id)
        return ChatResponse(session_id=session_id, reply=reply)

    @app.post("/api/chat/stream")
    async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
        service: ChatService = request.app.state.chat_service
        session_id, reply = await service.chat(payload.message, payload.session_id)

        async def events() -> AsyncIterator[str]:
            reply_metadata = reply.model_copy(update={"content": ""}).model_dump(mode="json")
            yield json.dumps({"type": "start", "session_id": session_id, "reply": reply_metadata}) + "\n"
            for offset in range(0, len(reply.content), 24):
                yield json.dumps({"type": "delta", "delta": reply.content[offset : offset + 24]}) + "\n"
                await asyncio.sleep(0.015)
            yield json.dumps({"type": "done"}) + "\n"

        return StreamingResponse(
            events(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/sessions/{session_id}", response_model=SessionSummary)
    async def get_session(session_id: str, request: Request) -> SessionSummary:
        service: ChatService = request.app.state.chat_service
        summary = service.summary(session_id)
        if summary is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return summary

    return app


app = create_app()
