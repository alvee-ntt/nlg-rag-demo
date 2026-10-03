# M04 — Follow-up Questions

## 1. Summary

M04 is **conversational continuity inside a single Ask Navigator chat session**:
the user can ask a follow-up ("and the floor?", "what about for a smoker?")
without restating the topic, the app interprets it against recent turns, and it
keeps grounding product facts in the FlexLife Knowledge Foundation rather than
drifting into a general chatbot.

The product owner has confirmed the end-user Ask Navigator chat runs on the
**hosted Azure AI Foundry agent** (`POST /v1/foundry/chat` →
`service.chat_foundry` → `foundry.chat`), **not** the local `/v1/chat`
pipeline. This matters for M04 because **follow-up continuity on the Foundry
path is already implemented and shipping**: `foundry.chat` replays the running
conversation to the hosted agent on every turn
(`src/rag_layer/foundry.py:347-361`), the frontend sends the last 8 finished
turns as `history` (`ui/learn.html:1189`), the M02 domain gate keeps clearly
unrelated turns from being answered (`service.chat_foundry`,
`src/rag_layer/service.py:187`), and the M03 abstention keeps factual answers
grounded (`foundry.py:388-396`). **This spec is therefore substantially
"already met — here is the proof", plus two genuine gaps.**

The two gaps are: (1) a **tension between M04's "tangential IUL concepts may be
answered" and M03's zero-citation abstention** — today any ungrounded reply
(including a legitimate general-IUL explanation the agent gives without a KB
citation) is replaced with the NLG-support message; and (2) **no follow-up
suggestion chips on the Foundry path** — the hosted agent returns no
`follow_ups`, so the chip row falls back to the static `NAV_CHIPS`
(`ui/learn.html:1182`, `learn.html:1039-1043`). Neither is strictly required by
M04's acceptance criteria, but both are worth resolving; the first is a product
decision, the second is a small optional enhancement. M04 adds **no new backend
endpoint** — its deliverables are a documented grounding/tangential-topic
decision and (optionally) follow-up chip generation.

## 2. User Story (verbatim)

**User Story: Support Follow-Up Questions** — As a FlexLife sales agent or
support user, I want to ask follow-up questions within the same conversation, so
that I can explore a topic naturally without repeatedly restating prior context.

Description: The POC will support conversational continuity within the active
chat session. Follow-up questions should use relevant context from the current
conversation so that users can refer to prior answers, concepts, or terms
without having to repeat them. The application should continue grounding factual
content in the FlexLife Knowledge Foundation. Conversation history provides
context for understanding the user's intent, but it should not replace the
Knowledge Foundation as the source of product facts or details. The conversation
may move between related FlexLife topics without requiring the user to start a
new thread. Questions that are tangentially related, such as general IUL
concepts that help explain FlexLife, may also be supported. For the POC,
sophisticated long-term conversation memory or automatic summarization of
lengthy conversations is not required.

Acceptance criteria (source):

- A user can ask a follow-up question without restating the full original topic.
- The application uses relevant context from the current chat session to
  interpret follow-up questions.
- Recent conversational context is sufficient for the POC; no specific
  requirement exists to preserve or summarize arbitrarily long conversations.
- Factual statements and product details in follow-up responses remain grounded
  in the FlexLife Knowledge Foundation.
- The conversation can transition between related FlexLife topics without
  requiring a new session.
- Questions tangentially related to FlexLife, such as general IUL concepts, may
  be answered when they support the user's understanding of FlexLife.
- Clearly unrelated requests are not treated as general-purpose conversation.
- The application does not rely on prior conversations or maintain a persistent
  user profile for this capability.

Out of scope: Cross-session conversational memory; persistent learner or user
profiles; long-term conversation summarization or compression; production-grade
conversation history management.

## 3. Current State — what exists

Most of M04 is already satisfied by shipped M02 + M03 + Foundry code. Verified
against the code below; each claim carries a file:line anchor.

### 3.1 Follow-up context via history replay (backend, Foundry path)

- **The end-user chat replays conversation history on every turn.**
  `foundry.chat(*, settings, question, history, ...)`
  (`src/rag_layer/foundry.py:317-404`) builds the provider request by walking
  `history` and mapping each `{role, text}` turn with
  `role ∈ {user, assistant}` and non-empty text into a Responses
  `{role, content}` message, then appends the new user message
  (`foundry.py:347-361`). The hosted agent therefore sees the running thread and
  resolves elisions/pronouns ("and the floor?", "what about for him?"). The
  endpoint is **stateless per call**, so this replay *is* the continuity
  mechanism. **This directly satisfies "uses relevant context to interpret
  follow-ups" and "ask a follow-up without restating".**
- **Recent-context-only, by construction.** The request model
  `FoundryChatRequest.history` is capped at `max_length=20`
  (`src/rag_layer/server.py:121`) and the frontend sends only the last 8
  finished turns (`ui/learn.html:1189`, `.slice(-8)`). There is no
  summarization or long-term memory — exactly M04's "recent context is
  sufficient; no requirement to preserve/summarize arbitrarily long
  conversations."
- **Local path parity (fallback only).** When Foundry is unavailable,
  `chat_foundry` falls back to the local `service.chat`
  (`src/rag_layer/service.py:212-237`), which also receives `history`
  (`service.py:124-139`) and even widens retrieval using the previous user
  message so short follow-ups land on the right chunks
  (`service.py:137-138`: `query = f"{prior_user[-1]}\n{message}"`). So follow-up
  continuity survives a Foundry outage.

### 3.2 Grounding stays with the Knowledge Foundation (backend)

- On the Foundry path the hosted agent grounds server-side against its Azure AI
  Search KB; the repo relays the synthesized answer + numbered citations via
  `_extract_answer` (`foundry.py:120-170`). History is used for *intent*, not as
  a substitute source of facts — the answer still comes with KB citations when
  grounded. **This satisfies "factual statements remain grounded in the KF;
  history provides context but does not replace the KF."**
- The M03 abstention (`foundry.py:388-396`) converts a zero-citation / empty
  reply into the shared `NLG_SUPPORT_MESSAGE` (`src/rag_layer/config.py:15-18`)
  with `escalate: true`, so an ungrounded answer is never surfaced as a
  substantive product answer. (See §3.5 for the tension this creates with
  tangential IUL topics.)

### 3.3 Not-a-general-chatbot (backend, from M02)

- `service.chat_foundry` runs the M02 domain gate first
  (`service.py:187`): `classify_domain(client, settings, message, history)`
  (`src/rag_layer/embeddings.py:155-184`). A clearly non-FlexLife turn is
  declined in-repo with `DOMAIN_DECLINE` (`service.py:29-32`,
  `service.py:188-200`) **without calling the agent**, tagged
  `domain: "out_of_domain"`. **This satisfies "clearly unrelated requests are
  not treated as general-purpose conversation."**
- Crucially for M04, the classifier prompt explicitly counts continuity as
  in-domain: *"Greetings and conversational follow-ups that continue a FlexLife
  thread count as IN_DOMAIN"* (`embeddings.py:171-172`), and it receives the
  `history` so a terse follow-up is judged in the thread's context
  (`embeddings.py:177`). So the gate does not accidentally block follow-ups.

### 3.4 Frontend — one persisted thread, history built per turn

- `mountChat` (`ui/learn.html:1157-1256`) owns the message log. `send(q)`
  (`learn.html:1186-1195`) builds `history` from the last 8 finished turns
  (`learn.html:1189`) and calls `opts.send(q, history)`; `Object.assign(pending,
  …)` (`learn.html:1192`) merges every returned field onto the log message.
- `navSend(message, history)` (`learn.html:1046-1063`) posts to
  `/v1/foundry/chat` with `history` and returns `{text, citations, sources,
  domain, escalate, escalate_reason}` (`learn.html:1062`). **Note it does not
  read `follow_ups`** — see the gap in §3.6.
- A single thread is persisted in `localStorage` and shared between the
  full-page Ask page (`renderChat`, `learn.html:1322-1342`) and the pull-up
  sheet (`openNavigator`, `learn.html:1296-1317`); threads live under the
  `salesdj.` prefix via `store` (`learn.html:850-853`). The conversation can
  freely move between FlexLife topics in one thread — no "new session" is
  required (**satisfies "transition between related topics without a new
  session"**). There is **no server-side conversation store and no persistent
  user profile tied to this capability** (**satisfies "does not rely on prior
  conversations / persistent profile"**).

### 3.5 The one real gap — tangential IUL vs. zero-citation abstention

M04 says *"questions tangentially related to FlexLife, such as general IUL
concepts, may be answered when they support the user's understanding."* Today:

- The M02 domain gate **allows** such a turn through — the classifier treats
  "life insurance" generally as in-domain (`embeddings.py:169-170`).
- But if the hosted agent answers a general-IUL question from its own knowledge
  **without a KB citation**, `foundry.chat`'s M03 abstention
  (`foundry.py:388-396`, `if not citations or non_answer:`) replaces that answer
  with the NLG-support message and raises `escalate`. So a legitimate,
  helpful general-IUL explanation that happens not to cite a corpus document is
  suppressed. **This is the central M04 design question** (see §5 FR6 and §10
  Open Q1). It is a *policy* conflict between "stay grounded / abstain" (M03,
  M07) and "tangential IUL may be answered" (M04), not a bug.

### 3.6 The second gap — no follow-up suggestion chips on Foundry

- `mountChat`'s `paintChips` (`learn.html:1180-1185`) will render tappable
  suggestion chips from `last.follow_ups` **when present**, else it falls back to
  the static `NAV_CHIPS` (`learn.html:1182`, `learn.html:1039-1043`).
- The Foundry response carries **no `follow_ups`** (`foundry.chat` returns
  `answer/citations/escalate/...` only, `foundry.py:390-404`) and `navSend` does
  not surface any (`learn.html:1062`). So today the chips are always the three
  static `NAV_CHIPS`, never context-aware next-question suggestions.
- The local `chat_with_context` **does** generate up to two `follow_ups`
  (`embeddings.py:209-233`), but that path only runs on the Foundry *fallback*,
  and `chat_foundry`'s fallback branch drops them
  (`service.py:225-237` returns no `follow_ups`). So even the fallback shows
  static chips. Context-aware follow-up *suggestions* are an **optional M04
  enhancement**, not an acceptance-criteria requirement (M04 requires
  *interpreting* follow-ups, which already works; it does not require suggesting
  them).

### 3.7 Verified behavior (per the M02 live test, 2026-09-30)

The M02 spec's live test against `/v1/foundry/chat` already demonstrates M04's
core: a prior turn established the "S&P 500 1% Floor" strategy, then *"And what
about the cap on that strategy?"* correctly resolved "that strategy" and
answered with citations (M02 spec §3, "Follow-up context works"). That is M04's
primary acceptance criterion passing on the shipping engine. (Re-verify on the
current build per §8; code changes require a Docker rebuild per the dev-loop
memory, and the browser extension is on another machine so UI is checked with
jsdom.)

## 4. Scope

**In scope**

- Document and prove that in-session follow-up continuity, recent-context
  interpretation, grounded follow-up answers, topic transitions, and
  not-a-general-chatbot are **met** by the current Foundry path (with anchors),
  so M04 can be signed off against the shipping engine.
- Resolve the **tangential-IUL vs. abstention** policy (FR6) — make an explicit,
  testable decision and, if the decision is to permit labeled general-IUL
  explanations, implement the minimal change (a bounded relaxation of the
  zero-citation abstention for in-domain "concept" questions, clearly marked as
  general, never for product-specific facts).
- Optional: **context-aware follow-up suggestion chips** on the Foundry path
  (generate 1–2 next-question suggestions and surface them as `follow_ups` so the
  existing `paintChips` renders them).

**Out of scope** — see §9.

## 5. Functional Requirements

- **FR1 — Interpret follow-ups from recent context.** A follow-up turn is
  answered using the recent conversation (the replayed `history`), resolving
  pronouns/elisions without the user restating the topic. (Met:
  `foundry.py:347-361`, `learn.html:1189`.)
- **FR2 — Recent context is sufficient; no summarization.** The app relies only
  on the last N turns (frontend 8, server cap 20) and performs no long-term
  memory or summarization. (Met: `server.py:121`, `learn.html:1189`.)
- **FR3 — Follow-up answers stay grounded.** Product facts in a follow-up answer
  come from the KF (hosted-agent grounding + citations); history is used only for
  intent, never as a fact source. (Met: `foundry.py:120-170`, `388-396`.)
- **FR4 — Topic transitions within one thread.** The user can move between
  related FlexLife topics in a single persisted thread without starting a new
  session. (Met: `learn.html:1296-1342`, single `localStorage` thread.)
- **FR5 — Not a general chatbot.** A clearly unrelated follow-up is declined by
  the M02 domain gate, not answered. (Met: `service.py:187-200`,
  `embeddings.py:155-184`.)
- **FR6 — Tangential IUL concepts (policy decision).** A general-IUL concept
  question that helps explain FlexLife *may* be answered. The app MUST make a
  deterministic, documented choice between:
  - **(6a) Grounded-only (status quo):** if the agent cannot cite the KF, abstain
    to NLG support. Simple, safe, consistent with M03/M07; **rejects** some
    legitimate tangential explanations.
  - **(6b) Permit labeled general concepts:** allow an in-domain "concept"
    explanation even without a KB citation, but visibly label it as general
    educational context (not approved product wording), and never allow
    product-specific facts (caps, rates, eligibility) to be stated ungrounded.
  The decision is a product-owner call (Open Q1); this spec recommends **6a for
  the POC** with 6b as a documented future option, because the whole product is
  grounding-first and 6b widens the surface for unsupported claims.
- **FR7 — No persistent profile dependency.** This capability must not depend on
  cross-session memory or a persistent user profile. (Met; note the
  preferences/memories feature is a separate, user-configured personalization —
  see Open Q2.)
- **FR8 — (Optional) Context-aware follow-up chips.** The chip row may show 1–2
  next-question suggestions derived from the latest answer + recent context,
  rendered via the existing `follow_ups` → `paintChips` path; absent that, it
  falls back to `NAV_CHIPS`. Not required for sign-off.

## 6. Technical Design

M04 adds **no new endpoint and no new request/response model**. Its changes are a
documented policy plus two small, optional code touches.

### 6.1 What is already wired (no change needed)

- History replay: `foundry.chat` (`foundry.py:347-361`). Frontend history build:
  `mountChat.send` (`learn.html:1189`). Domain gate: `chat_foundry`
  (`service.py:187`) + `classify_domain` (`embeddings.py:155-184`). Grounding +
  abstention: `foundry.py:388-396`. These implement FR1–FR5, FR7.

### 6.2 FR6 — tangential-IUL policy

**Recommended (6a, status quo): no code change.** Document that a tangential-IUL
question the agent cannot ground will abstain to NLG support, and that this is
intentional for the POC. Add a test that pins the behavior (a zero-citation reply
→ `escalate: true`, `NLG_SUPPORT_MESSAGE`), so the decision is explicit and
regression-guarded.

**If 6b is chosen (permit labeled general concepts):** the change lives in the
Foundry abstention tail (`foundry.py:388-396`). Introduce a narrow "concept vs.
product-fact" distinction so the abstention only fires for product-specific
questions:

- Add a lightweight classifier call (mirroring `classify_domain`,
  `embeddings.py:155-184`) — e.g. `classify_question_kind(client, settings,
  message, history) -> "concept" | "product_fact"` — run in `chat_foundry`
  before the Foundry call.
- When the kind is `"concept"` **and** the agent returns a non-empty answer with
  zero citations, do **not** abstain; instead return the answer with a
  machine-readable marker (`grounded: false` / `concept: true`) and a one-line
  UI label ("General IUL background — not approved FlexLife product wording").
- When the kind is `"product_fact"`, keep today's abstention unchanged.
- Never allow product-specific numbers (caps/floors/rates/eligibility) in an
  ungrounded concept answer — enforce via the agent instruction + the classifier,
  and keep M07 (underwriting) strictly grounded.

Because this widens the ungrounded-answer surface, it is deferred behind the
product-owner decision (Open Q1) and is **not** the POC default.

### 6.3 FR8 — optional follow-up suggestion chips

Two implementation options; both reuse the existing `paintChips` renderer
(`learn.html:1180-1185`), which already consumes `follow_ups`:

- **Option A (client-only, cheapest):** keep the static `NAV_CHIPS` as today and
  do nothing. (FR8 is optional.)
- **Option B (server-generated):** add a small follow-up generator and surface it
  on the Foundry response.
  - Backend: after a grounded Foundry answer in `chat_foundry`
    (`service.py:201-211`), call a new `suggest_follow_ups(client, settings,
    message, answer, history) -> list[str]` in `embeddings.py` (reuse the JSON
    pattern of `chat_with_context`, `embeddings.py:209-233`; ask for ≤2 short
    next questions phrased as the agent would ask them). Add `follow_ups:
    list[str] = []` to `FoundryChatResponse` (`server.py:133-147`).
  - Frontend: `navSend` returns `follow_ups: d.follow_ups || []`
    (`learn.html:1062`); `Object.assign` (`learn.html:1192`) puts it on the log
    message and `paintChips` (`learn.html:1182`) renders them automatically. No
    widget-shape change.
  - Cost: one extra cheap LLM call per grounded turn. For the POC this is
    optional; recommend shipping only if a demo wants live next-question chips.

### 6.4 Frontend surfaces (reference)

`ui/learn.html`: `mountChat` (`learn.html:1157-1256`), `send`/history
(`learn.html:1186-1195`), `paintChips` (`learn.html:1180-1185`), `navSend`
(`learn.html:1046-1063`), `NAV_CHIPS` (`learn.html:1039-1043`), the shared
thread + `store` (`learn.html:850-853`), `openNavigator`
(`learn.html:1296-1317`), `renderChat` (`learn.html:1322-1342`). Helpers: `api`
(`learn.html:874-879`), `toast` (`learn.html:855`).

## 7. Acceptance Criteria (Given/When/Then)

- **AC1 (follow-up without restating; story AC "ask a follow-up without
  restating").** *Given* a prior turn established a FlexLife topic (e.g. the S&P
  500 1% Floor strategy), *When* the user sends "and the cap on that?", *Then*
  the reply resolves "that" from `history` and answers without the user
  restating the strategy. (Backed by `foundry.py:347-361`, `learn.html:1189`.)
- **AC2 (uses current-session context; story AC "uses relevant context").**
  *Given* a multi-turn thread, *When* a terse follow-up is sent, *Then* the last
  ≤8 turns are replayed to the agent and the domain gate judges it in-thread.
- **AC3 (recent context sufficient; story AC "recent context sufficient").**
  *Given* a long thread, *When* a follow-up is sent, *Then* only the last ≤20
  turns (server cap) / ≤8 (client) are used and no summarization occurs.
- **AC4 (grounded follow-ups; story AC "remain grounded in the KF").** *Given* a
  follow-up about a product fact, *When* answered, *Then* the answer carries KB
  citations; *and* when it cannot be grounded, it abstains to NLG support rather
  than inventing facts (`foundry.py:388-396`).
- **AC5 (topic transition; story AC "transition between related topics").**
  *Given* an in-progress thread, *When* the user pivots to a related FlexLife
  topic, *Then* it is answered in the same thread with no "new session" required.
- **AC6 (not a general chatbot; story AC "clearly unrelated not treated as
  general conversation").** *Given* a clearly unrelated follow-up ("now write me
  a poem"), *When* processed, *Then* it is declined with `domain:
  "out_of_domain"` and the agent is not called (`service.py:187-200`).
- **AC7 (tangential IUL — policy; story AC "tangential IUL may be answered").**
  *Given* a general-IUL concept question, *When* processed under the chosen
  policy, *Then* the behavior is deterministic and documented: under 6a it is
  answered if grounded else abstains; under 6b it may return a labeled general
  explanation with no product-specific facts. (This AC is satisfied by making and
  testing the decision, not by a fixed output.)
- **AC8 (no persistent-profile dependency; story AC "does not rely on prior
  conversations / persistent profile").** *Given* the capability, *When*
  exercised, *Then* it uses only the current thread; clearing the thread
  (`mountChat.clear`, `learn.html:1254`) removes all continuity and no
  server-side history or profile is consulted.
- **AC9 (optional chips).** *Given* FR8 Option B is shipped, *When* a grounded
  answer returns, *Then* the chip row shows ≤2 context-aware next questions from
  `follow_ups`; otherwise it shows `NAV_CHIPS`.

## 8. Test Plan

**Backend (pytest; mock `foundry.FoundryAgentClient.respond` / `_generate`;
no network, mirroring `tests/test_m02_domain_gate.py` and
`tests/test_m03_citations.py`):**

- History replay: `foundry.chat` maps a two-turn `history` into the provider
  `input` as `{role, content}` messages in order, then appends the new user
  message (assert the payload passed to `respond`, matching the loop at
  `foundry.py:347-361`).
- Follow-up stays in-domain: `classify_domain` returns `IN_DOMAIN` for a terse
  follow-up ("and the floor?") given a FlexLife `history` (extend
  `test_m02_domain_gate.py`).
- Grounded follow-up: agent returns citations → `chat_foundry` passes the answer
  + citations through with `escalate: false` (already covered by
  `test_chat_foundry_proxies_in_domain_and_tags_domain`).
- FR6 policy pin (6a): agent returns a non-empty answer with **zero citations**
  → `foundry.chat` returns `NLG_SUPPORT_MESSAGE`, `escalate: true`
  (regression-guards the status-quo tangential-IUL behavior; mirrors M03's
  zero-citation test).
- FR6 (only if 6b is implemented): `kind == "concept"` + zero citations →
  answer preserved with `grounded: false`/`concept: true`, no product-specific
  numbers; `kind == "product_fact"` + zero citations → still abstains.
- FR8 (only if Option B is implemented): `suggest_follow_ups` returns ≤2
  non-empty strings from well-formed JSON and `[]` on malformed JSON (mirror the
  `chat_with_context` fallback test); `FoundryChatResponse` accepts `follow_ups`.

**Frontend (jsdom, per the dev-loop memory — browser is on another machine):**

- `mountChat.send` builds `history` as the last ≤8 finished turns mapped to
  `{role, text}` and omits pending/failed turns (`learn.html:1189`).
- `Object.assign` merges returned fields (`citations`, `escalate`, and, with
  Option B, `follow_ups`) onto the pending message (`learn.html:1192`).
- `paintChips` renders `follow_ups` when present, else `NAV_CHIPS`
  (`learn.html:1180-1185`); a chip tap calls `send` with the full question.
- Thread continuity: two sends accumulate in one `t.messages` thread; `clear`
  empties it; topic pivot within the thread does not spawn a new thread.

**Manual smoke (rebuild the Docker image per the dev-loop memory, then
`/app/learn.html`):**

- Ask a base question, then a pronoun follow-up ("and the floor on that?") and a
  topic pivot; confirm context resolution and KB citations.
- Ask a clearly unrelated follow-up; confirm a fast in-repo decline (no agent
  round-trip).
- Ask a general-IUL concept question; confirm it behaves per the chosen FR6
  policy.

## 9. Out of Scope

- Cross-session conversational memory; persistent learner/user profiles.
- Long-term conversation summarization or compression / production-grade history
  management (the POC uses last-N replay only).
- Changing the hosted agent's internal retrieval/grounding (Azure config,
  out-of-repo).
- Query rewriting / coreference resolution performed in-repo (the hosted agent
  handles this via history replay; the local fallback's prior-message query
  widening at `service.py:137-138` already exists and is unchanged).
- The handoff/draft flow when abstaining (that is M09; M04 only relies on the
  existing abstention signal).
- Re-pointing the end-user chat to `/v1/chat` (it stays on Foundry).

## 10. Dependencies & Open Questions

**Dependencies**

- **M02 (Natural Language Search)** — provides the Foundry chat path, the history
  replay, and the domain gate M04 proves. M04 adds no gate of its own.
- **M03 (Citations)** — provides the grounding + zero-citation abstention and the
  shared `NLG_SUPPORT_MESSAGE` (`config.py:15-18`). M04's FR6 tension is a direct
  consequence of M03's abstention rule; any 6b change must be coordinated with
  M03 so the two policies stay consistent.
- Hosted Foundry agent configured (`FOUNDRY_PROJECT_ENDPOINT`/`FOUNDRY_API_KEY`;
  `foundry_configured`, `foundry.py:39-40`); Azure OpenAI configured for the
  domain classifier and any optional follow-up generator (`config.py:131-134`).
- Docker image rebuild to ship any backend change (dev-loop memory).

**Open questions**

1. **FR6 — tangential IUL policy (the key decision).** Should a general-IUL
   concept question the agent cannot ground be (6a) abstained to NLG support
   (status quo, recommended for the POC), or (6b) answered as a labeled general
   explanation with no product-specific facts? This is a product-owner call that
   trades M04's permissiveness against M03/M07's grounding-first stance.
2. **Preferences/memories vs. "no persistent profile" (FR7).** The chat already
   sends user-configured `preferences`, `about_me`, and `memories`
   (`learn.html:1049-1061`, serialized server-side in
   `foundry._current_user_content`, `foundry.py:173-225`). These are explicit,
   user-set personalization (M06/M12), not an auto-learned profile, so they do
   not violate M04's "does not maintain a persistent user profile for this
   capability" — but confirm the product owner agrees that user-set memories are
   acceptable alongside M04.
3. **Follow-up chips (FR8).** Ship context-aware suggestion chips now (Option B,
   one extra LLM call/turn) or keep the static `NAV_CHIPS`? Recommend deferring
   unless a demo specifically wants live next-question chips.
4. **History window size.** Frontend sends 8 turns, server allows 20. Confirm 8
   is enough for the demo's longest expected follow-up chains; it is trivially
   tunable (`learn.html:1189`).

## 11. Rough Effort Estimate

| Area | Work | Est. |
| --- | --- | --- |
| Docs/sign-off | Prove FR1–FR5, FR7 met; write anchors + ACs | ~0.25 day |
| Backend | FR6 decision + regression test pinning 6a | ~0.25 day |
| Backend (only if 6b) | concept classifier + bounded abstention relax + tests | ~1 day |
| Backend (only if FR8 Option B) | `suggest_follow_ups` + `FoundryChatResponse.follow_ups` + tests | ~0.5 day |
| Frontend (only if FR8 Option B) | surface `follow_ups` in `navSend` | ~0.1 day |
| Tests | history-replay + follow-up-in-domain pytest + jsdom | ~0.5 day |
| Verify | Docker rebuild + jsdom/manual smoke | ~0.25 day |

**Baseline M04 (prove-and-pin, status-quo FR6, no new chips): ~1.25 days.**
With FR8 Option B chips: ~2 days. With FR6 option 6b as well: ~3 days.
