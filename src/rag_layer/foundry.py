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

import time
from urllib.parse import unquote, urlparse

import requests
import urllib3

from .config import Settings

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


def chat(*, settings: Settings, message: str, history: list[dict]) -> dict:
    """One turn against the hosted Foundry agent, with the running conversation replayed
    as context (the endpoint is stateless per call unless you thread response ids)."""
    client = FoundryAgentClient(settings)

    conversation: list[dict] = []
    for turn in history:
        role = turn.get("role")
        text = str(turn.get("text", "")).strip()
        if role in {"user", "assistant"} and text:
            conversation.append({"role": role, "content": text})
    conversation.append({"role": "user", "content": message})

    data = client.respond({"input": conversation})
    answer, citations = _extract_answer(data)
    return {
        "answer": answer or "(the agent returned no text)",
        "citations": citations,
        "agent": client.agent,
        "model": data.get("model"),
        "response_id": data.get("id"),
        "status": data.get("status"),
    }
