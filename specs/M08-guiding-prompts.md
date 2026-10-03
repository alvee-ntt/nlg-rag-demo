# M08 — Guiding Prompts

## 1. Summary

M08 makes the Agent Navigator **recognize when a question cannot be answered well
because client-specific details are missing, and ask for those details** — with
the clarifying questions **driven by the distinctions in the Knowledge
Foundation**, not a fixed questionnaire. Example: the underwriting guide applies
different rules to smokers vs. non-smokers; if the agent asks "will my client be
approved?" without stating smoking status, the app should recognize that smoking
status *materially changes the applicable rules* and ask for it before giving a
specific answer. Once enough detail exists, it gives the specific documented
guidance; if the corpus still cannot support an answer, it refers the user to NLG
support.

This is **net-new work — nothing in the codebase implements it today** (grep for
guiding/clarify/needs-info logic returns nothing in `src/`, see §3.1). It is also
the **hardest of the chat milestones to place on the shipping engine**, because
the end-user Ask Navigator chat runs on the **hosted Azure AI Foundry agent**
(`POST /v1/foundry/chat` → `service.chat_foundry` → `foundry.chat`), which grounds
**server-side** and whose retrieval/instructions live in Azure (out-of-repo). Two
facts make M08 impossible to satisfy by simply "letting the Foundry agent ask":

1. **We cannot inject corpus-distinction logic into the Foundry agent from this
   repo.** Its KB, retrieval, and instructions are Azure config; we can only
   prepend text and post-process what comes back. M08 requires the clarifying
   questions to be grounded in *actual* corpus distinctions — which this repo can
   only verify through the **local** retrieval path (pgvector + embeddings).
2. **The M03 zero-citation abstention would clobber a clarifying question.** If
   the hosted agent responded with a clarifying question (which naturally carries
   **no citations**), `foundry.chat`'s abstention
   (`src/rag_layer/foundry.py:388-396`, `if not citations or non_answer:`)
   replaces it with the NLG-support message and raises `escalate`. So today a
   clarifying turn is silently converted into an escalation.

**Design decision:** M08 adds a **local, corpus-grounded "clarifier gate"** in
`service.chat_foundry`, mirroring the proven M02 domain-gate pattern
(`classify_domain`, `embeddings.py:155-184`; wired at `service.py:187`). Before
calling the hosted agent, the clarifier uses **local retrieval** on the current
question + conversation to decide whether answering well depends on
client-specific characteristics the corpus distinguishes but the conversation has
not yet supplied. If so, it returns those clarifying questions directly (tagged
`guiding: true`) **instead of** calling Foundry — which also sidesteps the
zero-citation abstention. When enough detail is present (or no distinction
applies), it proceeds to the normal grounded Foundry answer. The clarifier
remembers already-provided details because it receives the full `history`, and it
refuses to invent guidance — if unsupported, it defers to the existing M03
abstention / NLG-support path.

M08 adds one service-layer step, one LLM helper + prompt in `embeddings.py`, a
few fields on `FoundryChatResponse`, and frontend rendering of the clarifying
questions (reusing the existing `follow_ups` → chip mechanism and answer bubble).
No new endpoint.

## 2. User Story (verbatim)

**User Story: Identify Needed Client Information** — As a FlexLife sales agent or
support user, I want the application to identify additional client information
that may be needed, so that I can provide the details required to retrieve the
most relevant FlexLife guidance, rules, or recommendations.

Description: The POC will recognize when the information already provided is
insufficient to determine which documented FlexLife rules or guidance are most
applicable. When important client-specific details are missing, the application
should ask relevant follow-up questions based on the information and distinctions
contained in the Knowledge Foundation. The purpose of these questions is to help
the agent gather information they may not otherwise realize is significant. For
example, if the documentation applies different rules based on a particular
client characteristic, the application should recognize that the characteristic
matters and ask for it before providing a more specific answer. Questions should
be driven by what is still needed in the current conversation rather than by a
fixed questionnaire. The application may use this information both to identify
applicable documented rules and to support recommendations where sufficient
knowledge exists. If the application cannot support an answer from the Knowledge
Foundation, it should refer the user to NLG support rather than provide
unsupported guidance.

Acceptance criteria (source):

- The application can recognize when additional client information would
  materially affect the relevance or applicability of an answer.
- The application asks follow-up questions based on information that is still
  missing from the current conversation.
- Follow-up questions are relevant to retrieving, categorizing, or applying
  information contained in the Knowledge Foundation.
- The application can identify client characteristics or circumstances that are
  significant according to the source documentation, even when the agent has not
  explicitly recognized their importance.
- The application does not require a fixed sequence or comprehensive
  questionnaire before providing assistance.
- Previously supplied information from the current conversation is taken into
  account so the application does not unnecessarily ask for the same information
  again.
- Once sufficient information is available, the application can use it to provide
  more specific documented rules, guidance, or recommendations.
- Any factual guidance produced after clarification remains grounded in the
  Knowledge Foundation.
- If the Knowledge Foundation does not sufficiently support an answer, the
  application refers the user to NLG support.

Out of scope: A complete client intake or application form; production-grade
needs analysis or suitability workflows; mandatory question ordering; making
final underwriting, eligibility, or approval decisions; generating unsupported
recommendations from information outside the Knowledge Foundation.

## 3. Current State — what exists

### 3.1 No guiding-prompts / clarifier feature exists

A grep of `src/` and `tests/` for guiding/clarify/needs-info logic returns no
feature code — only unrelated mentions (the roleplay customer-sim is told it
*may* ask for clarification, `src/rag_layer/roleplay.py:613`; the sales playbook
copy mentions "clarify", `roleplay_data/sales_playbook.md`). There is **no
clarifying-question step in the Ask Navigator path**: `service.chat_foundry`
(`src/rag_layer/service.py:168-237`) runs only the M02 domain gate and then calls
the hosted agent. The hosted agent might *sometimes* ask a clarifying question on
its own, but that is (a) uncontrolled and untestable in-repo, and (b) actively
broken by the M03 abstention (see §3.4). M08 is therefore all new work.

### 3.2 The engine the feature must run on

- End-user chat path: `foundry_chat_endpoint` (`server.py:529-551`) →
  `service.chat_foundry` (`service.py:168-237`) → `foundry.chat`
  (`foundry.py:317-404`). The hosted agent grounds server-side; the repo relays
  `{answer, citations, …}` (`foundry.py:390-404`).
- M08's clarifying questions must be grounded in corpus **distinctions**. The
  repo can only inspect those distinctions via the **local** retrieval path:
  `retrieve_contexts` (`service.py:35-45`) → `embed_texts` + `search_chunks`,
  and the chunk text/metadata exposed in `format_sources` (`service.py:47-64`).
  So the clarifier is a **local** step layered before the Foundry call — the same
  architectural shape as the M02 domain gate.

### 3.3 Reusable plumbing (the clarifier builds on these)

- **Domain-gate pattern to copy.** `classify_domain(client, settings, message,
  history) -> str` (`embeddings.py:155-184`) is a cheap, fail-open LLM
  classification that runs **before** the Foundry call inside `chat_foundry`
  (`service.py:187`). M08's clarifier is the same shape: a local LLM call, run
  after the domain gate, that can short-circuit the turn. Its unit tests follow
  `tests/test_m02_domain_gate.py` (mock `_generate` / `classify_domain`).
- **Local retrieval.** `retrieve_contexts(*, settings, client, text, limit)`
  (`service.py:35-45`) returns best-first chunks; `select_citations`
  (`service.py:67-91`) and the `min_similarity`/`max_sources` knobs
  (`config.py:65-70`) already exist. The clarifier retrieves on the question so
  the LLM sees the *actual* candidate corpus material and can name the
  distinctions in it (e.g. "smoker vs. non-smoker", "issue age band", "rider
  eligibility") rather than inventing a generic intake form.
- **JSON-returning LLM helper pattern.** `chat_with_context`
  (`embeddings.py:187-234`) shows the exact pattern M08's helper uses: build a
  prompt with `_history_text(history)` (`embeddings.py:145-152`) and
  `_context_text(contexts)` (`embeddings.py:92-97`), call `_generate`
  (`embeddings.py:100-116`), parse with `_parse_json_object`
  (`embeddings.py:130-142`), and fall back gracefully on malformed JSON.
- **Shared abstention message.** `NLG_SUPPORT_MESSAGE` (`config.py:15-18`) is the
  single "refer to NLG support" constant used by both tracks; M08 reuses it for
  the "cannot support an answer" case.
- **Frontend chip + answer rendering.** `mountChat.paintChips`
  (`ui/learn.html:1180-1185`) already renders tappable chips from a message's
  `follow_ups`; `navSend` (`learn.html:1046-1063`) merges response fields onto
  the log message via `Object.assign` (`learn.html:1192`). M08 renders its
  clarifying questions through these existing seams.

### 3.4 The critical constraint — M03 abstention clobbers clarifying turns

In `foundry.chat` (`foundry.py:388-396`):

```python
non_answer = (not answer) or answer.strip() in {"", "(the agent returned no text)"}
if not citations or non_answer:
    return {"answer": NLG_SUPPORT_MESSAGE, "citations": [], "escalate": True,
            "escalate_reason": "no_citations" if not citations else "empty_answer", **meta}
```

A clarifying question carries **no citations**, so any clarifying reply from the
hosted agent is converted into the NLG-support abstention. **This is why M08
cannot rely on the Foundry agent to ask clarifying questions, and why the
clarifier must run *before* `foundry.chat` and short-circuit the turn** so the
abstention never sees it. (Documented as the main design driver in §6.)

### 3.5 Corpus supports the use case

The ingested corpus includes an underwriting guide, product/quick-reference
guides, rider/living-benefits brochures, and IUL/crediting guides
(`src/rag_layer/learn.py:171`), and the repo even ships
`tests/underwriting_guide_questions.md`. Underwriting rules are exactly the kind
of material that branches on client characteristics (smoking status, age band,
health class, rider eligibility), so the "different rules by characteristic"
premise of M08 is real against this corpus. (M08 intersects M07 — Underwriting
Technicals — which independently requires grounded, non-decisional underwriting
answers; M08 adds the *ask-for-missing-info* step in front of it.)

## 4. Scope

**In scope**

- A **local clarifier step** in `service.chat_foundry` that, for an in-domain
  turn, uses local retrieval + one LLM call to decide whether answering well
  depends on client-specific details the corpus distinguishes but the
  conversation has not supplied.
- When details are missing: return 1–3 **corpus-driven clarifying questions**
  (tagged `guiding: true`) **without** calling the hosted agent, so the M03
  abstention never fires on the clarifying turn.
- When details are sufficient (or no distinction applies): proceed to the normal
  grounded Foundry answer unchanged.
- **Remember already-provided info**: the clarifier receives `history` and must
  not re-ask for details already stated.
- **Grounding + NLG fallback preserved**: post-clarification answers stay on the
  grounded Foundry path with its citations and M03 abstention; an unsupported
  question defers to `NLG_SUPPORT_MESSAGE`.
- Response-model fields (`guiding`, `guiding_questions`) and frontend rendering
  (answer bubble + tappable chips).

**Out of scope** — see §9.

## 5. Functional Requirements

- **FR1 — Recognize materially-missing info.** For an in-domain question, the
  app decides whether additional client-specific detail would materially change
  which documented rules/guidance apply, **grounded in retrieved corpus material**
  (not a generic form).
- **FR2 — Ask corpus-driven clarifying questions.** When info is missing, the app
  returns 1–3 specific clarifying questions, each tied to a distinction present in
  the retrieved sources (e.g. smoking status, issue age, health class, rider in
  question), phrased for the agent to answer.
- **FR3 — Surface significance the agent may not see.** The questions may ask for
  characteristics the agent did not think to mention, because the *documentation*
  treats them as significant (AC "identify significant characteristics even when
  the agent has not recognized their importance").
- **FR4 — No fixed questionnaire / no mandatory ordering.** Questions are derived
  per-turn from what is still needed; there is no required sequence and no
  comprehensive intake gate before any assistance.
- **FR5 — Remember provided info.** The clarifier reads the conversation
  `history` and must not re-ask for details already supplied; as the agent
  answers, remaining gaps shrink until the app proceeds.
- **FR6 — Proceed when sufficient.** Once enough detail exists (or the question
  needs no client-specific detail), the app gives the specific documented
  guidance via the grounded Foundry answer.
- **FR7 — Stay grounded after clarification.** Any factual guidance produced
  after clarification remains grounded in the KF (hosted-agent grounding +
  citations; M03 abstention still applies to the *answer* turn).
- **FR8 — Refer to NLG when unsupported.** If, even with the detail, the KF
  cannot support an answer, the app refers to NLG support
  (`NLG_SUPPORT_MESSAGE`) rather than inventing guidance. (This also covers M07's
  "no case-specific underwriting decision" — the clarifier asks for inputs; it
  never *decides* approval.)
- **FR9 — Machine-readable guiding signal.** The response carries a discrete
  `guiding: bool` (+ `guiding_questions: list[str]`) so the UI and tests can
  distinguish a clarifying turn from an answered turn and from an abstention.
- **FR10 — Clarifying turn is not an abstention.** A guiding turn must **not** be
  converted into the M03 NLG-support abstention (it short-circuits before
  `foundry.chat`), and must not set `escalate`.
- **FR11 — Fail open.** If the clarifier LLM call errors or returns garbage,
  default to **not** asking a clarifying question and proceed to the normal
  answer, so a hiccup never blocks assistance (mirrors `classify_domain`'s
  fail-open, `embeddings.py:180-184`).

## 6. Technical Design

### 6.1 Where the clarifier lives — decision

Put the clarifier in `service.chat_foundry` (`service.py:168-237`),
**after** the M02 domain gate (`service.py:187`) and **before** the Foundry call
(`service.py:201-211`). Rationale:

- It must run on the **local** retrieval path (the only place this repo can
  inspect real corpus distinctions), which `chat_foundry` can reach via
  `retrieve_contexts` + the injected `client` (`service.py:35-45`).
- Short-circuiting here means the clarifying turn never reaches `foundry.chat`,
  so the M03 zero-citation abstention (`foundry.py:388-396`) cannot clobber it
  (FR10).
- It reuses the exact M02 gate shape (classify → maybe short-circuit → else
  proxy), which is already proven and tested (`test_m02_domain_gate.py`).

Rejected alternatives: (a) instructing the Foundry agent to ask clarifying
questions — untestable in-repo and still clobbered by abstention unless M03 is
also relaxed, and it cannot guarantee the questions reflect the *actual* corpus
distinctions; (b) a new endpoint — unnecessary, the signal rides the existing
`/v1/foundry/chat` response.

### 6.2 Backend — service wiring (`service.py`)

Add a clarifier call inside `chat_foundry`, after the domain gate:

```python
# service.py, inside chat_foundry, after the OUT_OF_DOMAIN short-circuit (line 187-200)
contexts = retrieve_contexts(settings=settings, client=client, text=message,
                             limit=settings.rag_search_limit)
guide = assess_missing_info(client, settings, message, history, contexts)  # embeddings.py
if guide.get("needs_info") and guide.get("questions"):
    return {
        "answer": guide["answer"],              # short framing + the questions as prose
        "citations": [], "sources": [],
        "agent": settings.foundry_agent_name, "model": None,
        "response_id": None, "status": "guiding",
        "domain": "in_domain",
        "escalate": False, "escalate_reason": None,
        "guiding": True, "guiding_questions": guide["questions"],
        "follow_ups": guide["questions"],       # render as tappable chips (reuse paintChips)
        "source_engine": "clarifier",
    }
# else: proceed to the existing Foundry call (service.py:201-211), unchanged.
```

Notes:

- The clarifier reuses the already-computed `contexts` so there is at most **one**
  extra retrieval per turn; the normal Foundry answer still grounds server-side,
  so local retrieval here is used only to *inform the clarifier*, not to answer.
- `follow_ups` is set to the questions so the existing chip renderer
  (`learn.html:1180-1185`) lets the agent tap a question to answer it. (This also
  motivates adding `follow_ups` to `FoundryChatResponse` if M04 has not already —
  see §6.4.)
- Fail-open (FR11): `assess_missing_info` returns `needs_info: False` on any error
  so the turn proceeds to the normal answer.

### 6.3 Backend — LLM helper (`embeddings.py`)

Add `assess_missing_info`, following the `chat_with_context` JSON pattern
(`embeddings.py:187-234`) and reusing `_history_text` (`:145-152`),
`_context_text` (`:92-97`), `_generate` (`:100-116`), `_parse_json_object`
(`:130-142`):

```python
def assess_missing_info(client, settings, message, history, contexts) -> dict:
    """Decide whether a good answer depends on client-specific details the corpus
    distinguishes but the conversation has not supplied (M08). Corpus-driven, not a
    fixed questionnaire. Fails open to needs_info=False."""
    prompt = f"""You help a FlexLife sales agent. Using ONLY the approved source
excerpts below, decide whether answering the agent's question well depends on
client-specific details that the sources treat as significant (they apply
different rules/guidance depending on the detail) and that have NOT yet been
provided in the conversation.

Rules:
- Base the needed details on DISTINCTIONS ACTUALLY PRESENT in the sources (e.g. a
  rule that differs by smoking status, issue age, health class, rider, or funding).
  Do NOT invent a generic intake form or ask for details the sources don't use.
- Do NOT re-ask for anything already stated in the conversation so far.
- Ask at most 3 of the most decision-relevant missing details, phrased so the
  agent can answer them quickly.
- If the question needs no client-specific detail, or enough detail is already
  present, set needs_info=false.

Return ONLY JSON: {{"needs_info": true|false,
  "questions": ["...", "..."],
  "answer": "<one short sentence saying you need a couple of details to give the
             right FlexLife guidance, then the questions as a short list>"}}

Conversation so far:
{_history_text(history)}

Agent's latest question:
{message}

Approved source excerpts (what the Knowledge Foundation actually distinguishes):
{_context_text(contexts) or "(no sources retrieved)"}
"""
    try:
        data = _parse_json_object(_generate(client, settings, prompt))
        needs = bool(data.get("needs_info", False))
        questions = [str(q).strip() for q in data.get("questions", []) if str(q).strip()][:3]
        answer = str(data.get("answer", "")).strip()
        if needs and questions:
            return {"needs_info": True, "questions": questions,
                    "answer": answer or "I need a couple of details to point you to the right FlexLife guidance."}
        return {"needs_info": False, "questions": [], "answer": ""}
    except Exception:  # noqa: BLE001 - fail open: never block assistance on a hiccup
        return {"needs_info": False, "questions": [], "answer": ""}
```

Key properties: corpus-driven (`contexts` in the prompt → FR1/FR2/FR3),
history-aware (FR5 "don't re-ask"), bounded to ≤3 questions and no mandatory
ordering (FR4), fail-open (FR11), and it never produces product facts itself — it
only asks for inputs, so grounding (FR7) and the no-decision rule (FR8/M07) are
preserved.

### 6.4 Backend — response model (`server.py`)

Add to `FoundryChatResponse` (`server.py:133-147`):

```python
guiding: bool = False
guiding_questions: list[str] = []
follow_ups: list[str] = []   # (add here if M04 has not; drives the chip row)
```

`status: "guiding"` labels the turn. No request-model change —
`FoundryChatRequest` (`server.py:119-124`) already carries `message` + `history`.

**Sample response — clarifying turn:**

```json
{
  "answer": "I can point you to the right FlexLife underwriting guidance once I know a couple of things:\n- Does the client use nicotine/tobacco?\n- What is the client's issue age?",
  "citations": [],
  "sources": [],
  "agent": "KnowledgeBase",
  "status": "guiding",
  "domain": "in_domain",
  "escalate": false,
  "guiding": true,
  "guiding_questions": ["Does the client use nicotine/tobacco?", "What is the client's issue age?"],
  "follow_ups": ["Does the client use nicotine/tobacco?", "What is the client's issue age?"],
  "source_engine": "clarifier"
}
```

### 6.5 Frontend (`ui/learn.html`)

- `navSend` (`learn.html:1046-1063`) returns the new fields:
  `guiding: !!d.guiding`, `guiding_questions: d.guiding_questions || []`,
  `follow_ups: d.follow_ups || []`. `Object.assign` (`learn.html:1192`) merges
  them onto the log message.
- **Answer bubble:** the clarifying questions render as the normal AI answer
  (`mdToHtml(m.text)`, `learn.html:1174`) — the prose + list `answer` already
  reads as a short ask. Optionally add a subtle "I need a bit more info" label
  when `m.guiding` is true (cosmetic).
- **Tappable chips:** because the service sets `follow_ups` to the questions,
  `paintChips` (`learn.html:1180-1185`) renders each as a chip; tapping it calls
  `send` with the question text, letting the agent answer in one tap — the loop
  then re-runs the clarifier with the now-fuller history (FR5/FR6).
- **No escalation affordance:** a guiding turn has `escalate: false`, so the M09
  handoff CTA (`learn.html:1175`) does not appear — correct, since this is a
  request for info, not an abstention.
- Surfaces unchanged: pull-up sheet (`openNavigator`, `learn.html:1296-1317`) and
  full page (`renderChat`, `learn.html:1322-1342`) both get the behavior for free
  via `navSend`/`mountChat`.

### 6.6 Data flow summary

In-domain turn → local retrieval (`retrieve_contexts`) → `assess_missing_info`:
**needs info** → return guiding questions (short-circuit, no Foundry call, no
abstention) → UI shows questions + chips → agent answers → loop re-runs with
fuller history → **sufficient** → normal grounded Foundry answer (citations, M03
abstention if unsupported → NLG support).

## 7. Acceptance Criteria (Given/When/Then)

- **AC1 (recognize missing info; story AC "recognize when additional info would
  materially affect the answer").** *Given* an in-domain question whose answer the
  corpus branches on a client characteristic not yet provided (e.g. "will my
  client be approved?" with no smoking status), *When* processed, *Then* the
  response has `guiding: true` and ≥1 `guiding_questions`, and the hosted agent is
  not called for that turn.
- **AC2 (corpus-driven questions; story ACs "based on missing info", "relevant to
  retrieving/categorizing/applying KF info").** *Given* a guiding turn, *When* the
  questions are inspected, *Then* each maps to a distinction present in the
  retrieved sources (not a generic intake field).
- **AC3 (surface unrecognized significance; story AC "identify significant
  characteristics even when the agent has not recognized their importance").**
  *Given* an agent who omitted a characteristic the documentation treats as
  significant, *When* processed, *Then* the app asks for it.
- **AC4 (no fixed questionnaire; story ACs "no fixed sequence/questionnaire").**
  *Given* two different questions, *When* each is processed, *Then* the questions
  differ by what each needs; there is no mandatory intake gate or fixed order, and
  a question needing no client detail proceeds straight to an answer.
- **AC5 (remember provided info; story AC "previously supplied info is taken into
  account — don't re-ask").** *Given* the agent already stated a detail earlier in
  the thread, *When* a follow-up is processed, *Then* the clarifier does not
  re-ask for that detail.
- **AC6 (proceed when sufficient; story AC "once sufficient, provide specific
  guidance").** *Given* the agent has supplied the needed details, *When* the next
  turn is processed, *Then* `guiding` is false and a grounded Foundry answer with
  citations is returned.
- **AC7 (grounded after clarification; story AC "guidance remains grounded").**
  *Given* a post-clarification answer, *When* inspected, *Then* product facts
  carry KB citations; if ungrounded, the M03 abstention still applies.
- **AC8 (NLG fallback when unsupported; story AC "refer to NLG support if
  unsupported").** *Given* a question the KF cannot support even with details,
  *When* answered, *Then* the reply is `NLG_SUPPORT_MESSAGE` with `escalate: true`
  (the existing M03 path), not invented guidance.
- **AC9 (machine-readable + not an abstention; FR9/FR10).** *Given* a guiding
  turn, *When* inspected, *Then* `guiding: true`, `status: "guiding"`,
  `escalate: false`, `citations: []` — a downstream consumer can branch on
  `guiding` without parsing text, and no M09 handoff CTA appears.
- **AC10 (fail-open; FR11).** *Given* the clarifier LLM errors or returns garbage,
  *When* the turn is processed, *Then* it proceeds to the normal answer (no
  guiding turn is forced).

## 8. Test Plan

**Backend (pytest; mock `_generate` / `assess_missing_info` / `foundry.chat`; no
network — following `tests/test_m02_domain_gate.py`):**

- `assess_missing_info`: well-formed JSON with `needs_info: true` + questions →
  returns them (≤3); `needs_info: false` → empty; malformed JSON or raised
  exception → `needs_info: False` (fail-open, AC10); questions derived only from
  the `contexts` passed in (assert the prompt includes `_context_text(contexts)`).
- `chat_foundry` guiding branch: stub `assess_missing_info` → `needs_info: True`;
  assert the result has `guiding: true`, `status: "guiding"`, `escalate: false`,
  `citations == []`, `follow_ups == guiding_questions`, and that **`foundry.chat`
  is not called** (assert the mock is not invoked — mirrors the M02
  `test_chat_foundry_declines_out_of_domain_without_calling_agent` pattern,
  `test_m02_domain_gate.py:66-86`).
- `chat_foundry` proceed branch: stub `assess_missing_info` → `needs_info: False`;
  assert it calls `foundry.chat` and returns `guiding: false` with the agent's
  answer/citations (extends `test_chat_foundry_proxies_in_domain_and_tags_domain`).
- Remember-provided-info: pass a `history` that already states the detail and
  assert (via the prompt string) the clarifier is told not to re-ask; with a
  cooperating stub, `needs_info: False` (AC5).
- Ordering with the domain gate: an out-of-domain turn is still declined by M02
  **before** the clarifier runs (assert `assess_missing_info` not called on an
  out-of-domain turn).
- Endpoint (`TestClient`, offline, like `test_m02_domain_gate.py:172-223`): a
  guiding response validates against the updated `FoundryChatResponse`
  (`guiding`, `guiding_questions`, `follow_ups`).

**Frontend (jsdom, per the dev-loop memory — browser is on another machine):**

- `navSend` surfaces `guiding`, `guiding_questions`, `follow_ups`; `Object.assign`
  merges them (`learn.html:1192`).
- A guiding message renders its questions in the answer bubble and as tappable
  chips via `paintChips` (`learn.html:1180-1185`); tapping a chip calls `send`
  with the question text.
- A guiding message shows **no** M09 handoff CTA (`escalate` false,
  `learn.html:1175`) and **no** sources toggle (`citations`/`sources` empty,
  `learn.html:1169`).

**Manual smoke (rebuild the Docker image per the dev-loop memory, then
`/app/learn.html`; use the underwriting corpus from
`tests/underwriting_guide_questions.md`):**

- Ask "Will my client be approved for FlexLife?" with no details → app asks for
  the corpus-distinguished details (e.g. tobacco use, issue age). Answer one via a
  chip; confirm it does not re-ask it and narrows to the remaining gap. Supply the
  rest → confirm a grounded answer (or an NLG referral if still unsupported).
- Ask a question needing no client detail (e.g. "What is the FlexLife floor?") →
  confirm it answers directly with citations, no clarifying turn.

## 9. Out of Scope

- A complete client intake or application form; production-grade needs-analysis /
  suitability workflows.
- Mandatory question ordering or a comprehensive questionnaire gate.
- Making final underwriting / eligibility / approval decisions or predicting an
  applicant's outcome (that is forbidden by M07; the clarifier only gathers
  inputs, it never decides).
- Generating recommendations from information outside the KF (ungrounded answers
  defer to NLG support via the existing M03 path).
- Cross-session memory / persistent profiles (M08 uses only the current thread's
  `history`).
- Changing the hosted agent's internal retrieval/grounding (Azure config,
  out-of-repo) and re-pointing the chat off Foundry.

## 10. Dependencies & Open Questions

**Dependencies**

- **M02 (Natural Language Search)** — the clarifier runs **after** the M02 domain
  gate inside `chat_foundry` (`service.py:187`) and copies its pattern. Only
  in-domain turns reach the clarifier.
- **M03 (Citations)** — provides the grounding + abstention on the *answer* turn
  and the `NLG_SUPPORT_MESSAGE` constant (`config.py:15-18`) M08 reuses for FR8.
  M08 deliberately short-circuits **before** `foundry.chat` so the M03
  zero-citation abstention (`foundry.py:388-396`) cannot clobber a clarifying turn
  (FR10). If M03's abstention logic changes, re-check this ordering.
- **M07 (Underwriting Technicals)** — M08 is most valuable on underwriting
  questions and must respect M07's no-case-specific-decision rule; the clarifier
  asks for inputs only.
- **M04 (Follow-up Questions)** — M08's chips ride the same `follow_ups` → chip
  mechanism; if M04 adds `follow_ups` to `FoundryChatResponse`, M08 reuses it,
  otherwise M08 adds it (§6.4).
- Azure OpenAI configured for the clarifier LLM call and local retrieval
  (`config.py:131-134`); hosted Foundry agent configured for the answer turn
  (`foundry_configured`, `foundry.py:39-40`). One extra retrieval + one extra LLM
  call per in-domain turn (acceptable for a POC; the clarifier reuses the single
  retrieval it makes). Docker rebuild to ship (dev-loop memory).

**Open questions**

1. **Clarifier aggressiveness.** How readily should it ask vs. answer? Over-asking
   is annoying; under-asking misses the point of M08. Tune the prompt's threshold
   and validate against a small labeled set from
   `tests/underwriting_guide_questions.md`. Recommendation: bias toward answering
   when the corpus does not clearly branch, and cap at 3 questions.
2. **Where retrieval for the clarifier points.** The clarifier uses *local*
   pgvector retrieval to see corpus distinctions, while the *answer* comes from the
   hosted Foundry agent's own KB. If the two corpora diverge, the clarifier might
   ask for a distinction the hosted agent's KB does not actually use (or miss one
   it does). Confirm the local pgvector corpus and the Foundry KB are the same
   source material; if not, flag the mismatch (this is the main architectural
   risk, and a reason the clarifier questions should stay high-level).
3. **One combined turn vs. a round-trip.** This spec asks for details, then the
   agent answers in a *subsequent* turn (a natural chat loop). An alternative is to
   ask + attempt a best-effort grounded answer in one turn. Recommendation: keep
   the simple ask-then-answer loop for the POC (clearer, testable).
4. **Should sufficiency be LLM-judged or heuristic?** The POC judges "enough info"
   via the same LLM each turn (re-running the clarifier with fuller history). A
   future version could track a checklist of required fields; out of scope here
   (would drift toward the forbidden fixed questionnaire).
5. **Interaction with user `memories`/`about_me`.** The chat already carries
   user-set memories (`learn.html:1049-1061`); should a saved detail (e.g. "I
   mostly sell to pre-retirees") pre-satisfy a clarifier question? For the POC,
   the clarifier reads only the conversation `history`; confirm whether memories
   should also count as "already provided."

## 11. Rough Effort Estimate

| Area | Work | Est. |
| --- | --- | --- |
| Backend | `assess_missing_info` prompt + parse + fail-open | ~0.75 day |
| Backend | clarifier wiring in `chat_foundry` (retrieve + short-circuit) | ~0.5 day |
| Backend | `FoundryChatResponse` fields (`guiding`, `guiding_questions`, `follow_ups`) | ~0.25 day |
| Frontend | surface fields in `navSend`; guiding bubble + chip tap loop | ~0.5 day |
| Tests | pytest (clarifier, guiding branch, don't-call-agent, fail-open) + jsdom | ~1 day |
| Tuning | prompt threshold vs. underwriting question set | ~0.5 day |
| Verify | Docker rebuild + jsdom/manual smoke | ~0.25 day |

**Total: ~3.5–4 developer-days.** The main risk is tuning (Open Q1) and the
local-retrieval-vs-Foundry-KB corpus alignment (Open Q2), not the plumbing.
