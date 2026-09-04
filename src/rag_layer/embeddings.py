from __future__ import annotations

import time

import urllib3
import requests

from .config import Settings

# Azure OpenAI throttles with HTTP 429 (and occasionally 5xx) when the embedding
# deployment's per-minute token/request quota is exceeded. Retry those with backoff
# so a burst of chunks does not permanently drop documents from the index.
_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 6
_BACKOFF_BASE = 2.0
_BACKOFF_CAP = 60.0


class AzureOpenAIClient:
    def __init__(self, settings: Settings) -> None:
        if settings.model_provider != "azure_openai":
            raise ValueError("Only MODEL_PROVIDER=azure_openai is configured in this project")
        if not settings.azure_openai_endpoint or not settings.azure_openai_api_key:
            raise ValueError("AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY are required")
        self.settings = settings
        self.base_url = settings.azure_openai_endpoint.rstrip("/")
        self.session = requests.Session()
        self.session.trust_env = False
        self.headers = {
            "Authorization": f"Bearer {settings.azure_openai_api_key}",
            "api-key": settings.azure_openai_api_key,
            "Content-Type": "application/json",
        }
        if not settings.azure_storage_verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self.verify_ssl = settings.azure_storage_verify_ssl

    def post(self, path: str, payload: dict, timeout: tuple[int, int] = (10, 120)) -> dict:
        url = f"{self.base_url}{path}"
        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            response = self.session.post(
                url,
                headers=self.headers,
                json=payload,
                timeout=timeout,
                verify=self.verify_ssl,
            )
            if response.status_code not in _RETRY_STATUS:
                response.raise_for_status()
                return response.json()

            # Throttled or transient server error: back off and retry.
            last_error = requests.HTTPError(
                f"{response.status_code} {response.reason} for url: {url}", response=response
            )
            if attempt == _MAX_RETRIES:
                break
            time.sleep(self._retry_delay(response, attempt))

        assert last_error is not None
        raise last_error

    @staticmethod
    def _retry_delay(response: "requests.Response", attempt: int) -> float:
        """Prefer the server's Retry-After hint; otherwise exponential backoff."""
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), _BACKOFF_CAP)
            except ValueError:
                pass
        return min(_BACKOFF_BASE * (2 ** attempt), _BACKOFF_CAP)


def get_openai_client(settings: Settings) -> AzureOpenAIClient:
    return AzureOpenAIClient(settings)


def embed_texts(client: AzureOpenAIClient, settings: Settings, texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    payload = {
        "model": settings.azure_openai_embedding_deployment,
        "input": texts,
        "dimensions": settings.embedding_dimensions,
    }
    data = client.post("/embeddings", payload)
    return [item["embedding"] for item in data["data"]]


def _context_text(contexts: list[dict]) -> str:
    from .db import citation

    return "\n\n".join(
        f"Source: {citation(item)}\n{item['content']}" for item in contexts
    )


def _generate(client: AzureOpenAIClient, settings: Settings, prompt: str) -> str:
    data = client.post(
        "/responses",
        {
            "model": settings.azure_openai_chat_deployment,
            "input": prompt,
        },
    )
    if "output_text" in data:
        return data["output_text"]
    output = data.get("output", [])
    parts: list[str] = []
    for item in output:
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                parts.append(content["text"])
    return "\n".join(parts).strip()


def answer_with_context(client: AzureOpenAIClient, settings: Settings, question: str, contexts: list[dict]) -> str:
    prompt = f"""Answer the question using only the context below. If the context does not contain the answer, say you do not know.

Context:
{_context_text(contexts)}

Question: {question}
"""
    return _generate(client, settings, prompt)


def factcheck_claim(client: AzureOpenAIClient, settings: Settings, claim: str, contexts: list[dict]) -> str:
    prompt = f"""You are verifying a claim against the source documents below. Using ONLY the context, decide whether the claim is SUPPORTED, CONTRADICTED, or NOT ADDRESSED. Do not use outside knowledge; if the context does not settle the claim, answer NOT ADDRESSED.

Context:
{_context_text(contexts)}

Claim: {claim}

Respond in exactly this format:
Verdict: <SUPPORTED | CONTRADICTED | NOT ADDRESSED>
Evidence: <exact quote(s) from the context with their Source citation, or "none">
Reasoning: <one or two sentences>
"""
    return _generate(client, settings, prompt)


def parse_verdict(report: str) -> str:
    """Pull the verdict token out of a factcheck report; UNKNOWN if unparseable."""
    for line in report.splitlines():
        if line.strip().lower().startswith("verdict:"):
            value = line.split(":", 1)[1].strip().upper()
            for token in ("SUPPORTED", "CONTRADICTED", "NOT ADDRESSED"):
                if token in value:
                    return token
    return "UNKNOWN"
