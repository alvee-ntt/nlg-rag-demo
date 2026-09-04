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
