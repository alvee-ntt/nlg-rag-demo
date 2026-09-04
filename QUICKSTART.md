# RAG Layer — Quickstart

Run one command. It starts the RAG layer, ingests every document from Azure Blob,
embeds them into the pgvector store, and exposes a live API you can call.

## 1. Install Docker Desktop (one-time)

- Mac: https://www.docker.com/products/docker-desktop/
- You do **not** need to open Docker yourself — the launcher starts it for you.

## 2. Start everything (one command)

Open **Terminal**, `cd` into this folder (tip: type `cd ` then drag the folder into
the Terminal window), then run:

**Mac:**
```bash
bash start-rag-demo.sh
```

**Windows:** double-click `start-rag-demo-windows.bat` (or run `./start-rag-demo-windows.bat`).

That single command will:
1. Start Docker Desktop if it isn't running, and wait until it's ready.
2. Build the API image and start Postgres (pgvector) + the API.
3. **On the first run only:** download the full document corpus from Azure Blob,
   embed it, and load it into the vector store. This takes several minutes —
   leave the window open until it prints `Ingestion complete`.

When you see **`RAG API is running`**, it's ready. Later runs start in seconds
(the data is already stored).

## 3. Use the live endpoints

Interactive docs (try requests in the browser): **http://localhost:8000/docs**

Health check:
```bash
curl http://localhost:8000/health
```

Semantic search:
```bash
curl -X POST http://localhost:8000/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query":"accelerated benefit riders for chronic care","limit":5}'
```

Grounded answer with citations:
```bash
curl -X POST http://localhost:8000/v1/answer \
  -H "Content-Type: application/json" \
  -d '{"question":"What is FlexLife and who is it for?","limit":8}'
```

Fact-check a claim against the sources:
```bash
curl -X POST http://localhost:8000/v1/fact-check \
  -H "Content-Type: application/json" \
  -d '{"claim":"FlexLife has no downside protection.","limit":8}'
```

Request fields: `/v1/search` takes `query`, `/v1/answer` takes `question`,
`/v1/fact-check` takes `claim`. All accept an optional `limit` (1–20, default 8).

## 4. Stop it

**Mac:** `bash stop-rag-demo-mac.command`   **Windows:** double-click `stop-rag-demo-windows.bat`

Your ingested data persists (Docker volume `rag-demo_pgdata`), so the next start is instant.

---

## Notes

- **First run before Aug 22, 2026.** The Azure Blob access token in `.env` expires
  then. The first run is what ingests the documents, so do it before that date.
  (After ingestion, everyday queries don't need the Blob token — only the Azure
  OpenAI key, which is also in `.env`.)
- **Use `bash start-rag-demo.sh` on Mac** rather than double-clicking the `.command`
  file — a zip doesn't preserve the "executable" permission, so double-click may show
  a permission or security warning. If you prefer double-click, run this once first:
  ```bash
  chmod +x start-rag-demo.sh start-rag-demo-mac.command stop-rag-demo-mac.command
  ```
  then right-click the `.command` → **Open** the first time.
- **Re-ingest later** (e.g. after documents change), without wiping anything:
  ```bash
  docker compose --project-name rag-demo exec rag-api python -B -m src.rag_layer.ingest
  ```
