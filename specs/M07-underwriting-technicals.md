# M07 — Underwriting Technicals

Build-ready implementation spec for the FlexLife POC (`nlg-rag`).

## 1. Summary

FlexLife underwriting questions (exam/lab requirements, rate classes, build/tobacco
rules, financial underwriting, state rules, medical knockouts, "best offer" by
condition) must be answerable in Ask Navigator, but held to a **higher grounding bar**
and wrapped in **three guardrails the rest of the app does not impose**:

1. The app may **explain documented underwriting rules, requirements, conditions,
   limitations and exceptions** — but it must **never make a case-specific
   underwriting decision** and must **never predict whether a given applicant will be
   approved or declined** at a specific rate class.
2. When the approved underwriting material does not clearly establish the answer — or
   the user is really asking for a case-specific *decision* rather than a documented
   *rule* — the app must **refer the user to NLG Support** instead of guessing.
3. Every underwriting answer must carry a **"reference aid, not authoritative for
   underwriting decisions"** disclaimer.

M07 is **not a new engine or endpoint**. The end-user Ask Navigator chat already runs
on the hosted Azure AI Foundry agent via `POST /v1/foundry/chat`
(`server.py:529-551`) → `service.chat_foundry` (`service.py:168-237`) → `foundry.chat`
(`foundry.py:317-404`), with retrieval and grounding server-side inside the hosted
agent. M07 is a **behavioral / guardrail layer** that sits in exactly the same place
M02's out-of-domain gate already sits: `chat_foundry` in this repo. It (a) detects
underwriting-topic turns, (b) injects an explicit "explain rules, never decide a case"
instruction into the turn, (c) forces an NLG-Support referral when the question is a
case-specific *decision* or when grounding is insufficient — **reusing** M03's
`escalate`/`escalate_reason` abstention contract and the M09 handoff — and (d) attaches
the reference-aid disclaimer.

The **underwriting corpus already exists and is identifiable** (see §3.4): the FlexLife
Life Insurance Underwriting Guide is indexed, cited live by the Foundry agent, and a
130-question regression harness (`tests/run_foundry_eval.py`) already drives it. So M07
is purely the guardrail/disclaimer behavior on top of an already-grounded chat.

**Where the agent's own instructions live is the central constraint:** the hosted
Foundry agent's instructions are in Azure, **outside this repo** (confirmed in M02
§3 and M03 §3.1/§9 and reconfirmed here — nothing in `src/` writes them). So the only
reliable, testable levers M07 owns are **in-repo**: the per-turn message preamble
(`foundry._current_user_content`, `foundry.py:173-225`) and the `chat_foundry`
pre/post pass (`service.py:168-237`). M07 implements the enforceable guardrails there
and documents the agent-side tuning as out-of-repo defense-in-depth, mirroring the
Option-A decision M02 already made.

## 2. User Story (verbatim)

> **User Story: Provide Underwriting Guidance**
>
> As a FlexLife sales agent or support user,
> I want to ask questions about FlexLife underwriting rules,
> so that I can better understand documented underwriting requirements and considerations when preparing an application.

Description:

The POC will support questions related to FlexLife underwriting using the supplied technical underwriting documentation as an authoritative source within the Knowledge Foundation.

Because underwriting information may affect how an application is prepared or discussed, responses should be held to a higher standard of grounding and should only provide guidance that is clearly supported by the approved source material.

The application may explain documented underwriting rules, requirements, conditions, limitations, and related technical details. It should not make case-specific underwriting decisions or represent that an applicant will be approved or declined.

Responses should make clear that the application is a reference and training aid and is not an authoritative underwriting decision-maker.

Acceptance Criteria:

- A user can ask natural-language questions about FlexLife underwriting rules.
- Responses are grounded in the approved underwriting documentation contained in the Knowledge Foundation.
- The application can explain documented underwriting requirements, conditions, exceptions, and limitations.
- Underwriting responses are generated only when the available source material provides sufficient support.
- The application does not make independent underwriting judgments or extrapolate beyond documented rules.
- Responses distinguish documented underwriting guidance from case-specific decisions that require NLG review.
- Underwriting responses include language indicating that the application is a reference aid and is not authoritative for underwriting decisions.
- Existing citation and grounding behavior applies to underwriting responses.

Out of Scope:

- Predicting the underwriting outcome of a specific applicant.
- Replacing NLG underwriting personnel or formal underwriting review.
- Application-processing workflows beyond the underwriting rules represented in the supplied POC documentation.
- Inferring undocumented underwriting practices or exceptions.

## 3. Current State — what exists

There is **no underwriting-specific behavior anywhere in the codebase today**. Ask
Navigator answers underwriting questions exactly like any other FlexLife question —
same grounding bar, no case-specific-decision guard, no reference-aid disclaimer. A
grep of `src/` and `ui/learn.html` for `underwrit` returns only the corpus/eval
tooling and prospect-persona data, never a guardrail. Everything below is the plumbing
M07 builds on.

### 3.1 The end-user chat path (backend)

- `POST /v1/foundry/chat` → `foundry_chat_endpoint` — `server.py:529-551`. Guards on
  `foundry_configured` (503 if unset, `server.py:534-538`), then calls
  `chat_foundry(settings, client, message, history, preferences, about_me, memories,
  trace_session_id)` (`server.py:540-549`), re-raising provider errors as
  `HTTPException(502, ...)` (`server.py:550-551`).
- `service.chat_foundry` — `service.py:168-237` — **is the orchestration seam, and the
  natural home for M07's guardrails.** Today it:
  1. Runs M02's out-of-domain gate: `classify_domain(...)` (`service.py:187`); an
     `OUT_OF_DOMAIN` turn is declined in-repo **without calling the agent**
     (`service.py:187-200`), returning `DOMAIN_DECLINE` (`service.py:29-32`) with
     `domain: "out_of_domain"`, `source_engine: "gate"`.
  2. Otherwise delegates to `foundry.chat(...)` and tags the result
     `{**result, "sources": [], "domain": "in_domain", "source_engine": "foundry"}`
     (`service.py:201-211`).
  3. On any Foundry error, falls back to the **local** grounded pipeline
     `service.chat(...)` and maps `insufficient_support` → `escalate`
     (`service.py:212-237`).
  This is the same pattern M07 extends: classify the turn, inject behavior, post-process
  the result.
- `foundry.chat` — `foundry.py:317-404`. Replays history (`foundry.py:347-361`),
  constructs the provider message via `_current_user_content` (`foundry.py:355`),
  calls the hosted agent, extracts `(answer, citations)` (`foundry.py:370`), caps
  citations to `settings.max_sources` (M03, `foundry.py:374-375`), then applies **M03
  abstention**: when `not citations or non_answer`, it replaces the answer with
  `NLG_SUPPORT_MESSAGE` and returns `escalate: True`, `escalate_reason:
  "no_citations"|"empty_answer"` (`foundry.py:388-404`). M07 **consumes** this escalate
  contract rather than re-deriving a confidence signal.
- `foundry._current_user_content` — `foundry.py:173-225` — serializes the per-turn
  preamble (`[Context for how to answer — do not repeat this back to me: ...]`) in
  front of the question. **This is the in-repo hook for an underwriting guardrail
  instruction** (analogous to M02's `buildPreamble` note). Its content is text the
  hosted agent *may* honor — reliable enough as one layer, not as the sole guard.

### 3.2 The reusable abstention / escalation contract (M03 + M09)

- `NLG_SUPPORT_MESSAGE` — `config.py:15-18`. The single shared "couldn't find enough
  approved FlexLife material… reach out to NLG support" constant, imported by both
  `service.py` and `foundry.py`. M07 reuses it for the insufficient-grounding referral.
- M03 sets `escalate: bool` + `escalate_reason: str | None` on the Foundry response
  (`foundry.py:388-404`; field on `FoundryChatResponse`, `server.py:144-145`). M07 adds
  one new reason value (`case_specific`) and otherwise reuses the mechanism.
- M09 consumes `escalate`/`escalate_reason`: the UI renders a handoff CTA
  (`learn.html:1175`) and `POST /v1/handoff/draft` (`server.py:502-515` →
  `service.draft_support_email`, `service.py:254-275`) drafts the NLG email. The draft
  path **already accepts `reason: "case_specific"`** (`HandoffDraftRequest`,
  `server.py:150-157`; `generate_support_email` reason-note, `embeddings.py:250-254`).
  **M07 must not reinvent any of this** — it only needs to *produce* the
  `case_specific` escalate signal so M09's existing flow fires.

### 3.3 Chat UI (frontend, `ui/learn.html`)

- `navSend` — `learn.html:1046-1063`. Posts to `/v1/foundry/chat` and returns
  `{ text, citations, sources, domain, escalate, escalate_reason }` — it already passes
  `escalate`/`escalate_reason` through (`learn.html:1062`). A new response field (e.g.
  `disclaimer`) must be added here to reach the log message.
- `mountChat` — `learn.html:1157-1256`. `send()` merges every returned field onto the
  pending log message via `Object.assign(pending, await opts.send(...))`
  (`learn.html:1192`), so a new `disclaimer` field would be available per-bubble with no
  widget-shape change. The AI-answer render is `learn.html:1174-1176`; the M03/M09
  escalation CTA is `learn.html:1175`; the always-on "Ask NLG Support" action is
  `learn.html:1173`. **This is where the reference-aid disclaimer renders.**
- The draft/handoff flow (`openHandoff` `learn.html:1260-1270`, `showDraftSheet`
  `learn.html:1272-1291`) already maps `escalate_reason === "case_specific"` to the
  "Ask NLG for a decision" CTA (`learn.html:1175`) and `reason: "case_specific"`
  (`learn.html:1233`). So once M07 emits `escalate_reason: "case_specific"`, the UI
  handoff is already wired.

### 3.4 Underwriting corpus — present and identifiable (evidence)

The supplied underwriting documentation **is in the Knowledge Foundation**:

- `tests/underwriting_guide_questions.md:3-4` states the source is the "Life Insurance
  Underwriting Guide, Cat No 62797(0126)", **"Indexed as `From NLG/Underwriting
  Guide.pdf` (doc #59)"**, with a second edition **"`From NLG/Downloaded
  Files/Life-Insurance-Underwriting-Guide.pdf` (doc #51)"** (`:8`).
- M03's live test (`specs/M03-citations.md:146`, `:159`) records the live Foundry agent
  returning a real citation to **"Underwriting Guide.pdf"**.
- `tests/run_foundry_eval.py` is a **130-question underwriting regression harness** that
  parses `underwriting_guide_questions.md` and drives `POST /v1/foundry/chat`
  (`run_foundry_eval.py:36`, `:107`, `:120-124`).
- `DEMO_QUESTIONS.md:4` sources its in-corpus questions from the underwriting guide; M10
  demo step expects "the Underwriting Guide PDF opens in a new tab" (`DEMO_QUESTIONS.md:119`).
- Blobs are ingested from the `From NLG` prefix (storage prefixes via
  `AZURE_BLOB_PREFIXES`, `config.py:123`), which is where both guide editions live.

**Two ingest caveats M07's grounding bar must respect** (from
`underwriting_guide_questions.md:6`, `:112-114`):

- The p.23 requirements grid and the pp.35–37 "Potential Best Offer" column are
  **green-checkmark tables that text extraction drops** — the indexed chunks carry the
  condition and its criteria but **not** the rating class. So a "best offer" answer
  often **cannot** be grounded from the corpus. The guide file flags questions like
  #52, #120, #130 as explicit **hallucination / abstention checks** (the assistant must
  say it cannot establish the class and refer to underwriting, not invent one). This is
  precisely what M07's higher grounding bar + case-specific referral must enforce.

## 4. Scope

**In scope**

- Detect **underwriting-topic** turns on the `/v1/foundry/chat` path, in-repo.
- Inject an explicit **"explain documented rules; never decide a specific case; never
  predict an applicant's approve/decline or exact rate class"** instruction into the
  underwriting turn (preamble lever), as the first guardrail layer.
- Distinguish a **documented-rule question** (answerable, with disclaimer) from a
  **case-specific-decision request** (must refer to NLG). On a case-specific-decision
  request, short-circuit to an NLG-Support referral that reuses M03's escalate contract
  with a new `escalate_reason: "case_specific"` so the **existing M09 handoff** fires.
- Apply the **higher grounding bar**: for underwriting turns, treat M03's zero-citation
  abstention as a referral (already happens) and additionally refer rather than answer
  when the question demands a rating/outcome the corpus cannot ground.
- Attach a **reference-aid disclaimer** to every underwriting answer, surfaced as a
  discrete response field and rendered under the answer in Ask Navigator.
- A shared `UNDERWRITING_DISCLAIMER` constant in `config.py` (next to
  `NLG_SUPPORT_MESSAGE`) and an optional `UNDERWRITING_DISCLAIMER` env override.

**Out of scope** — see §9.

## 5. Functional Requirements

- **FR1 — Underwriting-topic detection (in-repo).** On `/v1/foundry/chat`, after the
  M02 domain gate passes, classify whether the turn is an underwriting-technical
  question. The classifier runs in `chat_foundry` using the in-repo Azure OpenAI client
  (same call style as `classify_domain`), and returns one of `not_underwriting`,
  `underwriting_rule`, `underwriting_case`.
- **FR2 — Rule explanation allowed.** For `underwriting_rule` turns, the app answers
  normally through the hosted agent (grounded, cited), and may explain requirements,
  conditions, exceptions, and limitations.
- **FR3 — Never decide a case / never predict an outcome.** For `underwriting_case`
  turns — a request for a specific applicant's approval/decline, exact rate class, or a
  yes/no issue decision on a described individual — the app MUST NOT answer with a
  decision. It returns the NLG-referral message and fires the escalation path
  (`escalate: true`, `escalate_reason: "case_specific"`), so M09's handoff surfaces.
- **FR4 — Guardrail instruction injected.** For any underwriting turn that *is* proxied
  to the agent (`underwriting_rule`), the per-turn preamble carries an explicit
  instruction: explain only documented rules; cite them; do not state or predict a
  specific applicant's outcome or exact class; if the sources do not clearly establish
  the answer, say so and point to NLG. (Defense-in-depth with FR1/FR3; the enforceable
  decisions remain the in-repo classifier + abstention, not this text.)
- **FR5 — Higher grounding bar → refer, don't guess.** Underwriting answers are
  produced only when the approved material clearly supports them. On the Foundry track
  the practical floor is M03's zero-citation abstention (already → `escalate`,
  `NLG_SUPPORT_MESSAGE`). For underwriting turns the app MUST NOT paper over a missing
  rating/requirement with general knowledge; an answer the corpus cannot ground becomes
  an NLG referral, not an invented class (see the pp.35–37 checkmark-column caveat,
  §3.4).
- **FR6 — Reference-aid disclaimer on every underwriting answer.** A grounded
  underwriting answer carries a disclaimer — the `UNDERWRITING_DISCLAIMER` constant —
  stating the app is a reference and training aid and is not authoritative for
  underwriting decisions. It is a discrete response field, not spliced into the model's
  prose, and renders as a visible note under the answer.
- **FR7 — Distinguish guidance from decisions in the response shape.** The response
  carries a machine-readable `underwriting` signal (`rule` | `case` | null) so the UI
  and tests can tell an explained-rule answer from a referred case, and so the
  disclaimer and the `case_specific` handoff attach to the right turns.
- **FR8 — Reuse M03 + M09, don't reinvent.** The referral message is
  `NLG_SUPPORT_MESSAGE`; the escalation flag is M03's `escalate`/`escalate_reason`; the
  draft email is M09's existing `/v1/handoff/draft` with `reason: "case_specific"`. M07
  adds only the underwriting classification, the `case_specific` reason value, and the
  disclaimer.
- **FR9 — Non-underwriting turns unchanged.** A `not_underwriting` turn behaves exactly
  as today: no disclaimer, no extra referral, no added latency beyond the one
  classification call.
- **FR10 — Existing citation/grounding behavior applies.** Citations, the
  `max_sources` cap, `Sources (n)` toggle, source-open links (M10), and M03 abstention
  all continue to apply to underwriting answers unchanged.
- **FR11 — Fail-open safely.** If the underwriting classifier errors or returns
  garbage, default to `underwriting_rule` when the domain gate already judged the turn
  in-domain and underwriting-ish, so a legitimate rule question is never blocked; a
  classifier hiccup must never *auto-upgrade* a turn to a case decision. (See §10 Open
  Q2 for the alternative of folding this into one classifier call.)

## 6. Technical Design

### 6.1 Where the guardrails live — decision

| Guardrail | Lives in | Why |
| --- | --- | --- |
| Underwriting-topic + rule/case classification (FR1, FR3) | **`service.chat_foundry`** (`service.py:168-237`), a new `classify_underwriting` in `embeddings.py` | The decision must be code we own and can unit-test; same seam and pattern as M02's `classify_domain` gate (`service.py:187`). The hosted agent's instructions are in Azure and not reliable or testable here. |
| "Explain rules, never decide" instruction (FR4) | **`foundry._current_user_content` preamble** (`foundry.py:173-225`), fed a flag from `chat_foundry` | The only in-repo way to steer the hosted agent per-turn. Defense-in-depth, not the enforcing layer. |
| Case-specific → NLG referral (FR3, FR5) | **`service.chat_foundry`** — short-circuit before/after the agent call, reusing `NLG_SUPPORT_MESSAGE` + `escalate` | Mirrors the M02 out-of-domain short-circuit and M03 abstention; keeps the endpoint a thin proxy. |
| Reference-aid disclaimer (FR6) | **`chat_foundry`** sets a discrete field; **`ui/learn.html` mountChat** renders it | A discrete field avoids mutating the model's markdown and lets the UI style it like the existing source list / handoff note. |

**Rejected alternatives.** (a) Editing the hosted Foundry agent's instructions to
self-enforce — **not possible from this repo** (Azure-side; see §1, §10 Open Q1), and
untestable here. (b) Putting the guardrails on the local `/v1/chat` path
(`embeddings.chat_with_context`, prompt at `embeddings.py:200-221`) — that path is
**not** the end-user Ask Navigator engine; it is only reached as the Foundry *fallback*
(`service.py:212-237`). M07 should still harden the fallback (§6.5) so behavior is
consistent when Foundry is down, but the primary seam is `chat_foundry`.

### 6.2 Backend — underwriting classifier (`embeddings.py`)

Add next to `classify_domain` (`embeddings.py:155-184`), same tolerant-parse, fail-open
style:

```python
def classify_underwriting(
    client: AzureOpenAIClient,
    settings: Settings,
    message: str,
    history: list[dict],
) -> str:
    """Return 'not_underwriting' | 'underwriting_rule' | 'underwriting_case'.

    'underwriting_rule'  -> a question about documented FlexLife underwriting rules,
                            requirements, conditions, limits, exceptions (answerable).
    'underwriting_case'  -> a request to DECIDE a specific applicant's outcome: will
                            THIS person be approved/declined, at what exact class/table,
                            is THIS individual insurable. (Must refer to NLG.)
    Fails open to 'underwriting_rule' on any error/garbage so a real rule question is
    never blocked, and never auto-upgrades to a case decision.
    """
    prompt = f"""You are a router for a FlexLife underwriting assistant.
Classify the user's latest message as exactly one token:
- UNDERWRITING_CASE: it asks you to DECIDE or PREDICT a specific applicant's outcome —
  whether a described individual will be approved or declined, their exact rate
  class/table, or whether this specific person is insurable.
- UNDERWRITING_RULE: it asks about documented FlexLife underwriting rules,
  requirements, conditions, limits, exceptions, or what the guide says in general
  (even if it mentions an example age/amount), WITHOUT asking you to decide a person's
  outcome.
- NOT_UNDERWRITING: it is not about underwriting.
Reply with exactly one token: UNDERWRITING_CASE, UNDERWRITING_RULE, or NOT_UNDERWRITING.

Conversation so far:
{{_history_text(history)}}
Latest message: {{message}}
"""
    try:
        raw = _generate(client, settings, prompt).upper()
    except Exception:  # noqa: BLE001 - a classifier hiccup must never block a real question
        return "underwriting_rule"
    if "UNDERWRITING_CASE" in raw:
        return "underwriting_case"
    if "NOT_UNDERWRITING" in raw:
        return "not_underwriting"
    return "underwriting_rule"
```

Note the inherent fuzziness of rule-vs-case (§10 Open Q2). The classifier is the
enforceable guard; the preamble instruction and the agent's own tendency to decline a
specific class (observed in M03's live test) are additional layers.

### 6.3 Backend — guardrail wiring in `service.chat_foundry` (`service.py:168-237`)

Insert after the M02 domain gate (`service.py:187-200`), before the `foundry.chat`
call. Pseudocode:

```python
# M02 out-of-domain gate unchanged ...

uw = classify_underwriting(client, settings, message, history)  # M07

# M07 — case-specific decision request: never decide / predict; refer to NLG and reuse
# M03's escalate contract so M09's existing handoff fires.
if uw == "underwriting_case":
    return {
        "answer": NLG_SUPPORT_MESSAGE,          # shared constant, config.py:15-18
        "citations": [], "sources": [],
        "agent": settings.foundry_agent_name, "model": None,
        "response_id": None, "status": "underwriting_case",
        "domain": "in_domain",
        "escalate": True, "escalate_reason": "case_specific",   # M07's new reason value
        "underwriting": "case",                 # M07 signal (FR7)
        "disclaimer": settings.underwriting_disclaimer,         # FR6
        "source_engine": "gate",
    }

# underwriting_rule turns get the guardrail instruction; non-UW turns do not.
try:
    result = foundry.chat(
        settings=settings, question=message, history=history,
        preferences=preferences, about_me=about_me, memories=memories,
        underwriting=(uw == "underwriting_rule"),   # NEW kwarg -> preamble (6.4)
        trace_session_id=trace_session_id,
    )
    out = {**result, "sources": [], "domain": "in_domain", "source_engine": "foundry"}
except Exception:
    out = _local_fallback(...)   # 6.5

# FR6/FR7 — tag underwriting-rule answers with the signal + disclaimer. A rule turn that
# the agent already abstained on (M03 escalate) keeps its referral but still carries the
# disclaimer; no disclaimer on non-underwriting turns (FR9).
if uw == "underwriting_rule":
    out["underwriting"] = "rule"
    out["disclaimer"] = settings.underwriting_disclaimer
else:
    out.setdefault("underwriting", None)
    out.setdefault("disclaimer", None)
return out
```

Key reuse points: the `underwriting_case` return shape is a sibling of the existing
`OUT_OF_DOMAIN` decline (`service.py:187-200`) and the M03 abstention
(`foundry.py:388-404`); it sets `escalate_reason: "case_specific"`, which the UI
already maps to the "Ask NLG for a decision" CTA (`learn.html:1175`) and to
`reason: "case_specific"` on the draft request (`learn.html:1233`), which
`HandoffDraftRequest` already accepts (`server.py:156`). No M09 change required.

### 6.4 Backend — guardrail preamble (`foundry.py`)

Add an `underwriting: bool = False` kwarg to `foundry.chat` (`foundry.py:317-326`) and
to `_current_user_content` (`foundry.py:173-225`). When set, prepend a guardrail block
to the preamble lines built at `foundry.py:209-217`, e.g.:

```python
if underwriting:
    lines.append(
        "This is a FlexLife underwriting question. Explain only what the approved "
        "underwriting documentation states — rules, requirements, conditions, limits, "
        "exceptions — and cite it. Do NOT decide or predict whether any specific "
        "applicant will be approved or declined, and do NOT state an exact rate "
        "class/table for a described individual. If the sources do not clearly "
        "establish the answer, say so and point me to NLG Support."
    )
```

This is one layer only (text the hosted agent may or may not honor, per the M02 Option-B
caveat). The classifier (6.2/6.3) is what actually enforces the case-decision refusal.

### 6.5 Backend — local fallback consistency (`service.py:212-237`)

When Foundry is down and `chat_foundry` falls back to local `service.chat`, M07's
guardrails must still hold: a `underwriting_case` turn is already short-circuited in
6.3 before the fallback, so it never reaches local. For `underwriting_rule` fallback
answers, tag `underwriting: "rule"` + `disclaimer` on the mapped result (same block as
6.3). Optionally harden `embeddings.chat_with_context` (`embeddings.py:200-221`) with an
underwriting-aware line, but since that path is fallback-only and already abstains
without inventing facts (`embeddings.py:204`, `grounded` flag), the disclaimer tag is
the essential piece.

### 6.6 Config (`config.py`)

Add next to `NLG_SUPPORT_MESSAGE` (`config.py:15-18`) and the `nlg_support_email`
field (`config.py:80-82`):

```python
# M07 — appended to every underwriting answer so it reads as a reference/training aid,
# not an authoritative underwriting decision.
UNDERWRITING_DISCLAIMER = (
    "This is a reference and training aid based on the approved FlexLife underwriting "
    "documentation. It is not an authoritative underwriting decision. Final eligibility, "
    "rate class, and approval are determined by NLG underwriting."
)
```

and a `Settings` field + `load_settings()` wiring, following the existing env pattern
(`config.py:82`, `config.py:158`):

```python
underwriting_disclaimer: str = UNDERWRITING_DISCLAIMER            # Settings dataclass
underwriting_disclaimer=os.getenv("UNDERWRITING_DISCLAIMER", UNDERWRITING_DISCLAIMER)  # load_settings
```

### 6.7 Response model (`server.py`)

Add to `FoundryChatResponse` (`server.py:133-147`), both defaulted so non-underwriting
turns and existing tests are unaffected:

```python
underwriting: Literal["rule", "case"] | None = None
disclaimer: str | None = None
```

`escalate_reason` is already `str | None` (`server.py:145`), so the new
`"case_specific"` value needs no schema change.

**JSON — underwriting rule answer (grounded):**

```json
{
  "answer": "For FlexLife EZ Underwriting the face limits by age are ... [1][2]",
  "citations": [{"n": 1, "title": "Underwriting Guide.pdf", "url": "https://.../Underwriting%20Guide.pdf"}],
  "sources": [],
  "agent": "KnowledgeBase", "model": "gpt-5", "response_id": "resp_...",
  "status": "completed", "domain": "in_domain",
  "escalate": false, "escalate_reason": null,
  "underwriting": "rule",
  "disclaimer": "This is a reference and training aid ... determined by NLG underwriting.",
  "source_engine": "foundry"
}
```

**JSON — case-specific decision request (referred, agent never asked to decide):**

```json
{
  "answer": "I couldn't find enough approved FlexLife material to answer that confidently. Please reach out to NLG support so they can help.",
  "citations": [], "sources": [],
  "agent": "KnowledgeBase", "model": null, "response_id": null,
  "status": "underwriting_case", "domain": "in_domain",
  "escalate": true, "escalate_reason": "case_specific",
  "underwriting": "case",
  "disclaimer": "This is a reference and training aid ... determined by NLG underwriting.",
  "source_engine": "gate"
}
```

### 6.8 Frontend (`ui/learn.html`)

- **`navSend`** (`learn.html:1046-1063`): pass the two new fields through, alongside the
  existing `escalate`/`escalate_reason`:
  `return { ..., escalate: !!d.escalate, escalate_reason: d.escalate_reason || null,
  underwriting: d.underwriting || null, disclaimer: d.disclaimer || null };`
  `mountChat`'s `Object.assign` (`learn.html:1192`) then copies them onto the log
  message (`m.underwriting`, `m.disclaimer`).
- **`mountChat` render** (`learn.html:1174-1176`): when `m.disclaimer` is present on a
  rich (non-pending, non-intro, non-failed) AI message, render it as a small styled note
  under the answer bubble (next to `handoffCta`, `learn.html:1175`), e.g.
  `<div class="uw-disclaimer">${esc(m.disclaimer)}</div>`. Add one CSS rule for
  `.uw-disclaimer` (muted, small, bordered — reuse the `.handoff-note` look,
  `learn.html:1275`).
- **Case-specific handoff**: no new UI. A `case` turn arrives with
  `escalate: true`, `escalate_reason: "case_specific"`, so the existing escalation CTA
  (`learn.html:1175`) renders "Ask NLG for a decision" and `openHandoff` already sends
  `reason: "case_specific"` (`learn.html:1233`). The disclaimer still shows on the
  referral bubble.

**Data flow:** chat turn → `/v1/foundry/chat` → `chat_foundry` (classify_underwriting →
rule/case branch → preamble instruction + disclaimer tag) → `FoundryChatResponse`
(`underwriting`, `disclaimer`, `escalate_reason`) → `navSend`/`mountChat` → disclaimer
note + (for `case`) the existing M09 handoff CTA.

## 7. Acceptance Criteria (Given/When/Then)

- **AC1 (ask underwriting rules; story AC "a user can ask… underwriting rules").**
  *Given* Ask Navigator, *When* the user asks a documented-rule question (e.g. "What are
  the EZ Underwriting face limits on FlexLife by age?"), *Then* a grounded
  natural-language answer returns with `underwriting: "rule"`, `domain: "in_domain"`,
  and citations.
- **AC2 (grounded in approved underwriting docs; story ACs "grounded…", "existing
  citation/grounding behavior applies").** *Given* a rule question the guide covers,
  *When* answered, *Then* `citations` reference the underwriting guide (doc #59/#51,
  §3.4), capped at `max_sources`, with the `Sources (n)` toggle and source-open links
  working as for any other answer.
- **AC3 (explain requirements/conditions/exceptions/limitations; story AC).** *Given*
  questions about exam/lab requirements, tobacco/build rules, financial limits, state
  rules, or knockouts, *When* the guide supports them, *Then* the app explains the
  documented rule (e.g. "ages 60+ need a physical within 24 months or the application is
  declined").
- **AC4 (never decide a case / never predict outcome; story ACs "does not make
  independent underwriting judgments", "distinguish… case-specific decisions", Out of
  Scope "predicting the outcome of a specific applicant").** *Given* a request to decide
  a specific applicant ("Will my 47-year-old diabetic smoker client be approved, and at
  what exact rate class?"), *When* processed, *Then* `underwriting: "case"`,
  `escalate: true`, `escalate_reason: "case_specific"`, the answer is the NLG-referral
  message, and **no** approve/decline/rate-class decision for that individual is stated.
- **AC5 (distinguish guidance from decisions; story AC).** *Given* the classifier,
  *When* a `rule` question and a `case` request are each sent, *Then* the `rule` turn is
  answered with the disclaimer and the `case` turn is referred — the response's
  `underwriting` field distinguishes them.
- **AC6 (higher grounding bar → refer, don't guess; story ACs "only… clearly
  supported", "generated only when… sufficient support", Out of Scope "inferring
  undocumented practices").** *Given* an underwriting question the corpus cannot ground
  (e.g. a pp.35–37 "best offer" rating-class the text extraction dropped, §3.4, or #52
  "which states are exempt from the Statement of Health"), *When* asked, *Then* the app
  refers to NLG / says it isn't established rather than inventing a class or exception
  (M03 zero-citation abstention → `escalate`, reused).
- **AC7 (reference-aid disclaimer; story AC "include language indicating… a reference
  aid… not authoritative").** *Given* any underwriting answer (rule or case), *When*
  rendered, *Then* the `disclaimer` field is present and a visible "reference and
  training aid, not an authoritative underwriting decision" note shows under the answer.
- **AC8 (reuse M09 handoff).** *Given* a `case_specific` escalation, *When* the user
  taps "Ask NLG for a decision", *Then* `POST /v1/handoff/draft` is called with
  `reason: "case_specific"` and the existing draft sheet opens — no new handoff code.
- **AC9 (non-underwriting unchanged; FR9).** *Given* a non-underwriting FlexLife
  question, *When* answered, *Then* `underwriting: null`, no disclaimer, and behavior is
  identical to today (one added classification call aside).
- **AC10 (fail-open; FR11).** *Given* the underwriting classifier errors or returns
  garbage, *When* the turn is in-domain, *Then* it defaults to `underwriting_rule`
  (answered + disclaimer) and never silently auto-escalates to a `case` decision.

## 8. Test Plan

**Unit — classifier (pytest, mock `embeddings._generate`, mirror
`tests/test_m02_domain_gate.py`):**

- `classify_underwriting` → `underwriting_case` for "will THIS 47-y-o diabetic smoker be
  approved and at what class?"; `underwriting_rule` for "what are the EZ face limits by
  age?"; `not_underwriting` for "how do caps and floors work?".
- Tolerates wrapped/lower-cased tokens (substring + `.upper()`); fails open to
  `underwriting_rule` on raised exception and on garbage (never to `case`).

**Unit — `service.chat_foundry` (monkeypatch `classify_domain`, `classify_underwriting`,
`foundry.chat`):**

- `underwriting_case` → returns `NLG_SUPPORT_MESSAGE`, `escalate is True`,
  `escalate_reason == "case_specific"`, `underwriting == "case"`, `disclaimer` set, and
  **`foundry.chat` is not called** (assert the mock is not invoked, like
  `test_chat_foundry_declines_out_of_domain_without_calling_agent`).
- `underwriting_rule` → `foundry.chat` *is* called with `underwriting=True`; result
  carries `underwriting == "rule"` and `disclaimer` set; citations passed through.
- `not_underwriting` → `underwriting is None`, `disclaimer is None`, behavior unchanged.
- Local-fallback path (`foundry.chat` raises) on a `rule` turn still tags
  `underwriting`/`disclaimer`; a `case` turn never reaches fallback.

**Unit — preamble (`foundry._current_user_content` / `foundry.chat`, extend
`tests/test_foundry_prompt.py`):** `underwriting=True` adds the guardrail line to the
provider message; `underwriting=False` does not.

**Integration (FastAPI `TestClient`, classifiers + `foundry.chat` mocked):**

- `POST /v1/foundry/chat` with a rule question → 200, body validates against updated
  `FoundryChatResponse`, `underwriting == "rule"`, `disclaimer` non-null.
- Case-decision question → 200, `underwriting == "case"`, `escalate == true`,
  `escalate_reason == "case_specific"`, `citations == []`.
- `reason: "case_specific"` accepted by `POST /v1/handoff/draft` (already covered by
  M09 tests; add a case asserting the draft's reason-note wording).

**Regression harness (already exists):** `tests/run_foundry_eval.py` drives all 130
underwriting questions. Use it to (a) confirm rule questions stay answered+cited, and
(b) spot-check the ★ hallucination/abstention rows (#52, #120, #130) now refer rather
than invent — record a before/after in `tests/results/`.

**Frontend (jsdom, per the dev-loop memory — the browser extension is on another
machine):** the disclaimer note renders under a `rule` answer and under a `case`
referral; a `case` turn shows the "Ask NLG for a decision" CTA; a non-underwriting
answer shows no disclaimer.

**Manual smoke:** rebuild the Docker image (code change, per the dev loop), open
`/app/learn.html`, run the `DEMO_QUESTIONS.md` underwriting items plus one explicit
case-decision ask; confirm the disclaimer, the rule-vs-case split, citations, and the
simulated handoff.

## 9. Out of Scope

- Predicting the underwriting outcome of a specific applicant, or stating an exact rate
  class/table for a described individual (story Out of Scope — M07 *prevents* this).
- Replacing NLG underwriting personnel or formal underwriting review.
- Application-processing workflows beyond the underwriting rules in the supplied POC
  documentation.
- Inferring undocumented underwriting practices or exceptions.
- Editing the hosted Foundry agent's Azure-side instructions or its retrieval/grounding
  (out-of-repo; see §10). M07's enforcement is in-repo.
- Fixing the PDF ingest so the pp.23 / pp.35–37 checkmark tables extract their rating
  columns (a separate ingest story; M07 only ensures the app *refers* rather than
  invents when they're missing — §3.4).
- The abstention *decision* mechanics (owned by M03) and the handoff draft (owned by
  M09); M07 reuses both.
- Underwriting answers on surfaces other than Ask Navigator (Coach console, roleplay
  Ask sheet).

## 10. Dependencies & Open Questions

**Dependencies**

- **M03 (Citations)** — M07 reuses `escalate`/`escalate_reason` (`foundry.py:388-404`,
  `server.py:144-145`) and `NLG_SUPPORT_MESSAGE` (`config.py:15-18`); it adds the
  `case_specific` reason value. Keep that contract stable.
- **M09 (Handoff)** — M07 produces `escalate_reason: "case_specific"`, which M09's
  existing UI (`learn.html:1175`, `:1233`) and `POST /v1/handoff/draft`
  (`server.py:502-515`, `HandoffDraftRequest.reason` `server.py:156`,
  `generate_support_email` `embeddings.py:250-254`) already handle. No M09 code change.
- **M02 (Domain gate / Foundry chat)** — M07 runs *after* the `classify_domain` gate in
  the same `chat_foundry` seam (`service.py:187-237`) and reuses the hosted-agent chat +
  history replay.
- Azure OpenAI chat deployment configured (`config.py:131-134`) — M07 adds one
  classification call per turn on top of M02's. Hosted Foundry agent configured
  (`FOUNDRY_PROJECT_ENDPOINT` / `FOUNDRY_API_KEY`, `foundry.py:39-40`).
- The underwriting corpus must stay indexed (doc #59/#51, §3.4). Docker rebuild required
  to ship backend changes (dev-loop memory).

**Open questions**

1. **Can the hosted Foundry agent's instructions be controlled from this repo? — No
   (confirmed), which is why the guardrails are in-repo.** M02 §3 and M03 §3.1/§9 both
   state the agent's instructions/grounding live in Azure, out of repo; nothing in
   `src/` writes them, and the repo only proxies turns. So M07's enforceable guardrails
   are the in-repo classifier + referral + disclaimer in `chat_foundry`
   (`service.py:168-237`), with the preamble (`foundry.py:173-225`) as a non-guaranteed
   second layer. **Recommend** also asking the Azure agent owner to tighten the agent's
   own underwriting instructions as defense-in-depth, tracked as out-of-repo work — but
   M07 must not *depend* on it. Confirm who owns that Azure config.
2. **How reliably can one classifier split "documented-rule" from "case-specific
   decision", and should it fold into `classify_domain` to avoid a second LLM call per
   turn?** The line is genuinely fuzzy (many guide questions name an example age/amount
   yet are rule questions, e.g. #1–#8, while #120/#130 are deliberate abstention traps).
   Options: (a) a dedicated `classify_underwriting` (this spec — clearest, one extra
   call); (b) extend `classify_domain` to return `{domain, underwriting}` in one call
   (cheaper, but couples two concerns and complicates the M02 tests); (c) rely on the
   agent's own tendency to decline a specific class (observed in M03's live test) plus
   the disclaimer, and only hard-refer on zero-citation abstention. **Recommend (a)** for
   the POC, fail-open to `rule`; revisit (b) if per-turn latency matters. Validate the
   prompt against a small labeled slice of the 130 eval questions.
3. **Disclaimer placement & wording.** Discrete field rendered as a UI note (this spec)
   vs. appended into the answer text. Recommend the field; confirm exact copy and whether
   it should also appear on the `case` referral bubble (this spec: yes). Confirm final
   wording with the product owner.
4. **Scope of "underwriting answer" for the disclaimer.** Only turns the classifier tags
   `rule`/`case` (this spec), or any answer whose citations include the underwriting
   guide? The classifier-tag approach is simpler and testable; the citation-based
   approach would also catch underwriting facts surfaced inside a general question.
   Recommend classifier-tag for the POC.

## 11. Rough Effort Estimate

| Area | Work | Est. |
| --- | --- | --- |
| Config | `UNDERWRITING_DISCLAIMER` constant + `Settings` field + env | ~0.25 day |
| Backend | `classify_underwriting` (`embeddings.py`) + prompt tuning on eval slice | ~0.5 day |
| Backend | rule/case wiring + disclaimer tagging in `chat_foundry` | ~0.5 day |
| Backend | guardrail preamble in `foundry.chat` / `_current_user_content` | ~0.25 day |
| Backend | `FoundryChatResponse` fields (`underwriting`, `disclaimer`) | ~0.25 day |
| Frontend | pass fields through `navSend`; render disclaimer note + CSS | ~0.5 day |
| Tests | classifier + `chat_foundry` + preamble + endpoint pytest, jsdom | ~0.75 day |
| Eval | run `run_foundry_eval.py`, spot-check ★ abstention rows, tune | ~0.5 day |

**Total: ~3.5 days.** No new endpoint, no M09/M03 rework, no ingest change; the work is
the in-repo underwriting classification + referral + disclaimer on the existing Foundry
chat seam.
