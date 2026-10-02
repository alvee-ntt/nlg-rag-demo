# M09 — Handoff to NLG Support

## 1. Summary

When the Agent Navigator cannot reliably answer a question from the Knowledge
Foundation, or when the question needs an authoritative, case-specific decision
from NLG, the app offers to **prepare a draft support email on the user's
behalf**. The draft is written from the *user's* point of view, summarizes the
question and the context already exchanged in the chat, and states exactly what
clarification is being requested from NLG Support. The user reviews and can edit
the draft; a **simulated** "send" confirms the request without any real email,
ticket, or CRM integration.

This is a thin flow layered on top of the existing Ask Navigator chat. The
product owner has confirmed that the end-user Ask Navigator chat runs on the
**hosted Azure AI Foundry agent** (`POST /v1/foundry/chat`), **not** the local
`/v1/chat`. M09 is largely engine-agnostic — it builds the draft from the chat
**log/history**, so the design does not depend on which engine answered — but its
two engine-facing seams are pinned to the Foundry path: the escalation trigger it
consumes and the history/turn shape it receives.

M09 adds one backend endpoint (`POST /v1/handoff/draft`), one service function,
one LLM prompt, one config value (the NLG support address), and an editable draft
sheet in `ui/learn.html`. It **consumes** an abstention/escalation signal that
milestone **M03 (Citations)** owns — M09 does not re-decide whether the corpus
was sufficient. M03 now defines that signal on the **Foundry** chat response: when
the agent returns **zero citations**, the response carries `escalate: true` +
`escalate_reason` (`insufficient_support`). M09 fires the handoff flow when that
flag is set, or when the user taps an always-available "Prepare a request to NLG
Support" action.

## 2. User Story (verbatim)

**User Story: Handoff to NLG Support** — As a FlexLife sales agent or support
user, I want the application to prepare a support request when it cannot reliably
answer my question, so that I can efficiently escalate the issue to NLG Support
without having to reconstruct the conversation myself.

Description: A handoff experience for questions that can't be adequately answered
from the Knowledge Foundation OR that need an authoritative, case-specific
decision from NLG. When escalation is appropriate, use relevant info from the
current conversation to prepare a DRAFT support email written from the USER's
perspective. The draft summarizes the question, relevant context already
provided, and the specific clarification/assistance being requested from NLG
Support. The user can review and EDIT the draft before any further action. For
the POC the app does NOT actually send the email — a simulated experience (show
the draft, let the user edit or proceed) is sufficient.

Acceptance criteria (source): recognize when a question should be referred to
NLG Support because (a) the Knowledge Foundation lacks sufficient support, or (b)
the request needs an authoritative/case-specific decision; generate a draft
support email from conversation context; draft written from user's perspective;
draft summarizes the question + relevant context already provided; draft clearly
identifies what clarification/assistance is requested; user can review before any
send action is represented; user can edit the draft; app does NOT auto-send; a
simulated "review, edit, or send" experience suffices; no real
email/ticketing/support-system integration required.

## 3. Current State — what exists

There is **currently NO handoff, escalation, or draft-email feature** anywhere in
the codebase. A grep of `ui/learn.html` for `handoff | NLG Support | escalat |
support request` returns no matches, and there is no support-related route in
`src/rag_layer/server.py`. Everything below is reusable plumbing that M09 builds
on; none of it makes an escalation decision or drafts anything today.

### End-user chat: the Foundry path (backend)

The end-user Ask Navigator chat runs on the **hosted Foundry agent**, not the
local RAG chat:

- `POST /v1/foundry/chat` → `foundry_chat_endpoint(payload: FoundryChatRequest,
  request)` — `src/rag_layer/server.py:467-484`. It guards on
  `foundry_configured` (503 if unset), then delegates to `foundry.chat(...)`,
  passing `history=[t.model_dump() for t in payload.history]` (`server.py:481`),
  and re-raises provider errors as `HTTPException(502, ...)` (`server.py:483-484`).
- `foundry.chat(*, settings, message, history)` —
  `src/rag_layer/foundry.py:169-191`. Replays the running conversation as context
  (`foundry.py:174-182`): each history turn is `{"role", "text"}` with
  `role ∈ {user, assistant}`, appended as `{"role", "content"}`, then the new
  `message`. The Foundry knowledge base grounds server-side. Returns
  `{"answer", "citations": [{"n","title","url"}], "agent", "model",
  "response_id", "status"}` (`foundry.py:184-191`) — **no `follow_ups` and no
  `sources` field**; grounding shows up only as `citations`.
- **The draft is built from the chat LOG (history), not from this response
  shape**, so M09 works regardless of engine. The one thing M09 needs from the
  Foundry response is M03's escalation flag (see §10).

### Draft-generation plumbing (backend, engine-agnostic)

The draft itself is a one-shot completion through the **local** LLM helper,
independent of which chat engine answered:

- `_generate(client, settings, prompt)` — `embeddings.py:100-116`. Single
  Azure OpenAI `/responses` completion from a plain string prompt. **The
  draft-email generator will call this.**
- `_history_text(history)` — `embeddings.py:145-152`. Renders a history list into
  `Agent: ...` / `Navigator: ...` lines. History turns are dicts of shape
  `{"role": "user"|"assistant", "text": str}` (roles mapped to Agent/Navigator
  here) — **exactly the turn shape the Foundry chat log holds and the shape
  `POST /v1/handoff/draft` receives.** M09's draft prompt reuses this verbatim.
- `_context_text(contexts)` — `embeddings.py:92-97`. Renders retrieved chunks
  with citations; optional input for the draft (to note what was/wasn't found).
- `_parse_json_object(text)` — `embeddings.py:130-142`. Tolerant JSON parse used
  by `chat_with_context` (`embeddings.py:189-196`); the draft prompt reuses it.

### Pydantic models + endpoint pattern (backend)

- `ChatTurn` — `server.py:82-84`: `role: Literal["user","assistant"]`,
  `text: str (max_length=4000)`. **M09's `HandoffDraftRequest` reuses this model,
  and it is the same turn shape the Foundry chat carries.**
- `FoundryChatRequest` — `server.py:93-95`: `message` (1..4000), `history:
  list[ChatTurn] (max_length=20)`. (No `limit` — retrieval is server-side.)
- `FoundryChatResponse` — `server.py:104-110`: `answer`, `citations:
  list[FoundryCitation]` (`n`, `title`, `url` — `server.py:98-101`), `agent`,
  `model?`, `response_id?`, `status?`. **This is where M03 adds `escalate: bool` +
  `escalate_reason` (see §10); M09 consumes them.**
- The local `POST /v1/chat` / `ChatResponse` (`server.py:441-453`, `152-154`)
  still exists but is **not** the end-user Ask Navigator path; M09 does not build
  on it.
- The endpoint pattern to copy for M09's drafting route: try/except wrapping the
  service call and re-raising as `HTTPException(500, ...)` — as
  `chat_endpoint` does (`server.py:452-453`).

### Config (backend)

- `Settings` dataclass — `src/rag_layer/config.py:13-64`; `load_settings()`
  reads env with `os.getenv(...)` defaults — `config.py:92-132`. **The NLG
  support address will be added here** as an env-driven field with a default.

### Ask Navigator chat UI (frontend, `ui/learn.html`)

- `mountChat(root, opts)` — `learn.html:1149-1241`. The single reusable chat
  widget. It owns the message array `opts.log`, where each message is a plain
  object `{ who: "user"|"ai", text, sources?, citations?, follow_ups?, vote?,
  open?, pending?, failed?, question? }`. The action row under each AI answer
  (Copy / Sources / Shorter / More detail / Regenerate / Edit / thumbs) is built
  at `learn.html:1162-1167` — **this is where a "Prepare a request to NLG
  Support" action attaches.**
- `send(q)` — `learn.html:1177-1186`. Builds `history` as the last **8** finished
  turns mapped to `{ role: who==="user" ? "user":"assistant", text }`
  (`learn.html:1180`, `.slice(-8)`) — the exact `history` array M09 will POST to
  `/v1/handoff/draft`, and the same `{role,text}` shape the Foundry chat and
  `_history_text` consume. It then does `Object.assign(pending, await
  opts.send(q, history))` (`learn.html:1183`), so **every field the turn function
  returns is merged onto the log message** — this is how M03's `escalate` /
  `escalate_reason` reach the log for M09's auto-affordance.
- `navSend(message, history)` — `learn.html:1053-1058`. The Ask Navigator turn
  function used by both the full-page chat (`renderChat`, `learn.html:1272-1292`)
  and the pull-up sheet (`openNavigator`, `learn.html:1246-1267`). It calls
  `POST /v1/foundry/chat` (the hosted Foundry agent) and today returns
  `{ text, citations }` (`learn.html:1057`) — **it drops the other response
  fields**, so for M03's `escalate`/`escalate_reason` to survive, `navSend` must
  also pass them through (`return { text, citations, escalate: d.escalate,
  escalate_reason: d.escalate_reason }`). That is an M03-side change M09 depends
  on. The separate in-call Ask sheet (`openAsk`, `learn.html:2766-2783`) calls
  `/v1/roleplay/sessions/{id}/ask` returning `{ answer, sources }`. Because M09
  drafts from the **chat log (history)**, not from any one backend's response
  shape, it works regardless of which chat produced the answers.
- Surfaces that host the chat widget: full page `renderChat()`
  (`learn.html:1272-1292`), pull-up sheet `openNavigator()`
  (`learn.html:1246-1267`), in-call sheet `openAsk()` (`learn.html:2766-2783`).
- Reusable UI helpers: `api(path, opts)` — `learn.html:865-873` (JSON fetch,
  same-origin, 401→login, throws on !ok); `confirmSheet({title,text,...})`
  modal pattern — `learn.html:2740-2747`; `toast` / `toastAction` —
  `learn.html:845-853`; `store.get/set` (localStorage, `salesdj.` prefix) —
  `learn.html:841-844`; `esc(...)` HTML escaper; icon set `I`; `NAV_CHIPS`
  suggestion chips — `learn.html:1046-1050`.

### Verified behavior (live test — 2026-09-30)

Run against the live `/v1/foundry/chat` endpoint on the running container (signed
in as the demo user):

- **❌ No handoff exists.** A question that is the ideal escalation trigger — "My
  client is a 47-year-old smoker with type 2 diabetes and BMI 34; will he be
  approved for FlexLife and at what exact rate class?" — produced a full guidance
  answer with citations. There was **no escalation, no NLG-support referral, and
  no draft email**. The entire handoff flow is new work.
- **Plumbing to draft from is available.** The chat conversation is present as
  `history` (`{role, text}` turns) plus the current message, which is exactly what
  `POST /v1/handoff/draft` needs to synthesize a draft. The draft generation
  itself runs through the local `_generate()` helper and is independent of the
  Foundry chat engine.
- **Escalation trigger confirmed feasible:** per the M03 live test, the Foundry
  response carries `citations`; "zero citations" is the practical `escalate`
  signal M09 will consume (M03 adds `escalate`/`escalate_reason` to the Foundry
  response). Until that lands, M09 ships via the always-on manual "Prepare a
  request to NLG Support" action.

## 4. Scope

**In scope**

- A backend endpoint that turns a chat conversation + the triggering question
  into a structured draft support email (to / subject / body) written from the
  user's perspective.
- A service function + LLM prompt that generate the draft from conversation
  history (and, optionally, the retrieved-context summary).
- A config value for the NLG Support email address.
- A frontend affordance in Ask Navigator that appears (a) automatically when a
  reply carries an abstention/escalation signal from M03, and (b) always via a
  manual "Prepare a request to NLG Support" action.
- An editable draft sheet: the user reviews all three fields, edits any of them,
  and taps a simulated "Send to NLG Support" that shows a confirmation **without
  actually sending**.

**Out of scope** — see §9.

## 5. Functional Requirements

- **FR1 — Escalation trigger (a): insufficient support.** When a Foundry chat
  reply carries M03's escalation flag — `escalate: true` with `escalate_reason:
  "insufficient_support"`, set when the agent returns **zero citations** (see
  §10) — the app surfaces a "Prepare a request to NLG Support" affordance directly
  under that answer. `mountChat` has copied the flag onto the log message
  (`m.escalate` / `m.escalateReason`) via the `Object.assign` at
  `learn.html:1183`.
- **FR2 — Escalation trigger (b): case-specific decision.** When a reply is
  flagged as needing an authoritative/case-specific decision from NLG (see §10
  for how this is marked), the same affordance surfaces, worded to reflect a
  decision request rather than a knowledge gap. The Foundry zero-citation signal
  does not by itself distinguish this case; for the POC it is reached via the
  manual action (FR3), with automatic `case_specific` classification deferred to
  M03 (see §10, Open Q1).
- **FR3 — Manual escalation.** Independent of any signal, the user can always
  invoke the handoff — a "Prepare a request to NLG Support" action available in
  the answer action row (and/or the chat overflow menu) — so a user who is
  simply unsatisfied can escalate.
- **FR4 — Draft from conversation context.** On invocation, the app sends the
  current chat history + the triggering question to the backend and receives a
  draft email. The draft **must** be built from the conversation, not composed
  by the user from scratch.
- **FR5 — User's perspective.** The draft body is written in the first person as
  the requesting user (e.g. "I was helping a client and needed to confirm…"),
  addressed to NLG Support. It must not read as if the assistant is writing about
  the user.
- **FR6 — Summarize question + context provided.** The body summarizes (i) the
  specific question that could not be answered, and (ii) the relevant context
  already established in the conversation (product, scenario details, what the
  app was and wasn't able to confirm).
- **FR7 — State the ask.** The body clearly identifies the specific
  clarification or assistance being requested from NLG Support (a distinct,
  scannable statement or closing sentence).
- **FR8 — Prefilled recipient + subject.** The draft includes a `to` (the
  configured NLG Support address) and a concise `subject` summarizing the topic.
- **FR9 — Review before any send.** The full draft (to / subject / body) is
  shown for review before any action that represents sending.
- **FR10 — Editable.** The user can edit all three fields (to, subject, body)
  in place before proceeding. Edits are what a "send" would carry.
- **FR11 — No auto-send; simulated send only.** The app never sends email and
  never contacts a real support system. The "Send to NLG Support" button is
  simulated: it closes the draft and shows a confirmation (toast/inline note)
  such as "Draft ready to send to NLG Support (simulated — nothing was sent)."
- **FR12 — Cancel / keep chatting.** The user can dismiss the draft sheet and
  return to the conversation with no side effects.
- **FR13 — Graceful failure.** If draft generation fails, show a toast and leave
  the conversation intact (mirror the existing chat failure handling at
  `learn.html:1184`).

## 6. Technical Design

### 6.1 Backend — new endpoint `POST /v1/handoff/draft`

Add to `src/rag_layer/server.py` next to the chat endpoint (after
`server.py:453`), reusing the `ChatTurn` model (`server.py:82-84`).

**Request model** (add near the other request models, ~`server.py:90`):

```python
class HandoffDraftRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=12)
    # Why the handoff was triggered; steers wording. "insufficient" = corpus gap,
    # "case_specific" = needs an authoritative NLG decision, "manual" = user asked
    # to escalate. The UI maps M03's Foundry-response value
    # `escalate_reason: "insufficient_support"` -> "insufficient" here; the manual
    # action sends "manual".
    reason: Literal["insufficient", "case_specific", "manual"] = "manual"
    limit: int = Field(default=6, ge=1, le=20)
```

**Response model:**

```python
class HandoffDraftResponse(BaseModel):
    to: str            # configured NLG Support address
    subject: str       # concise topic line
    body: str          # first-person draft addressed to NLG Support
    reason: str        # echoes the trigger, so the UI can label the sheet
```

**Endpoint** (mirrors `chat_endpoint`, `server.py:441-453`):

```python
@app.post("/v1/handoff/draft", response_model=HandoffDraftResponse)
def handoff_draft_endpoint(payload: HandoffDraftRequest, request: Request) -> dict[str, Any]:
    """Prepare (not send) a draft NLG Support email from the current conversation."""
    try:
        return draft_support_email(
            settings=request.app.state.settings,
            client=request.app.state.openai_client,
            question=payload.question.strip(),
            history=[t.model_dump() for t in payload.history],
            reason=payload.reason,
            limit=payload.limit,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
```

Register `draft_support_email` in the service import block
(`server.py:56-73`, alongside `chat`).

**Sample request JSON:**

```json
{
  "question": "For a 58-year-old with a lapsed FlexLife policy, can premiums be back-dated on reinstatement?",
  "reason": "case_specific",
  "history": [
    {"role": "user", "text": "Does FlexLife allow reinstatement after a lapse?"},
    {"role": "assistant", "text": "Yes — within the reinstatement window, with evidence of insurability. The source library doesn't spell out premium back-dating rules."},
    {"role": "user", "text": "It's a 58-year-old, policy lapsed 5 months ago. Can premiums be back-dated?"}
  ]
}
```

**Sample response JSON:**

```json
{
  "to": "flexlife-support@nlgic.example.com",
  "subject": "FlexLife reinstatement: premium back-dating for a lapsed policy",
  "reason": "case_specific",
  "body": "Hello NLG Support,\n\nI'm a FlexLife sales agent and I need an authoritative decision on a specific case that the Agent Navigator couldn't confirm.\n\nMy question: For a client aged 58 whose FlexLife policy lapsed about 5 months ago, can premiums be back-dated when the policy is reinstated?\n\nContext I've already established: The Navigator confirmed that FlexLife allows reinstatement within the reinstatement window with evidence of insurability, but it noted the source library does not spell out the rules for back-dating premiums on reinstatement, and this depends on the client's specific age and lapse timing.\n\nWhat I need from you: Please confirm whether premiums can be back-dated for this case, and if so, the amount owed and any conditions. If a form or underwriting step is required, please tell me what to send.\n\nThank you,\n[Your name]"
}
```

### 6.2 Backend — service function `draft_support_email`

Add to `src/rag_layer/service.py` (alongside `chat`, `service.py:76-93`):

```python
def draft_support_email(
    *,
    settings: Settings,
    client: AzureOpenAIClient,
    question: str,
    history: list[dict[str, Any]],
    reason: str,
    limit: int,
) -> dict[str, Any]:
    """Build a draft NLG Support email from the conversation. Does not send."""
    # Optional: retrieve to summarize what the corpus did/didn't cover, reusing
    # the same retrieval as chat() (service.py:89-91).
    contexts = retrieve_contexts(settings=settings, client=client, text=question, limit=limit)
    draft = generate_support_email(client, settings, question, history, contexts, reason)
    return {
        "to": settings.nlg_support_email,
        "subject": draft["subject"],
        "body": draft["body"],
        "reason": reason,
    }
```

`retrieve_contexts` already exists (`service.py:24-33`). Retrieval is optional —
its only role is to let the prompt state "what the app could and could not
confirm"; if kept lightweight, it can be skipped and the prompt built from
history alone.

### 6.3 Backend — LLM prompt `generate_support_email` (embeddings.py)

Add to `src/rag_layer/embeddings.py`, reusing `_generate` (`embeddings.py:100-116`),
`_history_text` (`embeddings.py:145-152`), `_context_text`
(`embeddings.py:92-97`), and `_parse_json_object` (`embeddings.py:130-142`) —
the same JSON-return pattern as `chat_with_context` (`embeddings.py:155-196`).

**Prompt strategy:**

- Role framing: "You are drafting an email **on behalf of** a FlexLife sales
  agent to NLG Support. Write in the first person **as the agent** — never refer
  to the agent in the third person, and never claim to be an AI assistant."
- Give the model the full conversation via `_history_text(history)`, the
  triggering `question`, an optional corpus summary via `_context_text(contexts)`
  (framed as "what the app was able to find, for you to reference as what could
  not be confirmed"), and the `reason`.
- Reason-conditioned framing:
  - `insufficient` → "The app could not find this in the Knowledge Foundation."
  - `case_specific` → "This needs an authoritative, case-specific decision from
    NLG."
  - `manual` → "The agent chose to escalate this to NLG Support."
- Require a body with three clear beats: (1) the question, (2) the relevant
  context already provided in the conversation, (3) the specific
  clarification/assistance requested (FR6, FR7). Keep it professional, concise,
  no invented facts, no promises/guarantees (consistent with
  `embeddings.py:172`).
- Ask for JSON only: `{"subject": "...", "body": "..."}`. Parse with
  `_parse_json_object`; on parse failure, fall back to using the raw text as the
  body and a generic subject (mirroring `embeddings.py:190-196`).

```python
def generate_support_email(
    client: AzureOpenAIClient,
    settings: Settings,
    question: str,
    history: list[dict],
    contexts: list[dict],
    reason: str,
) -> dict:
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
    except Exception:  # noqa: BLE001
        subject, body = "FlexLife question for NLG Support", raw.strip()
    return {"subject": subject, "body": body or "Please see my question above."}
```

### 6.4 Config — NLG Support address

Add to `Settings` (`config.py:13-64`) and `load_settings()` (`config.py:92-132`),
following the existing `os.getenv(NAME, default)` pattern:

```python
# config.py — Settings dataclass (with the other defaulted fields, ~line 62)
nlg_support_email: str = "flexlife-support@nlgic.example.com"

# config.py — load_settings()
nlg_support_email=os.getenv("NLG_SUPPORT_EMAIL", "flexlife-support@nlgic.example.com"),
```

(A placeholder address is fine for the POC since nothing is actually sent.)

### 6.5 Frontend — Ask Navigator UX (`ui/learn.html`)

**Where the affordance appears.** Add a handoff action to the AI-answer action
row built in `mountChat` at `learn.html:1162-1167`:

- **Automatic (FR1/FR2):** if the answer message carries an escalation flag
  (`m.escalate` — copied from the Foundry response by `navSend` + the
  `Object.assign` at `learn.html:1183`; see §10), render a prominent
  "Prepare a request to NLG Support" button in that row. Wording adapts to
  `m.escalateReason` ("case_specific" → "Ask NLG for a decision"). When invoking
  `openHandoff`, map the M03 value `"insufficient_support"` → `reason:
  "insufficient"` for the endpoint.
- **Manual (FR3):** always render a lower-key "Ask NLG Support" action in the
  same row (like the existing `Copy` / `Sources` actions), so any answer can be
  escalated even when unflagged.

Both call a new `openHandoff({ question, log, reason })`.

**Draft generation + sheet.** `openHandoff` builds the history exactly as
`send()` does (`learn.html:1180`) — last ~8 finished turns mapped to
`{ role, text }` — and the triggering question (the user turn that preceded the
flagged answer, or the latest user turn for the manual case):

```js
async function openHandoff({ question, log, reason = "manual" }) {
  const history = log.filter(m => !m.pending && !m.failed)
    .slice(-8).map(m => ({ role: m.who === "user" ? "user" : "assistant", text: m.text }));
  // Loading sheet first, then fetch (mirrors the pending-bubble pattern).
  let draft;
  try {
    draft = await api("/v1/handoff/draft", { method: "POST",
      body: JSON.stringify({ question, history, reason }) });
  } catch (err) { toast(err.message || "Couldn't prepare the request"); return; }
  showDraftSheet(draft);   // editable review sheet
}
```

**Editable review sheet (`showDraftSheet`)** — a `sheet-wrap` overlay in the
style of `openNavigator` (`learn.html:1246-1267`) / `confirmSheet`
(`learn.html:2740-2747`):

- Header: "Request to NLG Support" + a note stating this is a draft that will
  **not** actually be sent.
- Editable `To` (`<input>`, prefilled `draft.to`), editable `Subject`
  (`<input>`, prefilled `draft.subject`), editable `Body` (`<textarea>`,
  prefilled `draft.body`) — satisfies FR9/FR10.
- Footer buttons: **Cancel** (removes the sheet, FR12) and **Send to NLG
  Support** (simulated).
- Simulated send (FR11): on tap, read the current field values, remove the
  sheet, and `toast("Draft ready to send to NLG Support — simulated, nothing
  was sent.")`. Optionally append a small AI note into the chat log ("I've
  prepared your request to NLG Support.") for continuity. **No network call is
  made on send.**

The sheet reuses `esc`, `toast`, and the existing `.sheet` / `.modal` / `.btn`
styles already in `learn.html`.

**Data flow summary:** chat log (history) → `POST /v1/handoff/draft` →
`draft_support_email` → `generate_support_email` (`_generate`) → `{to, subject,
body, reason}` → editable sheet → simulated send (client-only).

## 7. Acceptance Criteria (Given/When/Then)

- **AC1 (trigger a — insufficient; story ACs "recognize… (a)…", "user can
  review before send").** *Given* a Foundry chat answer whose response carries
  M03's `escalate: true` / `escalate_reason: "insufficient_support"` (zero
  citations), *When* it renders, *Then* a "Prepare a request to NLG Support"
  affordance appears under that answer, and requesting a draft sends `reason:
  "insufficient"`.
- **AC2 (trigger b — case-specific; story AC "(b) authoritative/case-specific
  decision").** *Given* an answer flagged as needing a case-specific NLG
  decision, *When* it renders, *Then* the affordance appears worded as a
  decision request, and the resulting draft's `reason` is `case_specific`.
- **AC3 (manual escalation; story "user can escalate").** *Given* any answer,
  *When* the user taps the always-available "Ask NLG Support" action, *Then* a
  draft is generated with `reason: "manual"`.
- **AC4 (draft from context; story ACs "generate… from conversation context",
  "summarizes the question + relevant context already provided").** *Given* a
  conversation with prior turns, *When* a draft is requested, *Then*
  `POST /v1/handoff/draft` returns a body that references the specific question
  and the relevant context already exchanged, and the user did not have to
  retype it.
- **AC5 (user's perspective; story AC "written from user's perspective").*
  *Given* a generated draft, *When* the body is inspected, *Then* it is written
  in the first person as the user, addressed to NLG Support, and does not refer
  to the user in the third person.
- **AC6 (states the ask; story AC "clearly identifies what
  clarification/assistance is requested").** *Given* a generated draft, *When*
  the body is inspected, *Then* it contains a clear statement of the specific
  clarification/assistance being requested.
- **AC7 (recipient + subject).** *Given* a generated draft, *When* it is shown,
  *Then* `to` equals the configured `NLG_SUPPORT_EMAIL` and `subject` is a
  concise, topic-relevant line.
- **AC8 (review; story AC "user can review before any send action").** *Given* a
  draft, *When* the sheet opens, *Then* the full to/subject/body is visible
  before any send action is represented.
- **AC9 (edit; story AC "user can edit the draft").** *Given* the draft sheet,
  *When* the user edits to, subject, or body, *Then* the edited values are what a
  simulated send reflects.
- **AC10 (no auto-send + simulated; story ACs "app does NOT auto-send", "a
  simulated review/edit/send experience suffices").** *Given* the draft sheet,
  *When* the user taps "Send to NLG Support", *Then* no email/ticket/network
  send occurs, and a confirmation states the send was simulated.
- **AC11 (cancel).** *Given* the draft sheet, *When* the user cancels, *Then*
  the sheet closes and the conversation is unchanged.
- **AC12 (no real integration; story ACs "no real email/ticketing", out of
  scope).** *Given* the whole flow, *When* exercised end to end, *Then* the only
  network call is `POST /v1/handoff/draft` (drafting), and there is no call to
  any email/ticketing/CRM system.

## 8. Test Plan

**Backend (pytest, following existing service/endpoint tests):**

- `draft_support_email` returns `{to, subject, body, reason}`; `to` equals
  `settings.nlg_support_email`. Stub the Azure client so `_generate` returns a
  canned JSON.
- `generate_support_email` parses a well-formed JSON reply into subject/body,
  and falls back to raw-as-body on malformed JSON (mirror the
  `chat_with_context` fallback test, `embeddings.py:190-196`).
- Reason-conditioned prompt: assert the three `reason` values change the
  reason-note text in the prompt (inspect the string passed to `_generate`).
- Prompt uses `_history_text` so history turns appear as `Agent:` / `Navigator:`
  lines.
- `POST /v1/handoff/draft` (FastAPI `TestClient`): valid body → 200 with the
  response schema; empty `question` → 422; `history` over 12 turns → 422;
  provider error → 500 with `HTTPException` detail (matches `server.py:452-453`).

**Frontend (jsdom, per the dev-loop note — the browser is on another machine):**

- The handoff affordance renders in the AI action row for a flagged answer and
  the manual action renders for any answer.
- `openHandoff` posts the last ≤8 finished turns as `{role,text}` plus the
  question, and opens the sheet with the returned draft prefilled.
- Editing subject/body/to updates the fields; simulated send makes **no**
  network call (assert `fetch` is not called on send) and shows the simulated
  confirmation toast.
- Cancel removes the sheet with no chat mutation.
- Draft-fetch failure shows a toast and leaves the chat intact.

**Manual smoke:** rebuild the Docker image (code change per the dev-loop note),
open `/app/learn.html`, ask a question, trigger both the manual action and a
simulated M03-flagged answer, edit the draft, simulate send, confirm nothing is
sent.

## 9. Out of Scope

- Actually sending the email; SMTP/Graph/mailto integration.
- Email / CRM / ticketing / case-management integration.
- Tracking support-request status or a support inbox.
- Auto-resuming the conversation when NLG responds.
- Production-grade routing to specific NLG teams/queues.
- The **decision** of when the corpus is insufficient (owned by M03 — see §10).

## 10. Dependencies & Open Questions

**Dependencies**

- **M03 (Citations) — owns the abstention decision on the Foundry path.** M03
  decides "insufficient support → direct user to NLG support." M09 **consumes**
  that decision; it does not re-implement it. **Confirmed contract:** the Foundry
  chat response carries `escalate: bool` + `escalate_reason` (M03 sets
  `escalate: true`, `escalate_reason: "insufficient_support"` when the agent
  returns **zero citations**). M03 adds these two fields to `FoundryChatResponse`
  (`server.py:104-110`) and sets them in `foundry.chat` (`foundry.py:169-191`,
  where `citations` is already computed). On the client, `navSend`
  (`learn.html:1053-1058`) must pass the two fields through in its return object
  (today it returns only `{text, citations}`), and `mountChat`'s `Object.assign`
  (`learn.html:1183`) then copies them onto the log message (`m.escalate`,
  `m.escalateReason`). M09's auto-affordance (FR1/FR2) keys off `m.escalate`.
  Until M03 lands, M09 ships fully functional via the **manual** action (FR3), and
  the auto-trigger is wired to the flag the moment M03 provides it.
- **M02 (Natural Language Search) / the Foundry chat** provide the chat + history
  (`navSend` → `POST /v1/foundry/chat`, `mountChat` log) that the draft is built
  from. M09 reuses that `{role,text}` history verbatim.
- Azure OpenAI must be configured — the draft itself runs through the **local**
  `_generate`/Responses path (`embeddings.py:100-116`), independent of the Foundry
  chat engine.

**Open questions**

1. **What marks a "case-specific decision" (trigger b)?** M03's abstention likely
   covers the corpus-gap case cleanly. Detecting "needs an authoritative
   case-specific decision" is fuzzier — options: (a) M03 classifies it and sets
   `escalate_reason: "case_specific"`; (b) a lightweight heuristic/classifier in
   the draft path; (c) rely on the manual action for the POC and treat all
   auto-triggers as `insufficient`. **Recommendation for the POC:** ship the
   manual action + `insufficient` auto-trigger now; add `case_specific`
   classification with M03.
2. **Which chat backend feeds the Ask Navigator page? — RESOLVED.** The product
   owner confirmed the end-user chat is the hosted **Foundry** agent
   (`/v1/foundry/chat`, `learn.html:1053-1058`), not `/v1/chat`. The abstention
   signal therefore comes from the **Foundry response**: M03 sets `escalate` /
   `escalate_reason` on `FoundryChatResponse` when the agent returns zero
   citations. Remaining coordination is only mechanical: M03 adds the two fields
   and `navSend` passes them through (see Dependencies). M09 stays agnostic (it
   drafts from the log).
3. **User identity in the draft.** The POC has one shared demo login
   (`config.py:62-63`), so the draft signs off with `[Your name]`. Fine for the
   POC; a later story could inject the profile name.
4. **NLG Support address.** `NLG_SUPPORT_EMAIL` defaults to a placeholder since
   nothing is sent; confirm the real address before any real-send story.

## 11. Rough Effort Estimate

| Area | Work | Est. |
| --- | --- | --- |
| Config | `nlg_support_email` field + env | ~0.25 day |
| Backend | `generate_support_email` prompt + parse | ~0.5 day |
| Backend | `draft_support_email` service fn | ~0.25 day |
| Backend | `POST /v1/handoff/draft` + models | ~0.25 day |
| Frontend | affordance in action row (manual + flag-driven) | ~0.5 day |
| Frontend | `openHandoff` + editable draft sheet + simulated send | ~1 day |
| Tests | backend pytest + jsdom | ~0.75 day |
| Integration | M03 signal wiring (when M03 lands) | ~0.25 day |

**Total: ~3.5–4 days** (excluding the M03 abstention decision itself, which is
owned by M03). Manual-trigger MVP alone is ~2–2.5 days.
