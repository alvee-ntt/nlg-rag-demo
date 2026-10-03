# M06 — Explain Like… / Support User Response Preferences

## 1. Summary

Users can configure **how** the Ask Navigator presents its answers — **Length**
(Brief / Balanced / Detailed), **Format** (Auto / Bullets / Prose), **Tone**
(Warm / Plain / Formal), and **Plain Language** (on/off) — without changing the
facts, grounding, or source material behind the answer. The preferences are
chosen in an "Answer preferences" sheet, stored per browser, and sent on every
Ask Navigator turn, where they are turned into a short style instruction that is
prepended to the message handed to the hosted Foundry agent. Because grounding
and retrieval happen inside the hosted agent's knowledge base and the style text
only instructs *how to answer*, the preferences shape presentation, not factual
content.

**This milestone is mostly already built.** The preferences UI, the
localStorage persistence, the request wiring, the backend request models, and
the Foundry-side style-preamble application all exist and are live (see §3 for
exact `file:line`). M06 is therefore not a green-field feature — it is a
**completion and hardening** story. The real gaps are: (1) preferences live in
`localStorage` only, not "as part of the user's profile" as the story asks; (2)
the **local fallback** chat path silently drops the preferences, so style is
inconsistent whenever Foundry is unavailable; (3) there is no explicit rule that
**saved preferences override conflicting in-chat instructions**; and (4) the
preference editor is reachable only from the full-page chat, not the pull-up
Navigator sheet. The spec closes those gaps and keeps the existing, working
preamble mechanism.

The hard design question the milestone poses — *how do presentation preferences
reach an answer the hosted Foundry agent grounds server-side?* — is **already
answered in code** by prepending a style preamble to the user message
(`foundry._current_user_content`, `src/rag_layer/foundry.py:173-225`). §6.3
analyzes the three candidate mechanisms (preamble prepend vs. a post-processing
restyle pass vs. switching the end-user chat to the local `/v1/chat`) and
recommends keeping the shipped preamble approach.

## 2. User Story (verbatim)

**User Story: Support User Response Preferences** — As a FlexLife sales agent or
support user, I want to configure how application responses are presented, so
that the information is delivered in a style that is useful and comfortable for
me.

Description: The POC will allow users to configure response preferences through a
settings interface. These preferences should influence how responses are
presented without changing the underlying factual content, grounding, or source
material used to generate the response. The supported preferences are:

- **Length** — Brief, Balanced, Detailed
- **Format** — Auto, Bullets, Prose
- **Tone** — Warm, Plain, Formal
- **Plain Language** — On / Off

When Plain Language is enabled, responses should favor simpler wording, reduce
unnecessary jargon, and explain concepts in a way that is more accessible to
newer agents. When Format is set to Auto, the application may choose the
presentation format that best fits the request. Preferences should be stored as
part of the user's profile and remain available across sessions.

Acceptance Criteria:

- A user can configure response length as Brief, Balanced, or Detailed.
- A user can configure response formats such as Auto, Bullets, or Prose.
- A user can configure response tones such as Warm, Plain, or Formal.
- A user can enable or disable Plain Language mode.
- The selected preferences are applied consistently throughout the user's
  session.
- The selected preferences are persisted and restored across sessions.
- When Plain Language is enabled, responses use simpler language and provide
  additional explanation where useful for a newer agent.
- When Format is set to Auto, the application may determine an appropriate
  response structure.
- Response preferences affect presentation and communication style, not the
  factual grounding of the answer.
- For the POC, it is acceptable for configured preferences to take precedence
  over conflicting formatting or style instructions given during the
  conversation.

Out of Scope:

- Automatically changing preferences based on observed user behavior.
- Adaptive personalization beyond the explicitly configured settings.
- Complex conflict resolution between saved preferences and conversational
  instructions.
- Per-message preference profiles.

## 3. Current State — what exists

Unlike M09 (which was entirely new work), **the M06 happy path is implemented
end to end** on the Foundry chat. Below is the audited reality, grounded in
`file:line`. The gaps are called out in §3.6.

### 3.1 Preferences editor UI (frontend) — EXISTS

- `openChatSettings()` — `ui/learn.html:1410-1445` builds the "Answer
  preferences" bottom sheet. It renders exactly the four story preferences plus
  two extras:
  - **Length** segmented control `[["brief","Brief"],["balanced","Balanced"],["detailed","Detailed"]]`
    — `ui/learn.html:1417`.
  - **Format** segmented control `[["auto","Auto"],["bullets","Bullets"],["prose","Prose"]]`
    — `ui/learn.html:1418`.
  - **Tone** segmented control `[["warm","Warm"],["plain","Plain"],["formal","Formal"]]`
    — `ui/learn.html:1419`.
  - **Plain language (new-agent friendly)** checkbox — `ui/learn.html:1420`.
  - Two extras beyond the story: **Always cite sources** toggle
    (`ui/learn.html:1421`) and an **About me** / **Memories** block
    (`ui/learn.html:1422-1426`). These are not M06 requirements; they ride the
    same sheet and the same transport.
- Each segment writes straight to `chatPrefs[key]` and calls `savePrefs()` on
  tap (`ui/learn.html:1432-1437`); the Plain toggle at `ui/learn.html:1435`.
- The sheet is opened by the gear button in the **full-page** chat header
  (`id="chatSet"`, rendered at `ui/learn.html:1326`, bound at
  `ui/learn.html:1339`).

### 3.2 Persistence (frontend, localStorage) — EXISTS, but browser-only

- `PREFS_KEY = "chatPrefs"` and `DEFAULT_PREFS = { length: "balanced", format:
  "auto", tone: "warm", plain: false, alwaysSources: false, role: "" }` —
  `ui/learn.html:1033-1034`.
- `chatPrefs` is hydrated from localStorage at load:
  `Object.assign({}, DEFAULT_PREFS, store.get(PREFS_KEY, {}))` —
  `ui/learn.html:1035`; `savePrefs = () => store.set(PREFS_KEY, chatPrefs)` —
  `ui/learn.html:1036`.
- `store.get/set` wrap `localStorage` under a `salesdj.` prefix, each in a
  try/catch — `ui/learn.html:850-853`.
- **This survives reloads and new sessions on the same browser**, so the
  literal "restored across sessions" AC is met *per device*. It is **not**
  stored "as part of the user's profile" (server-side) — see §3.6.

### 3.3 Transport to the backend — EXISTS

- `navSend(message, history)` — `ui/learn.html:1046-1063` — the single turn
  function used by both the full-page chat and the pull-up sheet. It POSTs to
  `/v1/foundry/chat` with the live preference snapshot:
  ```js
  preferences: { length, format, tone, plain, always_sources },
  about_me: chatPrefs.role || "",
  memories: chatMemories.map(m => m.text)
  ```
  (`ui/learn.html:1052-1061`). Preferences are sent as **structured fields**,
  not folded into the visible user bubble — the user's own message stays clean.

### 3.4 Backend request models + endpoint — EXISTS

- `FoundryAnswerPreferences` — `src/rag_layer/server.py:111-116`:
  `length: Literal["brief","balanced","detailed"] = "balanced"`,
  `format: Literal["auto","bullets","prose"] = "auto"`,
  `tone: Literal["warm","plain","formal"] = "warm"`, `plain: bool = False`,
  `always_sources: bool = False`. The enums are the exact story options.
- `FoundryChatRequest` — `src/rag_layer/server.py:119-124`: `message`, `history`,
  `preferences: FoundryAnswerPreferences = default`, `about_me`, `memories`.
- `foundry_chat_endpoint` — `src/rag_layer/server.py:529-547` — passes the
  preferences through: `preferences=payload.preferences.model_dump()`,
  `about_me=payload.about_me.strip()`, cleaned `memories`
  (`server.py:545-547`).

### 3.5 Application to the answer (the mechanism) — EXISTS

- `chat_foundry(...)` — `src/rag_layer/service.py:168-223` — the service wrapper.
  On the in-domain path it calls `foundry.chat(..., preferences=preferences,
  about_me=about_me, memories=memories, ...)` (`service.py:202-210`).
- `foundry.chat(...)` — `src/rag_layer/foundry.py:317-404` — accepts the
  `preferences`, `about_me`, `memories` kwargs (`foundry.py:322-324`) and passes
  them to `_current_user_content(...)` when it builds the final user message
  (`foundry.py:355-360`).
- **`_current_user_content(...)` — `src/rag_layer/foundry.py:173-225` — is the
  heart of M06.** It maps each preference to a one-line style instruction:
  - Length → `foundry.py:183-187` ("Be brief and to the point." /
    `balanced` = "" / "Give a thorough, detailed answer.").
  - Format → `foundry.py:188-192` ("Prefer bullet points." / "Answer in prose
    paragraphs, not lists." / `auto` = "").
  - Tone → `foundry.py:193-197` (plain / warm / formal).
  - Plain → `foundry.py:204-205` ("Explain simply, so a brand-new agent can
    follow.").
  - `always_sources` → `foundry.py:206-207`.
  The non-empty lines are joined into a preamble
  `"[Context for how to answer — do not repeat this back to me:\n ... ]\n\n"`
  prepended to the question (`foundry.py:218-225`). The user's own history turns
  (`foundry.py:348-352`) and the hosted agent's own retrieval/grounding are
  untouched, so **this changes presentation only**.
- **Auto = app chooses:** `format="auto"` and `length="balanced"` and
  `tone="warm"` map to empty strings, i.e. no instruction is emitted, so the
  agent/app is free to pick the structure — satisfies the Auto requirement by
  design (`foundry.py:185,191` etc.).

### 3.6 Gaps (what M06 still has to do)

1. **Not "part of the user's profile."** Preferences persist only in
   `localStorage` (`ui/learn.html:1033-1038`). The server-side profile store
   (`app_settings` KV table) persists **only** `app_language` and
   `practice_language` — `SETTING_KEYS = ("app_language","practice_language")`
   at `src/rag_layer/profile.py:109`; `ProfileSettingsRequest` exposes only
   those two (`src/rag_layer/server.py:1071-1073`); `PUT /v1/profile/settings`
   at `server.py:1082-1088`. Response prefs are **not** in the profile payload
   (`profile.build_profile`, `profile.py:153-173`). So prefs do not follow a
   user across browsers/devices and are not "part of the profile."
2. **Local fallback drops preferences.** When Foundry is unavailable,
   `chat_foundry` falls back to the local pipeline: `chat(settings, client,
   message, history, limit=...)` — `src/rag_layer/service.py:217-223` — which
   calls `embeddings.chat_with_context(...)` (`embeddings.py:187-196`). Neither
   takes a `preferences` argument, so **style silently stops applying** on the
   fallback path. The user sees inconsistent presentation with no signal why.
3. **No explicit precedence over in-chat instructions.** The story allows saved
   prefs to override conflicting in-conversation instructions. The current
   preamble says "Context for how to answer" (`foundry.py:218`) but does **not**
   tell the model that these instructions win over contradictory requests in the
   conversation. Note the built-in "Shorter" / "More detail" action buttons
   (`ui/learn.html:1170` render; `ui/learn.html:1219-1220` send literal "Make
   that shorter." / "Give me more detail on that.") are exactly such in-chat
   style instructions, so the conflict is real and reachable in the UI.
4. **Editor reachable from only one surface.** The gear that opens
   `openChatSettings()` is rendered only in the full-page chat header
   (`ui/learn.html:1326,1339`). The pull-up Navigator sheet (`openNavigator`,
   `ui/learn.html:1296-1314`) has Clear/Close but **no** preferences gear.
   Prefs still *apply* there (both surfaces call `navSend`), but they cannot be
   *edited* there.
5. **Default `balanced`/`warm` emit no instruction.** Because `balanced` and
   `warm` map to `""` (`foundry.py:184,195`), the default state sends *no* tone
   or length guidance at all. That is acceptable (and arguably desirable for
   "Auto"-like behavior), but it means the defaults are indistinguishable from
   "unset" at the provider boundary — worth noting for tests.

### 3.7 Verified behavior (static audit — 2026-10-02)

This audit is code-reading only (the browser extension is on another machine per
the dev-loop memory note; runtime verification is via jsdom + a Docker rebuild).
The data path **message → preferences → `/v1/foundry/chat` →
`FoundryChatRequest` → `chat_foundry` → `foundry.chat` → `_current_user_content`
preamble → provider request** is complete and consistent. The
`foundry.py:281-287` request trace (`provider-request.json` / `prompt.txt`)
captures the assembled preamble, so the applied style is observable per turn at
runtime.

## 4. Scope

**In scope**

- Persist the four response preferences (Length, Format, Tone, Plain Language)
  **server-side as part of the profile**, so they are restored across sessions
  and not merely per-browser, while keeping the localStorage snapshot as the
  fast client cache and offline default.
- Make preference application **consistent across the whole Ask Navigator
  engine**, including the **local fallback** path, so a Foundry outage does not
  silently drop the user's style.
- Add an explicit **precedence** instruction so saved preferences win over
  conflicting in-chat style instructions (POC-grade, prompt-level).
- Expose the preferences editor from **both** chat surfaces (full page and
  pull-up sheet).
- Tests (backend + jsdom) and a manual demo.

**Out of scope** — see §9. In particular: behavioral auto-personalization,
per-message preference profiles, and deep conflict arbitration are explicitly
out per the story.

## 5. Functional Requirements

- **FR1 — Four preferences, exact options.** The user can set Length
  (Brief/Balanced/Detailed), Format (Auto/Bullets/Prose), Tone
  (Warm/Plain/Formal), and Plain Language (on/off) from a settings surface.
  *(Already built — `ui/learn.html:1417-1420`.)*
- **FR2 — Applied every turn, consistently in-session.** Every Ask Navigator
  turn carries the current preferences and the answer reflects them, for the
  whole session, on **all** engines that can answer the turn (Foundry primary
  **and** the local fallback).
- **FR3 — Persisted & restored across sessions as part of the profile.**
  Preferences are saved server-side (profile) and restored on next load, in
  addition to the localStorage cache. A new browser/device signed into the same
  POC account restores the saved preferences.
- **FR4 — Presentation only.** Preferences change wording, length, structure,
  and tone, never the retrieved sources, grounding, or factual claims. The
  retrieval/grounding call is unchanged by any preference.
- **FR5 — Auto lets the app choose.** With Format = Auto, no format instruction
  is emitted and the app/agent picks the structure that best fits the request.
- **FR6 — Plain Language simplifies.** With Plain Language on, the answer uses
  simpler wording, less jargon, and more explanation suitable for a newer agent.
- **FR7 — Saved prefs override conflicting in-chat instructions.** When the
  conversation contains a conflicting style instruction (including the
  "Shorter"/"More detail" buttons), the saved preferences take precedence for
  the POC; the override is a prompt-level instruction, not a hard arbitration
  engine.
- **FR8 — Editable from every chat surface.** The preferences sheet is reachable
  from both the full-page chat and the pull-up Navigator sheet.
- **FR9 — Graceful + fail-open.** A failure to load server-side prefs falls back
  to the localStorage snapshot and then to `DEFAULT_PREFS`; a failure to save
  server-side still keeps the local snapshot so the session is unaffected.

## 6. Technical Design

### 6.1 Backend — persist response prefs in the profile

Extend the existing profile settings store rather than inventing a new one. The
settings live in the `app_settings` key-value table already used for
`app_language`/`practice_language` (`profile.py:15,109`).

- **Model.** Extend `ProfileSettingsRequest` (`src/rag_layer/server.py:1071-1073`)
  with the four optional response-preference fields (mirroring the strict enums
  already defined for the chat path at `server.py:111-116`):
  ```python
  class ProfileSettingsRequest(BaseModel):
      app_language: str | None = Field(default=None, max_length=8)
      practice_language: str | None = Field(default=None, max_length=8)
      answer_length: Literal["brief", "balanced", "detailed"] | None = None
      answer_format: Literal["auto", "bullets", "prose"] | None = None
      answer_tone: Literal["warm", "plain", "formal"] | None = None
      plain_language: bool | None = None
  ```
- **Validation + storage.** Extend `profile.SETTING_KEYS` and
  `profile.validate_settings` (`profile.py:109,116-126`) to accept the new keys.
  Language keys keep their language-code validation; the response-pref keys are
  validated against their enum (and `plain_language` stored as `"true"`/`"false"`
  since `app_settings` is string-valued — see `set_app_settings`/`get_app_settings`
  usage in `profile.py:15,129-138`). `current_settings` (`profile.py:129-131`)
  returns them alongside the language settings, so `build_profile`
  (`profile.py:153-173`) surfaces them under `settings` with no shape change for
  existing callers.
- **No new endpoint.** `GET /v1/profile` (`server.py:1076-1079`) already returns
  `settings`; `PUT /v1/profile/settings` (`server.py:1082-1088`) already persists
  a partial `settings` dict. The response prefs ride those two routes.
- **One shared login caveat.** The POC has a single shared credential
  (`config.py:76-77`) and the profile is deployment-wide (`profile.py` module
  docstring). "Profile persistence" here is therefore one shared server-side
  preference set, which is correct for the POC; per-user scoping is the same
  migration path already noted in `profile.py`.

### 6.2 Backend — honor prefs on the local fallback path (FR2)

Today the fallback drops prefs (`service.py:217-223`). Thread them through so a
Foundry outage keeps the style consistent:

- Add an optional `preferences`/`about_me`/`memories` argument to the local
  `chat(...)` service function (alongside `message`, `history`, `limit`) and to
  `embeddings.chat_with_context(...)` (`embeddings.py:187-196`).
- In `chat_with_context`, build the same style lines used on the Foundry path.
  **Reuse, don't fork:** factor the style-string builder out of
  `foundry._current_user_content` (`foundry.py:183-216`) into a shared helper
  (e.g. `embeddings.style_preamble(preferences, about_me, memories)`), and have
  both `_current_user_content` and `chat_with_context` call it. The local prompt
  then appends the same "Answer style: …" guidance to its existing system
  instructions (`embeddings.py:200-205`) **without** changing the grounding rule
  ("Use only the source context below …", `embeddings.py:204`).
- `chat_foundry`'s fallback call (`service.py:217`) passes the preferences it
  already received (`service.py:174-176`).

This makes the preamble mechanism engine-independent and removes the silent
inconsistency in gap §3.6(2).

### 6.3 The mechanism question — preamble vs. restyle vs. local chat

The milestone asks how presentation prefs reach an answer the **hosted Foundry
agent** grounds server-side. Three options were considered:

- **(a) Prepend a style preamble to the message sent to the agent — CHOSEN, and
  already implemented** (`foundry._current_user_content`, `foundry.py:173-225`).
  The agent still retrieves and grounds from its own KB; the preamble only
  instructs *how to answer*. **Pros:** one call, no extra latency/cost, cannot
  alter citations (they come from the agent's own grounding), already shipped and
  traced (`foundry.py:281-287`). **Cons:** style adherence is advisory (the agent
  may under-apply an instruction); the agent's own Azure-side instructions could
  in principle override, though in practice it honors the preamble. This is the
  right trade for a POC and is the mechanism M06 keeps.
- **(b) Post-processing "restyle" pass** — take the grounded answer and run a
  second local `_generate` (`embeddings.py:100-116`) to reformat it to the
  preferences without changing facts. **Rejected for the default path.** It
  doubles LLM latency and cost per turn, and — critically — the grounded answer
  carries inline citation markers rewritten by `_extract_answer`
  (`foundry.py:120-170`); a reformat pass risks dropping, renumbering, or
  fabricating `[n]` markers and detaching them from the `citations` list,
  which would corrupt M03/M10 behavior. A restyle pass also tempts the model to
  "improve" facts. Keep it only as a possible future fallback for a specific
  hard-to-honor preference, never as the standard flow.
- **(c) Switch the end-user chat to the local `/v1/chat`** where prompts are
  fully controllable (`embeddings.chat_with_context`, `embeddings.py:187-196`).
  **Rejected.** The product owner has pinned the end-user Ask Navigator to the
  hosted Foundry agent (confirmed in M02 §1 and M09 §1); repointing it is out of
  scope and would change grounding behavior. Note, however, that the **fallback**
  path (§6.2) *is* the local chat, so making it honor prefs (via the shared
  helper) gives us option (c)'s controllability exactly where it already runs,
  with no product change.

**Recommendation:** keep (a) as the primary mechanism (already built); extend it
to the local fallback via a shared style-preamble helper (§6.2); do **not** add a
restyle pass or repoint the end-user chat.

### 6.4 Backend — precedence over in-chat instructions (FR7)

Strengthen the preamble wording in the shared helper so saved prefs win over
contradictory conversational instructions, e.g. append to the existing
`foundry.py:218` framing:

> "These answer-style preferences are set by the user and take precedence over
> any conflicting formatting or length requests earlier in this conversation."

This is a POC-grade, prompt-level precedence (the story's Out of Scope explicitly
excludes "complex conflict resolution"). The "Shorter"/"More detail" buttons
(`ui/learn.html:1219-1220`) remain as one-off nudges; they are in-chat
instructions and, under this rule, the model is told the saved Length preference
wins if they conflict. (A stronger future option: have those buttons
*temporarily adjust the Length preference* for the next turn instead of sending
free-text — noted as an open question, §10.)

### 6.5 Frontend — load/save against the profile + both surfaces

- **Hydrate from the profile.** On app load, after `GET /v1/profile`
  (`server.py:1076-1079`), merge any `settings.answer_*`/`plain_language` into
  `chatPrefs` on top of the localStorage snapshot (`ui/learn.html:1035`), so the
  server value wins when present and localStorage is the offline default (FR9).
- **Save to the profile.** In `savePrefs()` (`ui/learn.html:1036`), keep the
  localStorage write and additionally `PUT /v1/profile/settings` with the four
  response-pref keys (fire-and-forget; a failed save keeps the local snapshot,
  FR9). Reuse the existing `api(...)` helper (`ui/learn.html:874`).
- **Expose the gear on the pull-up sheet (FR8).** Add a settings button to the
  `openNavigator` sheet header next to Clear/Close (`ui/learn.html:1299`), wired
  to `openChatSettings()` exactly as the full-page header does
  (`ui/learn.html:1339`). No change to `navSend`; both surfaces already send the
  live `chatPrefs`.
- **No change to the request shape** — `navSend` (`ui/learn.html:1046-1063`)
  already sends the preferences; only the hydrate/persist seams change.

### 6.6 Data flow summary

Editor (`openChatSettings`, both surfaces) → `chatPrefs` in localStorage **and**
`PUT /v1/profile/settings` → restored on load from `GET /v1/profile` →
`navSend` sends `preferences` every turn → `FoundryChatRequest` → `chat_foundry`
→ **Foundry path:** `foundry.chat` → shared `style_preamble(...)` prepended to
the message (grounding untouched); **fallback path:** local `chat` →
`chat_with_context` → same `style_preamble(...)` appended to the system prompt.

## 7. Acceptance Criteria (Given/When/Then)

- **AC1 (configure length — story AC "configure response length").** *Given* the
  Answer preferences sheet, *When* the user selects Brief / Balanced / Detailed,
  *Then* `chatPrefs.length` updates and persists, and subsequent answers reflect
  the chosen length. *(UI: `ui/learn.html:1417`; apply: `foundry.py:183-187`.)*
- **AC2 (configure format — story AC "configure response formats").** *Given*
  the sheet, *When* the user selects Auto / Bullets / Prose, *Then* the answer
  structure follows: Bullets → list, Prose → paragraphs, Auto → app's choice.
  *(UI: `ui/learn.html:1418`; apply: `foundry.py:188-192`.)*
- **AC3 (configure tone — story AC "configure response tones").** *Given* the
  sheet, *When* the user selects Warm / Plain / Formal, *Then* the answer tone
  follows. *(UI: `ui/learn.html:1419`; apply: `foundry.py:193-197`.)*
- **AC4 (toggle plain language — story AC "enable or disable Plain Language").**
  *Given* the sheet, *When* the user toggles Plain Language, *Then*
  `chatPrefs.plain` flips and, when on, the answer uses simpler wording and more
  explanation for a newer agent. *(UI: `ui/learn.html:1420`; apply:
  `foundry.py:204-205`.)*
- **AC5 (consistent in session — story AC "applied consistently throughout the
  session").** *Given* a set of preferences, *When* the user asks several
  questions in a row, *Then* every turn carries the preferences (Foundry **and**
  local fallback), with no turn reverting to defaults.
- **AC6 (persist & restore — story AC "persisted and restored across sessions").**
  *Given* preferences saved in one session, *When* the user reloads or signs in
  on another browser to the same POC account, *Then* the saved preferences are
  restored from the profile (`GET /v1/profile`), not just from localStorage.
- **AC7 (plain language quality — story AC "simpler language … for a newer
  agent").** *Given* Plain Language on, *When* an answer is produced, *Then* it
  measurably favors plainer wording/explanation vs. the same question with Plain
  Language off.
- **AC8 (auto format — story AC "Format = Auto … determine appropriate
  structure").** *Given* Format = Auto, *When* an answer is produced, *Then* no
  format instruction is emitted and the app/agent picks the structure.
  *(`foundry.py:191`.)*
- **AC9 (presentation not grounding — story AC "affect presentation … not the
  factual grounding").** *Given* any preference combination, *When* answers are
  compared, *Then* the retrieved sources/citations and factual claims are
  unchanged; only wording/length/format/tone differ. *(Grounding call unchanged;
  preamble only instructs style, `foundry.py:218-225`.)*
- **AC10 (override in-chat instructions — story AC "preferences take precedence
  over conflicting … instructions").** *Given* a saved Length = Detailed and an
  in-chat "Make that shorter.", *When* the next turn is produced, *Then* the
  saved preference is instructed to win for the POC. *(`foundry.py:218`
  precedence line.)*
- **AC11 (editable everywhere — supports "settings interface").** *Given* either
  the full-page chat or the pull-up Navigator sheet, *When* the user opens
  settings, *Then* the same Answer preferences sheet appears and edits apply to
  both surfaces.
- **AC12 (fail-open).** *Given* `GET /v1/profile` or `PUT /v1/profile/settings`
  fails, *When* the app loads or saves, *Then* it falls back to the localStorage
  snapshot and `DEFAULT_PREFS`, and the chat session is unaffected.

## 8. Test Plan

**Backend (pytest):**

- `profile.validate_settings` accepts the four new keys with valid enum values,
  rejects bad enums, and leaves unknown keys out (extend existing
  `profile.py:116-126` tests).
- `PUT /v1/profile/settings` with response prefs persists them and `GET
  /v1/profile` returns them under `settings`; language settings still round-trip
  unchanged (FastAPI `TestClient`).
- Shared `style_preamble(preferences, about_me, memories)` helper emits the right
  lines for each enum value (length/format/tone/plain/always_sources), and emits
  **nothing** for `balanced`/`warm`/`auto`/plain-off (guards gap §3.6(5)).
- `foundry.chat` still prepends the preamble (regression on `foundry.py:355-360`)
  and the grounded `citations` are unaffected by preferences (assert identical
  citations with prefs on vs. off, Foundry client mocked).
- Local fallback path: `chat`/`chat_with_context` now receive and apply
  preferences; assert the style lines appear in the prompt passed to the mocked
  Azure client and the grounding rule (`embeddings.py:204`) is still present.
- Precedence line present in the assembled preamble when a conflicting
  instruction is in history.

**Frontend (jsdom, per the dev-loop note — the browser is on another machine):**

- `openChatSettings` renders all four controls with current `chatPrefs` selected
  (`ui/learn.html:1413-1420`); tapping a segment updates `chatPrefs` and calls
  `savePrefs`.
- `savePrefs` writes localStorage **and** issues `PUT /v1/profile/settings`
  (assert `fetch` called with the four keys); a rejected PUT still leaves the
  localStorage value intact.
- Load hydration merges `GET /v1/profile` `settings` over the localStorage
  snapshot (server value wins when present).
- `navSend` includes the live `preferences` object on every call
  (`ui/learn.html:1052-1061`).
- The pull-up Navigator sheet exposes a settings gear that opens the same sheet
  (new button at `ui/learn.html:1299`).

**Manual smoke** (rebuild the Docker image per the dev-loop note, then
`/app/learn.html`): set Detailed + Bullets + Formal + Plain on, ask a question,
confirm the answer is long, bulleted, formal, and plain; flip to Brief + Prose +
Warm, ask again, confirm the shift with the **same** citations; reload and
confirm the settings restored; force a Foundry outage (misconfigure) and confirm
the fallback answer still honors the style; inspect `provider-request.json` /
`prompt.txt` under the trace dir (`foundry.py:281-287`) to see the assembled
preamble.

## 9. Out of Scope

- Automatically changing preferences from observed behavior; adaptive
  personalization beyond the explicit settings (story Out of Scope).
- Per-message preference profiles (story Out of Scope).
- Complex conflict-resolution engine between saved prefs and conversational
  instructions — the POC uses a single prompt-level precedence rule (story Out of
  Scope).
- Applying response prefs to **non-chat** surfaces: generated Learn lessons
  (that is M15, which already names "response length" and "Plain Language" as
  inputs), the in-call roleplay Ask sheet (`openAsk` → `/v1/roleplay/.../ask`),
  and the Coach console. M06 covers the Ask Navigator end-user chat.
- Per-user accounts/scoping (the POC has one shared login; profile is
  deployment-wide — `profile.py` docstring, `config.py:76-77`).
- The two non-story extras already riding the sheet (**Always cite sources**,
  **About me/Memories**) — they exist and keep working but are not M06
  requirements.

## 10. Dependencies & Open Questions

**Dependencies**

- **M02 (Natural Language Search) / the Foundry chat path** provide the
  `navSend` → `/v1/foundry/chat` → `chat_foundry` → `foundry.chat` pipeline that
  M06 injects style into. M06 reuses it unchanged except for the shared preamble
  helper.
- **Profile store (`app_settings` KV + `GET /v1/profile` / `PUT
  /v1/profile/settings`)** — `profile.py:15,109,129-138`, `server.py:1076-1088` —
  is the persistence substrate M06 extends.
- **Azure OpenAI** must be configured for the local fallback path to run
  (`config.py:131-134`); the Foundry path is grounded by the hosted agent.
- Docker image rebuild required to ship backend changes (dev-loop memory note).

**Open questions**

1. **Is localStorage-only persistence actually sufficient for the POC?** The
   literal AC ("persisted and restored across sessions") is already met per
   browser by localStorage (`ui/learn.html:1035`). Server-side profile
   persistence (§6.1) is what satisfies "stored as part of the user's profile"
   and cross-device restore. Confirm with the product owner whether cross-device
   restore matters for the demo, or whether the shipped localStorage behavior is
   enough — this is the main scope lever for the milestone's remaining effort.
2. **Should "Shorter"/"More detail" buttons mutate the Length preference** (a
   sticky change) **or stay one-off nudges?** (`ui/learn.html:1219-1220`.) Today
   they send free-text that FR7 says the saved pref overrides — which is slightly
   self-defeating. Decide the intended interaction.
3. **Precedence strength.** The POC override is a prompt instruction, not a hard
   guarantee the agent honors. Confirm that advisory precedence is acceptable for
   the demo (consistent with the story's "complex conflict resolution" Out of
   Scope).
4. **Relationship to M12 (User Preferences).** M12 has no user story and
   `USER_STORIES.md` flags "How is this different from M06?" See the companion
   memo `specs/M12-user-preferences.md`; M06 should own *response-presentation*
   prefs, M12 (if kept) the non-response prefs (default voice/mode, default tab,
   retention). This spec assumes that split.
5. **Default emits nothing.** `balanced`/`warm`/`auto` send no instruction
   (§3.6(5)). Confirm that is the intended "neutral" baseline rather than
   explicit "balanced length"/"warm tone" instructions.

## 11. Rough Effort Estimate

Most of M06 already ships; the table covers only the remaining completion/hardening work.

| Area | Work | Est. |
| --- | --- | --- |
| Backend | Extend `ProfileSettingsRequest` + `profile.SETTING_KEYS`/`validate_settings`/`current_settings` for 4 prefs | ~0.5 day |
| Backend | Factor shared `style_preamble` helper out of `foundry._current_user_content` | ~0.25 day |
| Backend | Thread prefs into local fallback (`service.chat`, `embeddings.chat_with_context`) | ~0.5 day |
| Backend | Precedence line in the preamble | ~0.1 day |
| Frontend | Hydrate from `GET /v1/profile`; PUT on `savePrefs`; fail-open | ~0.5 day |
| Frontend | Settings gear on the pull-up Navigator sheet | ~0.25 day |
| Tests | backend pytest (profile round-trip, shared helper, fallback) + jsdom | ~0.75 day |
| Manual | Docker rebuild + demo (both engines, restore, grounding-unchanged) | ~0.25 day |

**Total: ~2.5–3 days** to fully satisfy the story. If the product owner accepts
localStorage-only persistence (Open Q1) and skips the fallback-path work, the
remaining gap is only the precedence line + the pull-up gear — **~0.5 day** — since
the core preference feature is already live.
