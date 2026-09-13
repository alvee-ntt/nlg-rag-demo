# NLG RAG Layer

Ingests Azure Blob Storage documents into PostgreSQL with pgvector, then retrieves relevant chunks for Azure OpenAI chat.

## Current Status

Verified locally:

- Azure Blob SAS can list `nlg-agent-navigator` documents.
- Azure OpenAI embedding deployment returns 1536-d vectors.
- PostgreSQL/pgvector schema initializes successfully.
- Sample ingestion embedded two FlexLife HTML files.
- Retrieval and answer generation work.

## Setup

Dependencies are installed project-locally in `.vendor`, and `src/rag_layer/__init__.py` adds that folder to Python's import path for `python -m src.rag_layer...` commands.

To reinstall dependencies later:

```powershell
python -m pip install --target .vendor --upgrade --no-cache-dir -r requirements.txt
icacls .vendor /grant "$($env:USERNAME):(OI)(CI)F" /T
```

The normal virtual environment path is still fine on machines where `python -m venv` works:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Database

Postgres (pgvector) is part of `docker-compose.yml`, so you no longer start it by hand. `docker compose up --build -d` brings up the `postgres` service alongside the API, with a persistent `pgdata` volume and `restart: unless-stopped` so it survives reboots. The port is published on `localhost:5432` for host-side tools.

The schema (the `vector` extension, `rag_documents`/`rag_chunks` tables, and the HNSW index) is created **automatically on API startup** — see `_ensure_schema` in `src/rag_layer/server.py`. You can still initialize it manually if you are running the code outside the container:

```powershell
python -B -m src.rag_layer.db init
```

Check how many documents are indexed:

```powershell
python -B -m src.rag_layer.db count
```

> **One-time migration:** if you previously started the standalone `postgres-pgvector` container from an older README, remove it first so it does not conflict on port 5432 with the compose-managed Postgres:
>
> ```powershell
> docker rm -f postgres-pgvector
> ```
>
> Its data does not carry into the new `pgdata` volume, so re-ingest your documents afterward.

## Blob Access

The project currently uses `AZURE_STORAGE_SAS_TOKEN` from `.env`. The SAS must have:

- `Read`
- `List`

For this local environment, `.env` includes:

```env
AZURE_STORAGE_VERIFY_SSL=false
```

That bypasses Python certificate verification for Azure Blob only. For production, replace this with a trusted corporate CA bundle and set SSL verification back to true.

## Commands

List candidate blobs without downloading or embedding:

```powershell
python -B -m src.rag_layer.ingest --dry-run --limit 10
```

Ingest a small sample:

```powershell
python -B -m src.rag_layer.ingest --prefix flexlife --limit 3
```

Ingest everything under configured prefixes:

```powershell
python -B -m src.rag_layer.ingest
```

Reindex existing blobs:

```powershell
python -B -m src.rag_layer.ingest --force-reindex
```

Reindex a single document (change detection is a hash of the raw bytes, so
`--force-reindex` is required after any extraction or chunking change — the bytes are
unchanged and the document would otherwise be skipped):

```powershell
python -B -m src.rag_layer.ingest --blob-name "shared/riders/Rider_Premium Chronic Care.pdf" --force-reindex
```

## PDF extraction

PDFs are extracted layout-aware by `src/rag_layer/pdf_layout.py`, not by a plain text
dump. Page blocks are ordered by recursive XY-cut so designed multi-column pages read
the way a human reads them, headings are recovered from font metrics, borderless tables
are rebuilt as markdown, and legal disclosures are separated into their own zone.
Chunks carry `{"page", "zone", "heading_path"}` in `rag_chunks.metadata`.

PyMuPDF is AGPL-3.0 (or commercial). All engine-specific code is confined to
`_load_pages`, so replacing it with a permissively licensed backend is a local change.

Query retrieved chunks only:

```powershell
python -B -m src.rag_layer.query "FlexLife email" --no-answer
```

Query with answer generation:

```powershell
python -B -m src.rag_layer.query "What is the FlexLife email template about?"
```

## Local API

Run the FastAPI server directly:

```powershell
python -B -m src.rag_layer.server
```

The API is available at:

- `GET http://127.0.0.1:8000/health`
- `POST http://127.0.0.1:8000/v1/search`
- `POST http://127.0.0.1:8000/v1/answer`
- `POST http://127.0.0.1:8000/v1/fact-check`
- `http://127.0.0.1:8000/docs` for interactive OpenAPI docs

Example answer request:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/v1/answer `
  -ContentType 'application/json' `
  -Body '{"question":"What is the FlexLife email template about?","limit":8}'
```

## salesDJ app

One phone-width training app for new agents, served by the API at
`http://localhost:8000/learn` (a single static page, `ui/learn.html`), with three tabs:

- **Learn**: generated articles, flashcard decks, and audio episodes (below).
- **Prepare**: spoken roleplay calls against an AI prospect, with a live coach and an
  end-of-call report (see *Prepare* further down).
- **Coach**: the original RAG console (`ui/index.html`) for search, answers, and fact-checks.

The whole app, the API, and the interactive docs sit behind one shared demo sign-in
(`LOGIN_USERNAME` / `LOGIN_PASSWORD`, default `user` / `flexlife`; set
`COOKIE_SECURE=true` behind TLS). It is a gate, not an identity system: it exists
because `/v1/speech/token` mints Azure Speech tokens against a real key. `/health`, the
static files, and the sign-in endpoints are open; everything under `/v1` returns 401
without the cookie. A server restart signs everyone out.

### Learn

A **mix** is one topic rendered in one of three formats, all grounded in the indexed documents:

| Kind | Short | Long | What you get |
| --- | --- | --- | --- |
| `article` | 5 min read | 10 min read | Sectioned lesson, key takeaways, "say it like this" talk track, watch-outs, sources. **Listen** narrates it with Ava's voice on demand |
| `flashcards` | 10 cards | 20 cards | Flip deck with self-grading, missed-card review, source refs |
| `audio` | 5 min | 10 min | Two-host coaching episode (Ava & Andrew) rendered to MP3 by Azure Speech, with transcript |

Two ways a mix gets its sources:

- **Curriculum mixes** (`src/rag_layer/curriculum.py`) are the 21-lesson starter pack in
  three tiers (day-one essentials, deeper plain-English product mechanics, selling).
  Each lesson pins the exact document pages it is written from and carries a coverage
  brief listing the facts it must land. Tier 1 is what the Learn home recommends.
  The pack is written for the non-New-York FlexLife; NY material is excluded.
- **Custom mixes** (the Create screen) expand the agent's prompt into three retrieval
  queries and pull the best-matching chunks from pgvector.

Either way the chat model returns a structured JSON result constrained to the
excerpts. Generation runs on a small worker pool; rows in `learn_mixes` move
`queued -> generating -> ready | failed`.

Endpoints:

- `GET  /v1/learn/status` — speech configured?, length specs, recommended prompts
- `GET  /v1/learn/curriculum` — the tiered lesson outline
- `GET  /v1/learn/mixes` / `GET /v1/learn/mixes/{id}` / `DELETE /v1/learn/mixes/{id}`
- `POST /v1/learn/mixes` `{"kind":"audio|article|flashcards","prompt":"...","length":"short|long"}`
- `POST /v1/learn/seed` — queue every curriculum lesson not yet in the library
- `POST /v1/learn/mixes/{id}/retry`
- `GET  /v1/learn/mixes/{id}/audio` — the MP3
- `POST /v1/learn/mixes/{id}/render-audio` and `POST /v1/learn/audio/render-pending` —
  synthesize audio for episodes whose script exists but has no MP3 yet. `render-audio`
  also narrates an article (single voice, one request per section, offsets stored so the
  reader can highlight the section being read); articles are only narrated when someone
  taps **Listen**, never at creation, so speech minutes are spent on what gets played

Audio needs `AZURE_SPEECH_KEY` and `AZURE_SPEECH_REGION` in `.env` (voices are
overridable with `AZURE_SPEECH_VOICE_AVA` / `AZURE_SPEECH_VOICE_ANDREW`). If Speech is
provided by an Azure AI Services / Foundry resource rather than a standalone Speech
resource, its key only works on the resource's own domain: set
`AZURE_SPEECH_ENDPOINT=https://<name>.cognitiveservices.azure.com` and use that
resource's key (this project's Foundry resource shares the Azure OpenAI key). Without a
valid key the episode is still written and shown as a transcript; once the key is in
place, call `render-pending` (or tap **Render audio now** on an episode) to fill in
the MP3s without regenerating scripts.

Unit tests for the pipeline helpers: `PYTHONPATH=.vendor python -m pytest tests/test_learn.py`.

### Prepare (roleplay calls)

The agent picks a prospect (three built-in personas under
`src/rag_layer/roleplay_data/personas/`: Fred, Oliver and Zac, ported from the
`nlg-roleplay` chat app; or one they describe themselves), and talks. The built-in
three pick up **mid-conversation**: the
fact-find is already done, so there is no greeting and the prospect expects the agent to
use what they already know. Fred and Oliver are `presentation` mode (the agent has just
turned to how FlexLife applies and the prospect reacts from a known background plus a
hidden state). Zac is `discovery` mode (the agent turns to what happens if he ever
needed care, and he reveals his situation one layer at a time, only when a question or
real empathy earns it). Custom prospects keep the original `callback` scene (a lead-form
callback with an objection ladder), and the earlier ten callback personas are kept under
`personas/archive/` and are not loaded. Speech runs in the browser with the Azure
Speech SDK on a short-lived token from `/v1/speech/token`; the server only exchanges
text. A call that cannot use the microphone (no key, permission refused, SDK blocked)
still works as a typed conversation.

- The prospect is played by the chat model from its persona JSON plus the persona
  playbook; only the reply is on the spoken path (about two seconds per turn).
- Each turn, coaching and a fact-check of the agent's claim run in the background via
  the same retrieval used by `/v1/fact-check`, and feed the end-of-call report.
- **Ask Navigator** answers the agent's questions mid-call from the sales playbook and
  the source documents.
- When the prospect's time is up they leave the call with a reason; the call ends with
  one of the playbook's four outcomes (applied, second appointment, soft no, hard no).
- Finishing a call generates the coaching report (did well / improve / don't repeat)
  and stores the call in `roleplay_sessions`, which powers **Recent calls**, the
  streak, and the minutes total. Custom prospects persist in `roleplay_personas`.
- Voice profiles (`roleplay_data/voice_profiles.json`) map gender x age band to a
  DragonHD voice with prosody; a persona pins one or gets one selected.

Endpoints (all need the sign-in cookie):

- `POST /v1/auth/login` `{"username","password"}` / `POST /v1/auth/logout` / `GET /v1/auth/me`
- `GET  /v1/roleplay/status`, `GET /v1/roleplay/scenarios`, `GET /v1/roleplay/scenarios/{id}` (inspect a persona)
- `POST /v1/roleplay/scenarios/custom` `{"notes":"..."}` / `DELETE /v1/roleplay/scenarios/{id}`
- `POST /v1/roleplay/sessions` `{"persona_id"}` then
  `POST /v1/roleplay/sessions/{sid}/turn` `{"agent_text"}`,
  `.../ask` `{"question"}`, `.../feedback`, `.../metrics` (browser STT/TTS timings)
- `GET  /v1/roleplay/history`, `GET /v1/roleplay/history/{id}`, `DELETE /v1/roleplay/history/{id}`
- `POST /v1/speech/token` `{"profile_id"}`, `GET /v1/speech/profiles`

Settings: `AZURE_OPENAI_REPLY_REASONING_EFFORT` (default `minimal`, the spoken reply
only), `RAG_SEARCH_LIMIT` (default 8), `MODEL_WARM_INTERVAL_SECONDS` (default 240; `0`
disables the keep-warm ping). Latency for every phase prints as `[lat]` lines on stdout.

Tests, including the persona/voice preflight (every persona resolves to a voice matching
its gender and age band): `PYTHONPATH=.vendor python -m pytest tests/test_roleplay.py`.

The standalone `voice_demo/` folder is the previous version of this feature and is no
longer wired to anything; it can be deleted once the Prepare tab has been signed off.

## Local Container

Build and run the whole stack (Postgres + API) with Docker Compose — the same single command on Windows and Mac:

```powershell
docker compose up --build -d
```

The container reads `.env` at runtime through `docker-compose.yml`. It does not copy `.env` into the image.

`docker-compose.yml` defines a `postgres` service and sets `POSTGRES_HOST=postgres` on the API so it reaches the database over the compose network. The `.env` default (`POSTGRES_HOST=localhost`) still applies to tools you run on the host, which reach the same database on the published `localhost:5432`.

From your web or mobile app, call:

```text
http://localhost:8000/v1/answer
http://localhost:8000/v1/fact-check
http://localhost:8000/v1/search
```

If your frontend runs on a different local port, add it to `CORS_ORIGINS` in `docker-compose.yml` or your environment as a comma-separated list.

## One-click local launch

Prerequisite for both Windows and Mac: install Docker Desktop. You do **not** need to start it first — the launcher opens Docker Desktop for you if the engine is not already running and waits until it is ready.

Windows:

```text
Double-click start-rag-demo-windows.bat
```

Mac:

```bash
chmod +x start-rag-demo.sh start-rag-demo-mac.command stop-rag-demo-mac.command
```

Then double-click:

```text
start-rag-demo-mac.command
```

The launcher starts Docker Desktop if needed, brings up Postgres + the API, and waits for `http://localhost:8000/health`. **On first run, if the database is empty, it automatically ingests the full document corpus from Azure Blob** (all prefixes in `AZURE_BLOB_PREFIXES`). This downloads and embeds every document, so it can take several minutes — leave the window open. On later runs the data is already there and startup is fast.

Requirements for the auto-ingest: valid Azure Blob SAS + Azure OpenAI credentials in `.env`. To re-pull the corpus later (e.g. after documents change):

```text
docker compose --project-name rag-demo exec rag-api python -B -m src.rag_layer.ingest
```

If you prefer a plain terminal instead of the double-click launcher, the equivalent one command on both platforms is:

```text
docker compose up --build -d
```

(Started this way, the schema still auto-creates, but the corpus is not ingested — run the `ingest` command above once, or use the launcher script which does it for you.)

Stop scripts:

- Windows: double-click `stop-rag-demo-windows.bat`
- Mac: double-click `stop-rag-demo-mac.command`
