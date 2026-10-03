# M15 — Specialized Learning / Generate Dynamic Learning Sessions

## 1. Summary

A user asks for a lesson on **any** FlexLife topic in plain natural language ("explain
the 1% floor to a nervous client", "how do accelerated benefit riders pay out") and the
app builds a **coherent, grounded explanatory lesson** — presented as **text and audio** —
without the user picking from a fixed catalog.

**M15 is largely already built.** The salesDJ "Learn" **mix** generator
(`src/rag_layer/learn.py`) is a dynamic, free-text, topic-driven, Knowledge-Foundation-
grounded lesson pipeline that already produces structured text articles (with a talk
track) and two-host audio episodes rendered to MP3. Free-text topic entry is live in the
UI (`ui/learn.html` → `renderCreate`, POST `/v1/learn/mixes`). So the hard parts —
retrieval, synthesis into a coherent lesson, text + audio presentation, "never invent"
persona — exist and ship today.

M15's remaining work is **three targeted gaps**, all about honoring the story's grounding
and preference contracts rather than building new machinery:

1. **Graceful abstention when the topic is unsupported.** Today a free-text topic with no
   usable corpus support produces a **failed mix** (a raw `RuntimeError` on the row), and a
   weakly-supported topic generates anyway (no relevance floor). The story requires the app
   to *not invent* and to *refer the user to NLG Support*. M15 wires the Learn retrieval
   path into the **same M03 relevance floor + `NLG_SUPPORT_MESSAGE`** abstention the chat
   tracks already use (`service.select_citations`, `config.NLG_SUPPORT_MESSAGE`).
2. **M06 preference-awareness.** Mix generation sends only `{kind, prompt, length}` and
   ignores the user's M06 response preferences (response length, **Plain Language**), which
   today only reach the Ask Navigator chat (`navSend`, `ui/learn.html:1046-1063`). M15 feeds
   the M06 length + Plain Language preferences into lesson synthesis so a lesson respects the
   same presentation settings as chat.
3. **Explanatory-lesson scope + (optional) structure hint.** The story's "lesson" is the
   **article** (text) and **audio** formats; flashcards are a drill, out of M15's
   explanatory scope. Optionally, let the app *suggest* the structure (article vs. audio)
   from the topic instead of always requiring the user to choose.

This spec reuses M03's grounding/abstention contract verbatim (the `NLG_SUPPORT_MESSAGE`
constant and the `min_similarity`/`max_sources` floor) and depends on M06 for the
preference source. It changes `learn.py` (retrieval + prompts), `server.py`
(`MixCreateRequest` gains optional preferences), and `ui/learn.html` (`renderCreate` passes
prefs; a graceful "refer to NLG Support" state on the mix screen). No new engine is
introduced.

## 2. User Story (verbatim)

**User Story: Generate Dynamic Learning Sessions** — As a FlexLife sales agent or learner,
I want to request a lesson on a topic of my choosing, so that I can receive targeted
training based on the FlexLife knowledge available to the application.

Description: The POC will allow users to request learning content dynamically using natural
language. The user may specify a topic, concept, rule, or area they want to learn about
without selecting from a predefined course catalog. The application should use the Knowledge
Foundation to retrieve relevant information and organize it into a coherent explanatory
learning session. The application may determine an appropriate structure for the lesson
based on the requested topic and available source material. Generated lessons should support
both text and audio presentation and should respect applicable user response preferences,
such as desired level of detail and Plain Language mode. All factual lesson content should
remain grounded in the Knowledge Foundation. If the requested topic cannot be adequately
supported by the available knowledge, the application should not invent instructional
content and should direct the user to NLG Support where appropriate.

Acceptance Criteria:
- A user can request a learning session using a natural-language description of the topic
  they want to learn about.
- The user is not required to select from a predefined curriculum or course catalog.
- The application retrieves relevant information from the Knowledge Foundation for the
  requested topic.
- The application synthesizes the retrieved information into a coherent explanatory lesson
  rather than simply returning raw search results.
- The application can determine an appropriate lesson structure based on the requested topic
  and available content.
- Generated lessons remain grounded in the Knowledge Foundation.
- Generated lessons can be presented as text.
- Generated lessons can be presented as audio.
- Applicable user preferences, including response length and Plain Language mode, can
  influence how the lesson is presented.
- If sufficient supporting content cannot be found, the application does not generate
  unsupported instructional material.
- An explanatory lesson is sufficient for the POC; interactive exercises or assessments are
  not required.

Out of Scope:
- Predefined course catalogs or curriculum mapping.
- Formal learning paths or course sequencing.
- Quizzes, assessments, or knowledge checks.
- Scoring, certification, or completion tracking.
- Production-grade instructional design.
- Generating lesson content from sources outside the approved Knowledge Foundation.

## 3. Current State — what exists

The Learn "mix" generator is the foundation for M15 and is already a dynamic, grounded,
topic-driven text+audio lesson builder. Verified against the code below. Each claim is a
`file:line` anchor so the gaps can be read precisely against the shipped behavior.

### 3.1 Free-text topic entry (no catalog) — ALREADY BUILT

- **UI compose screen.** `renderCreate()` (`ui/learn.html:1781-1834`) is a free-text
  "What do you want to learn?" textarea (`ui/learn.html:1786`, `maxlength="400"`), with
  suggestion chips (`ui/learn.html:1808-1810`) and voice input
  (`ui/learn.html:1812-1823`). The chips are *suggestions*, not a catalog; any free text is
  accepted. On submit it POSTs `{kind, prompt, length}` to `/v1/learn/mixes`
  (`ui/learn.html:1825-1833`).
- **API accepts free text.** `MixCreateRequest` (`src/rag_layer/server.py:285-288`):
  `kind: Literal["audio","article","flashcards"]`, `prompt: str (3..400)`,
  `length: Literal["short","long"] = "short"`. `create_mix_endpoint`
  (`server.py:683-693`) persists a queued row and enqueues `run_generation` on a background
  pool (`learn.py:62-68`, `learn.py:363`). Returns `202` + `MixSummary`.
- **Verdict:** story ACs "request a learning session using natural language" and "not
  required to select from a predefined curriculum" are **met today**. (A fixed `CURRICULUM`
  also exists — `src/rag_layer/curriculum.py`, seeded via `/v1/learn/seed`,
  `server.py:696-724` — but it is *recommended* content, not a requirement to use; free-text
  is the primary path.)

### 3.2 Retrieval + synthesis into a coherent lesson — ALREADY BUILT (grounding gap, see 3.5)

- **Topic → retrieval queries.** For a free-text mix, `plan_queries`
  (`learn.py:164-178`) turns the prompt into 3 focused search queries via an LLM JSON call.
- **Grounded retrieval from the local pgvector KB.** `gather_contexts`
  (`learn.py:181-197`) unions the top chunks per query (deduped, best-similarity first) via
  `service.retrieve_contexts` (`service.py:35-44` → `embed_texts` + `db.search_chunks`).
  `run_generation` pulls 12 (short) or 18 (long) contexts (`learn.py:382-384`).
  *(Pinned curriculum mixes instead read exact document sections via
  `gather_pinned_contexts`, `learn.py:200-214`; free-text mixes always use semantic
  retrieval.)*
- **"Never invent" persona.** `_PERSONA` (`learn.py:70-79`) constrains every generator:
  "Use ONLY the source excerpts provided: never invent numbers, rates, caps … If the
  excerpts do not cover something, say so briefly instead of guessing." This is the same
  grounding intent as M03's hardened prompts.
- **Synthesis into a structured lesson (not raw results).** `generate_article`
  (`learn.py:229-255`) returns a JSON lesson: `title`, `subtitle`, `sections[]`
  (heading/paragraphs/bullets), `key_takeaways`, `say_it_like_this`, `watch_outs`.
  `generate_audio_script` (`learn.py:282-308`) returns a two-host (`Ava`/`Andrew`) spoken
  script. Each generated object also stores the numbered `refs` (`excerpt_refs`,
  `learn.py:342-360`) and a trimmed `sources` list (`_trim_sources`, `learn.py:314-327`) so
  the lesson is traceable to the corpus.
- **Verdict:** ACs "retrieves relevant information", "synthesizes … a coherent explanatory
  lesson rather than … raw search results", and "grounded in the Knowledge Foundation" are
  **met today** — subject to the abstention gap in 3.5.

### 3.3 Text and audio presentation — ALREADY BUILT

- **Text.** `renderArticle` (`ui/learn.html:~2023`) renders the article lesson (sections,
  takeaways, say-it-like-this, watch-outs). This is the text presentation.
- **Audio.** Two routes, both via Azure Speech:
  - *Audio episodes* are synthesized at generation time when Speech is configured —
    `run_generation` calls `get_speech_client(settings).synthesize_dialogue(turns)` and
    stores the MP3 on the row (`learn.py:426-442`).
  - *Articles* get an on-demand "Listen" narration — `article_narration`
    (`learn.py:457-484`) splits the article into read-aloud segments and
    `run_audio_render` (`learn.py:487-541`) synthesizes them; the UI exposes "Listen"
    (`ui/learn.html:2015`, `renderArticle`).
  - Served by `GET /v1/learn/mixes/{id}/audio` with HTTP Range support for seeking
    (`server.py:744-789`); (re)rendered by `POST …/render-audio` (`server.py:792-818`).
- **Verdict:** ACs "presented as text" and "presented as audio" are **met today**.

### 3.4 Lesson structure selection — PARTIAL

- Structure is a **fixed template per `kind`**: article = sections/takeaways/etc., audio =
  two-host turns, flashcards = Q/A deck (`learn.py:229-308`). The length spec
  (`LENGTH_SPECS`, `learn.py:38-51`) sets target words/sections/turns per `kind`×`length`.
- The **user chooses the `kind`** in `renderCreate` (`ui/learn.html:1800-1806`); the app
  does **not** infer structure from the topic. The story says the app *may* determine
  structure, so this is a permissive AC — currently satisfied at the "appropriate template
  per chosen format" level, not auto-selected. (See 4 / FR7 for the optional enhancement.)

### 3.5 Grounding abstention when unsupported — GAP (does not meet the story)

- **M03's abstention exists but the Learn path does not use it.** `config.NLG_SUPPORT_MESSAGE`
  (`config.py:15-18`) and `service.select_citations` (filter below
  `settings.min_similarity`, cap at `settings.max_sources`; `service.py:67-91`,
  `config.py:69-70`) are implemented and used by `service.answer`/`service.chat`
  (`service.py:113-120`, `140-155`) and the Foundry track (`foundry.py:374-391`).
- **The Learn path bypasses all of it.** `gather_contexts` (`learn.py:181-197`) uses
  `retrieve_contexts` with **no `min_similarity` floor** and no `NLG_SUPPORT_MESSAGE`
  branch. In `run_generation`:
  - *Zero contexts* → `raise RuntimeError("No documents are indexed yet, so nothing can be
    generated.")` (`learn.py:386`), which the `except` turns into a **failed mix**
    (`status="failed"`, raw error text on the row, `learn.py:448-451`). The user sees a
    generic failure screen (`renderFailed`), **not** a "refer to NLG Support" message.
  - *Weak-but-nonzero contexts* → the lesson is generated anyway from low-similarity chunks.
    There is no relevance floor, so an off-topic prompt that still returns some chunks yields
    an ungrounded-feeling lesson.
- **Verdict:** ACs "if sufficient supporting content cannot be found, the application does
  not generate unsupported instructional material" and "direct the user to NLG Support where
  appropriate" are **NOT met** — the current behavior is a hard failure or an ungrounded
  lesson, not a graceful, floor-gated abstention. **This is M15's primary gap.**

### 3.6 M06 preference-awareness — GAP

- **M06 preferences live client-side and reach only chat.** `DEFAULT_PREFS = { length:
  "balanced", format: "auto", tone: "warm", plain: false, alwaysSources: false, role: "" }`
  stored in `localStorage` key `chatPrefs` (`ui/learn.html:1033-1038`). `navSend`
  (`ui/learn.html:1046-1063`) sends a `preferences` object (`length`, `format`, `tone`,
  `plain`, `always_sources`) to `/v1/foundry/chat`. *(Per-user persistence of these in the
  server profile is M06's concern; `profile.py`/`PUT /v1/profile/settings`,
  `server.py:1082-1088`, currently store only language codes, not answer preferences.)*
- **Mix creation ignores them.** `renderCreate` POSTs only `{kind, prompt, length}`
  (`ui/learn.html:1830`); `MixCreateRequest` has no preference fields
  (`server.py:285-288`); the Learn generators (`learn.py:229-308`) take no preference input.
  Note the mix `length` (`short`/`long`) is a **duration** knob (5 vs. 10 min), a different
  axis from M06's **response length** (Brief/Balanced/Detailed) and unrelated to Plain
  Language.
- **Verdict:** AC "applicable user preferences, including response length and Plain Language
  mode, can influence how the lesson is presented" is **NOT met** — the lesson does not read
  M06 preferences. **This is M15's second gap.**

### 3.7 Explanatory-lesson scope

- The story's lesson is explanatory **text** (article) and **audio**. `flashcards`
  (`learn.py:258-279`) is a drill/Q-A deck — closer to a knowledge check, which the story
  lists as out of scope. M15 does not need to remove flashcards (it is an existing Learn
  feature), but the **M15 lesson surface is article + audio**; flashcards is explicitly out
  of M15's deliverable (see §9).

## 4. Scope

**In scope**

- Route the Learn free-text retrieval path through the **M03 grounding floor** and the
  shared **`NLG_SUPPORT_MESSAGE`** abstention, so an unsupported topic produces a graceful
  "refer to NLG Support" result instead of a failed mix or an ungrounded lesson.
- A **distinct, machine-readable "unsupported topic" state** on a mix (not a generic
  `failed`) so the UI can show the NLG-Support referral and offer to ask the Navigator /
  escalate.
- Feed the user's **M06 response-length and Plain Language** preferences into article and
  audio lesson synthesis (presentation only — grounding and source material unchanged).
- Keep **text + audio** presentation (already built) and confirm both honor preferences.
- **Optional:** let the app *suggest* a lesson structure (article vs. audio) from the topic,
  while keeping user override.
- Tests (backend + jsdom) and a manual smoke per the dev loop.

**Out of scope** — see §9.

## 5. Functional Requirements

- **FR1 — Free-text topic (already built; keep).** A user requests a lesson by typing a
  natural-language topic; no catalog selection is required. (`renderCreate`,
  `MixCreateRequest`.)
- **FR2 — Grounded retrieval (already built; keep).** Lesson facts come from the Knowledge
  Foundation via `retrieve_contexts`/`search_chunks`, and the generator persona forbids
  inventing facts.
- **FR3 — Coherent synthesis (already built; keep).** The lesson is a structured
  explanatory artifact (article sections / audio script), not a raw result list.
- **FR4 — Relevance floor on the Learn path.** Learn retrieval MUST apply the same
  `min_similarity` floor as the M03 local track (`service.select_citations`/`config`). If,
  after the floor, there is insufficient grounded support, the lesson MUST NOT be generated.
- **FR5 — Graceful abstention + NLG Support referral.** When FR4 finds insufficient support,
  the mix resolves to an **`unsupported`** state carrying the shared `NLG_SUPPORT_MESSAGE`
  (`config.py:15-18`). The UI shows that message (not a raw error) and offers a path to NLG
  Support (reuse the M09 handoff affordance where available; otherwise a plain referral).
  No instructional content is fabricated.
- **FR6 — Preference-aware presentation (M06).** Article and audio synthesis MUST honor the
  user's M06 **response length** (Brief/Balanced/Detailed) and **Plain Language** mode,
  affecting verbosity/wording only — never the facts, grounding, or sources. Other M06
  axes (format, tone) MAY be honored where they make sense for a lesson (e.g. tone in the
  article talk track); at minimum length + Plain Language are respected.
- **FR7 — Structure selection (optional).** The app MAY suggest the lesson structure
  (article vs. audio) from the topic/preferences; the user can always override. If not
  implemented, the fixed-template-per-chosen-kind behavior satisfies the permissive AC.
- **FR8 — Text presentation (already built; keep).** The lesson renders as readable text
  (article page).
- **FR9 — Audio presentation (already built; keep).** The lesson renders as audio (audio
  episode MP3, or article "Listen" narration), served seekably.
- **FR10 — No quiz/assessment required.** An explanatory lesson suffices; M15 adds no
  quizzes, scoring, or completion gating.
- **FR11 — Knowledge-Foundation only.** No lesson content is drawn from outside the approved
  corpus. (The Learn path uses the local pgvector index; see Open Questions re: which KB is
  canonical for lessons vs. the Foundry chat KB.)

## 6. Technical Design

### 6.1 Backend — grounding floor + abstention on the Learn path (`learn.py`, `service.py`)

**Where the floor goes.** Add the `min_similarity`/sufficiency check in `run_generation`
(`learn.py:363-451`), right after contexts are gathered for the **free-text** branch
(`learn.py:382-386`). Reuse the M03 primitives rather than inventing a new rule:

- Apply the same floor `service.select_citations` uses (`service.py:67-91`,
  `settings.min_similarity`/`settings.max_sources`, `config.py:69-70`). Concretely, keep the
  wide `gather_contexts` retrieval for synthesis breadth, but compute a **sufficiency gate**
  from it: the lesson proceeds only if at least one chunk clears `settings.min_similarity`
  (optionally require N≥2 distinct documents — see Open Q2). If the gate fails, abstain.
- Replace the current hard `RuntimeError` on empty/weak contexts (`learn.py:386`) with the
  abstention path below. (The *zero-indexed-documents* operational error can remain a real
  failure, but a *topic-not-supported* outcome must be the graceful state.)

**New terminal state `unsupported`.** Rather than overload `failed` (which the UI renders as
a generic error, `renderFailed`), add an `unsupported` resolution so the UI can show the
NLG-Support message. Two implementation options:

- **Option A (recommended): a new status value.** Extend the mix status set to include
  `"unsupported"`. `run_generation` sets `status="unsupported"` and stores
  `NLG_SUPPORT_MESSAGE` in `summary`/`content` instead of raising. `serialize_mix`
  (`learn.py:544-570`) passes it through; `MixSummary.status` Literal
  (`server.py:297`) gains `"unsupported"`. Small, explicit, and symmetric with M03's
  `insufficient_support` flag.
- **Option B: keep `failed` + a flag.** Set `status="failed"` but add
  `content.insufficient_support = true` + the message. Less invasive to the status enum but
  muddies "failed" (a real error) with "unsupported" (an expected outcome). **Rejected** for
  the same reason M03 chose explicit flags.

Sketch (free-text branch of `run_generation`, replacing `learn.py:382-386`):

```python
queries = plan_queries(client, settings, prompt, kind)
total = 18 if length == "long" else 12
contexts = gather_contexts(settings, client, queries, total=total)

# M15: reuse the M03 relevance floor as a sufficiency gate (do not invent a new rule).
grounded = [c for c in contexts if float(c["similarity"]) >= settings.min_similarity]
if not grounded:                      # topic not supported by the approved corpus
    with connect(settings) as conn:
        update_mix(conn, mix_id, status="unsupported", error=None,
                   summary=NLG_SUPPORT_MESSAGE,
                   content={"unsupported": True, "message": NLG_SUPPORT_MESSAGE,
                            "queries": queries})
    return
contexts = grounded                   # synthesize only from floor-clearing chunks
```

Import `NLG_SUPPORT_MESSAGE` from `config` (already a shared constant, `config.py:15-18`;
`service.py:8` and `foundry.py:29` import it the same way). *Zero indexed documents* (an
operational problem, not an unsupported topic) can still raise/`failed`.

### 6.2 Backend — preference-aware synthesis (`learn.py`, `server.py`)

**Carry preferences into the mix.** Add optional preference fields to `MixCreateRequest`
(`server.py:285-288`), defaulting to none so existing callers and curriculum seeding are
unaffected:

```python
class MixCreateRequest(BaseModel):
    kind: Literal["audio", "article", "flashcards"]
    prompt: str = Field(..., min_length=3, max_length=400)
    length: Literal["short", "long"] = "short"
    # M06 presentation preferences (optional; presentation only, never changes facts).
    response_length: Literal["brief", "balanced", "detailed"] | None = None
    plain_language: bool | None = None
```

Thread them through `create_mix` (persist on the row, or pass into `run_generation`) so the
background job can read them. (Simplest: store a small `prefs` dict in the row's `content`
or a new column; the generator reads it. Keep it POC-light.)

**Apply in the prompts.** Add a preference preamble to `_PERSONA` usage in
`generate_article` (`learn.py:229-255`) and `generate_audio_script`
(`learn.py:282-308`) — a presentation directive, kept strictly separate from the
grounding rules:

- **Plain Language on** → "Use the simplest possible wording, expand jargon on first use,
  and add a one-line explanation where a brand-new agent would need it." (The persona
  already leans this way; Plain Language makes it explicit and stronger.)
- **Response length** → nudge verbosity *within* the existing `LENGTH_SPECS` target
  (`learn.py:38-51`): `brief` trims toward the low end / fewer sub-points, `detailed` toward
  the high end / more worked explanation, `balanced` = current behavior. **Do not** let the
  M06 length silently override the mix duration (`short`/`long`) — they are different axes;
  M06 length tunes density, mix length tunes runtime. (Resolve the interaction in Open Q3.)

Presentation-only guarantee (mirrors M06 FR "affect presentation … not the factual
grounding"): the preference text must never introduce facts or relax the "use ONLY the
source excerpts" rule.

### 6.3 Backend — optional structure suggestion (FR7)

If implemented: a tiny classifier (reuse `_llm_json`, `learn.py:114-149`) maps the topic to
a suggested `kind` (e.g. conceptual/"explain" → article; conversational/"how do I talk
about" → audio), surfaced as a default selection in `renderCreate`. The user still chooses.
Low priority; can be deferred without failing any hard AC.

### 6.4 Frontend — compose + abstention rendering (`ui/learn.html`)

- **Pass preferences on create.** In `renderCreate`'s submit (`ui/learn.html:1825-1833`),
  include the M06 prefs from `chatPrefs` (`ui/learn.html:1033-1035`):

  ```js
  const m = await api("/v1/learn/mixes", { method: "POST", body: JSON.stringify({
    kind, prompt, length,
    response_length: chatPrefs.length,      // brief | balanced | detailed
    plain_language: !!chatPrefs.plain,
  }) });
  ```

  (Reuse the same `chatPrefs` object `navSend` already reads, `ui/learn.html:1046-1063`, so
  Learn and chat honor one preference source.)
- **Render the `unsupported` state.** In `renderMix` (`ui/learn.html:1837-1859`), branch on
  `m.status === "unsupported"` *before* `failed` and render the `NLG_SUPPORT_MESSAGE`
  (`m.summary`/`m.content.message`) as a calm referral card — not the red error box
  (`renderFailed`). Offer a "Ask the Navigator" / "Prepare a request to NLG Support" action
  that reuses the M09 handoff flow when present, or a plain link/toast otherwise. No retry
  spinner (retrying the same unsupported topic won't help).
- **Audio/text unchanged.** `renderArticle`/`renderAudio` already present the lesson and
  honor seeking; no change needed beyond the content now reflecting preferences.

### 6.5 Data flow summary

```
Topic (free text) + M06 prefs (chatPrefs)
  → POST /v1/learn/mixes  (MixCreateRequest + response_length/plain_language)
  → run_generation:
       plan_queries → gather_contexts (local pgvector KB)
       → M15 sufficiency gate (min_similarity floor)
            ├─ insufficient → status="unsupported" + NLG_SUPPORT_MESSAGE  (FR4/FR5)
            └─ sufficient  → generate_article / generate_audio_script
                               (persona grounding + M06 preference preamble)  (FR3/FR6)
       → (audio) Azure Speech synth → MP3 on row
  → renderMix: article text / audio player / unsupported referral card
```

## 7. Acceptance Criteria (Given/When/Then)

- **AC1 (free-text topic; story ACs "request … using natural language", "not required to
  select from a predefined curriculum").** *Given* the Create screen, *When* the user types
  any FlexLife topic and submits, *Then* a mix is created from that free text with no catalog
  selection. *(Already built — `renderCreate` → `MixCreateRequest`.)*
- **AC2 (retrieval + coherent synthesis; ACs "retrieves relevant information",
  "synthesizes … a coherent explanatory lesson rather than raw search results").** *Given* a
  supported topic, *When* generation runs, *Then* the lesson is a structured article/audio
  built from retrieved corpus chunks, not a list of search hits.
- **AC3 (grounded; AC "remain grounded in the Knowledge Foundation").** *Given* a generated
  lesson, *When* its facts are checked, *Then* they trace to retrieved excerpts (persona
  forbids invention; `refs`/`sources` recorded).
- **AC4 (relevance floor + no unsupported content; ACs "if sufficient supporting content
  cannot be found, the application does not generate unsupported instructional material").**
  *Given* a topic with no corpus chunk clearing `MIN_SIMILARITY`, *When* generation runs,
  *Then* no lesson is synthesized and the mix resolves to `unsupported`.
- **AC5 (NLG Support referral; AC "direct the user to NLG Support where appropriate").**
  *Given* an `unsupported` mix, *When* the user opens it, *Then* the screen shows the shared
  `NLG_SUPPORT_MESSAGE` and a path to NLG Support (not a raw error/failed screen).
- **AC6 (text; AC "presented as text").** *Given* a ready article mix, *When* opened, *Then*
  the lesson renders as readable text.
- **AC7 (audio; AC "presented as audio").** *Given* a ready audio mix (Speech configured),
  *When* opened, *Then* an MP3 plays and is seekable; an article exposes "Listen"
  narration.
- **AC8 (preference-aware length; AC "response length … can influence how the lesson is
  presented").** *Given* the user's M06 response length is Brief vs. Detailed, *When* the
  same topic is generated, *Then* the lesson's density/verbosity differs accordingly while
  the facts and sources are unchanged.
- **AC9 (preference-aware Plain Language; AC "Plain Language mode … can influence").**
  *Given* Plain Language is On, *When* a lesson is generated, *Then* wording is simpler and
  jargon is expanded, with no change to grounding.
- **AC10 (explanatory suffices; AC "interactive exercises or assessments are not
  required").** *Given* M15 scope, *When* a lesson is delivered, *Then* it contains no
  quiz/assessment and that is acceptable.
- **AC11 (structure; AC "can determine an appropriate lesson structure").** *Given* a topic,
  *When* a lesson is built, *Then* it uses an appropriate structure for the chosen format
  (and, if FR7 is implemented, the app suggests the format from the topic).
- **AC12 (KB-only; out-of-scope "no sources outside the approved Knowledge Foundation").**
  *Given* any lesson, *When* its content is inspected, *Then* all facts come from the
  approved corpus, none from outside it.

## 8. Test Plan

**Backend (pytest; stub the Azure client + Speech):**

- Sufficiency gate: feed `run_generation` synthetic contexts all below `min_similarity` →
  mix resolves `unsupported` with `NLG_SUPPORT_MESSAGE`; the article/audio generator is
  **not** called (assert via mock). Contexts above the floor → generation proceeds.
- Distinguish `unsupported` (expected) from `failed` (operational): zero indexed documents
  still yields `failed`; a floor-gated topic yields `unsupported`.
- Preference plumbing: `MixCreateRequest` accepts `response_length`/`plain_language`;
  `run_generation` passes them into the generator; assert the preference preamble text
  appears in the prompt string for Plain Language on vs. off and for brief vs. detailed.
- `POST /v1/learn/mixes` with prefs → 202; without prefs (legacy) → 202 unchanged; curriculum
  seeding (`/v1/learn/seed`, `server.py:696-724`) still works with no prefs.
- `serialize_mix` emits the `unsupported` status/message; `MixSummary.status` validates it.

**Frontend (jsdom, per dev-loop memory — browser is on another machine):**

- `renderCreate` submit includes `response_length` + `plain_language` from `chatPrefs`.
- `renderMix` renders the `unsupported` referral card (NLG message + support action), not
  `renderFailed`, when `status === "unsupported"`.
- Article and audio screens still render for a ready mix (regression).

**Manual smoke (rebuild Docker per dev loop; open `/app/learn.html`):**

- Create a well-supported topic (e.g. "how the 1% floor works") → grounded article + Listen
  audio; toggle Plain Language / Brief↔Detailed in Answer preferences and regenerate →
  observe wording/length change, same facts.
- Create an off-corpus topic (e.g. "FlexLife quantum computing roadmap") → `unsupported`
  referral to NLG Support, no fabricated lesson.
- Set `MIN_SIMILARITY` high, rebuild, create a known-good topic → forced `unsupported`
  (proves the floor drives abstention).

## 9. Out of Scope

- Quizzes, assessments, knowledge checks, scoring, certification, completion gating (story
  out-of-scope). Flashcards remain an existing Learn feature but are **not** an M15 lesson
  deliverable.
- Predefined course catalogs, curriculum mapping, formal learning paths / sequencing. (The
  existing `CURRICULUM`/seed is recommended content, untouched by M15.)
- Persisting M06 preferences in the server profile / a settings UI — that is **M06**. M15
  only *consumes* the preferences the client already holds (`chatPrefs`).
- Progress/resume and expiration of generated lessons — that is **M13** (already built
  client-side; see Dependencies).
- Long-term learner tracking / syllabus / "what to learn next" — that is **M14** (see the
  M14 memo).
- Changing retrieval/ranking internals, embeddings, or chunking; tuning the corpus.
- Production-grade instructional design.

## 10. Dependencies & Open Questions

**Dependencies**

- **M03 (Citations/abstention) — the grounding contract M15 reuses.** M15 depends on
  `config.NLG_SUPPORT_MESSAGE` (`config.py:15-18`) and the `min_similarity`/`max_sources`
  floor used by `service.select_citations` (`service.py:67-91`, `config.py:69-70`). M15 does
  not re-decide "what is sufficient support" — it applies the same floor on the Learn path.
- **M06 (Response Preferences) — the preference source.** M15 reads the user's response
  length + Plain Language from `chatPrefs` (`ui/learn.html:1033-1038`). If/when M06 moves
  preferences into the server profile, M15 should read them there instead; the backend
  already accepts them on the request, so the UI source can change without a backend change.
- **Azure Speech** must be configured for audio (`speech_configured`, `learn.py:426`,
  `server.py:815-816`); already a soft dependency — audio degrades to "unconfigured" without
  it while text still works.
- **M13 (Resume/expiration)** already persists playback position per mix in `localStorage`
  (`ui/learn.html:1957-1958`, `2047-2048`) and `mark_stale_mixes` ages queued rows
  (`db.py:404`). M15 lessons are ordinary mixes, so M13 applies unchanged.
- **M09 (Handoff)** — if present, the `unsupported` referral should reuse the M09 "Prepare a
  request to NLG Support" flow; otherwise a plain referral suffices.
- Docker rebuild required for backend changes (dev-loop memory); UI verified via jsdom.

**Open questions**

1. **Which KB is canonical for lessons?** Learn generation retrieves from the **local
   pgvector index** (`retrieve_contexts`), whereas the end-user Ask Navigator chat is the
   **hosted Foundry agent KB** (per M02/M03). Both are "the Knowledge Foundation" in
   different tracks. Confirm the local index is the intended lesson corpus (it is today) and
   that it is populated with the same approved material — otherwise a topic answerable in
   chat could read as `unsupported` for a lesson, and vice versa.
2. **Sufficiency threshold for lessons.** Is "≥1 chunk clears `MIN_SIMILARITY`" enough, or
   should a lesson require more support than a single chat answer (e.g. ≥2 distinct documents
   or a higher floor) before synthesizing a full lesson? Default: reuse the M03 floor as-is;
   revisit if lessons feel thin.
3. **M06 response-length vs. mix duration.** The mix already has a `short`/`long` duration
   knob; M06 adds Brief/Balanced/Detailed density. Confirm the intended interaction (density
   *within* the chosen duration is the recommendation) and whether the Create screen should
   hide the duration control when an M06 length is set.
4. **`unsupported` as status vs. flag.** Recommendation is a new `unsupported` status
   (Option A, §6.1); confirm no external consumer assumes the status enum is closed.
5. **Structure auto-selection (FR7).** Is suggesting article vs. audio from the topic worth
   the build for the POC, or is user-chosen format sufficient? (Permissive AC — safe to
   defer.)

## 11. Rough Effort Estimate

| Area | Work | Est. |
| --- | --- | --- |
| Backend | Sufficiency gate + `unsupported` state in `run_generation` (reuse M03 floor + `NLG_SUPPORT_MESSAGE`) | ~0.5 day |
| Backend | `MixCreateRequest` prefs + thread into `run_generation` | ~0.25 day |
| Backend | Preference preamble in `generate_article` / `generate_audio_script` (length + Plain Language) | ~0.5 day |
| Backend | `serialize_mix` / `MixSummary` status; status enum | ~0.25 day |
| Frontend | `renderCreate` passes `chatPrefs`; `renderMix` `unsupported` referral card (+ M09 reuse) | ~0.5 day |
| Frontend | (optional) structure suggestion in Create | ~0.25 day |
| Tests | backend pytest (gate, prefs, status) + jsdom | ~0.75 day |
| Integration | Docker rebuild, smoke, `MIN_SIMILARITY` tuning for lessons | ~0.5 day |

**Total: ~3–3.5 days** (core gaps, excluding the optional structure suggestion). Because
free-text entry, grounded synthesis, and text+audio are **already built**, M15 is mostly
wiring the Learn path into existing M03 grounding and M06 preferences, not new machinery.
