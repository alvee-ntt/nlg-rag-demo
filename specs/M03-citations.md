# M03 — Citations

Build-ready implementation spec for the FlexLife POC (`nlg-rag`).

## 1. Summary

Answers must name the FlexLife corpus material they rest on, so a user (or an agent)
can validate them against the approved knowledge base. There are **two citation-bearing
answer surfaces** in this repo, and M03 now governs **both**:

1. **End-user Ask Navigator chat (primary)** — the pull-up sheet and Ask page in
   `ui/learn.html` call **`/v1/foundry/chat`** (`navSend`, `learn.html:1053-1058`). The
   product owner has confirmed the end-user chat is served by the **hosted Azure AI
   Foundry agent** ("KnowledgeBase"), **not** the local pgvector `/v1/chat` pipeline.
   Retrieval, ranking and grounding all happen **inside the hosted agent**; the repo
   receives a deduped, agent-numbered citation list `[{n, title, url}]`
   (`foundry.chat()`, `foundry.py:169-191`).
2. **Coach console + fact-check (secondary)** — `ui/index.html` calls `/v1/answer`,
   `/v1/search`, `/v1/fact-check`, which run the local pgvector stack. Here
   `service.format_sources()` attaches a per-chunk source list with **real similarity
   scores** (`blob_name`, `citation`, `page`, `zone`, `similarity`, `preview`).

M03 formalizes trustworthy citation behavior across both tracks. Because the two paths
expose fundamentally different signals (Foundry gives no similarity scores; local does),
M03 is deliberately **two-track**:

- **Foundry track (end-user chat):** cap the citation list at `max_sources`; abstain
  (replace the answer with the NLG-support message and raise an `escalate` flag) when the
  agent returns **zero citations** / a non-answer. `min_similarity` **cannot** be applied
  here — no scores are exposed — so relevance/grounding tuning is pushed to the agent's
  Azure config (out-of-repo).
- **Local track (Coach/fact-check):** the full original M03 behavior — `MAX_SOURCES` +
  `MIN_SIMILARITY` config floors, dedup chunks → distinct documents, a stable
  `document_id` per citation (M10 needs it here), and abstention when no chunk clears the
  floor.

Scope is POC: a source list at the end of the response suffices; page/passage precision,
ranking calibration and formal bibliography styles are explicitly out.

## 2. User Story (verbatim)

**User Story: Provide Citations for Generated Answers** — As a FlexLife sales agent or
support user, I want answers to identify the source material used to support them, so
that I can validate the information against the approved FlexLife knowledge corpus.

Description: Provide source citations with generated answers. Citations establish trust —
answers are grounded, not fabricated. When multiple relevant sources support an answer,
present multiple citations where appropriate. The number of citations and the confidence
threshold may be configurable/refined during the POC. A list of supporting sources at the
end of the response is sufficient. If the app cannot find sufficient supporting info in
the approved corpus, it should NOT generate an unsupported substantive answer — instead
it should direct the user to NLG support.

Acceptance criteria (source): answers include one or more citations to Knowledge
Foundation material; citations identify the source documents used; multiple citations
when multiple sources materially support; citation selection limited by configurable
criteria (max sources, min relevance/confidence threshold); a source list at end of
response suffices; citation behavior works across all source formats in the corpus; if
insufficient support, no unsupported substantive answer — direct user to NLG support.

## 3. Current State

There are now **two distinct citation sources**, and M03 must be explicit about which
governs which surface. Verified against the code:

### 3.1 Foundry track — end-user Ask Navigator chat (primary)

- **Endpoint.** `POST /v1/foundry/chat` (`server.py:467-484`) → `foundry.chat()`
  (`foundry.py:169-191`). Returns
  `{answer:str, citations:[{n:int,title:str,url:str}], agent, model, response_id, status}`.
- **Citations are produced inside the hosted agent.** `_extract_answer()`
  (`foundry.py:116-166`) parses the OpenAI Responses object: it reads the final
  `message`'s `url_citation` annotations, dedupes them by URL (first mention wins), numbers
  them `[1][2]…` **inline in the answer text**, and returns the deduped footnote list. For
  each citation, `url` is the hosted AI Search KB source and `title` is the filename
  derived from the URL via `_blob_filename()` (`foundry.py:97-104`) when the agent's own
  title is empty or is just the URL.
- **No similarity scores.** Retrieval, ranking and grounding happen entirely inside the
  hosted agent (Azure AI Search KB exposed to it as MCP — see `foundry.py:1-15`). **No
  per-citation relevance score is exposed to this repo.** Therefore M03's `min_similarity`
  floor is **not applicable** to Foundry citations. `max_sources` **is** applicable — the
  citation list can be trimmed after the fact.
- **No abstention today.** `foundry.chat()` returns `answer or "(the agent returned no
  text)"` (`foundry.py:185`) and always returns whatever `citations` came back (possibly
  empty). There is **no machine-readable "insufficient support" / escalate flag**, and an
  empty-citation answer is passed straight through.
- **Request/response models (`server.py`).** `FoundryChatRequest` (`server.py:93-95`)
  `{message, history}` — note **no `limit` field** (unlike the local `ChatRequest`, which
  has `limit`, `server.py:87-90`). `FoundryCitation` (`server.py:98-101`) `{n, title, url}`.
  `FoundryChatResponse` (`server.py:104-110`) `{answer, citations, agent, model,
  response_id, status}`. These three models are where response-shape changes go.
- **UI.** `navSend` (`learn.html:1053-1058`) posts `message` (with a preamble) + `history`
  to `/v1/foundry/chat` and returns `{text: d.answer || "The sources don't cover that.",
  citations: d.citations || []}`. `mountChat` (`learn.html:1149`) renders each turn; the
  `Sources (n)` toggle counts `(m.sources||[]).length + (m.citations||[]).length`
  (`learn.html:1161`) and the list renders `m.citations` via `citeHtml`
  (`learn.html:1143-1146`) and any `m.sources` via `srcHtml` (`learn.html:1060-1069`).
  Inline `[n]` markers are made tappable in `mdInline` (`learn.html:1082`). So the sheet
  already supports both shapes; today it only receives `citations`.

### 3.2 Local track — Coach console + fact-check (secondary)

- `service.format_sources()` — `src/rag_layer/service.py:36`. Maps each retrieved chunk to
  `{blob_name, chunk_index, citation, page, zone, similarity, preview}` where
  `preview = row["content"][:500]`. No filtering, no dedup, **no `document_id`**.
- `db.citation(row)` — `src/rag_layer/db.py:298`. Human-readable locator: `blob_name`,
  then `p.<page>`, then `heading_path`, else `chunk-<index>`; joined with `" | "`.
- `db.search_chunks()` — `src/rag_layer/db.py:217`. Returns top-`limit` chunks by cosine
  distance with `similarity = 1 - (embedding <=> query)`. **No relevance floor**; selects
  `d.blob_name`, **not** `d.id` (no `document_id` returned).
- `service.answer()`, `service.chat()`, `service.search()`, `service.fact_check()` all call
  `format_sources(contexts)` and return `{..., "sources": [...]}`.
- **Grounding prompts are soft.** `embeddings.answer_with_context()`
  (`src/rag_layer/embeddings.py:119`): "answer using only the context; if not present, say
  you do not know." `embeddings.chat_with_context()` prompt (`embeddings.py:172`) says "If
  the sources do not cover it, say so … **give safe general guidance without inventing
  product details.**" — this *permits* an unsupported substantive answer, conflicting with
  the abstention requirement. Fallback default at `embeddings.py:196`:
  `"I couldn't find that in the sources."` No machine-readable insufficient-support flag.
- **Response models (`server.py`).** `Source` (`server.py:134-142`) — no `document_id`.
  `SearchResponse` (`:144`) → `sources: list[Source]`; `AnswerResponse` (`:148`) adds
  `answer`; `ChatResponse` (`:152`) adds `answer` + `follow_ups`; `FactCheckResponse`
  (`:157`). No abstention field anywhere.
- **Config.** `src/rag_layer/config.py` `Settings` has `rag_search_limit: int = 8`. **No
  `max_sources`, no `min_similarity`.**
- **UI.** Coach console (`ui/index.html`) renders the `format_sources` list via
  `sourcesHtml()` / `srcMini()` with a `similarity` percentage. This is the reference
  local rendering and stays on the local path.

### 3.3 Gap summary M03 must close

Foundry track: (a) apply `max_sources` trim to the citation list; (b) abstention on empty
citations → NLG-support message + `escalate` flag; (c) surface the flag in the response
model and render the abstention state in Ask Navigator. Local track: (d) config
thresholds `MAX_SOURCES`/`MIN_SIMILARITY`; (e) dedup chunks → documents; (f) abstention on
empty-after-floor; (g) `document_id` on each citation for M10; (h) harden grounding prompt.

### 3.4 Verified behavior (live test — 2026-09-30)

Run against the live `/v1/foundry/chat` endpoint on the running container (signed in as the
demo user). Evidence for what M03 already does vs. the gaps:

- **Citations already flow with answers.** Every in-domain answer returned a `citations`
  array of `{n, title, url}` naming real source documents (e.g. "FlexLife_Product Quick Ref
  Guide.pdf", "Underwriting Guide.pdf"). Confirms the baseline citation requirement on the
  Foundry track.
- **❌ No `max_sources` cap and imperfect dedup.** A case-specific underwriting question
  returned **5 citations** with no cap applied. Two entries were the SAME filename
  ("FlexLife_Product Quick Ref Guide.pdf") at two DIFFERENT blob paths (`.../From
  NLG/FlexLife Product Information/...` and `.../FlexLife Product Information/...`). So a
  `max_sources` trim is needed, and dedup by filename alone will be imperfect — dedup should
  consider the full path/URL.
- **❌ No abstention.** The case-specific question ("Will my 47-year-old diabetic smoker
  client be approved, and at what rate class?") was answered with extensive guidance and
  citations; the agent sensibly declined to state a definitive approval/class but **never
  abstained or referred the user to NLG support**. The "insufficient support → direct to
  NLG" behavior does not exist. Note the Foundry response exposes **no similarity scores**,
  so the practical abstention trigger on this track is "agent returned zero citations" (plus
  agent-side instruction tuning in Azure); a numeric `min_similarity` floor is only available
  on the local track.

## 4. Scope

**In scope — Foundry track (end-user chat)**

- Trim the returned Foundry `citations` list to at most `max_sources` (post-processing;
  order preserved — the agent already ranked/numbered them).
- Abstention: when the agent returns **zero citations** (and/or the empty/non-answer
  marker), replace `answer` with the fixed NLG-support message and set an
  `escalate: bool` + `escalate_reason: str` flag for M09 to consume.
- Add `escalate` / `escalate_reason` to `FoundryChatResponse`; wire `navSend` + `mountChat`
  to render the abstention state (support message, no sources toggle, escalation surfaced).
- Explicitly document that `min_similarity` is **not** applicable on this track and that
  grounding/threshold tuning lives in the agent's Azure config.

**In scope — Local track (Coach console + fact-check)**

- Add `max_sources` and `min_similarity` to `Settings` (env-driven, POC defaults).
- `select_citations` helper: filter chunks by `min_similarity`, aggregate chunks to
  distinct documents, cap at `max_sources`, shape final citation objects.
- Add a stable `document_id` to each local citation (for M10) — `search_chunks` must also
  return `d.id`.
- Abstention: if no chunk clears `min_similarity`, do not generate; return
  `insufficient_support = true` + the fixed NLG-support message + empty `sources`.
- Response model additions: `document_id` on `Source`; `insufficient_support` on
  answer/chat responses. Harden the `embeddings` grounding prompt/fallback.
- Apply to `/v1/answer` and `/v1/chat`; keep `/v1/search` and `/v1/fact-check` source-list
  shape consistent.

**Out of scope** — see §9.

## 5. Functional Requirements

### Foundry track (end-user Ask Navigator chat)

- **FR-F1.** Every substantive answer from `/v1/foundry/chat` returns the agent's deduped
  citation list `[{n, title, url}]`, and Ask Navigator renders it as a source list under
  the answer (plus tappable inline `[n]` markers).
- **FR-F2.** The citation list is capped at `max_sources` distinct sources. Because the
  agent already dedupes by URL and numbers them, the trim is a simple length cap that keeps
  the agent's ordering and re-uses the existing `n` values.
- **FR-F3.** `min_similarity` is **not** applied on this track — no per-citation score is
  exposed to the repo. Relevance/grounding thresholds are configured on the hosted agent in
  Azure (out-of-repo) and are documented as such.
- **FR-F4.** When the agent returns **zero citations** (or the empty/non-answer marker),
  the app MUST NOT surface the raw agent text as a substantive answer. It returns the fixed
  NLG-support message as `answer`, an empty `citations` list, `escalate = true`, and a
  short `escalate_reason` (e.g. `"no_citations"`).
- **FR-F5.** `escalate` is a discrete boolean field on `FoundryChatResponse` (not inferable
  only from message text) so M09 can auto-surface the handoff.

### Local track (Coach console + fact-check)

- **FR-L1.** Every substantive answer from `/v1/answer` and `/v1/chat` returns a non-empty
  list of citations to corpus material.
- **FR-L2.** Each local citation identifies its source document (human-readable `citation`
  string + `blob_name` + stable `document_id`).
- **FR-L3.** When multiple distinct documents clear the floor, multiple citations are
  returned (one per document), ordered by best supporting similarity.
- **FR-L4.** Chunks from the same document are aggregated to one citation, retaining the
  best-matching chunk's locator/preview and the document's max similarity.
- **FR-L5.** Local selection is bounded by configurable criteria: at most `max_sources`
  documents, only documents with a chunk at or above `min_similarity`.
- **FR-L6.** A source list at the end suffices (both tracks); no inline footnote-to-sentence
  binding is required (the Foundry track's `[n]` markers are a bonus, not a requirement).
- **FR-L7.** Local citation behavior is format-agnostic — derived only from `blob_name` +
  chunk metadata (`page`/`zone`/`heading_path`), so all corpus file formats cite uniformly.
- **FR-L8.** If no retrieved chunk clears `min_similarity`, the app MUST NOT emit a
  substantive answer: returns `insufficient_support = true`, empty `sources`, and the fixed
  NLG-support message.
- **FR-L9.** `insufficient_support` is a discrete boolean field for downstream (M09).
- **FR-L10.** The grounding prompts must not instruct the model to produce unsupported
  "general guidance"; thin context defers to the abstention path.
- **FR-L11.** `MAX_SOURCES` / `MIN_SIMILARITY` are env-adjustable without code changes.

### Cross-cutting

- **FR-X1.** Both abstention seams use the **same** fixed NLG-support message constant, so
  wording is defined once and M09 has a single seam.

## 6. Technical Design

### 6.1 Foundry track (primary)

**Where the abstention/trim logic lives — decision.** Put it in a small post-processing
step inside **`foundry.chat()`** (`foundry.py:169-191`), right after
`answer, citations = _extract_answer(data)`. Rationale:

- `foundry.chat()` already owns the response-shaping (it builds the returned dict there),
  so this keeps all Foundry response semantics in one module and out of the FastAPI
  handler, which stays a thin proxy (`server.py:467-484`, just error mapping).
- It needs `settings` for `max_sources` — which `foundry.chat()` already receives.
- It avoids a new service wrapper for a single hosted call (over-engineering for POC).

The alternative (logic in `foundry_chat_endpoint`) was rejected: it would split
response-shape ownership across two files and duplicate the constant. A thin service
wrapper was rejected as unnecessary indirection for one call.

Revised tail of `foundry.chat()`:

```python
answer, citations = _extract_answer(data)

# Cap the agent's (already deduped, already numbered) citation list. Order preserved.
if settings.max_sources and len(citations) > settings.max_sources:
    citations = citations[: settings.max_sources]

# Abstention: the practical low-confidence signal on this track is the agent returning
# no grounded sources. min_similarity cannot be used here (no scores are exposed).
non_answer = (not answer) or answer.strip() in {"", "(the agent returned no text)"}
if not citations or non_answer:
    return {
        "answer": NLG_SUPPORT_MESSAGE,
        "citations": [],
        "escalate": True,
        "escalate_reason": "no_citations" if not citations else "empty_answer",
        "agent": client.agent, "model": data.get("model"),
        "response_id": data.get("id"), "status": data.get("status"),
    }

return {
    "answer": answer,
    "citations": citations,
    "escalate": False,
    "escalate_reason": None,
    "agent": client.agent, "model": data.get("model"),
    "response_id": data.get("id"), "status": data.get("status"),
}
```

`NLG_SUPPORT_MESSAGE` is a single module-level constant shared with the local track (define
it once, e.g. in `service.py`, and import it into `foundry.py`), so M09 has one seam.

**Response model (`server.py:104-110`).** Add to `FoundryChatResponse`:

```python
escalate: bool = False
escalate_reason: str | None = None
```

`FoundryChatRequest` (`server.py:93`) is unchanged — no `limit`; the cap is server-side
config, not a per-request knob.

**Foundry-track JSON — grounded answer:**

```json
{
  "answer": "FlexLife credits interest with both a cap and a floor... [1][2]",
  "citations": [
    {"n": 1, "title": "FlexLife Brochure.pdf", "url": "https://…/FlexLife%20Brochure.pdf"},
    {"n": 2, "title": "Living Benefits Guide.pdf", "url": "https://…/Living%20Benefits%20Guide.pdf"}
  ],
  "escalate": false,
  "escalate_reason": null,
  "agent": "KnowledgeBase",
  "model": "gpt-4o",
  "response_id": "resp_…",
  "status": "completed"
}
```

**Foundry-track JSON — abstention (no citations):**

```json
{
  "answer": "I couldn't find enough approved FlexLife material to answer that confidently. Please reach out to NLG support so they can help.",
  "citations": [],
  "escalate": true,
  "escalate_reason": "no_citations",
  "agent": "KnowledgeBase",
  "model": "gpt-4o",
  "response_id": "resp_…",
  "status": "completed"
}
```

**UI (`learn.html`).**

- `navSend` (`learn.html:1053-1058`) returns
  `{text: d.answer, citations: d.citations || [], escalate: !!d.escalate}` (drop the
  client-side `"The sources don't cover that."` fallback — abstention now comes from the
  server as a real message).
- `mountChat` (`learn.html:1149`): when a turn has `escalate === true`, render the answer
  bubble as the (server-supplied) NLG-support message and **suppress the sources toggle**
  (there are no citations anyway, so the existing `n` count already hides it — but also
  add a subtle "Contact NLG support" affordance so the handoff reads clearly; M09 will
  make it actionable). No shape change to `citeHtml` (`learn.html:1143`).

### 6.2 Config knobs (`config.py`) — used by BOTH tracks for `max_sources`

Add to `Settings` (after `rag_search_limit`, same env-driven pattern):

```python
max_sources: int = 4          # MAX_SOURCES — cap on cited sources (both tracks)
min_similarity: float = 0.30  # MIN_SIMILARITY — LOCAL-track relevance floor only
```

Wire in `load_settings()`:

```python
max_sources=_int("MAX_SOURCES", 4),
min_similarity=float(os.getenv("MIN_SIMILARITY", "0.30")),
```

(Add a small `_float` helper mirroring `_int`, or inline as above.) `max_sources` applies
to both tracks (trim on Foundry, aggregate-cap on local). `min_similarity` applies to the
**local track only** — the Foundry track has no scores to compare it against.
`rag_search_limit` (local retrieval breadth) stays independent of `max_sources` (cited
breadth): retrieve wide, then filter/aggregate down.

### 6.3 Local selection, dedup and aggregation (`service.py`)

`search_chunks` must also return the document id — change its SELECT to include
`d.id AS document_id` (`db.py:217`). Then add the selection helper and route
`format_sources` / `answer` / `chat` through it:

```python
def select_citations(contexts, *, settings):
    """Filter by min_similarity, aggregate chunks -> distinct documents, cap at max_sources."""
    kept = [c for c in contexts if float(c["similarity"]) >= settings.min_similarity]
    by_doc: dict[int, dict] = {}
    for row in kept:                      # contexts arrive best-first from search_chunks
        doc_id = row["document_id"]
        best = by_doc.get(doc_id)
        if best is None or float(row["similarity"]) > float(best["similarity"]):
            by_doc[doc_id] = row          # keep the best chunk per document
        # (increment a chunk_count on the retained entry)
    ranked = sorted(by_doc.values(), key=lambda r: float(r["similarity"]), reverse=True)
    return ranked[: settings.max_sources]
```

`format_sources()` shapes the objects from that selected list (adding `document_id`,
optional `chunk_count`). Ordering is deterministic: documents best-similarity first.

### 6.4 Local abstention (insufficient support)

Two layers, defense in depth:

1. **Service decision (authoritative).** In `service.answer()` and `service.chat()`, after
   retrieval compute `selected = select_citations(contexts, settings=settings)`. If empty:
   - Do **not** call `answer_with_context` / `chat_with_context`.
   - Return `{"answer": NLG_SUPPORT_MESSAGE, "sources": [], "insufficient_support": True}`
     (chat also returns `"follow_ups": []`).
   - Otherwise generate normally and return
     `{"answer": ..., "sources": format_sources(selected), "insufficient_support": False}`.
2. **Prompt reinforcement (`embeddings.py`).** `chat_with_context` (`embeddings.py:172`):
   **remove** the "give safe general guidance without inventing product details" clause
   (FR-L10); replace with an instruction to say the approved sources don't cover it and to
   suggest NLG support. `answer_with_context` (`embeddings.py:120`): harden "defer to
   support rather than guess." Fallback (`embeddings.py:196`) stays as last resort.

`NLG_SUPPORT_MESSAGE` is the single shared constant (also used by the Foundry track):

```python
NLG_SUPPORT_MESSAGE = (
    "I couldn't find enough approved FlexLife material to answer that confidently. "
    "Please reach out to NLG support so they can help."
)
```

### 6.5 Local response model changes (`server.py`)

- `Source` (`:134`): add `document_id: int` and optional `chunk_count: int | None = None`.
- `AnswerResponse` (`:148`) / `ChatResponse` (`:152`): add
  `insufficient_support: bool = False`.
- `SearchResponse` / `FactCheckResponse`: `sources` gains `document_id` via `Source`; no
  abstention flag (search/fact-check are not "answers").

**Local `/v1/chat` success JSON:**

```json
{
  "answer": "FlexLife credits interest with both a cap and a floor...",
  "follow_ups": ["What is the floor rate?", "Can I promise a minimum?"],
  "insufficient_support": false,
  "sources": [
    {"document_id": 42, "blob_name": "...FlexLife Brochure.pdf", "chunk_index": 7,
     "citation": "...FlexLife Brochure.pdf | p.10 | Caps and Floors", "page": 10,
     "zone": "body", "similarity": 0.83, "preview": "...", "chunk_count": 3}
  ]
}
```

**Local abstention JSON:**

```json
{
  "answer": "I couldn't find enough approved FlexLife material to answer that confidently. Please reach out to NLG support so they can help.",
  "follow_ups": [],
  "insufficient_support": true,
  "sources": []
}
```

### 6.6 Frontend rendering summary

- **Ask Navigator (`ui/learn.html`) — Foundry track.** As §6.1: render `citations` via the
  existing `citeHtml`, render the abstention state on `escalate`. No `/v1/chat` rewire — the
  end-user chat stays on Foundry per the product decision.
- **Coach console (`ui/index.html`) — local track.** `sourcesHtml()` / `srcMini()` already
  render the `format_sources` shape and ignore extra fields (`document_id`, `chunk_count`
  need no renderer change). Add handling for `insufficient_support: true` on `/v1/answer`:
  show the NLG-support message and suppress the sources block.

## 7. Acceptance Criteria

### Foundry track (end-user Ask Navigator chat)

- **AC-F1** (citations present). *Given* a corpus-answerable question, *when* asked via
  `/v1/foundry/chat`, *then* the response has `escalate = false` and a non-empty
  `citations` list, and Ask Navigator shows a `Sources (n)` toggle plus tappable `[n]`.
- **AC-F2** (max_sources trim). *Given* the agent returns more than `MAX_SOURCES`
  citations, *when* the response is shaped, *then* `citations` is capped at `MAX_SOURCES`,
  preserving the agent's order and numbering.
- **AC-F3** (no similarity floor). *Given* the Foundry track, *when* citations are shaped,
  *then* no `min_similarity` filtering is attempted (documented limitation; scores absent).
- **AC-F4** (empty citations → NLG support; the key case). *Given* a question for which the
  agent returns **zero citations** (or an empty/non-answer), *when* asked via
  `/v1/foundry/chat`, *then* `answer` is the fixed NLG-support message, `citations = []`,
  `escalate = true`, `escalate_reason` set — and **no** raw agent text is shown as a
  substantive answer.
- **AC-F5** (escalation is machine-readable for M09). *Given* an abstention response, *when*
  a downstream consumer inspects it, *then* `escalate` is a discrete boolean it can branch
  on without parsing message text.

### Local track (Coach console + fact-check)

- **AC-L1** (≥1 citation). *Given* a corpus-answerable question via `/v1/answer` or
  `/v1/chat`, *then* `insufficient_support = false` and `sources` has ≥1 citation.
- **AC-L2** (identify document). *Given* any local citation, *then* it carries a
  human-readable `citation` string, `blob_name`, and `document_id`.
- **AC-L3** (multiple documents). *Given* supporting chunks from ≥2 distinct documents each
  clearing `min_similarity`, *then* `sources` lists multiple documents (up to
  `max_sources`), best-similarity first.
- **AC-L4** (aggregation). *Given* several chunks from the *same* document, *then* it
  appears exactly once, using its best chunk's locator/preview and max similarity.
- **AC-L5** (configurable). *Given* `MAX_SOURCES`/`MIN_SIMILARITY` env values, *then* cited
  count never exceeds `MAX_SOURCES` and no cited document is below `MIN_SIMILARITY`.
- **AC-L6** (all formats). *Given* corpus documents of different original formats, *when*
  any is the top source, *then* it is cited via the same object shape and renderer.
- **AC-L7** (insufficient support → NLG support). *Given* no retrieved chunk at/above
  `min_similarity`, *when* asked via `/v1/answer` or `/v1/chat`, *then*
  `insufficient_support = true`, `sources = []`, `answer` is the fixed NLG-support message,
  and no substantive answer is generated.
- **AC-L8** (no unsupported "general guidance"). *Given* thin context that still clears the
  floor for one weak source, *when* `/v1/chat` answers, *then* the reply stays grounded in
  that source and volunteers no invented product facts.

### Cross-cutting

- **AC-X1** (single message constant). *Given* an abstention on either track, *then* the
  user-facing message is the same `NLG_SUPPORT_MESSAGE` constant.

## 8. Test Plan

**Unit — Foundry track (pytest, mock `FoundryAgentClient.respond` / `_extract_answer`).**

- `foundry.chat` trims to `max_sources` when the agent returns more, preserving order/`n`.
- `foundry.chat` with empty citations → returns `NLG_SUPPORT_MESSAGE`, `citations == []`,
  `escalate is True`, `escalate_reason == "no_citations"`.
- `foundry.chat` with a non-answer marker (`"(the agent returned no text)"`) →
  `escalate is True`, `escalate_reason == "empty_answer"`.
- `foundry.chat` with citations + real answer → `escalate is False`, citations passed
  through, `escalate_reason is None`.

**Unit — local track (feed synthetic `contexts` rows, no network).**

- `select_citations`: filters below `min_similarity`; caps at `max_sources`; aggregates
  duplicate `document_id` to one entry with max similarity + correct `chunk_count`;
  preserves best-first ordering; returns `[]` when all below floor.
- `format_sources`: emits `document_id`, `chunk_count`, `preview` truncated to 500 chars;
  handles missing `page`/`zone`/`heading_path` (falls back to `chunk-<index>`).
- Abstention branch in `service.answer` / `service.chat`: empty selection → NLG-support
  constant, `sources == []`, `insufficient_support is True`, LLM function **not** called
  (assert via mock).

**Integration (running app / seeded DB + configured Foundry agent).**

- Foundry: corpus-covered question → `escalate == false`, ≥1 citation with `url` + `title`.
- Foundry: off-corpus question the agent can't ground → `escalate == true`,
  `citations == []`, NLG-support message.
- Foundry: set `MAX_SOURCES=1` → at most one citation.
- Local: corpus-covered question → `insufficient_support == false`, ≥1 source, each
  `similarity >= min_similarity`, `len(sources) <= max_sources`.
- Local: off-corpus question → `insufficient_support == true`, `sources == []`, redirect.
- Local: set `MIN_SIMILARITY` very high, rebuild, ask known-good question → forced
  abstention. Set `MAX_SOURCES=1` → at most one citation.

**UI (jsdom / manual per dev-loop memory — Chrome extension is on another machine).**

- Ask Navigator: answered question shows `Sources (n)` with tappable `[n]`; `escalate`
  response shows the NLG-support message, no sources toggle, and a support affordance.
- Coach console `/v1/answer`: source list renders with similarity %, preview, citation;
  abstention shows the redirect message and suppresses the sources block.

Reminder: code changes require rebuilding the Docker image (per `nlg-rag dev loop`).

## 9. Out of Scope

- Opening/navigating to a source from a citation (that is **M10**). See §10 for how M10
  consumes each track.
- Building the "draft support email to NLG" handoff flow (that is **M09**); M03 only emits
  the signals (`escalate` on Foundry, `insufficient_support` on local) + the fixed message.
- Changing the hosted Foundry agent's internal retrieval/ranking/grounding, or applying a
  similarity floor to Foundry citations (no scores exposed; tune in the agent's Azure
  config, out-of-repo).
- Rewiring the end-user chat to `/v1/chat` — the product owner confirmed it stays on
  Foundry.
- Guaranteed page/section/passage-level precision of citations.
- Production-grade confidence calibration or citation-ranking (local similarity is the POC
  proxy; Foundry ranking is the agent's).
- Formal citation styles / bibliographies / footnote-to-sentence binding.

## 10. Dependencies & Open Questions

**Dependencies**

- **M09 (Handoff)** consumes the abstention signals: `escalate` (+ `escalate_reason`) on
  the Foundry response, and `insufficient_support` on local answer/chat responses. M03 owns
  the *decision to abstain* and the single `NLG_SUPPORT_MESSAGE` constant; M09 owns what
  happens next (draft email). Keep the flags and the constant stable as the contract.
- **M10 (Links)** — two routes:
  - **Foundry citations:** M10 uses the returned `url` directly (it already points at the
    hosted AI Search KB source) — the `title` is the label. No proxy needed.
  - **Local citations:** M10 uses the `document_id` (+ `blob_name`) proxy route to build an
    open-document link. M03 must include `document_id` on local citations; it must not build
    link/open behavior.
- Local `search_chunks` must return `d.id` (small SELECT change) — no schema migration
  (`rag_documents.id` already exists).
- Foundry track requires the hosted agent configured (`FOUNDRY_PROJECT_ENDPOINT`,
  `FOUNDRY_API_KEY`); `/v1/foundry/chat` already 503s when unconfigured (`server.py:472`).

**Open questions**

1. Default `min_similarity` value (local): 0.30 is a placeholder for cosine similarity
   (`1 - distance`); needs a quick empirical sweep against the seeded corpus.
2. Foundry abstention trigger: is "zero citations" sufficient, or should we also treat a
   very short answer / specific agent status as low-confidence? (Start with zero-citations +
   the empty-text marker; revisit if the agent emits ungrounded prose with no citations.)
3. Foundry relevance tuning is out-of-repo (agent's Azure AI Search config). Confirm who
   owns that config and whether a grounding-strictness setting exists there.
4. Should `/v1/fact-check` also abstain, or is per-statement `NOT ADDRESSED` sufficient?
   (Leaning: leave fact-check as-is; abstention is an *answer* concern.)
5. Exact NLG-support wording / whether to include a contact channel now or defer to M09.

## 11. Rough Effort Estimate

POC-scoped, one developer:

- Config knobs + `_float` helper: ~0.25 day.
- **Foundry track:** `max_sources` trim + abstention/escalate post-processing in
  `foundry.chat`, `FoundryChatResponse` fields, `navSend`/`mountChat` abstention rendering +
  unit tests: ~1 day.
- **Local track:** `search_chunks` `document_id` + `select_citations` + `format_sources`
  rework + unit tests: ~1 day.
- Local abstention in `service.answer`/`service.chat` + `embeddings` prompt/fallback +
  tests, response model updates, Coach console abstention rendering: ~1 day.
- Docker rebuild, integration/UI verification, `min_similarity` tuning pass: ~0.5 day.

**Total ≈ 3.75–4 developer-days.**
