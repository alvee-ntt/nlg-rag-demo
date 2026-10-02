"""Call the hosted Azure AI Foundry agent ("KnowledgeBase") over the OpenAI Responses
protocol.

Unlike the rest of the app (pgvector + Azure OpenAI), this talks to a Foundry Agent
Service agent whose own retrieval tools (an Azure AI Search knowledge base, exposed to
the agent as MCP) do the grounding server-side. It exists so the Coach console has a
"Foundry" tab to test that agent's chat side by side with the local RAG stack.

Wire protocol (confirmed against the live endpoint):
  POST {project_endpoint}/agents/{agent}/endpoint/protocols/openai/responses?api-version=v1
  Header: api-key: <project key>
  Body:   {"input": "<text>"}  or  {"input": [{"role","content"}, ...]}  (OpenAI Responses)
  Reply:  an OpenAI Responses object; the final answer is the last `message` output item's
          output_text, and its `annotations` carry url_citation entries for the sources.
"""

from __future__ import annotations

import json
import secrets
import time
from datetime import datetime
from urllib.parse import unquote, urlparse

import requests
import urllib3

from .config import Settings
from .db import connect, create_foundry_request_trace, update_foundry_request_trace

# Same transient-failure handling as embeddings.py: Foundry / the model behind it can
# throttle (429) or blip (5xx), so back off and retry rather than failing the chat turn.
_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 4
_BACKOFF_BASE = 2.0
_BACKOFF_CAP = 30.0


def foundry_configured(settings: Settings) -> bool:
    return bool(settings.foundry_project_endpoint and settings.foundry_api_key)


class FoundryAgentClient:
    """Thin client for one hosted Foundry agent's Responses endpoint."""

    def __init__(self, settings: Settings) -> None:
        if not foundry_configured(settings):
            raise ValueError(
                "Foundry is not configured (FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_API_KEY are required)"
            )
        self.settings = settings
        self.agent = settings.foundry_agent_name
        self.url = (
            f"{settings.foundry_project_endpoint}/agents/{self.agent}"
            f"/endpoint/protocols/openai/responses?api-version={settings.foundry_api_version}"
        )
        self.session = requests.Session()
        self.session.trust_env = False
        self.headers = {
            "api-key": settings.foundry_api_key,
            "Authorization": f"Bearer {settings.foundry_api_key}",
            "Content-Type": "application/json",
        }
        self.verify_ssl = settings.azure_storage_verify_ssl
        if not self.verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def respond(self, payload: dict, timeout: tuple[int, int] = (10, 180)) -> dict:
        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            response = self.session.post(
                self.url,
                headers=self.headers,
                json=payload,
                timeout=timeout,
                verify=self.verify_ssl,
            )
            if response.status_code not in _RETRY_STATUS:
                response.raise_for_status()
                return response.json()
            last_error = requests.HTTPError(
                f"{response.status_code} {response.reason} for url: {self.url}", response=response
            )
            if attempt == _MAX_RETRIES:
                break
            time.sleep(self._retry_delay(response, attempt))
        assert last_error is not None
        raise last_error

    @staticmethod
    def _retry_delay(response: "requests.Response", attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), _BACKOFF_CAP)
            except ValueError:
                pass
        return min(_BACKOFF_BASE * (2 ** attempt), _BACKOFF_CAP)


def _blob_filename(url: str) -> str:
    """Turn a source URL into a short human label (the file's name, unescaped)."""
    try:
        path = urlparse(url).path
    except Exception:  # noqa: BLE001 - a malformed URL should still show something
        path = url
    name = unquote(path.rsplit("/", 1)[-1]) if path else url
    return name or url


def _final_message(data: dict) -> dict | None:
    """The last `message` output item holds the agent's spoken answer."""
    message = None
    for item in data.get("output", []) or []:
        if item.get("type") == "message":
            message = item
    return message


def _extract_answer(data: dict) -> tuple[str, list[dict]]:
    """Pull the answer text out of a Responses object and rewrite its inline citation
    markers (e.g. ``【6:0†source】``) into ``[1] [2]`` numbered against a deduped source
    list, so the UI can show clean prose plus a footnote list."""
    message = _final_message(data)

    # Fall back to the SDK convenience field, or an empty answer, if there's no message.
    if message is None:
        text = (data.get("output_text") or "").strip()
        return text, []

    parts = message.get("content", []) or []
    text = ""
    annotations: list[dict] = []
    for content in parts:
        if content.get("type") in {"output_text", "text"} and content.get("text"):
            text = content["text"]
            annotations = content.get("annotations", []) or []
            break

    # Map each cited URL to a stable footnote number (first mention wins).
    order: dict[str, int] = {}
    citations: list[dict] = []
    for ann in annotations:
        if ann.get("type") != "url_citation":
            continue
        url = ann.get("url") or ""
        if url and url not in order:
            n = len(order) + 1
            order[url] = n
            # The agent's `title` is usually just the URL again; a filename reads better.
            title = ann.get("title") or ""
            if not title or title.startswith(("http://", "https://")):
                title = _blob_filename(url)
            citations.append({"n": n, "title": title, "url": url})

    # Replace the marker spans right-to-left so earlier indices stay valid.
    spans = [
        a for a in annotations
        if a.get("type") == "url_citation"
        and isinstance(a.get("start_index"), int)
        and isinstance(a.get("end_index"), int)
    ]
    for ann in sorted(spans, key=lambda a: a["start_index"], reverse=True):
        s, e = ann["start_index"], ann["end_index"]
        n = order.get(ann.get("url") or "")
        if n is None or not (0 <= s <= e <= len(text)):
            continue
        text = text[:s] + f" [{n}]" + text[e:]

    return text.strip(), citations


def _current_user_content(
    *,
    question: str,
    preferences: dict | None = None,
    prompt_augmentations: list[dict] | None = None,
    about_me: str = "",
    memories: list[str] | None = None,
) -> str:
    """Serialize the separately sourced Ask inputs only at the provider boundary."""
    prefs = preferences or {}
    style: list[str] = []
    length = {
        "brief": "Be brief and to the point.",
        "balanced": "",
        "detailed": "Give a thorough, detailed answer.",
    }
    output_format = {
        "bullets": "Prefer bullet points.",
        "prose": "Answer in prose paragraphs, not lists.",
        "auto": "",
    }
    tone = {
        "plain": "Use a neutral, plain tone.",
        "warm": "Use a warm, encouraging tone.",
        "formal": "Use a formal, professional tone.",
    }
    if length.get(prefs.get("length", "balanced")):
        style.append(length[prefs.get("length", "balanced")])
    if output_format.get(prefs.get("format", "auto")):
        style.append(output_format[prefs.get("format", "auto")])
    if tone.get(prefs.get("tone", "warm")):
        style.append(tone[prefs.get("tone", "warm")])
    if prefs.get("plain"):
        style.append("Explain simply, so a brand-new agent can follow.")
    if prefs.get("always_sources"):
        style.append("Always cite the source documents.")

    lines: list[str] = []
    if style:
        lines.append("Answer style: " + " ".join(style))
    clean_about_me = about_me.strip()
    clean_memories = [text.strip() for text in (memories or []) if text.strip()]
    augmentation_values = {
        "ask.about_me": ("{{about_me}}", clean_about_me),
        "ask.memories": ("{{memories}}", "; ".join(clean_memories)),
    }
    for augmentation in prompt_augmentations or []:
        key = str(augmentation.get("key", ""))
        if key not in augmentation_values:
            raise ValueError(f"Unsupported prompt augmentation key: {key!r}")
        placeholder, value = augmentation_values[key]
        if not value:
            continue
        template = str(augmentation.get("instructions", ""))
        if placeholder not in template:
            raise ValueError(
                f"Prompt augmentation {key!r} is missing required placeholder {placeholder}"
            )
        lines.append(template.replace(placeholder, value).strip())

    preamble = (
        "[Context for how to answer — do not repeat this back to me:\n"
        + "\n".join(lines)
        + "]\n\n"
        if lines
        else ""
    )
    return preamble + question


def _flatten_provider_request(payload: dict) -> str:
    """Render every prompt-bearing field; provider-request.json remains authoritative."""
    sections: list[str] = []
    if "instructions" in payload:
        instructions = payload.get("instructions", "")
        if not isinstance(instructions, str):
            instructions = json.dumps(instructions, ensure_ascii=False, indent=2)
        sections.append(f"===== INSTRUCTIONS =====\n{instructions}")

    request_input = payload.get("input", [])
    if isinstance(request_input, str):
        sections.append(f"===== INPUT =====\n{request_input}")
    else:
        for index, item in enumerate(request_input or [], start=1):
            role = str(item.get("role", "unknown")).upper()
            content = item.get("content", "")
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False, indent=2)
            sections.append(f"===== MESSAGE {index}: {role} =====\n{content}")

    # Render every remaining request field generically. This deliberately favors a
    # complete trace over guessing which future Responses fields affect model context.
    for field, field_value in payload.items():
        if field in {"instructions", "input"}:
            continue
        value = json.dumps(field_value, ensure_ascii=False, indent=2)
        sections.append(f"===== {field.upper()} =====\n{value}")
    return "\n\n".join(sections) + "\n"


class _RequestTrace:
    def __init__(
        self,
        *,
        settings: Settings,
        session_id: str | None,
        inputs: dict,
    ) -> None:
        self.settings = settings
        if not session_id:
            # Normally created at login. This fallback covers API clients carrying an
            # older auth cookie while still keeping their requests grouped together.
            session_id = "missing-login-session"
        now = datetime.now().astimezone()
        timestamp = (
            now.strftime("%Y-%m-%d_%H-%M-%S-")
            + f"{now.microsecond // 1000:03d}_"
            + now.strftime("%z")
        )
        self.request_id = f"{timestamp}_request_{secrets.token_hex(4)}"
        with connect(settings) as conn:
            create_foundry_request_trace(
                conn,
                request_id=self.request_id,
                session_id=session_id,
                received_at=now,
                inputs={
                    "trace_session_id": session_id,
                    "request_id": self.request_id,
                    "received_at": now.isoformat(),
                    **inputs,
                },
            )

    def provider_request(self, payload: dict) -> None:
        with connect(self.settings) as conn:
            update_foundry_request_trace(
                conn,
                self.request_id,
                provider_request=payload,
                prompt=_flatten_provider_request(payload),
            )

    def response(self, data: dict) -> None:
        with connect(self.settings) as conn:
            update_foundry_request_trace(conn, self.request_id, response=data)

    def error(self, exc: Exception) -> None:
        error = {
            "error_type": type(exc).__name__,
            "message": str(exc),
            "recorded_at": datetime.now().astimezone().isoformat(),
        }
        response = getattr(exc, "response", None)
        fields: dict = {"error": error}
        if response is not None:
            error["http_status"] = response.status_code
            error["http_reason"] = response.reason
            error["response_body"] = response.text
            fields["response"] = {
                "status_code": response.status_code,
                "reason": response.reason,
                "body": response.text,
            }
        with connect(self.settings) as conn:
            update_foundry_request_trace(conn, self.request_id, **fields)


def chat(
    *,
    settings: Settings,
    instructions: str,
    prompt_key: str,
    prompt_version: int,
    question: str,
    history: list[dict],
    preferences: dict | None = None,
    prompt_augmentations: list[dict] | None = None,
    about_me: str = "",
    memories: list[str] | None = None,
    user_id: str | None = None,
    trace_session_id: str | None = None,
) -> dict:
    """One turn against the hosted Foundry agent, with the running conversation replayed
    as context (the endpoint is stateless per call unless you thread response ids).

    The question, answer preferences, user description, memories, and history cross into
    this module as distinct values. They are serialized only when constructing the final
    provider message below.
    """
    trace = _RequestTrace(
        settings=settings,
        session_id=trace_session_id,
        inputs={
            "prompt_key": prompt_key,
            "prompt_version": prompt_version,
            "question": question,
            "history": history,
            "preferences": preferences or {},
            "prompt_augmentations": [
                {"key": item.get("key"), "version": item.get("version")}
                for item in (prompt_augmentations or [])
            ],
            "about_me": about_me,
            "memories": memories or [],
            "user_id": user_id,
        },
    )
    try:
        client = FoundryAgentClient(settings)
        # Agent-scoped Foundry endpoints reject the top-level Responses
        # ``instructions`` field ("Not allowed when agent is specified"). Put the
        # application-managed prompt in the input as a system message instead.
        conversation: list[dict] = [
            {"type": "message", "role": "system", "content": instructions}
        ]
        for turn in history:
            role = turn.get("role")
            text = str(turn.get("text", "")).strip()
            if role in {"user", "assistant"} and text:
                conversation.append({"type": "message", "role": role, "content": text})
        conversation.append({
            "type": "message",
            "role": "user",
            "content": _current_user_content(
                question=question,
                preferences=preferences,
                prompt_augmentations=prompt_augmentations,
                about_me=about_me,
                memories=memories,
            ),
        })

        provider_payload = {"input": conversation}
        trace.provider_request(provider_payload)
        data = client.respond(provider_payload)
        trace.response(data)
    except Exception as exc:
        trace.error(exc)
        raise
    answer, citations = _extract_answer(data)
    return {
        "answer": answer or "(the agent returned no text)",
        "citations": citations,
        "agent": client.agent,
        "model": data.get("model"),
        "response_id": data.get("id"),
        "status": data.get("status"),
    }
