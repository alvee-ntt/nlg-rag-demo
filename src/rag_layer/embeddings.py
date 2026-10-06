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
    prompt = f"""Answer the question using only the context below. If the context does not contain the answer, say the approved FlexLife material does not cover it and suggest contacting NLG support — do not guess or fill gaps with general knowledge.

Context:
{_context_text(contexts)}

Question: {question}
"""
    return _generate(client, settings, prompt)


def _parse_json_object(text: str) -> dict:
    """Tolerant JSON-object parse: strips code fences and trailing prose; raises on garbage."""
    import json
    import re

    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE | re.MULTILINE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def _history_text(history: list[dict]) -> str:
    lines = []
    for turn in history:
        who = "Agent" if turn.get("role") == "user" else "Navigator"
        text = str(turn.get("text", "")).strip()
        if text:
            lines.append(f"{who}: {text}")
    return "\n".join(lines) or "(this is the first message)"


def classify_turn(
    client: AzureOpenAIClient,
    settings: Settings,
    message: str,
    history: list[dict],
) -> dict:
    """Route a chat turn before it reaches the hosted Foundry agent.

    One call answers three things: whether the turn is about FlexLife at all (M02, so the
    app declines clearly unrelated requests instead of answering like a general chatbot),
    whether the agent is describing or asking about a specific client (M16, which is what
    brings the client-facts flow in), and whether it involves replacing existing coverage.

    Returns {"domain": "IN_DOMAIN" | "OUT_OF_DOMAIN", "client": bool, "replacement": bool}.
    Fails open to a plain in-domain turn on any error or unparseable reply, so a
    classifier hiccup never blocks a legitimate FlexLife question.
    """
    prompt = f"""You are a router for a FlexLife life-insurance sales-support assistant.
Decide if the user's latest message is about FlexLife, its products/riders/
pricing/eligibility/benefits/process, life insurance, or selling/servicing it.
Greetings and conversational follow-ups that continue a FlexLife thread count as
IN_DOMAIN. General knowledge, coding, other companies, creative writing, or
anything unrelated is OUT_OF_DOMAIN.

Then decide two more things about the latest message:
- CLIENT if it describes or asks about one specific client or applicant (their age,
  health, medications, habits, coverage amount, finances, or what applies to "him",
  "her", "my client"), including follow-ups about a client described earlier.
  NO_CLIENT for general questions about rules, products or wording.
- REPLACEMENT if it involves replacing, surrendering, cashing out or borrowing from an
  existing life insurance policy or annuity to fund new coverage. Otherwise NO_REPLACEMENT.

Reply with exactly three tokens separated by spaces, for example:
IN_DOMAIN NO_CLIENT NO_REPLACEMENT

Conversation so far:
{_history_text(history)}
Latest message: {message}
"""
    try:
        raw = _generate(client, settings, prompt).upper()
    except Exception:  # noqa: BLE001 - a classifier hiccup must never block a real question
        return {"domain": "IN_DOMAIN", "client": False, "replacement": False}
    return {
        "domain": "OUT_OF_DOMAIN" if "OUT_OF_DOMAIN" in raw else "IN_DOMAIN",
        "client": "CLIENT" in raw.replace("NO_CLIENT", ""),
        "replacement": "REPLACEMENT" in raw.replace("NO_REPLACEMENT", ""),
    }


def classify_domain(
    client: AzureOpenAIClient,
    settings: Settings,
    message: str,
    history: list[dict],
) -> str:
    """The M02 half of ``classify_turn``: IN_DOMAIN or OUT_OF_DOMAIN."""
    return classify_turn(client, settings, message, history)["domain"]


def extract_case_facts(
    client: AzureOpenAIClient,
    settings: Settings,
    message: str,
    history: list[dict],
    case: dict,
    rulebook: dict,
) -> dict:
    """Pull client facts out of the conversation as rulebook keys (M16).

    Returns {"conditions": [condition ids], "facts": {key: raw value}}. The caller
    validates everything against the rulebook, so this only has to be roughly right.
    Fails open to nothing extracted, which leaves the turn on the ordinary chat path.
    """
    import json

    def describe(key: str, spec: dict) -> str:
        kind = spec["type"]
        if kind == "choice":
            shape = "one of " + ", ".join(f'"{o["value"]}" ({o["label"]})' for o in spec["options"])
        elif kind == "yes_no":
            shape = "true or false"
        elif kind == "height_weight":
            shape = '{"height_in": total inches, "weight_lb": pounds}'
        elif kind == "amount":
            shape = "a number of US dollars"
        elif kind == "number":
            shape = "a number"
        else:
            shape = "a short phrase"
        return f"- {key}: {spec['label']}. Value: {shape}."

    fact_lines = "\n".join(describe(key, spec) for key, spec in rulebook["facts"].items())
    condition_fact_lines = "\n".join(describe(key, spec) for key, spec in rulebook["condition_facts"].items())
    condition_lines = "\n".join(f"- {key}: {c['label']}" for key, c in rulebook["conditions"].items())
    prompt = f"""You extract facts about a life-insurance client from a sales agent's chat, as JSON.

Only record what the agent actually stated about the client. Never guess, never infer a
value that was not said, and never record names, addresses, ID numbers or bank details.
A medication alone does not establish a condition unless the agent names the condition.

Client facts you may record (use these exact keys):
{fact_lines}

Medical conditions you may record, by id (pick only clear matches from this list):
{condition_lines}

For each condition the client has, you may also record these, keyed as
"<condition id>.<key>" (for example "diabetes_type_2.when"):
{condition_fact_lines}

Already on file (do not repeat unless the agent corrects it):
{json.dumps({"conditions": case.get("conditions", []), "facts": {k: v.get("value") for k, v in case.get("facts", {}).items() if "value" in v}})}

Return ONLY a JSON object: {{"conditions": ["<id>", ...], "facts": {{"<key>": <value>, ...}}}}
Use empty lists/objects when there is nothing to record.

Conversation so far:
{_history_text(history)}

Agent's latest message:
{message}
"""
    try:
        data = _parse_json_object(_generate(client, settings, prompt))
        conditions = [str(c) for c in data.get("conditions") or [] if isinstance(c, str)]
        facts = data.get("facts") if isinstance(data.get("facts"), dict) else {}
        return {"conditions": conditions, "facts": facts}
    except Exception:  # noqa: BLE001 - fail open: the turn simply proceeds without new facts
        return {"conditions": [], "facts": {}}


def chat_with_context(
    client: AzureOpenAIClient,
    settings: Settings,
    message: str,
    history: list[dict],
    contexts: list[dict],
) -> dict:
    """Conversational, chat-sized reply grounded in the retrieved chunks.

    Returns {"answer": str, "follow_ups": [str, ...]}. Unlike answer_with_context this is
    tuned for a phone chat bubble: short plain prose, no bullets or markdown, and it
    remembers the running conversation so follow-up questions make sense.
    """
    prompt = f"""You are Navigator, a friendly sales coach chatting with a life-insurance agent on their phone.

How to reply:
- Answer the agent's latest message directly in 1-3 short sentences of plain prose, like a text message. No bullet points, no headings, no markdown, no numbered lists.
- Use only the source context below for product facts and approved wording. If the sources do not cover it, say in one sentence that the approved FlexLife material doesn't cover that and suggest reaching out to NLG support — do NOT guess or give general guidance from outside the sources. Never promise guarantees or returns.
- If the agent asked something broad, give the single most useful point and offer to go deeper rather than listing everything.
- Keep the conversation going: the reply should read naturally after the earlier messages.
- Set "grounded" to false whenever the source context does not actually answer the agent's question (even when the topic is FlexLife-related) — i.e. you had to decline or point them to NLG support. Set it to true only when your answer is supported by the sources above.

Then suggest up to two short follow-up questions the agent might tap next (each under 6 words, phrased as the agent would ask them, e.g. "What do I ask next?").

Return ONLY a JSON object: {{"answer": "...", "grounded": true, "follow_ups": ["...", "..."]}}

Conversation so far:
{_history_text(history)}

Agent's latest message:
{message}

Source context:
{_context_text(contexts) or "(no sources retrieved)"}
"""
    raw = _generate(client, settings, prompt)
    try:
        data = _parse_json_object(raw)
        answer = str(data.get("answer", "")).strip()
        follow_ups = [str(x).strip() for x in data.get("follow_ups", []) if str(x).strip()][:2]
        grounded = bool(data.get("grounded", True))
    except Exception:  # noqa: BLE001 - a malformed JSON reply still has a usable answer in it
        answer, follow_ups, grounded = raw.strip(), [], True
    return {
        "answer": answer or "I couldn't find that in the sources.",
        "follow_ups": follow_ups,
        "grounded": grounded,
    }


def generate_support_email(
    client: AzureOpenAIClient,
    settings: Settings,
    question: str,
    history: list[dict],
    contexts: list[dict],
    reason: str,
) -> dict:
    """Draft an NLG Support email from the conversation (M09). First person, as the agent.

    Returns {"subject": str, "body": str}. Falls back to raw-text-as-body on a malformed
    JSON reply, mirroring chat_with_context.
    """
    reason_note = {
        "insufficient": "The Agent Navigator could not find this in the Knowledge Foundation.",
        "case_specific": "This needs an authoritative, case-specific decision from NLG.",
        "manual": "The agent chose to escalate this question to NLG Support.",
    }.get(reason, "The agent chose to escalate this question to NLG Support.")
    prompt = f"""You are drafting a support email ON BEHALF OF a FlexLife sales agent, addressed to NLG Support.
Write the body in the FIRST PERSON as the agent ("I ..."). Never describe the agent in the third person and never say you are an AI.

Why they are escalating: {reason_note}

Write a professional, concise email whose body has three clear parts:
1. The specific question that needs answering.
2. The relevant context the agent already established in the conversation (product, client details, what was and was not confirmed). Do not invent facts.
3. A clear statement of exactly what clarification or assistance is being requested from NLG Support.
Close with a sign-off line ending in "[Your name]". Do not promise guarantees or returns.

Return ONLY a JSON object: {{"subject": "<concise topic line>", "body": "<the full email body>"}}

Conversation so far:
{_history_text(history)}

The question that triggered this handoff:
{question}

What the app was able to find in the sources (reference only, to describe what could not be confirmed):
{_context_text(contexts) or "(nothing relevant retrieved)"}
"""
    raw = _generate(client, settings, prompt)
    try:
        data = _parse_json_object(raw)
        subject = str(data.get("subject", "")).strip() or "FlexLife question for NLG Support"
        body = str(data.get("body", "")).strip()
    except Exception:  # noqa: BLE001 - a malformed JSON reply still has a usable body in it
        subject, body = "FlexLife question for NLG Support", raw.strip()
    return {"subject": subject, "body": body or "Please see my question above."}


def factcheck_claim(client: AzureOpenAIClient, settings: Settings, claim: str, contexts: list[dict]) -> str:
    prompt = f"""You are verifying a claim against the source documents below.

First decide whether the text even makes a verifiable claim about the product (its coverage, pricing, terms, eligibility, benefits, or process). Greetings, questions, pleasantries, and pure process talk ("hi", "is now a good time?", "let me follow up") assert nothing checkable - for those answer NO CLAIM.

If it does make a product claim, then using ONLY the context, decide whether the claim is SUPPORTED, CONTRADICTED, or NOT ADDRESSED. Do not use outside knowledge; if the context does not settle the claim, answer NOT ADDRESSED.

Context:
{_context_text(contexts)}

Claim: {claim}

Respond in exactly this format:
Verdict: <NO CLAIM | SUPPORTED | CONTRADICTED | NOT ADDRESSED>
Evidence: <exact quote(s) from the context with their Source citation, or "none">
Reasoning: <one or two sentences>
"""
    return _generate(client, settings, prompt)


def parse_verdict(report: str) -> str:
    """Pull the verdict token out of a factcheck report; UNKNOWN if unparseable."""
    for line in report.splitlines():
        if line.strip().lower().startswith("verdict:"):
            value = line.split(":", 1)[1].strip().upper()
            for token in ("NO CLAIM", "SUPPORTED", "CONTRADICTED", "NOT ADDRESSED"):
                if token in value:
                    return token
    return "UNKNOWN"
