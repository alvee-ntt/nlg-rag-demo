#!/usr/bin/env bash
set -euo pipefail
RUNTIME_ROOT="${RAG_DEMO_RUNTIME:-/tmp/rag-demo-runtime}"
if [ -f "$RUNTIME_ROOT/docker-compose.yml" ]; then
  cd "$RUNTIME_ROOT"
  docker compose --project-name rag-demo down
else
  echo "No staged rag-demo runtime found at $RUNTIME_ROOT"
fi
read -r -p "Press Enter to close this window"
