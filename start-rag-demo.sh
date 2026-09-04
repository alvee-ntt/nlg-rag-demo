#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_ROOT="${RAG_DEMO_RUNTIME:-/tmp/rag-demo-runtime}"
NO_PAUSE="${NO_PAUSE:-0}"

wait_for_exit() {
  if [ "$NO_PAUSE" != "1" ]; then
    read -r -p "Press Enter to close this window"
  fi
}

copy_rag_runtime() {
  echo "Preparing Docker build context at $RUNTIME_ROOT..."
  mkdir -p "$RUNTIME_ROOT"
  cp -f "$PROJECT_ROOT/Dockerfile" "$RUNTIME_ROOT/Dockerfile"
  cp -f "$PROJECT_ROOT/docker-compose.yml" "$RUNTIME_ROOT/docker-compose.yml"
  cp -f "$PROJECT_ROOT/requirements.txt" "$RUNTIME_ROOT/requirements.txt"
  cp -f "$PROJECT_ROOT/.dockerignore" "$RUNTIME_ROOT/.dockerignore"
  cp -f "$PROJECT_ROOT/.env" "$RUNTIME_ROOT/.env"
  cp -f "$PROJECT_ROOT/sitecustomize.py" "$RUNTIME_ROOT/sitecustomize.py"
  rm -rf "$RUNTIME_ROOT/src"
  cp -R "$PROJECT_ROOT/src" "$RUNTIME_ROOT/src"
}

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker Desktop is required but docker was not found on PATH."
  echo "Install/start Docker Desktop, then run this launcher again."
  wait_for_exit
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker engine is not running. Starting Docker Desktop..."
  if command -v open >/dev/null 2>&1; then
    open -a Docker || true
  else
    echo "Could not auto-launch Docker on this platform. Please start Docker manually."
  fi

  engine_ready=0
  for i in $(seq 1 60); do
    if docker info >/dev/null 2>&1; then
      engine_ready=1
      break
    fi
    echo "Waiting for Docker engine... ($i)"
    sleep 3
  done

  if [ "$engine_ready" -ne 1 ]; then
    echo "Docker engine did not become ready within ~3 minutes."
    echo "Make sure Docker Desktop finishes starting, then run this launcher again."
    wait_for_exit
    exit 1
  fi
  echo "Docker engine is ready."
fi

copy_rag_runtime
cd "$RUNTIME_ROOT"

echo "Starting RAG API container..."
docker compose --project-name rag-demo up --build -d

health_url="http://localhost:8000/health"
docs_url="http://localhost:8000/docs"
ready=0

for _ in $(seq 1 30); do
  if curl -fsS "$health_url" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done

if [ "$ready" -ne 1 ]; then
  echo "The container started, but the API did not become healthy within 60 seconds."
  echo "Run this to inspect logs:"
  echo "  cd \"$RUNTIME_ROOT\""
  echo "  docker compose --project-name rag-demo logs -f rag-api"
  wait_for_exit
  exit 1
fi

echo "RAG API is running:"
echo "  $health_url"
echo "  $docs_url"
echo ""
echo "Endpoints:"
echo "  POST http://localhost:8000/v1/search"
echo "  POST http://localhost:8000/v1/answer"
echo "  POST http://localhost:8000/v1/fact-check"

# The schema is created automatically on API startup; check whether any documents exist.
doc_count="$(docker compose --project-name rag-demo exec -T rag-api python -B -m src.rag_layer.db count 2>/dev/null | tail -n 1 | tr -d '[:space:]' || true)"
echo ""
if [ "$doc_count" = "0" ] || [ -z "$doc_count" ]; then
  echo "Database is empty. Ingesting the full document corpus from Azure Blob..."
  echo "This can take several minutes (downloads + embeds every document). Leave this window open."
  if docker compose --project-name rag-demo exec -T rag-api python -B -m src.rag_layer.ingest; then
    after_count="$(docker compose --project-name rag-demo exec -T rag-api python -B -m src.rag_layer.db count 2>/dev/null | tail -n 1 | tr -d '[:space:]' || true)"
    echo "Ingestion complete. Documents indexed: ${after_count:-?}"
  else
    echo "Ingestion did not finish cleanly."
    echo "Check the Azure Blob SAS + Azure OpenAI credentials in .env, then retry with:"
    echo "  docker compose --project-name rag-demo exec rag-api python -B -m src.rag_layer.ingest"
  fi
else
  echo "Documents indexed: $doc_count"
  echo "(To re-pull the corpus from Azure Blob later, run:"
  echo "  docker compose --project-name rag-demo exec rag-api python -B -m src.rag_layer.ingest )"
fi

if command -v open >/dev/null 2>&1; then
  open "$docs_url"
fi

echo ""
echo "Leave Docker Desktop running while your app uses the API."
wait_for_exit
