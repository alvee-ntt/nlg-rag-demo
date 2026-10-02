# Alternative to the Foundry Agent — Query the Knowledge Base Directly

**Status:** proposal (not implemented)
**Date:** 2026-10-01
**Context:** the hosted Foundry agent is unusable (OBO auth), and we need the end-user
chat to run on the *official* FlexLife knowledge base without depending on it.

---

## 1. Summary / recommendation

The Foundry agent's "knowledge base" is nothing more than an **Azure AI Search index we can
query directly**. Instead of going through the Foundry Agent Service (which is blocked by an
OBO-auth setting we can't change), the app should **query that index itself** and generate
the answer with the **NLG-High** model we already use.

This gives us the Foundry agent's *actual* knowledge base **and** its *actual* instructions
**and** the same model family — with **no Foundry, no OBO, no agent, no RBAC** — using
credentials that already work. It is strictly better than today's two states:

- better than the **pgvector fallback** (that grounds on our own locally-ingested corpus;
  this grounds on the *official* Foundry KB), and
- better than the **Foundry agent** (that path is dead until an admin changes the connection).

Recommended over (a) fixing the Foundry OBO connection (needs admin access we lack) and
(b) creating a new Foundry agent (inherits the same OBO connection problem; more shared-infra
mutation).

---

## 2. Why the Foundry agent is down (one paragraph)

The hosted agent `KnowledgeBase` (resource `nlg-foundry-res`, project `nlg-main-proj`) has one
tool: an **MCP connection** (`kb-ks-nlg-flexlife-2torx`) to an Azure AI Search knowledge base.
That connection's auth was switched to **OBO ("on behalf of" the signed-in user)**. Our app
authenticates with an **API key**, which carries no user identity, so every call is rejected:
`400 "Tools configured with OBO auth are not supported with API key authentication."` This is
an Azure-side change to the connection; it is not in our code and cannot be fixed from code.
Fixing it requires Foundry-project admin rights (set the connection to the agent's managed
identity or the Search key) that the current `Contributor` identity does not have.

---

## 3. Key discovery — the KB is a directly-queryable index (verified)

Read from the live agent definition and the Search service:

- **Search service:** `nlg-main-proj-srch-px13` (resource group `nlg_sandbox`).
- **Index:** `ks-azureblob-639-index` — the single index backing the KB.
- **Fields:** `uid`, `snippet` (chunk text), `snippet_vector` (embedding), `blob_url` (source
  document URL), `snippet_parent_id`, `image_snippet_parent_id`.
- **Access:** the Search admin key is readable with the current `Contributor` role
  (`az search admin-key show`). A **query key** is sufficient for retrieval at runtime.
- **Proven:** a direct REST query (`POST /indexes/ks-azureblob-639-index/docs/search`) for
  *"interest rate floor minimum guaranteed"* returned relevant FlexLife snippets (crediting
  options) with their `blob_url` sources (BM25 score ~17.6). Same corpus, same container
  (`nlgragdemostorage/nlg-agent-navigator/...`) the Foundry agent cites.

So the knowledge base is reachable with a key — no Foundry, no OBO.

---

## 4. Proposed architecture

A new **"AI Search KB" chat engine** inside our app that mirrors what the Foundry agent did:

```
user question
   │
   ▼
[ M02 domain gate ]  (existing: decline clearly non-FlexLife)
   │ in-domain
   ▼
[ AI Search retrieval ]  → POST ks-azureblob-639-index/docs/search
   │                        (text, or hybrid with snippet_vector)
   │ snippets + blob_url
   ▼
[ NLG-High model ]  → /openai/v1/responses  (existing AZURE_OPENAI creds)
   │  system prompt = the Foundry agent's "adaptive FlexLife instructor" instructions
   │  grounded on the retrieved snippets
   ▼
answer + citations (+ M03 abstention, M09 escalate, M10 links)
```

Three ingredients, all already available:

1. **Retrieval** — call the Search index directly (REST). We already do the equivalent against
   pgvector; this is the same shape against a different store.
2. **Generation** — the **NLG-High** model via `/openai/v1/responses` — the exact resource
   `embeddings._generate()` already uses.
3. **Instructions** — the Foundry agent's own system prompt (the long "adaptive FlexLife
   instructor" text) was captured from the live agent config and can be ported verbatim, so the
   behavior/voice matches what the agent produced.

---

## 5. What changes in the codebase (design level)

- **New module** `search_kb.py` (sibling to `blob_store.py`): a thin client that POSTs to the
  Search index and returns snippets `[{snippet, blob_url, score}]`. Mirror the retry/backoff
  style of `blob_store.py` / `foundry.py`.
- **New service function** `chat_search_kb(...)` (in `service.py`): retrieve from the index →
  build the instructor prompt with the snippets → call the model → shape `{answer, sources,
  insufficient_support/escalate}` exactly like the existing chat paths.
- **Prompt** `answer_with_kb(...)` (in `embeddings.py`): the Foundry instructor instructions +
  retrieved context + the same JSON/grounded contract M03 added (so abstention/escalate work).
- **Wire the endpoint**: point `/v1/foundry/chat` (or a new `/v1/kb/chat`) at `chat_search_kb`
  instead of the Foundry proxy. Keep `chat_foundry` + the pgvector fallback in place as a
  deeper safety net.
- **Config** (`config.py` + `.env`): `AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_INDEX`,
  `AZURE_SEARCH_KEY` (a query key).

No new external dependency — it's HTTP calls with the vendored `requests`.

---

## 6. Integration with the milestones already built

- **M02 (domain gate):** unchanged — runs before retrieval, same as today.
- **M03 (citations + abstention):** snippets map to the existing source shape; abstain when the
  index returns nothing relevant or the model reports `grounded: false`. `max_sources` applies.
- **M09 (handoff):** the `escalate` flag flows the same way; the draft is built from the chat
  log regardless of engine.
- **M10 (open links):** citations carry `blob_url`. Because our open route is keyed by our
  `document_id`, we either (a) map `blob_url` → local `document_id` by blob path/basename and
  reuse `/v1/documents/{id}/open`, or (b) add a variant of the proxy that accepts a blob path.
  (The blobs live in the same `nlgragdemostorage` container our refreshed SAS already serves.)

---

## 7. Options compared

| Option | Uses official FlexLife KB | Needs admin / RBAC | Depends on Foundry | In our control | Effort |
|---|---|---|---|---|---|
| **A. Direct AI Search KB (this proposal)** | ✅ yes | ❌ no (query key only) | ❌ no | ✅ fully | ~1–1.5 days |
| B. Fix the Foundry OBO connection | ✅ yes | ✅ yes (we lack it) | ✅ yes | ❌ no | blocked |
| C. Create a new Foundry agent | ✅ yes | ⚠️ data-plane write + new non-OBO connection | ✅ yes | ⚠️ partly | medium, fragile |
| D. pgvector fallback only (today) | ❌ no (our local corpus) | ❌ no | ❌ no | ✅ fully | done |

---

## 8. Retrieval details

- **Text (BM25):** works today (proven). Simplest; good enough to ship.
- **Hybrid (text + vector):** best quality — add a `vectorQueries` clause against
  `snippet_vector`. Requires embedding the user query with the **same embedding model and
  dimensions** the index was built with. That model is **unconfirmed** (open question); if it
  matches our `text-embedding-3-large`, hybrid is a drop-in. Start with text; add hybrid once
  the embedding is confirmed.
- **De-dup:** the index returned the same snippet across multiple `blob_url`s (the corpus has
  the same content in several folders) — apply the same document-level de-dup M03 uses.

---

## 9. Configuration additions

```
# Azure AI Search knowledge base (the Foundry agent's KB, queried directly)
AZURE_SEARCH_ENDPOINT=https://nlg-main-proj-srch-px13.search.windows.net
AZURE_SEARCH_INDEX=ks-azureblob-639-index
AZURE_SEARCH_KEY=<query key>   # query key, not admin key, for runtime retrieval
```

(Mint a dedicated **query key** rather than using the admin key at runtime.)

---

## 10. Risks & open questions

1. **Embedding model of the index** — needed for hybrid/vector search; unconfirmed. Text search
   works without it.
2. **The index is maintained by the Foundry KB ingestion** — if that pipeline stops or the index
   is deleted, this engine loses its source. (Same underlying data risk the Foundry agent had.)
3. **Instructions drift** — we'd copy the agent's system prompt once; if someone edits the agent
   in Azure later, ours won't auto-update. Acceptable for a POC; note it.
4. **M10 link mapping** — `blob_url` → openable document needs the small mapping described in §6.
5. **Query-key provisioning** — needs a query key minted on the Search service (the user has
   access to do this).

---

## 11. Effort estimate

~1–1.5 developer-days: `search_kb.py` client + `chat_search_kb` service fn + instructor prompt
+ endpoint wiring + config + tests + live verification against the index. (Hybrid search and the
M10 `blob_url` mapping are small add-ons on top.)

---

## 12. Decision needed

Proceed with **Option A** (build the direct AI Search KB engine on `tasks_1`), or hold for the
Foundry OBO fix (Option B, admin-gated)? Option A unblocks a real, KB-grounded chat now and
keeps the Foundry path as a no-op fallback that revives automatically if the OBO issue is ever
fixed.
