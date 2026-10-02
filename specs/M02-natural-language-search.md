# M02 — Natural Language Search

## 1. Summary

FlexLife's end-user "Ask Navigator" chat is already an end-to-end conversational search experience, and the product owner has confirmed its engine: the **hosted Azure AI Foundry agent** reached through `POST /v1/foundry/chat` (`src/rag_layer/server.py:467-484` → `foundry.chat`, `src/rag_layer/foundry.py:169-191`), **not** the in-repo `/v1/chat` pgvector pipeline. The Foundry agent runs its own Azure AI Search knowledge base, so for M02 that hosted KB effectively **is** the "FlexLife Knowledge Foundation" for chat. Retrieval, grounding, and synthesis all happen inside the hosted agent; this repo only proxies the turn, replays conversation history, and post-processes the citations it returns. The chat UI (`ui/learn.html`) is built and wired to this path, so NL question submission, grounded answers, and in-conversation follow-ups already work.

The one real gap M02 must close is **explicit out-of-domain scoping** — the app must recognize a clearly non-FlexLife request and decline instead of acting as a general chatbot. Because the hosted agent's grounding and its own instructions live in Azure (out of this repo) and cannot be reliably counted on to refuse off-topic prompts, M02 adds a lightweight **domain gate in this repo**, on the `/v1/foundry/chat` path, that declines clearly off-domain turns before (or instead of) calling the agent, and surfaces a `domain` signal in the response. Deep grounding quality and similarity thresholds are agent-side (Azure config) and out of scope here — we set that expectation honestly. Citation display and the "insufficient support → refer to NLG" abstention (now framed as the agent returning zero citations) are M03; advanced follow-up handling is M04.

## 2. User Story (verbatim)

**User Story: Natural Language Search** — As a FlexLife sales agent or support user, I want to ask questions about FlexLife using natural language, so that I can quickly obtain useful answers without needing to understand how the underlying knowledge is organized.

Description: Conversational search over the FlexLife Knowledge Foundation. Users ask in everyday language; the app finds relevant info across the corpus and synthesizes a direct response. Users need not know which documents hold the info or how it's indexed. Must support follow-up questions within the conversation. Must recognize when a request is unrelated to FlexLife / the supported domain and NOT become a general-purpose chatbot.

Acceptance criteria (source): submit NL question; retrieves relevant info from Knowledge Foundation; synthesizes a direct NL response; user not required to identify source doc/type/category/structure; uses conversational context for follow-ups; supports explanations/summaries/comparisons (not just factual lookup) when supported; responses grounded in FlexLife knowledge; clearly unrelated requests are not treated as general conversation; demonstrable via a simple POC chat interface without production-grade conversation management.

Out of scope: citation display/links (that's M03/M10); production-scale conversation storage/history; advanced auth/personalization; production-grade moderation / comprehensive topic classification.

## 3. Current State

The end-user chat engine is the hosted Foundry agent, and most of the story is already satisfied by shipped code. The single deliverable left is out-of-domain scoping.

### Already working (reusable)

- **NL question submission + conversational endpoint (Foundry).** `POST /v1/foundry/chat` is defined at `src/rag_layer/server.py:467-484`, with request model `FoundryChatRequest` (`message` 1–4000 chars, `history` up to 20 turns — **no `limit` field**) at `server.py:93-95` and response model `FoundryChatResponse` (`answer`, `citations`, `agent`, `model`, `response_id`, `status`) at `server.py:104-110`. `foundry_configured` gates it — a 503 if `FOUNDRY_PROJECT_ENDPOINT` / `FOUNDRY_API_KEY` are unset (`server.py:472-476`, `foundry.py:35-36`).
- **Retrieval + grounding happen inside the hosted agent.** `foundry.chat` (`foundry.py:169-191`) posts the conversation to the agent's OpenAI-Responses endpoint; the agent's own Azure AI Search KB (exposed to it as an MCP tool) does retrieval and grounding server-side. The user never names a document, and this repo sets no similarity threshold or max-sources — those are agent-side. Satisfies "retrieves relevant info" and "user not required to identify source doc/type/structure," with grounding delegated to Azure.
- **NL synthesis + citations.** The agent returns synthesized prose; `_extract_answer` (`foundry.py:116-166`) pulls the final message's `output_text`, dedupes the `url_citation` annotations into numbered footnotes `{n, title, url}` (first mention wins), and rewrites inline markers like `【6:0†source】` into `[1] [2]`. `url` points at the hosted KB's source; `title` is a filename derived from the URL when the agent's title is just the URL again (`_blob_filename`, `foundry.py:97-104`). Satisfies "synthesizes a direct NL response"; citation *display* is M03.
- **In-conversation follow-up context.** `foundry.chat` replays the running conversation on every turn (`foundry.py:174-182`): it maps each prior `{role, text}` history turn to `{role, content}` and appends the new user message, so short follow-ups ("and the floor?") resolve. The endpoint is stateless per call, so history replay is what threads the conversation. Meets M02's "basic follow-up" bar; deeper handling is M04.
- **Explanations / summaries / comparisons.** Nothing in the proxy restricts the agent to factual lookup — it answers whatever framing the hosted KB supports. No repo change needed.
- **POC chat interface, wired to Foundry.** `ui/learn.html` has a full chat implementation calling the Foundry path: the shared `mountChat` widget (`ui/learn.html:1149-1241`), the full-page Ask Navigator at `#/chat` via `renderChat` (`ui/learn.html:1272-1292`), and the pull-up sheet via `openNavigator` (`ui/learn.html:1246-1267`), all using `navSend` (`ui/learn.html:1053-1058`), which posts `buildPreamble() + message` plus `history` to `/v1/foundry/chat` and returns `{text, citations}`. `buildPreamble()` (`ui/learn.html:1030-1045`) already prepends a per-turn instruction block (answer-style prefs + saved memories) to the message without dirtying the visible user bubble — the natural hook for domain-scoping instructions. `mountChat` renders `citations` (via `citeHtml`) and `sources` (via `srcHtml`) as a source list (`ui/learn.html:1161-1167`) and drives follow-up chips from `follow_ups` when present (`ui/learn.html:1173`). Multi-thread persistence is `localStorage`-only. Satisfies "demonstrable via a simple POC chat interface." (Note: the Foundry response carries no `follow_ups`, so chips fall back to the default `NAV_CHIPS` today — acceptable for M02; richer follow-ups are M04.)

### Missing / needs hardening

- **Out-of-domain rejection ("not a general chatbot") — the core gap.** Nothing in this repo prevents the hosted agent from free-answering an off-topic prompt ("write me a poem," "capital of France"). The agent's own instructions (which could enforce a refusal) live in Azure, outside this repo, and cannot be tested or version-controlled here, so we cannot rely on them for M02's acceptance criteria. There is no domain check in the `/v1/foundry/chat` path. This must be added **in-repo**.
- **No `domain` signal in the response.** `FoundryChatResponse` (`server.py:104-110`) has no field distinguishing an answered turn from an off-domain decline, so the UI and tests cannot tell them apart. This must be added if a gate is implemented here (it is).
- **The `buildPreamble()` hook is style-only.** Today it injects answer-style/memory context (`ui/learn.html:1030-1045`); it carries no domain-scoping instruction. It is available as a *secondary* (defense-in-depth) lever but is not, on its own, a reliable gate (see Technical Design).

### Explicitly not the end-user chat (leave intact)

- The in-repo `/v1/chat` pgvector pipeline (`server.py:441-453` → `service.chat` at `service.py:76` → `embeddings.chat_with_context` at `embeddings.py:155` → `service.format_sources`) still exists and powers the Coach console and the fact-check tooling. It is **not** the Ask Navigator engine and M02 does **not** remove, rewire, or repoint it. `/v1/search` and `/v1/answer` are likewise untouched.

### Verified behavior (live test — 2026-09-30)

Run against the live `/v1/foundry/chat` endpoint on the running container (signed in as the demo user). Evidence for what M02 already does vs. the gap:

- **In-domain NL query works.** "What is the interest rate floor on FlexLife?" → grounded answer ("0% floor on indexed strategies, except the S&P 500 Point-to-Point 1% Floor strategy") with a citation to a real source document. Confirms grounded natural-language search + synthesis.
- **Follow-up context works.** With prior turns in `history`, "And what about the cap on that strategy?" correctly resolved "that strategy" to the earlier S&P 500 1% Floor strategy and answered with 2 citations. Confirms in-conversation follow-up (the basic M02 requirement).
- **❌ Out-of-domain scoping is NOT implemented — the core M02 gap, now proven.** "Write me a haiku about the ocean and recommend a good pasta recipe" produced a full haiku AND a complete Aglio e Olio recipe, then a soft one-line nudge back to FlexLife. The app behaves as a general-purpose chatbot, which the story explicitly forbids. This is the primary new work for M02: a domain gate implemented in-repo (the hosted agent's own instructions live in Azure, outside this repo).

## 4. Scope

This milestone delivers:

1. An **out-of-domain guard on the Foundry chat path** (`/v1/foundry/chat`) that recognizes a clearly non-FlexLife request and returns a short, polite decline instead of letting the hosted agent answer as a general chatbot — implemented in this repo so it is testable and version-controlled.
2. A **`domain` field** (`in_domain` | `out_of_domain`) on `FoundryChatResponse` so the UI and tests can distinguish a decline from an answer.
3. **UI decline handling**: `navSend` surfaces the decline text and the `domain` flag; the decline renders as a normal answer bubble (optionally suppressing the thumbs/sources affordances).
4. Optionally, a **domain-scoping line in `buildPreamble()`** as defense-in-depth (belt-and-braces with the server gate), plus a documented note that the hosted agent's own Azure instructions can also be tightened out-of-repo.
5. Tests and a manual demo script covering NL Q&A, follow-ups, comparison/summary framings, and off-topic rejection on the Foundry path.

Explicitly **out**: any change to the hosted agent's server-side retrieval, grounding quality, or similarity thresholds (Azure config, out of this repo); any rewire of `/v1/chat`, `/v1/search`, `/v1/answer`, `/v1/fact-check`, or the in-call roleplay Ask sheet; citation display and the zero-citation → NLG abstention (M03); advanced follow-ups (M04).

## 5. Functional Requirements

- **FR1** — A user can submit a free-text natural-language question through the Ask Navigator chat and receive a synthesized natural-language answer from the hosted agent; no document/category selection is required.
- **FR2** — Product-fact grounding is performed by the hosted Foundry agent against its own AI Search KB. This repo does not enforce grounding; it faithfully relays the agent's answer and citations. (Grounding *quality* and thresholds are agent-side — see Dependencies.)
- **FR3** — The system uses prior turns to interpret follow-up questions (pronouns, elisions like "and the floor?") by replaying conversation history to the agent on each turn.
- **FR4** — The system supports explanation, summary, and comparison requests, not only single-fact lookup, when the hosted KB supports the request.
- **FR5** — When a request is clearly unrelated to FlexLife / life-insurance sales support (e.g. general trivia, coding, creative writing, other companies' products), the app must decline briefly **in this repo** and must not let the hosted agent answer as a general chatbot.
- **FR6** — The chat response payload must include a machine-readable `domain` field indicating whether the turn was answered (`in_domain`) or declined as out-of-domain (`out_of_domain`).
- **FR7** — On an out-of-domain decline, the app must not call the hosted agent (saving the round-trip) and must return an empty `citations` list.
- **FR8** — The Ask Navigator UI (full-page `#/chat` and pull-up sheet) must render the decline as a normal answer and expose the `domain` flag to the widget.
- **FR9** — Behavior must be demonstrable in the POC UI with only `localStorage` thread persistence; no server-side conversation store is introduced.

## 6. Technical Design

### 6.1 Backend — the domain gate on the Foundry path

All new code sits **around** the existing Foundry proxy; `foundry.chat` itself is unchanged. The gate needs an LLM call, so it uses the in-repo Azure OpenAI client (`request.app.state.openai_client`, the same deployment the `/v1/chat` path uses, `config.py:110-111`) — this is a separate, cheap classification call, not a second Foundry round-trip.

**Design choice — where the gate lives.** Two families of approach; recommend the server-side classifier gate (Option A):

- **Option A (server-side classifier gate — recommended).** A single cheap classification call in this repo decides `IN_DOMAIN` / `OUT_OF_DOMAIN` *before* proxying to the agent. If out-of-domain, short-circuit with a decline and never call the agent. This is the reliable, testable path: the refusal is enforced by code we own and can unit-test, independent of whatever the hosted agent's Azure instructions happen to say.

  Add `classify_domain(client, settings, message, history) -> str` in `embeddings.py`, returning a strict token:

  ```
  You are a router for a FlexLife life-insurance sales-support assistant.
  Decide if the user's latest message is about FlexLife, its products/riders/
  pricing/eligibility/benefits/process, life insurance, or selling/servicing it.
  Greetings and conversational follow-ups that continue a FlexLife thread count as
  IN_DOMAIN. General knowledge, coding, other companies, creative writing, or
  anything unrelated is OUT_OF_DOMAIN.
  Reply with exactly one token: IN_DOMAIN or OUT_OF_DOMAIN.

  Conversation so far:
  {history}
  Latest message: {message}
  ```

  Parse tolerantly (uppercase, substring match), defaulting to `IN_DOMAIN` on any parse failure so the guard fails open and never blocks a legitimate FlexLife question. This mirrors the existing tolerant patterns in `parse_verdict` (`embeddings.py:219`) and `_parse_json_object` (`embeddings.py:130`).

- **Option B (preamble / agent-instruction only).** Prepend a domain-scoping instruction to the message via `buildPreamble()` (client-side) and/or tighten the hosted agent's own instructions in Azure. **Cheaper (no extra call) but unreliable for M02:** the preamble is just text the hosted agent may or may not honor, its own Azure-side instructions can override it, and none of it is unit-testable in this repo. Use it only as defense-in-depth behind Option A, not as the gate.

**Recommendation:** Option A as the gate; optionally add one domain-scoping line to `buildPreamble()` as a cheap second layer. Note plainly in the code/PR that the hosted agent's Azure instructions can also be tightened, but that work is out-of-repo and not part of M02's testable deliverable.

**Wiring.** Add a thin wrapper in `service.py` (keeps the endpoint slim and the gate unit-testable) that classifies, then delegates to the Foundry proxy:

```python
# service.py  — imports foundry as a module
from . import foundry
from .embeddings import classify_domain

DOMAIN_DECLINE = ("I'm the FlexLife Navigator, so I can only help with FlexLife "
                  "products, riders, and approved wording. Ask me anything about that.")

def chat_foundry(*, settings, client, message, history):
    if classify_domain(client, settings, message, history) == "OUT_OF_DOMAIN":
        return {"answer": DOMAIN_DECLINE, "citations": [],
                "agent": settings.foundry_agent_name, "model": None,
                "response_id": None, "status": "declined", "domain": "out_of_domain"}
    result = foundry.chat(settings=settings, message=message, history=history)
    return {**result, "domain": "in_domain"}
```

The endpoint (`server.py:467-484`) then calls `chat_foundry(..., client=request.app.state.openai_client, ...)` instead of `foundry_chat(...)` directly, keeping the existing `foundry_configured` 503 guard (`server.py:472-476`) and the try/except → 502 wrapper. Off-domain turns skip the agent call entirely (FR7).

**Response model change (`server.py:104-110`).** Add the domain field:

```python
class FoundryChatResponse(BaseModel):
    answer: str
    citations: list[FoundryCitation]
    agent: str
    model: str | None = None
    response_id: str | None = None
    status: str | None = None
    domain: Literal["in_domain", "out_of_domain"] = "in_domain"
```

Request model (`FoundryChatRequest`, `server.py:93-95`) is unchanged — no `limit` field exists or is added.

**Concrete JSON shapes.**

Request (unchanged) — `POST /v1/foundry/chat`:
```json
{
  "message": "How do caps and floors work on FlexLife?",
  "history": [
    {"role": "user", "text": "Tell me about FlexLife."},
    {"role": "assistant", "text": "FlexLife is an indexed universal life product..."}
  ]
}
```

Response — in-domain answer (agent-grounded, with numbered citations):
```json
{
  "answer": "FlexLife credits interest up to a cap and never below the floor [1].",
  "citations": [{"n": 1, "title": "FlexLife-brochure.pdf",
                 "url": "https://.../FlexLife-brochure.pdf"}],
  "agent": "KnowledgeBase", "model": "gpt-4o", "response_id": "resp_...",
  "status": "completed", "domain": "in_domain"
}
```

Response — out-of-domain decline (agent never called):
```json
{
  "answer": "I'm the FlexLife Navigator, so I can only help with FlexLife products, riders, and approved wording. Ask me anything about that.",
  "citations": [], "agent": "KnowledgeBase", "model": null,
  "response_id": null, "status": "declined", "domain": "out_of_domain"
}
```

### 6.2 Frontend

File: `ui/learn.html`. Surface: `navSend` (`ui/learn.html:1053-1058`), used by both the full-page Ask Navigator (`renderChat`, `ui/learn.html:1272-1292`) and the pull-up sheet (`openNavigator`, `ui/learn.html:1246-1267`).

- **Surface the `domain` flag in `navSend`** (still posting to `/v1/foundry/chat`):
  ```js
  const navSend = async (message, history) => {
    const d = await api("/v1/foundry/chat", { method: "POST",
      body: JSON.stringify({ message: buildPreamble() + message, history }) });
    return { text: d.answer || "The sources don't cover that.",
             citations: d.citations || [], domain: d.domain || "in_domain" };
  };
  ```
  `mountChat` merges the returned object onto the pending message (`ui/learn.html:1183`), so `domain` is available per bubble with no widget-shape change. A decline arrives as a normal `answer` bubble with empty `citations`.
- **Optional decline styling.** When `domain === "out_of_domain"`, the widget can suppress the thumbs/copy/sources affordances (`ui/learn.html:1163-1167`) since there is nothing to rate or cite. Not required for M02.
- **Optional preamble hardening (defense-in-depth).** Add a single domain-scoping sentence to `buildPreamble()` (`ui/learn.html:1030-1045`) so the hosted agent also sees a scoping instruction. Secondary to the server gate; not the mechanism M02 relies on.
- **Sizing.** The UI caps the input at `maxlength="1000"` (`ui/learn.html:1152`) and slices history to the last 8 turns (`ui/learn.html:1180`); `FoundryChatRequest` allows `message` up to 4000 and `history` up to 20, so `buildPreamble() + message` and the sliced history fit comfortably.

## 7. Acceptance Criteria

- **AC1 (submit NL question / synthesize NL response).** Given the Ask Navigator chat is open, When the user sends "How does FlexLife protect against market losses?", Then a natural-language answer bubble appears from the hosted agent with `domain: "in_domain"`. *(→ "submit NL question", "synthesizes a direct NL response")*
- **AC2 (no structure knowledge required).** Given the user names no document, category, or file, When they ask any in-domain question, Then the hosted agent retrieves and answers without the user specifying where the info lives. *(→ "user not required to identify source doc/type/category/structure")*
- **AC3 (grounded, agent-side).** Given a question the hosted KB covers, When the answer is produced, Then it comes back with `citations` referencing KB sources. (Grounding is enforced by the agent; this repo relays it.) *(→ "responses grounded in FlexLife knowledge")*
- **AC4 (follow-up context).** Given a prior turn established FlexLife caps, When the user then asks "and the floor?", Then history is replayed to the agent (`foundry.py:174-182`) and the reply resolves the elision. *(→ "uses conversational context for follow-ups")*
- **AC5 (explanations/summaries/comparisons).** Given a request like "Summarize FlexLife's living benefits" or "Compare the term and IUL options," When the KB supports it, Then a synthesized explanation/summary/comparison is returned (not just a single fact). *(→ "supports explanations/summaries/comparisons")*
- **AC6 (out-of-domain rejection — core).** Given the user asks "Write me a poem about the ocean" or "What's the capital of France?", When the turn is processed, Then the response has `domain: "out_of_domain"`, `citations: []`, a short decline that redirects to FlexLife, and **the hosted agent is not called** — the app does NOT answer the off-topic request. *(→ "clearly unrelated requests are not treated as general conversation")*
- **AC7 (fail-open).** Given the classifier call errors or returns garbage, When the turn is processed, Then it defaults to `in_domain` and proxies to the agent, so a legitimate FlexLife question is never blocked by a classifier hiccup.
- **AC8 (POC interface, no prod conversation mgmt).** Given the app is running, When the demo is performed, Then all of the above is reachable through the Ask Navigator UI (full-page and pull-up sheet) with only `localStorage` thread persistence and no server-side history store. *(→ "demonstrable via a simple POC chat interface without production-grade conversation management")*

## 8. Test Plan

### Unit (pytest, no network — mock `AzureOpenAIClient.post` and `foundry.chat`)
- `classify_domain` returns `OUT_OF_DOMAIN` for off-topic prompts and `IN_DOMAIN` for FlexLife prompts and continuing greetings; parse failure / garbage / raised exception → defaults to `IN_DOMAIN` (fail-open, AC7).
- `service.chat_foundry` short-circuits on `OUT_OF_DOMAIN`: returns the decline shape with empty `citations`, `status == "declined"`, `domain == "out_of_domain"`, and does **not** call `foundry.chat` (assert the `foundry.chat` mock is not invoked — FR7/AC6).
- `service.chat_foundry` in-domain path calls `foundry.chat`, tags the result `domain == "in_domain"`, and passes `citations` through unchanged.

### Integration (FastAPI `TestClient`, classifier + `foundry.chat` mocked; Foundry configured)
- `POST /v1/foundry/chat` with an off-topic message → 200, body validates against updated `FoundryChatResponse`, `domain == "out_of_domain"`, `citations == []`.
- `POST /v1/foundry/chat` with a FlexLife message → 200, `answer` non-empty, `domain == "in_domain"`, citations relayed.
- History round-trip: a two-turn history (≤20) is accepted and threaded into `foundry.chat`; the request has no `limit` field.
- When Foundry is unconfigured → still 503 via `foundry_configured` (`server.py:472-476`), unchanged.

### Manual / demo (rebuild the Docker image per the dev loop, then `/app/learn.html`)
- Ask a factual question, a summary, and a comparison; confirm grounded NL answers and citation footnotes.
- Ask a follow-up with an elision ("and the floor?") and confirm context resolution via history replay.
- Ask two clearly off-topic questions; confirm a short decline, `domain: "out_of_domain"`, and no agent round-trip (browser Network tab shows no upstream latency / a fast decline).
- Confirm the pull-up sheet and full-page `#/chat` both hit `/v1/foundry/chat` (Network tab, or verify markup via jsdom per the memory note since the browser extension is on another machine).

## 9. Out of Scope

- **The hosted agent's own behavior:** its server-side retrieval, grounding quality, similarity thresholds, max-sources, and its Azure-side instructions. These are Azure config, out of this repo. M02 can post-process what the agent returns and prepend instructions, nothing deeper.
- Citation *display*, source links, and the "insufficient support → refer to NLG" abstention — **M03 / M10**. On the Foundry path, "insufficient support" now means the agent returned **zero citations**; turning that into an NLG abstention is M03's concern, not M02's.
- Advanced follow-up handling (query rewriting, coreference, agent-returned `follow_ups` chips) — **M04**. M02 keeps the current history-replay behavior only.
- Production-scale / server-side conversation storage; the UI keeps `localStorage` threads only.
- Advanced auth, personalization, per-user memory (the `buildPreamble` prefs/memories at `ui/learn.html:1030-1045` are an existing UI convenience, not part of M02).
- Production-grade moderation or a comprehensive topic taxonomy; the domain gate is a lightweight binary router, deliberately narrow.
- Any change to `/v1/chat`, `/v1/search`, `/v1/answer`, `/v1/fact-check`, the Coach console, or the in-call roleplay Ask sheet.

## 10. Dependencies & Open Questions

**Dependencies**
- Hosted Foundry agent must be configured (`FOUNDRY_PROJECT_ENDPOINT`, `FOUNDRY_API_KEY`; `foundry_configured`, `foundry.py:35-36`) and reachable; its Azure AI Search KB must be populated (out-of-repo prerequisite).
- Azure OpenAI chat deployment must be configured (`config.py:110-111`) for the domain classifier — one extra cheap call per turn, on top of the Foundry round-trip.
- Docker image rebuild required to ship backend changes (per the `nlg-rag dev loop` memory note).

**Expectation-setting on grounding.** Because retrieval and grounding are inside the hosted agent, M02 cannot tune or guarantee grounding fidelity from this repo. If answers are weakly grounded or over-broad, the fix is agent-side Azure configuration (KB scope, agent instructions, retrieval settings), tracked separately — not an M02 code change.

**M03 boundary (Citations).** M02 relays the agent's `citations` in the payload but does not design their display, linking, or the zero-citation → NLG abstention — those are M03.

**M04 boundary (Follow-up Questions).** M02 keeps history replay only (`foundry.py:174-182`). Query rewriting, coreference, and agent-driven follow-up chips are M04. (The Foundry response has no `follow_ups`, so chips fall back to `NAV_CHIPS` today.)

**Open questions**
1. **Decline wording.** Confirm the exact off-domain decline copy with the product owner; the draft above is a placeholder.
2. **Preamble hardening vs. Azure instructions.** Should we also add a domain-scoping line to `buildPreamble()` and/or ask the Azure agent owner to tighten the hosted agent's instructions? Recommended as defense-in-depth, but the server gate is the mechanism M02 relies on and tests.
3. **Cost/latency.** One extra classifier call per turn is acceptable for a POC and *saves* a Foundry round-trip on off-domain turns. If the added latency on in-domain turns matters, the classifier could later be swapped for a cheaper heuristic; not needed for M02.
4. **Classifier borderline cases.** Continuing greetings and terse follow-ups should count as in-domain; validate the prompt against a small labeled set and keep the fail-open default.

## 11. Rough Effort Estimate

- Backend: `classify_domain` (`embeddings.py`), `service.chat_foundry` wrapper, endpoint rewire, `FoundryChatResponse.domain` field: ~0.5–1 day.
- Frontend: surface `domain` in `navSend`, optional decline styling + optional preamble line: ~0.25–0.5 day.
- Tests (unit + integration): ~0.5 day.
- Manual demo, Docker rebuild, tuning the classifier prompt / decline wording: ~0.5 day.

**Total: ~1.75–2.5 developer-days.** No UI backend-rewire is needed (the UI already targets Foundry); the work is the in-repo domain gate plus the `domain` signal end to end.
