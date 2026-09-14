# salesDJ Learn: change log

Date: 2026-09-12
Branch: `main` (uncommitted working tree)

This document describes everything added to the `nlg-rag` demo to build the salesDJ
**Learn** tab: a phone-width training app where a brand-new agent learns to sell FlexLife
through generated articles, flashcard decks, and two-host audio episodes, all grounded in
the indexed National Life Group documents.

---

## 1. Summary

| Area | What was added |
| --- | --- |
| Product concept | A **mix** = one topic rendered as `article`, `flashcards`, or `audio`. Lengths: audio 5 / 10 min, article 5 / 10 min read, flashcards 10 / 20 cards. |
| Starter content | A **21-lesson curriculum** in three tiers, each lesson pinned to specific document pages with a coverage brief. Written after reading the corpus (product guide, quick reference, riders, brochures, underwriting, sales transcripts). |
| Custom content | Agents type any topic on the Create screen; retrieval finds the relevant chunks and the same generators produce the mix. |
| Audio | Azure Speech text-to-speech with two neural voices (Ava = coach, Andrew = rookie co-host). Falls back to transcript-only when the key is missing or rejected. |
| UI | `ui/learn.html`: Learn home, Create custom, article reader, flashcard player with self-grading, audio player with transcript, tiered curriculum view. Served by the API at `/learn`. |
| API | Nine new `/v1/learn/*` endpoints. |
| Storage | New `learn_mixes` table in the existing Postgres/pgvector database. |
| Tests | `tests/test_learn.py`: 20 unit tests. Headless jsdom walkthrough of every screen (scratch only, not committed). |

Line counts: five new files (2,046 lines) and six modified files (+511 / -4).

---

## 2. New files

### `src/rag_layer/learn.py` (487 lines)
The generation pipeline.

- `LENGTH_SPECS`: what short and long mean per format (word targets, turn counts, card counts, display labels).
- `RECOMMENDED_MIXES`: derived from tier 1 of the curriculum; what the Learn home recommends.
- `enqueue()`: submits work to a three-worker `ThreadPoolExecutor` so generation runs off-request and several mixes proceed at once.
- **Retrieval path** (custom prompts): `plan_queries()` asks the chat model for three focused search queries; `gather_contexts()` embeds each, searches pgvector, merges and dedupes by best similarity (12 chunks short, 18 long).
- **Pinned path** (curriculum): `gather_pinned_contexts()` reads the exact document pages listed on the curriculum item, in reading order, capped at 90 chunks.
- `_llm_json()`: calls the Azure OpenAI Responses API in `json_object` mode, falls back to plain mode on HTTP 400, strips code fences, and retries once with a "convert to JSON" prompt if parsing fails.
- Three generators with a shared persona prompt (senior coach writing for a rookie, plain English, sources only, no invented numbers, compliance-aware) and an optional coverage brief:
  - `generate_article()` → title, subtitle, sections (paragraphs + bullets), key takeaways, "say it like this" talk track, watch-outs.
  - `generate_flashcards()` → title and exactly N cards (front, back, source refs), ordered foundational to advanced.
  - `generate_audio_script()` → title, description, 14-40 spoken turns for Ava and Andrew, numbers spelled out for TTS.
- `run_generation()`: the background job. Moves the row `queued → generating → ready | failed`, stores content, deduped per-page sources, an estimated duration, and for audio, calls Azure Speech and stores the MP3. Never raises; failures land on the row.
- `run_audio_render()`: re-synthesizes only the MP3 for an episode whose script already exists (used after the speech key is configured).
- `serialize_mix()`: API shape, with content and sources included only on detail requests.

### `src/rag_layer/curriculum.py` (522 lines)
The 21-lesson starter pack. Each entry has a stable `key`, `tier`, `kind`, `length`, `prompt`, a `brief` (the facts the lesson must land), and `sources` (document substring plus PDF pages or a chunk range). Written for the **non-New-York** FlexLife; NY material is excluded because caps, riders, and the bonus start year differ.

| Tier | Key | Format | Lesson |
| --- | --- | --- | --- |
| 1 | `t1-what-is-flexlife` | Article 5 min | What FlexLife is, on one page |
| 1 | `t1-three-questions` | Audio 5 min | The three questions every FlexLife conversation answers |
| 1 | `t1-cash-value-101` | Article 5 min | Cash value 101: floor, cap, and participation rate |
| 1 | `t1-by-the-numbers` | Flashcards 20 | FlexLife by the numbers |
| 1 | `t1-living-benefits-plain` | Article 5 min | Living benefits in plain English |
| 1 | `t1-living-benefits-facts` | Flashcards 20 | Living benefits: the facts you must not get wrong |
| 2 | `t2-premium-dollar` | Article 10 min | How a dollar of premium actually moves through a policy |
| 2 | `t2-crediting-options` | Audio 5 min | Cap Focus vs Participation Focus vs 1% Floor |
| 2 | `t2-pcc-vs-chronic` | Article 5 min | Premium Chronic Care vs the free Chronic Illness rider |
| 2 | `t2-libr` | Article 5 min | The Lifetime Income Benefit Rider and the fine print |
| 2 | `t2-loans-withdrawals` | Audio 10 min | Loans, withdrawals, and how people accidentally lapse a policy |
| 2 | `t2-options-and-tests` | Flashcards 10 | Option A vs Option B, GPT vs CVAT |
| 2 | `t2-underwriting` | Article 5 min | Getting the client approved: EZ Underwriting and what delays a case |
| 3 | `t3-first-conversation` | Audio 10 min | Your first FlexLife conversation with a prospect |
| 3 | `t3-discovery-questions` | Flashcards 20 | Discovery questions that actually work |
| 3 | `t3-living-benefits-story` | Audio 5 min | The living benefits story: castle and moat, YouFundYou |
| 3 | `t3-objections` | Audio 10 min | Objections: the sale starts when you hear no |
| 3 | `t3-objection-scripts` | Flashcards 20 | Objection scripts |
| 3 | `t3-illustration-and-close` | Article 10 min | Walking a client through the illustration and closing |
| 3 | `t3-after-the-sale` | Article 5 min | After the sale: delivery, reviews, and referrals |
| 3 | `t3-never-say` | Flashcards 20 | What you can never say about FlexLife |

Source documents used: FlexLife Product Guide, Product Quick Reference Guide, LIBR Quick Reference Guide, product page, agent training slide script, consumer brochures 01/03/05, IUL explainer article, index guide, Accelerated Benefit Riders guide, Living Benefits brochure, Premium Chronic Care deck and flyer, LIBR flyer, three living-benefits training videos, EZ Underwriting flyer, Underwriting Guide, Art of the Sale 1-4, KeyToTheSale objection notes, Presenting to Prospects panel, Guide to Conversational Questioning, Financial State of Mind questionnaire.

### `src/rag_layer/speech.py` (125 lines)
Azure Speech REST client.

- `speech_configured()`: true when `AZURE_SPEECH_KEY` and `AZURE_SPEECH_REGION` are set.
- `AzureSpeechClient.synthesize_dialogue()`: builds multi-voice SSML in batches of 8 turns (each request stays well under Azure's per-request audio ceiling), retries 429/5xx with backoff, and concatenates the constant-bitrate MP3 frames into one file.
- Output format `audio-24khz-48kbitrate-mono-mp3`; `estimate_seconds()` derives duration from byte length.
- Voices come from `AZURE_SPEECH_VOICE_AVA` / `AZURE_SPEECH_VOICE_ANDREW` (already present in `.env`).

### `ui/learn.html` (697 lines)
Single self-contained page, no build step, no external dependencies. Phone-width layout (400 px) centered on desktop, full-bleed on phones. Hash routing.

- **Learn home**: status bar and green header with streak pill; "Continue listening" card (last audio, progress from localStorage); Recommended mixes carousel (tier 1); orange "Create custom scenario" button; collapsible **FlexLife curriculum** section with three tiers and per-tier progress; "My mixes" list for custom prompts. Polls every 3 s while anything is generating. Offers "Generate the starter pack" when lessons are missing.
- **Create custom**: prompt box with voice input (Web Speech API where available), suggestion chips, Type segmented control (Audio / Article / Flashcards), Duration control whose labels change per type (5/10 mins, 5/10 min read, 10/20 cards), "Make my mix".
- **Generating state**: cover art, three-step progress copy, polls until ready.
- **Article**: hero, headline, subtitle, reading meta, sections, key takeaways box, "say it like this" quotes, watch-outs box, sources strip.
- **Flashcards**: progress bar, topic label, organic-shaped card that flips on tap, "Got it" / "Review again" grading, Back / Next, end-of-deck score with "Review the missed" loop and restart.
- **Audio**: cover art, scrubber with elapsed/remaining, playback rate (1x / 1.25x / 1.5x / 0.75x), 15-second skips, play/pause, animated blob, transcript toggle, share (copies link). When no MP3 exists: a notice with the reason and a "Render audio now" button that polls until rendered.
- **Bottom nav**: Learn (active), Prepare (toast: coming soon), Coach (links to the existing `index.html` console).
- API base is same-origin when served from `/app`, else `http://localhost:8000`; `?api=` overrides for local dev.

### `tests/test_learn.py` (215 lines)
20 unit tests, no network or database: JSON parsing and recovery, response text extraction, length specs, recommended mixes, retrieval merge and ranking, serialization, speech configuration, voice mapping, SSML escaping, request batching, duration estimate, curriculum shape, outline grouping, brief injection, source dedupe.

Run with:
```
PYTHONPATH=.vendor python -m pytest tests/test_learn.py
```

---

## 3. Modified files

### `src/rag_layer/db.py` (+171)
- Schema: new `learn_mixes` table (kind, prompt, length, status, title, summary, duration_seconds, content JSONB, sources JSONB, audio BYTEA, audio_mime, error, recommended, tier, curriculum_key, timestamps). Created automatically on API startup via the existing `_ensure_schema`; column additions use `ADD COLUMN IF NOT EXISTS`; a partial unique index on `curriculum_key` prevents duplicate lessons.
- New helpers: `create_mix`, `list_mixes`, `get_mix`, `get_mix_audio`, `update_mix` (JSON columns wrapped automatically), `delete_mix`, `mark_stale_mixes` (rows stuck queued/generating for more than 12 minutes are marked failed), `list_audio_mixes_without_audio`, and `get_chunks_for_source` (a document's chunks in reading order, filtered by PDF page list or chunk range; returns the same row shape as vector search so generators treat both alike).

### `src/rag_layer/server.py` (+275)
- Pydantic models for mix create/summary/detail/list, learn status, seed, curriculum outline, render-pending.
- Startup: marks stale mixes failed.
- Static mount: `ui/` is served at `/app`, and `/learn` redirects to `/app/learn.html`, so the browser has one origin for the API, the page, and audio streaming.
- CORS now allows `DELETE`.
- New routes (all under `/v1/learn`):

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/status` | speech configured?, kinds, lengths, length specs, recommended prompts |
| GET | `/curriculum` | tiered lesson outline (no briefs) |
| GET | `/mixes` | list (marks stale rows first) |
| POST | `/mixes` | create a custom mix, returns 202, generation queued |
| POST | `/seed` | queue every curriculum lesson not yet present (tier 1 flagged recommended) |
| GET | `/mixes/{id}` | detail with content and sources |
| GET | `/mixes/{id}/audio` | the MP3 (404 if not rendered) |
| POST | `/mixes/{id}/retry` | regenerate a failed or finished mix (409 if in progress) |
| POST | `/mixes/{id}/render-audio` | synthesize only the MP3 for a finished audio mix |
| POST | `/audio/render-pending` | render every audio episode that has a script but no MP3 |
| DELETE | `/mixes/{id}` | remove a mix |

### `Dockerfile` (+1)
`COPY ui ./ui` so the container serves the app.

### `start-rag-demo.ps1` (+13) and `start-rag-demo.sh` (+9)
Copy the `ui/` folder into the Docker build context, print the Learn app URL, list the new endpoint, and open `/learn` instead of `/docs` in the browser.

### `README.md` (+46)
New "salesDJ Learn app" section: the mix concept, format table, curriculum vs custom sourcing, endpoint list, Azure Speech configuration and the render-pending flow, test command.

---

## 4. How a mix is generated

```
POST /v1/learn/mixes  or  POST /v1/learn/seed
        │  row inserted, status = queued
        ▼
worker pool (3 threads)  →  status = generating
        │
        ├─ curriculum item?  gather_pinned_contexts()  (exact pages, reading order)
        └─ custom prompt?    plan_queries() → gather_contexts()  (pgvector search)
        │
        ▼
generate_article / generate_flashcards / generate_audio_script
   persona + optional coverage brief + numbered source excerpts → JSON
        │
        ├─ audio: AzureSpeechClient.synthesize_dialogue() → MP3 (or transcript-only with reason)
        ▼
row updated: status = ready, title, summary, duration, content, sources
        (any exception → status = failed, error text on the row)
```

The UI polls the list or detail endpoint until the row leaves `queued`/`generating`.

---

## 5. Configuration

Already present in `.env` (no new variables were required):

| Variable | Used for |
| --- | --- |
| `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_CHAT_DEPLOYMENT` (`gpt-5-mini`) | query planning and content generation |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | retrieval for custom prompts |
| `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION` | audio rendering (the key currently in `.env` is rejected with HTTP 401) |
| `AZURE_SPEECH_VOICE_AVA`, `AZURE_SPEECH_VOICE_ANDREW` | host voices (DragonHD neural voices) |

Local development without Docker: the API dependencies were installed into `.vendor` per the README (`psycopg`, `fastapi`, `uvicorn`, `python-dotenv`, `requests`, `pytest`). `.vendor` is git-ignored.

---

## 6. Current state of the live demo

- Docker image rebuilt via the Windows launcher; `http://localhost:8000/learn` serves the app.
- All 21 curriculum lessons are generated and `ready`. Tier 1 appears under Recommended.
- The eight audio episodes have full scripts and transcripts but no MP3 because the Azure Speech key is rejected. After a valid key is placed in `.env` and the launcher is rerun:
  ```
  curl -X POST http://localhost:8000/v1/learn/audio/render-pending
  ```
  renders every episode without regenerating scripts. Individual episodes also have a "Render audio now" button.
- Three early custom test mixes (crediting options article, living benefits deck, cash accumulation episode) remain under My mixes as examples.

---

## 7. Verification performed

- 20 unit tests pass.
- Every curriculum item's pinned sources were resolved against the live index: 15 to 72 chunks each, 1,900 to 11,200 words, all within the generator's limits.
- A headless jsdom walkthrough against the running API exercised the home screen (continue card, recommended carousel, curriculum tiers and toggles, my mixes), the Create screen (type and duration controls), article rendering, flashcard flip/grade/advance/summary, audio page with the transcript-only notice, a missing-mix error, and navigation, with zero runtime errors.
- Spot-read outputs: the cash-value article reproduces the source examples exactly (10% gain with a 6% cap credits 6%; 8% at 140% participation credits 11.2%) and flags that charges continue regardless of crediting; the objections episode walks the five buckets and the persistence statistics; the "never say" deck pairs each tempting claim with the compliant correction.
- Browser screenshots were not taken because the Chrome extension was not connected during the session.

---

## 8. Deliberately deferred

- Streak counter on the Learn home is a placeholder (localStorage, defaults to 12). The Prepare tab's streak is real (see Part B).
- New York product variant.
- Persisting playback position and flashcard results server-side (currently per-browser localStorage).
- Committing: nothing has been committed to git.

---

# Part B: Prepare tab (roleplay) merged into the app

Date: 2026-09-13

The standalone voice demo (`voice_demo/`: its own stdlib HTTP server on port 8010, its own
static SPA, its own login, calling the RAG API over HTTP) was folded into the salesDJ app
so there is one page, one API, one container, and one sign-in. The Prepare tab replaces
the old "coming soon" stub.

## B1. Summary

| Area | What changed |
| --- | --- |
| Backend | `src/rag_layer/roleplay.py` (new, ~900 lines): the voice demo's session engine, prompts, persona generation, voice resolution, and latency logging, ported from the `Handler` class into a `Roleplay` service object held on `app.state`. Retrieval and fact-check are in-process calls to `service.search` / `service.fact_check` instead of HTTP round trips to this same API. |
| Data | `src/rag_layer/roleplay_data/`: ten built-in personas, the persona / sales / feedback playbooks, and `voice_profiles.json`. `voice_profiles.py` and `gender.py` moved alongside. |
| Storage | Two new tables: `roleplay_personas` (custom prospects generated from a description, so they survive a container rebuild) and `roleplay_sessions` (one row per finished call: transcript, outcome, coaching report, fact-check events, elapsed time). In-flight sessions stay in process memory. |
| Sign-in | `src/rag_layer/auth.py`: one shared credential in front of the whole app, API, and docs. HttpOnly cookie, one token per process. Open paths: `/health`, static files, the sign-in endpoints. The Coach console follows a 401 to the sign-in screen and back. |
| API | 19 new routes: `/v1/auth/*`, `/v1/roleplay/*`, `/v1/speech/*` (table in the README). `/prepare` redirects into the app. |
| UI | `ui/learn.html` (+~700 lines): sign-in screen, Prepare home (stats, recommended, my scenarios, all scenarios, recent calls), scenario brief with call-length control, create-custom screen, the live call screen, Ask Navigator sheet, end-call modal, the "reviewing your call" wait state, the call report, and a menu sheet with sign-out. Same design tokens, header, tab bar, rows, carousel, segmented control, and bottom sheets as Learn. The Azure Speech SDK loads lazily when a call starts. |
| Tests | `tests/test_roleplay.py`: 48 tests. The persona/voice preflight from `voice_demo/preflight.py` became parametrised tests. |
| Config | `LOGIN_USERNAME`, `LOGIN_PASSWORD`, `COOKIE_SECURE`, `AZURE_OPENAI_REPLY_REASONING_EFFORT`, `RAG_SEARCH_LIMIT`, `MODEL_WARM_INTERVAL_SECONDS` (all defaulted; nothing new is required in `.env`). `requests` added to `requirements.txt` explicitly. |

## B2. Behaviour kept from the voice demo

Persona playbook injection, the three-layer objection ladder, hidden facts, the time
directive that makes the prospect leave when their slot is up, the four call outcomes and
the `[[END_CALL:...]]` marker, the duplicate-reply retry, `speechify` (dashes and ellipses
become sentence breaks for TTS), server-built SSML from the voice profile, per-profile
speech tokens, the background coaching + fact-check per turn, Ask Navigator, the
end-of-session feedback prompt, persona generation with per-field provenance, gender
inference for voice selection, the keep-warm ping, and the `[lat]` stdout latency log
including the browser's STT/TTS timings posted per turn.

## B3. Behaviour changed or dropped

- Text fallback: a call whose microphone or speech key is unavailable runs as a typed
  conversation (a type box is always present under the transcript). The old app had no
  fallback.
- Scenario brief before dialling: the old app started a call straight from the card. Now a
  brief shows what the agent knows, the ad, the skill being drilled, and the call length.
- Finished calls, custom prospects, and the streak/minutes stats persist in Postgres. The
  old app kept sessions in memory only and the stats were hard-coded.
- The old "Strategies" and "My saved scenarios" lists were mock data and were dropped; "My
  scenarios" (custom prospects) and "Recent calls" (real history) replace them.
- The per-session latency report text files (`voice_demo/reports/`) are no longer written;
  the `[lat]` stdout lines remain.
- The recognizer, synthesizer, pause/resume/stop, and mouth-to-ear metrics code was ported
  as-is from `voice_demo/static/app.js`.

## B4. Verification performed

- 68 unit tests pass (`tests/test_learn.py`, `tests/test_roleplay.py`).
- Live API on a dev port against the Docker Postgres: sign-in gate (401 on `/v1`, redirect
  to the sign-in screen for `/docs`), wrong and right password, scenarios, speech token
  issued (the current key is accepted by the token endpoint), a Priya call (customer reply
  in 1.7 s, background coaching and fact-check, Ask Navigator with 8 sources, feedback
  stored as history row 1), a custom prospect generated in 25 s with all 38 fields from the
  model and persisted.
- Headless jsdom walkthrough of the page against the live API, 24 steps, zero JS errors:
  sign-in gate, bad and good login, Learn home, Prepare home (stats, 3 recommended, 11
  rows, recent calls), scenario brief and duration control, live call in text fallback
  with a real customer reply, Ask sheet open/close, pause, end-call modal, reviewing
  state, report with three coaching boxes and transcript, report reopened from history,
  create screen, menu, sign out, gated deep link.
- Not verified: microphone and spoken audio in a real browser (the Chrome extension was
  disconnected during the session). The speech code is the voice demo's, unchanged.

## B5. Test data left in the database

Two finished calls (Priya, Keisha) and one custom prospect ("Dana", nurse) from the checks
above remain and show under Recent calls / My scenarios as examples. Delete them from the
report or scenario screens if unwanted.

---

# Part C. Prepare: nlg-roleplay personas replace the built-in ten

Date: 2026-09-12

The three personas from the `nlg-roleplay` chat app (`src/App.jsx`: Fred P01, Oliver
P02, Zac P03) are now the Prepare tab's built-in prospects. The earlier ten callback
personas moved to `src/rag_layer/roleplay_data/personas/archive/` (not loaded).

| Area | What changed |
| --- | --- |
| Personas | `personas/fred.json`, `oliver.json`, `zac.json`. `known_background`, `hidden_customer_state`, `training_objective` (and Zac's `disclosure_ladder`, `style_anchors`) are verbatim from the source. Card, voice, personality and `evaluation` fields were written from them; each file's `provenance` block says which is which. |
| Scene modes | New `mode` field on a persona: `callback` (the original lead-form scene, still used by custom prospects), `presentation` (Fred, Oliver) and `discovery` (Zac). `roleplay.py` carries the ported scene and behaviour text and the reply prompt branches on the mode; the persona playbook's speech and honesty rules still apply, its callback framing does not. |
| Card / UI | `scenario_card` gains `mode` and `known_background`; the scenario brief shows the fact-find notes for a mid-conversation persona. |
| Tests | `tests/test_roleplay.py`: preflight is mode-aware (callback fields checked on the archived set, brief fields on the new set) plus prompt tests for each scene. |

Not ported: the chat app let the prospect speak first; here the agent still opens. Its
per-turn `state` labels and the SME annotation / CSV export have no equivalent in the
voice demo.

---

# Part D. Learn: "Listen" on articles

Date: 2026-09-12

Articles can now be played as audio. It is a reading aid, not a fourth mix kind: the
audio kind stays the two-host episode, and an article's narration is Ava alone reading
the page top to bottom.

| Area | What changed |
| --- | --- |
| Narration | `learn.article_narration()` turns the stored article into segments in reading order: title + subtitle, each section (heading, paragraphs, bullets), then key takeaways, "say it like this" and watch-outs with a short spoken lead-in. Sources are not read. Empty blocks are skipped. |
| Speech | `AzureSpeechClient.synthesize_narration()` renders one request per segment in Ava's voice with paragraph / section pauses, and returns each segment's start offset in the joined MP3. |
| Render job | `run_audio_render` handles articles as well as episodes. For an article it stores the MP3, `content.narration` (`[{id, label, start}]`) and `content.audio_seconds`; `duration_seconds` stays the read time. |
| API | `POST /v1/learn/mixes/{id}/render-audio` accepts finished articles. A repeat call while a render is in flight is a no-op; a render stuck in "rendering" for over ten minutes (orphaned by a restart) can be retried. `render-pending` is unchanged and still only bulk-renders episodes. |
| UI | Article page: a **Listen to this article** card under the byline. Tapping it queues the narration and shows a recording state, then it becomes a sticky mini player (play/pause, scrubber, speed) that highlights and scrolls to the section being read. Position resumes across visits like episodes do. |
| Cost | Narration is lazy: created on first tap and cached on the row. Roughly one speech request per section. |
| Tests | `tests/test_learn.py`: narration order and empty-block handling, single-voice SSML, offset bookkeeping, the render job for articles (success, failure, wrong kind). |

---

# Part E. Agent Navigator: splash, sign-in, and Profile

Date: 2026-09-13

The app is presented as **Agent Navigator** (page title, menu sheet, sign-in copy) to match
the design mockups. Still one shared login and therefore one profile for the deployment.

| Area | What changed |
| --- | --- |
| Splash | On load the page shows a full-bleed green screen with the National Life Group mark and "Experience Life" while the session cookie is checked (minimum 1.4 s), then goes to sign-in or the last screen. |
| Sign-in | Rebuilt to the mockup: pale green page, "Agent Navigator" heading, serif labels, "Username or Agent ID" with placeholder, password show/hide, rectangular **Log In** button, "Forgot username? / Forgot password?" links (a toast explaining the shared demo login). Backend auth unchanged. |
| Profile | New `#/profile` screen (menu sheet, "Profile"): level card, three stat tiles, three skill bars, and two language rows. Data from `GET /v1/profile`. |
| Level | `profile.level_for(calls)`: four levels by finished calls (0 / 5 / 15 / 30), progress toward the next, criteria text. Never goes down. |
| Stats | `db.profile_stats()`: total calls with a week-over-week delta, average overall score with a week-over-week delta, last practice date, per-skill averages. |
| Scoring | The coaching report (`generate_feedback`) now also returns `score` (0-100) and `skills` (`living_benefits`, `illustration_design`, `objection_handling`; null when the call did not exercise the skill). Clamped by `profile.normalize_feedback_scores` and stored inside the existing `feedback` JSONB. Older sessions have no score and are simply excluded from the averages. |
| Skills | Tapping a skill opens Create custom with a topic prefilled for that skill (`#/create?topic=...`). |
| Languages | `app_settings` key-value table (`db.get_app_settings` / `set_app_settings`); `PUT /v1/profile/settings` validates `app_language` / `practice_language` against the eleven supported languages. Picker screen with search, flags (CSS gradients: emoji flags do not render on Windows), native names, and Save. |
| Practice language | `Roleplay.start()` takes the practice language: the prospect prompt is told to speak it, STT uses that Azure locale, SSML carries the locale, and the persona's DragonHD voice (English-only) is swapped for Azure's multilingual voice of the same gender. Haitian Creole has no Azure recogniser, so that call runs as a typed call. |
| App language | Stored and shown only; the demo's own screens stay in English. |
| Tests | `tests/test_roleplay.py`: level thresholds, score clamping, language validation, feedback normalisation, Spanish prompt line, multilingual voice swap (78 passing). jsdom walk-through of splash -> sign-in -> profile -> picker -> skill tap. |

Verified on the rebuilt container: `/v1/profile`, `PUT /v1/profile/settings` (valid and
rejected codes), a call started with Spanish returns `es-US` / `en-US-AndrewMultilingualNeural`.

---

## Part C: UI pass against the Agent Navigator mock-ups (2026-09-14)

Screens reworked in `ui/learn.html` to match the mock-up set (login, home, learn, ask, call, feedback).
Backend change: the coaching report's three lists now return `{title, detail, badge?}` objects
instead of plain strings (`roleplay.py` feedback prompt); the UI still renders older string reports.

| Screen | What changed |
| --- | --- |
| Login | Dark office-tower hero (CSS only, no photo), white "Agent Navigator Login" card overlapping it, `Login` button, agent-use-only footer. |
| Home (new, route `/`) | Greeting from the username, ask bar that opens Ask Navigator, "Picked for you" rail (audio + roleplay + flashcards), "Daily practice" roleplay card (rotates by day), coach follow-ups (demo list, done state in localStorage), "Your progress" tiles from roleplay stats plus listened/viewed mixes. |
| Learn (`/learn`) | Band-style recommended cards, green "Create custom mix", "My mixes" with See all (`/learn/mixes`), learning path kept, "FlexLife video library" categories (`/learn/videos/:slug`, placeholder rows that toast). Create screen keeps two lengths per type. |
| Ask Navigator | A pull-up bottom sheet (`openNavigator()`) that slides up over the current screen — matching the mock-up's floating chat card. Reverted from the interim full-page `/ask` version. Orb header with Clear + Close, orb avatar per answer, thumbs up/down (local), Sources/Copy, follow-up chips, mic + up-arrow input. Opened from the Ask tab button and the Home search bar; `#/ask` deep links still open it over Home. Same chat widget powers the in-call sheet. |
| Detail screens | Cream "light" header everywhere off the tab roots; Article/Flashcards/Audio use a close (X) header; back returns to wherever the screen was opened from (small route stack). |
| Call | Status pill, animated waveform, Transcription switch with a grey Client/You panel, type box kept, timer + pause / stop / Ask Navigator orb pinned to the bottom. Back = "Quit scenario?" (No/Yes, session discarded); stop = "End the call?" then feedback. |
| Feedback | Performance score card (score, sessions, streak, minutes), two-column cards for did well (with Mastered / New skill badges) and improve (+ My mix opens Create with the topic), horizontal row for not-to-repeat, bookmark with Undo toast; bookmarked scenarios listed on Prepare. |
| Tab bar | Home · Learn · Prepare · Coach · Ask; active tab gets a circular highlight. Coach still opens the console. Fixed: the bar now really hides on Ask and during a call. |

Verified with a jsdom walkthrough (scratch harness, all checks passing) and headless Chrome
screenshots against a stub API. Not verified against the live Azure stack in this pass.
