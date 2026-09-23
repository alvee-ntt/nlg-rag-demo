# Deploying nlg-rag to Azure Container Apps

This is the runbook for shipping code changes to the live demo and (re)running ingestion.
The app runs as a prebuilt container image; deploying = **build image → push to ACR →
point the Container App at the new tag → verify a new revision goes healthy**.

## Resource facts

| Thing | Value |
|---|---|
| Container App | `nlg-rag-demo-ca` |
| Resource group | `nlg_sandbox` |
| Subscription | `msft.azure.launch.nttdata.com` (`8540a7c3-333d-44b5-a5da-113b27c71833`) |
| Tenant | `Launch/Platform Accelerators` (`51ec3334-c8f6-4e64-a985-4b17e46e6f9e`) |
| Registry (ACR) | `nlgcontainers` → `nlgcontainers-bfayhcemg5e0fnhv.azurecr.io` |
| Image repo | `nlg-rag-demo` (tags are timestamps, e.g. `20260923-083915`) |
| Live URL | https://nlg-rag-demo-ca.grayplant-450f758c.centralus.azurecontainerapps.io |
| Postgres (external, persists) | `nlg-db-rag-demo.postgres.database.azure.com` |

The image bakes in `src/` and `ui/` only — **no secrets or `.env`**. All credentials come
from the Container App's env vars/secrets at runtime.

## Prerequisites (once per machine/session)

1. **Docker Desktop running** (build happens locally; `az acr build` does *not* work here —
   see Troubleshooting).
2. **Azure CLI logged in to the right tenant** (the subscription lives in a non-default tenant):
   ```bash
   az login --tenant 51ec3334-c8f6-4e64-a985-4b17e46e6f9e
   ```
3. You need **Contributor on `nlg_sandbox`**. Note: Contributor does **not** grant ACR push —
   see step 1 of Deploy.

---

## Deploy a code change

### 1. Registry admin user (kept enabled)

Our accounts have Contributor but **no `AcrPush` data-plane role**, so both `az acr build` and a
plain `docker push` fail with `authorization`/`insufficient_scope`. We push using the registry's
**admin user**, which is intentionally left **enabled** on `nlgcontainers` (the repo owner manages
this). Nothing to do here in the normal flow.

If it ever shows as disabled, re-enable it:
```bash
az acr update -n nlgcontainers --admin-enabled true
```

### 2. Build and push the image

```bash
cd /c/Users/Alvee/Desktop/nlg-rag
TAG=$(date +%Y%m%d-%H%M%S)

# log in to ACR with admin creds
PW=$(az acr credential show -n nlgcontainers --query "passwords[0].value" -o tsv)
echo "$PW" | docker login nlgcontainers-bfayhcemg5e0fnhv.azurecr.io -u nlgcontainers --password-stdin

# build + push
docker build -t nlgcontainers-bfayhcemg5e0fnhv.azurecr.io/nlg-rag-demo:$TAG .
docker push nlgcontainers-bfayhcemg5e0fnhv.azurecr.io/nlg-rag-demo:$TAG
echo "Pushed tag: $TAG"
```

Use a unique timestamp tag (not `latest`) — the app is pinned to a specific tag, so the tag is
what triggers the new revision.

### 3. Point the Container App at the new tag

```bash
az containerapp update -n nlg-rag-demo-ca -g nlg_sandbox \
  --image nlgcontainers-bfayhcemg5e0fnhv.azurecr.io/nlg-rag-demo:$TAG
```

This creates a new revision (`nlg-rag-demo-ca--00000NN`). Existing env vars/secrets carry over.

### 4. Verify the new revision is healthy

```bash
az containerapp revision list -n nlg-rag-demo-ca -g nlg_sandbox \
  --query "reverse(sort_by([?properties.active].{rev:name,created:properties.createdTime,health:properties.healthState,traffic:properties.trafficWeight,image:properties.template.containers[0].image},&created))" -o table
```

Want the newest revision **Healthy**, **100% traffic**, running your new tag. Also quick-check:
```bash
curl -s https://nlg-rag-demo-ca.grayplant-450f758c.centralus.azurecontainerapps.io/health
```

---

## Changing env vars / secrets

Env/secret changes only take effect on a **new revision**. The `az containerapp update` in Deploy
step 3 creates one; if you're *only* changing config (no new image), the commands below each create
a revision on their own.

```bash
# add or change a plain env var
az containerapp update -n nlg-rag-demo-ca -g nlg_sandbox --set-env-vars KEY=value

# remove an env var
az containerapp update -n nlg-rag-demo-ca -g nlg_sandbox --remove-env-vars KEY

# set/rotate a secret (then create a revision so it's picked up)
az containerapp secret set -n nlg-rag-demo-ca -g nlg_sandbox --secrets "secret-name=VALUE"
```

Prefer **secrets** (referenced from env) for anything sensitive — API keys, SAS tokens.

---

## Running ingestion

Ingestion is **not** automatic — nothing in the deployed app triggers it (no HTTP endpoint, no
startup hook). It's a CLI module you exec inside the running container. Data persists in the
external Postgres, so it survives revisions/restarts and only needs re-running when documents or
ingest logic change.

### Requirements before it will run
- **`AZURE_STORAGE_SAS_TOKEN`** must hold a valid, current SAS (raw query string form, no leading
  `?`; needs `racwl` + container scope `sr=c`).
- **`AZURE_STORAGE_CONNECTION_STRING` must NOT be set.** The code (`blob_store.py:75`) *refuses to
  run* if a connection string is present — it uses the SAS token only. If ingest fails with
  `NotImplementedError: This project currently uses AZURE_STORAGE_SAS_TOKEN`, remove that var:
  ```bash
  az containerapp update -n nlg-rag-demo-ca -g nlg_sandbox --remove-env-vars AZURE_STORAGE_CONNECTION_STRING
  ```

### Run it

```bash
# dry run first (no writes) — confirms blob access works
az containerapp exec -n nlg-rag-demo-ca -g nlg_sandbox \
  --command "python -B -m src.rag_layer.ingest --dry-run --limit 10"

# full ingest
az containerapp exec -n nlg-rag-demo-ca -g nlg_sandbox \
  --command "python -B -m src.rag_layer.ingest"
```

- `exec` streams the command's output and ends when it finishes. Ingest is **idempotent**
  (content-hash dedup), so if the session drops mid-run, just run it again — it resumes.
- Useful flags: `--force-reindex` (re-embed even if content hash unchanged — needed after chunking/
  extraction/embedding-model changes), `--prefix NAME`, `--blob-name "path" --force-reindex`.
- Success looks like: `Done. inspected=NNN embedded=NNN skipped=NNN failed=0`.

Only single-quote-free commands survive the exec parser — avoid `sh -c '...'` / shell operators
(`&`, `>`, `nohup`); run the plain module instead.

---

## Foundry (Coach console "Foundry" tab)

Needs these env vars (from `config.py`); the tab is disabled until endpoint + key are both set:

| Var | Notes |
|---|---|
| `FOUNDRY_PROJECT_ENDPOINT` | e.g. `https://nlg-foundry-res.services.ai.azure.com/api/projects/nlg-main-proj` |
| `FOUNDRY_API_KEY` | **store as a secret**, not plaintext |
| `FOUNDRY_AGENT_NAME` | the **exact** agent name in the Foundry project — `KnowledgeBase`. Goes straight into the request URL, so a wrong value 400s. |
| `FOUNDRY_API_VERSION` | `v1` |

The request URL is `{endpoint}/agents/{FOUNDRY_AGENT_NAME}/endpoint/protocols/openai/responses?api-version={version}`.

---

## Troubleshooting

- **`az acr build` fails: `when specifying push, at least one credential is required`** — our
  accounts lack the push credential the build task needs. Don't use `az acr build`; build locally
  and push with admin creds (Deploy steps 1–2).
- **`docker push` → `insufficient_scope: authorization failed`** — no `AcrPush` role and admin user
  disabled. Enable admin (Deploy step 1) or get `AcrPush` granted.
- **Ingest `NotImplementedError ... AZURE_STORAGE_SAS_TOKEN`** — remove
  `AZURE_STORAGE_CONNECTION_STRING` (see Ingestion).
- **Blob 403 / SAS expired** — refresh the `nlg-rag-demo-storage-sas` secret with a new container
  SAS and roll a revision.
- **Foundry 400 with `/agents/FOUNDRY_AGENT_NAME/` in the URL** — `FOUNDRY_AGENT_NAME` holds the
  placeholder; set it to `KnowledgeBase`.
- **Portal env edits vs a CLI image deploy** — both create revisions; make sure the newest active
  revision runs the image tag *and* has the env you expect (verify with the revision-list query in
  Deploy step 4).

---

## One-shot: deploy current code

```bash
cd /c/Users/Alvee/Desktop/nlg-rag
TAG=$(date +%Y%m%d-%H%M%S)
PW=$(az acr credential show -n nlgcontainers --query "passwords[0].value" -o tsv)
echo "$PW" | docker login nlgcontainers-bfayhcemg5e0fnhv.azurecr.io -u nlgcontainers --password-stdin
docker build -t nlgcontainers-bfayhcemg5e0fnhv.azurecr.io/nlg-rag-demo:$TAG .
docker push nlgcontainers-bfayhcemg5e0fnhv.azurecr.io/nlg-rag-demo:$TAG
az containerapp update -n nlg-rag-demo-ca -g nlg_sandbox \
  --image nlgcontainers-bfayhcemg5e0fnhv.azurecr.io/nlg-rag-demo:$TAG
# then verify the newest revision is healthy
echo "Deployed tag: $TAG"
```
