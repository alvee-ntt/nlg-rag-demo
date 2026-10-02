# M10: Provide Appropriate Links (Open Source Documents from Citations)

## 1. Summary

Every answer in the FlexLife POC comes back with citations that name a source, but the
value of a citation depends on the user being able to **open** it. M10 makes citations
openable. Following the product owner's decision, this spec is **two-track**, because the
two chat surfaces produce two different citation shapes:

- **Track A — Foundry / end-user chat (PRIMARY).** The end-user **Ask Navigator** chat
  (`ui/learn.html`) is powered by the hosted **Azure AI Foundry** agent via
  `POST /v1/foundry/chat` — *not* the local `/v1/chat`. Foundry citations already carry a
  ready-made `url` (`{n, title, url}`), so linking is a matter of rendering that `url` as an
  anchor. This is largely **already implemented** in the UI (`citeHtml`, `fdCites`); M10's
  real Track-A work is to **confirm those URLs are actually openable in the user's browser**
  and to spec a fallback for when they are not.

- **Track B — Local / Coach surfaces (SECONDARY).** The Coach console (`ui/index.html`) and
  the local RAG endpoints (`/v1/search`, `/v1/answer`, `/v1/chat`, `/v1/fact-check`,
  `/v1/transcript-check`, Learn mix) return `format_sources` citations that carry
  `blob_name` but **no openable URL**. For these, M10 adds a backend proxy route,
  `GET /v1/documents/{document_id}/open`, that streams the original blob from Azure Storage,
  plus the `document_id`/`url` plumbing to reach it.

Document-level linking only; no page/section deep-links (POC scope).

## 2. User Story (verbatim)

**User Story: Open Source Documents from Citations** — As a FlexLife sales agent or
support user, I want citations to include links to their source documents, so that I can
review the original material used to support an application response.

Description: Allow users to open source documents directly from citations. Each citation
provides a simple link to the referenced document; selecting it opens the document so the
user can review the original source. For the POC, document-level navigation is sufficient
(no page/section/passage navigation, no highlighting). Purpose: make citation verification
practical via direct access to the underlying source.

Acceptance criteria (source): citations can include a link to the referenced source
document; user can select a citation link and open the associated document; the link opens
the CORRECT document for that citation; links work for the source document types in the
corpus where a viewable document is available; multiple citations can link to multiple
documents; users need not know where the doc is stored or how the Knowledge Foundation is
organized; document-level linking suffices.

Out of scope: navigating to the exact page/section/paragraph; highlighting supporting
text; mapping individual statements to specific citations; production-grade access
controls / document management; editing/annotating source docs.

## 3. Current State

There are **two distinct citation kinds** in the codebase today, one per chat surface.

**Foundry citations (Track A — the end-user Ask Navigator chat):**

- The Ask Navigator chat sends every turn to the hosted Foundry agent:
  `navSend` (`ui/learn.html:1053-1058`) calls `POST /v1/foundry/chat` and returns
  `{ text, citations }`. It does **not** use the local `/v1/chat`.
- The backend proxy is `foundry_chat_endpoint` (`src/rag_layer/server.py:467-481`), typed by
  `FoundryChatResponse` / `FoundryCitation` (`server.py:98-110`). Each citation is
  `{ n: int, title: str, url: str }`.
- Those come from `foundry.chat()` (`src/rag_layer/foundry.py:169-191`) →
  `_extract_answer` (`foundry.py:116-166`). The `url` is taken directly from the hosted
  agent's `url_citation` **annotations** (`foundry.py:139-150`) and points to the agent's
  **Azure AI Search knowledge-base source document**. `title` is derived from the URL's
  filename via `_blob_filename` (`foundry.py:97-104`) when the agent's own title is empty or
  is just the URL again.
- **The Foundry `url` is produced by the hosted agent, not by this app.** This app has no
  control over whether it is a public URL, a SAS-signed blob URL, or an internal AI Search
  reference — see the open question in §10.
- **UI rendering is already link-capable (Track A is largely built):**
  - Ask Navigator: `citeHtml` (`ui/learn.html:1144-1145`) already renders each citation as
    `<a class="ncite" href="${c.url}" target="_blank" rel="noopener">…</a>`. The inline
    footnote-marker handler (`learn.html:1187-1192`) opens `c.url` in a new tab on click,
    falling back to expanding the sources list.
  - Coach "Foundry" tab: `fdCites` (`ui/index.html:787-799`) already renders each citation
    as `<a href="${c.url}" target="_blank" rel="noopener">${c.title}</a>`.
  - So the remaining Track-A work is verification of URL openability + a fallback, not new
    rendering (unless the fallback in §6.1 is required).

**Local `format_sources` citations (Track B — Coach console & local endpoints):**

- Built in `format_sources` (`src/rag_layer/service.py:36-48`) and carry
  `{ blob_name, chunk_index, citation, page, zone, similarity, preview }`. **No link/URL
  field, no document id.** `blob_name` is the only pointer to the source document.
- API shape is `class Source` (`server.py`, same fields). `format_sources` feeds
  `/v1/search`, `/v1/answer`, `/v1/chat`, `/v1/fact-check`, `/v1/transcript-check`, and the
  Learn mix `sources` list.
- The chunk rows feeding `format_sources` do **not** include the document id.
  `db.search_chunks` (`src/rag_layer/db.py:217-234`) selects
  `c.content, c.chunk_index, c.metadata, d.blob_name, similarity` — it joins
  `rag_documents d` but does not project `d.id`.
- Documents already map `document_id ↔ blob_name`: `rag_documents.id` (BIGSERIAL PK) with a
  unique `blob_name`. `db.get_document_chunks` already does
  `SELECT id, blob_name, ... FROM rag_documents WHERE id = %s`.
- **There is NO open/download route.** The only document routes are `GET /v1/documents`
  (`server.py:515`) and `GET /v1/documents/{document_id}/chunks` (`server.py:523`).
- `SasBlobStore.download_blob(blob_name) -> bytes` already exists
  (`blob_store.py:67`), constructed via `get_blob_store(settings)` (`blob_store.py:74`). The
  SAS token is a **container-level read token held only by the server** — the app has the
  SAS string, not the account key.
- Coach local sources render as **plain spans, not links**: `sourcesHtml`
  (`ui/index.html:504-520`) and `srcMini` (`ui/index.html:598-611`) emit
  `<span class="src-cite">`.

**Shared infrastructure:**

- **Auth gate**: `auth.py` gates every `/v1/**` path behind the demo sign-in cookie
  (`_OPEN_PATHS`/`_OPEN_PREFIXES`, `auth.py:31-32`; `gate()` returns 401 for an
  unauthenticated `/v1/` request, `auth.py:65`). Any new `/v1/documents/...` route is
  automatically protected. Note the Foundry `url` in Track A points **off-app** to the
  agent's KB, so it is *not* behind this gate.
- **CORS**: `allow_origins` is a fixed localhost list with `allow_credentials=False`; the UI
  is served same-origin from `/app`, so Track-B links are same-origin and unaffected.
- **Bytes-serving precedent**: `get_mix_audio_endpoint` (`server.py:638`) returns
  `Response(content=<bytes>, media_type=<mime>, headers=...)`. Track B reuses this pattern.
- **Corpus file types** (`extractors.py:14`, `SUPPORTED_EXTENSIONS`):
  `.csv, .docx, .html, .htm, .md, .pdf, .pptx, .txt, .xlsx`.

### Verified behavior (live test — 2026-09-30)

Run against the live `/v1/foundry/chat` endpoint and against Azure Blob directly:

- **UI already renders citations as links** (`citeHtml` in ui/learn.html emits `<a href="${url}" target="_blank" rel="noopener">`, and the inline chip handler does `window.open(url)`), so no new rendering is needed.
- **❌ ★ Openability RESOLVED — the links do NOT open.** Opening a returned citation URL directly in a browser (no auth) returned **HTTP 409 with `<Code>PublicAccessNotPermitted</Code>` ("Public access is not permitted on this storage account")**. The Foundry citation `url`s are bare `https://nlgragdemostorage.blob.core.windows.net/...` blob URLs with **no SAS token**, and the storage account has public access disabled. So Track A's "just link the returned url" approach is dead on arrival.
- **Conclusion:** the authenticated **proxy fallback is required, not optional** — resolve each citation's document via the corpus and serve the bytes through an authenticated `/v1/documents/{id}/open` route. Note a complication observed in testing: the same filename ("FlexLife_Product Quick Ref Guide.pdf") appears under multiple blob paths, so filename→document resolution must handle path ambiguity (prefer full-path/URL match over basename).

## 4. Scope

**In scope**

- **Track A (PRIMARY):** confirm that Foundry citation `url`s open in the end user's
  browser; render each citation's title as a link to its `url` (already present in
  `citeHtml`/`fdCites` — verify and keep, with `target="_blank" rel="noopener"`). If the
  URLs are **not** directly openable, implement the filename→local-blob fallback in §6.1.
- **Track B (SECONDARY):** a backend proxy route that opens/streams the original source
  document for a local citation, keyed by stable `document_id`; correct `Content-Type` per
  extension (inline for viewable types, download for the rest); extend `format_sources` /
  `Source` with `document_id` and a ready-to-use `url`; thread `document_id` from the DB
  through retrieval; make Coach local sources (`sourcesHtml`/`srcMini`) clickable; graceful
  handling of missing documents/blobs.

**Out of scope** — see §9.

## 5. Functional Requirements

- **FR1** — Every citation surfaced to a user SHALL include an openable link to its source
  document (Foundry citations already carry `url`; local citations gain `url`).
- **FR2** — Selecting a citation link SHALL open the **correct** document for that citation.
- **FR3 (Track A openability)** — The Ask Navigator (Foundry) chat's citation `url`s SHALL
  open the referenced document in the user's browser. If a returned `url` is **not** directly
  openable (403 / auth-gated / internal AI Search ref), the system SHALL fall back to
  opening the corresponding document from the local corpus (see §6.1), OR the limitation
  SHALL be documented as a known gap for that citation.
- **FR4 (Track B storage hiding)** — The local open route SHALL open the document without
  exposing where/how it is stored: no blob paths, SAS tokens, or container structure reach
  the client; the link references an opaque `document_id`.
- **FR5** — Viewable types (PDF, HTML/HTM, TXT, MD, CSV) SHALL open **inline**; non-inline
  office types (DOCX, PPTX, XLSX) SHALL be served as a **download** with the original
  filename (Track B route; Track A depends on the agent's URL).
- **FR6** — Multiple citations in one answer SHALL each link to their own document.
- **FR7** — For the Track-B route: an unknown `document_id` SHALL return `404`; a document
  whose blob cannot be fetched SHALL return `502` with a clear message. Neither leaks the SAS
  token or storage URL.
- **FR8** — The Track-B open route SHALL require the same demo sign-in as the rest of `/v1`
  (inherited from the existing auth gate).
- **FR9** — The account/container SAS token SHALL never be sent to the browser (Track B
  proxy, not SAS redirect).

## 6. Technical Design (two-track)

### 6.1 Track A — Foundry / end-user chat (PRIMARY)

**Simple case (expected default): the `url` is directly openable.** Each Foundry citation
is `{ n, title, url }` and both UIs already render the title as an anchor:

- `citeHtml` (`ui/learn.html:1144-1145`):
  `<a class="ncite" href="${esc(c.url || "#")}" target="_blank" rel="noopener" …>` — the
  footnote chip title links straight to the source URL, opening in a new tab. The inline
  marker handler (`learn.html:1187-1192`) also does `window.open(c.url, "_blank", "noopener")`.
- `fdCites` (`ui/index.html:787-799`) does the same for the Coach Foundry tab.

If the agent's URLs open cleanly (public or SAS'd blob URLs), **Track A needs no code
change** — it is already built. The only tasks are (a) verify openability (§8 live check),
(b) ensure `rel="noopener"` and `target="_blank"` are present (they are), and (c) keep
`esc()` on `href`/`title` (already the case) so a malformed agent URL can't inject markup.

**Fallback (only if the `url` is NOT directly openable).** The agent's `url` points at its
Azure AI Search KB source, which may be (b) a blob URL **without** a SAS token (the user's
browser gets 403) or (c) an internal AI Search reference the browser cannot resolve at all.
If the live check (§8) shows either, spec the following fallback rather than shipping dead
links:

- **Filename → local blob mapping.** `_blob_filename(url)` (`foundry.py:97-104`) already
  extracts the source's filename from the citation URL. The Foundry KB is populated from the
  **same corpus** as the local pgvector store, so that filename should match a `blob_name`
  basename in `rag_documents`. Add a resolver that, for each Foundry citation, looks up the
  local document whose `blob_name` basename equals the citation filename and, when found,
  **rewrites the citation `url` to the Track-B proxy route** `/v1/documents/{document_id}/open`
  (which is same-origin, authed, and known-openable). Where no local match exists, keep the
  original agent `url` (best effort).
  - Placement: do the rewrite server-side in `foundry_chat_endpoint` /
    `foundry.chat()` (so both UIs benefit and the mapping logic lives with the DB access),
    adding a `db` lookup `get_document_id_by_basename(conn, filename) -> int | None`. This
    reuses Track B's proxy route and DB layer — the fallback is essentially "route Foundry
    citations through Track B by filename."
  - Caveat: basename matching is heuristic (duplicate basenames across folders, or a KB doc
    absent from the local corpus, would miss). Acceptable at POC scale; note as a limitation.

**Decision rule:** implement the simple case (already done); implement the fallback **only
if** the §8 live check fails. Until that check is run, the fallback is specced but not built,
and the openability question is tracked as the primary Track-A risk (§10).

### 6.2 Track B — Local / Coach surfaces (SECONDARY): backend proxy route

**Decision: a backend proxy route** (unchanged from the original design). `GET
/v1/documents/{document_id}/open` downloads the blob server-side via
`SasBlobStore.download_blob` and streams the bytes to the client.

**Why not a SAS redirect.** The only credential the app holds is a **container-level SAS
token** granting read to *every* blob. Redirecting the browser to `…/{blob}?{sas}` would
expose that shared token, letting anyone enumerate the whole Knowledge Foundation
(contradicts FR4/FR9). Minting a *scoped, short-lived per-blob* SAS would require the
**storage account key**, which this app does not have. So a safe redirect is not achievable.

**Why the proxy wins for the POC.** (a) The container SAS stays on the server. (b) The route
rides the existing sign-in gate. (c) It lets us set `Content-Type`/`Content-Disposition` so
PDFs render inline while office files download. (d) It reuses the `get_mix_audio_endpoint`
bytes-serving pattern. Trade-off: documents stream through the app process (fine at POC
corpus size; no Range needed).

**Keyed by `document_id`, not `blob_name`.** The client passes an opaque integer id; the
server resolves it to `blob_name`. Keeps storage layout hidden (FR4), avoids client-supplied
blob paths (no path-traversal), and matches the `/v1/documents/{document_id}/chunks`
convention.

#### 6.2.1 New DB helper

Add to `src/rag_layer/db.py`:

```python
def get_document_blob_name(conn, document_id: int) -> str | None:
    row = conn.execute(
        "SELECT blob_name FROM rag_documents WHERE id = %s", (document_id,)
    ).fetchone()
    return row["blob_name"] if row else None
```

(Plus, **only if the Track-A fallback in §6.1 is needed**, a
`get_document_id_by_basename(conn, filename) -> int | None` that matches on the basename of
`blob_name`.)

#### 6.2.2 New service helper

Add to `src/rag_layer/service.py`:

```python
def open_document(*, settings: Settings, document_id: int) -> tuple[str, bytes] | None:
    """Resolve a document id to its blob_name and download the original bytes.
    Returns (blob_name, data) or None if the id is unknown."""
    with connect(settings) as conn:
        blob_name = get_document_blob_name(conn, document_id)
    if blob_name is None:
        return None
    data = get_blob_store(settings).download_blob(blob_name)
    return blob_name, data
```

#### 6.2.3 New endpoint

In `src/rag_layer/server.py`, near the other document routes (~`server.py:515-531`):

```python
import mimetypes
from urllib.parse import quote
from pathlib import PurePosixPath

_INLINE_EXTENSIONS = {".pdf", ".html", ".htm", ".txt", ".md", ".csv"}
_EXTENSION_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".html": "text/html", ".htm": "text/html",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

@app.get("/v1/documents/{document_id}/open", include_in_schema=True)
def open_document_endpoint(document_id: int, request: Request) -> Response:
    try:
        result = open_document(settings=request.app.state.settings, document_id=document_id)
    except Exception as exc:  # blob download / storage failures
        raise HTTPException(status_code=502, detail=f"Could not fetch source document: {type(exc).__name__}: {exc}") from exc
    if result is None:
        raise HTTPException(status_code=404, detail=f"No document with id {document_id}")
    blob_name, data = result
    filename = PurePosixPath(blob_name).name
    ext = PurePosixPath(blob_name).suffix.lower()
    media_type = _EXTENSION_CONTENT_TYPES.get(ext) or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    disposition = "inline" if ext in _INLINE_EXTENSIONS else "attachment"
    cd = f"{disposition}; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": cd, "Cache-Control": "private, max-age=3600"},
    )
```

**Behavior / error handling**
- **200** — bytes with type-appropriate `Content-Type`; `inline` for `_INLINE_EXTENSIONS`,
  else `attachment` (FR5). `filename` is the basename only (FR4).
- **404** — unknown `document_id` (FR7).
- **502** — blob fetch failed; message names the failure but not the SAS URL (FR7/FR9).
- **401** — unauthenticated, via the existing gate (FR8).

#### 6.2.4 Extending the local citation object (depends on M03)

M03 owns the citation shape; M10 **adds fields** rather than a parallel structure:

1. **`db.search_chunks`** (`db.py:217-234`) — add `d.id AS document_id` to the SELECT so each
   retrieved row carries its document id (one-line change).
2. **`service.format_sources`** (`service.py:36-48`) — add `document_id` and a relative `url`:

```python
def format_sources(contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "document_id": row["document_id"],
            "url": f"/v1/documents/{row['document_id']}/open",
            "blob_name": row["blob_name"],
            "chunk_index": row["chunk_index"],
            "citation": citation(row),
            "page": (row.get("metadata") or {}).get("page"),
            "zone": (row.get("metadata") or {}).get("zone", "body"),
            "similarity": float(row["similarity"]),
            "preview": row["content"][:500],
        }
        for row in contexts
    ]
```

3. **`class Source`** (`server.py`) — add `document_id: int` and `url: str` so responses
   validate.

The `url` is **relative** (`/v1/documents/{id}/open`) so it works same-origin and the browser
sends the sign-in cookie automatically.

**Example local citation JSON:**

```json
{
  "document_id": 42,
  "url": "/v1/documents/42/open",
  "blob_name": "FlexLife Product Information/FlexLife Brochure.pdf",
  "chunk_index": 7,
  "citation": "FlexLife Product Information/FlexLife Brochure.pdf | p.10 | FlexLife > Downside Protection",
  "page": 10, "zone": "body", "similarity": 0.83,
  "preview": "FlexLife includes a 0% floor that protects the account value from..."
}
```

#### 6.2.5 Front-end (Coach local sources)

Coach local sources are same-origin; open in a new tab. In `sourcesHtml`
(`ui/index.html:504-520`) and `srcMini` (`ui/index.html:598-611`), change the `.src-cite`
span to an anchor when `s.url`/`s.document_id` is present:

```js
const href = s.url || (s.document_id != null ? '/v1/documents/' + s.document_id + '/open' : '');
const cite = href
  ? '<a class="src-cite" href="' + esc(href) + '" target="_blank" rel="noopener">' + esc(s.citation || s.blob_name) + ' ↗</a>'
  : '<span class="src-cite">' + esc(s.citation || s.blob_name) + '</span>';
```

(Ask Navigator's local-source renderer `srcHtml` at `ui/learn.html:1060-1069` also emits a
plain `.nsrc-cite` span; since the end-user chat is Foundry-only, this path is not normally
hit, but apply the same link pattern there for completeness/back-compat.)

**Foundry citations need no front-end change** — `citeHtml`/`fdCites` already link `c.url`
(§6.1). If the §6.1 fallback rewrites `url` server-side to a proxy route, the UI is unchanged
because it just renders whatever `url` the citation carries.

## 7. Acceptance Criteria (Given/When/Then)

- **AC1 (Foundry link present — PRIMARY)** — *Given* an Ask Navigator question that returns
  citations, *When* `/v1/foundry/chat` responds, *Then* every citation has a non-empty `url`
  and the UI renders it as an `<a target="_blank" rel="noopener">`.
- **AC2 (Foundry link opens — PRIMARY)** — *Given* a Foundry citation link, *When* the user
  clicks it, *Then* the referenced source document opens in a new tab (directly if the agent
  URL is openable; via the §6.1 filename→proxy fallback otherwise).
- **AC3 (local link present)** — *Given* a query hitting a local endpoint (`/v1/answer`,
  `/v1/chat`, `/v1/search`, `/v1/fact-check`), *Then* every `sources` element includes a
  non-empty `url` and an integer `document_id`.
- **AC4 (local open works)** — *Given* a local citation `url = /v1/documents/{id}/open`,
  *When* an authenticated user requests it, *Then* `200` with the document bytes and a
  matching `Content-Type`.
- **AC5 (correct document)** — *Given* a citation for `blob_name` X, *When* its link is
  opened, *Then* the bytes returned are the blob stored under X (verify basename in
  `Content-Disposition` and byte match for Track B).
- **AC6 (viewable inline / office download — Track B)** — *Given* a PDF/HTML/TXT/MD/CSV
  citation, *Then* it renders inline; *Given* DOCX/PPTX/XLSX, *Then* it downloads with the
  original filename.
- **AC7 (multiple citations → multiple documents)** — *Given* an answer citing documents A
  and B, *When* each link is opened, *Then* A's opens A and B's opens B.
- **AC8 (storage hidden — Track B)** — *Given* any local citation link, *Then* the URL
  contains only `document_id` (no blob path, no SAS token), and no response header exposes
  the SAS token or raw blob URL.
- **AC9 (unknown id / unfetchable blob — Track B)** — unknown `document_id` → `404`;
  valid id whose blob errors → `502` with no SAS token in the message.
- **AC10 (auth — Track B)** — no sign-in cookie → `401` on the open route.
- **AC11 (document-level suffices)** — the link opens the whole document; page/section
  targeting is not required.

## 8. Test Plan

**Track A — Foundry / end-user chat (do this FIRST; it gates the fallback decision)**
- **Live URL openability check (manual, the key test).** Run one real Ask Navigator query
  that returns citations. Capture a returned citation `url` (from the `/v1/foundry/chat`
  response, or the DevTools network tab). **Open that url directly in a browser** and record
  the outcome:
  - Opens the document → simple case holds; no code change needed. Resolve §10 open question.
  - `403` / auth wall / download of an unusable ref → implement the §6.1 filename→proxy
    fallback and re-test.
- jsdom: `citeHtml` (learn) and `fdCites` (coach) render an `<a href>` to `c.url` with
  `target="_blank" rel="noopener"`; malformed/empty `url` degrades safely (`href="#"`).
- (If fallback built) unit: `get_document_id_by_basename` matches a KB filename to a local
  `document_id`; the endpoint rewrites the citation `url` to `/v1/documents/{id}/open` when a
  match exists and leaves it untouched otherwise.

**Track B — Local / Coach (unit)**
- `db.get_document_blob_name`: returns `blob_name` for a known id; `None` for unknown.
- `db.search_chunks`: projected rows now include `document_id`.
- `service.format_sources`: each dict has `document_id` and
  `url == f"/v1/documents/{document_id}/open"`; the seven original fields unchanged (M03
  regression guard).
- Content-type/disposition mapping: `.pdf → application/pdf` + `inline`;
  `.docx → …wordprocessingml…` + `attachment`; unknown ext → `application/octet-stream` +
  `attachment`.

**Track B — API (FastAPI TestClient, authenticated; mock `download_blob`)**
- `GET /v1/documents/{id}/open` for a seeded PDF → `200`, `Content-Type: application/pdf`,
  `Content-Disposition` starts with `inline`, body equals stored bytes (AC4/AC5/AC6).
- DOCX id → `attachment` (AC6). Unknown id → `404`; `download_blob` raising → `502` (AC9).
  No cookie → `401` (AC10).
- End-to-end: `POST /v1/answer`, take `sources[0].url`, `GET` it → `200`, basename in
  `Content-Disposition` matches `sources[0].blob_name`'s basename (AC3/AC5); ≥2 distinct docs
  each resolve to their own document (AC7).

**Track B — Front-end (jsdom)**
- `sourcesHtml`/`srcMini` (coach) render an `<a href>` to the citation `url` with
  `target="_blank" rel="noopener"` when present; fall back to a plain span when absent.

**Manual smoke (Docker rebuild required for code changes)**
- Ask Navigator: ask a FlexLife question, click a citation → source opens (Track A).
- Coach: run an Answer, click a local source → PDF opens inline / DOCX downloads (Track B).

## 9. Out of Scope

- Navigating to the exact page / section / paragraph within a document (document-level only).
- Highlighting supporting passages or mapping statements to citations.
- Production-grade access control / per-user authorization / document management.
- Editing or annotating source documents.
- Inline-previewing office formats (DOCX/PPTX/XLSX) in the browser — these download; no
  server-side conversion.
- HTTP Range / partial-content streaming for the Track-B open route.
- Minting scoped per-blob SAS URLs (requires the storage account key, which the app lacks).
- Changing how the hosted Foundry agent produces its citation `url`s (that is the agent's /
  its AI Search KB's configuration, outside this app). M10 either uses the URL as-is or
  reroutes it through the local proxy by filename.

## 10. Dependencies & Open Questions

**Dependencies**
- **M03 (Citations) — BOTH tracks.** M10 depends on the two citation shapes M03 governs:
  Foundry `{n, title, url}` (Track A) and local `format_sources` (Track B). For Track B, M10
  adds `document_id`/`url` to the local object; coordinate so there is one citation object per
  track (no parallel link structures). If M03 changes either shape, keep the link fields.
- **Foundry agent + `/v1/foundry/chat`** (`foundry.py`, `server.py:467-481`) must be
  configured (`FOUNDRY_PROJECT_ENDPOINT`, `FOUNDRY_API_KEY`) for Track A — it is already the
  end-user chat's only backend.
- **`SasBlobStore.download_blob`** (`blob_store.py:67`) and container SAS config — Track B
  (and the Track-A fallback) proxy through it.
- **DB migration**: none. `search_chunks` gains `d.id AS document_id` (query change only);
  `rag_documents.id` already exists.
- **Docker**: backend code changes require rebuilding the image (project dev loop).

**Open questions**
- **★ PRIMARY RISK — Are Foundry citation `url`s directly openable by the end user's
  browser?** The `url` comes from the hosted agent's `url_citation` annotations
  (`foundry.py:139-150`) and points at the agent's Azure AI Search KB source. From code alone
  we cannot tell whether it is (a) a public/SAS'd blob URL that opens fine, (b) a blob URL
  **without** a SAS token → `403` for the user, or (c) an internal AI Search reference the
  browser can't resolve. **Concrete test to resolve it:** run one Ask Navigator query, grab a
  returned citation `url`, and open it in a browser (see §8). If (a), Track A is already done;
  if (b)/(c), build the §6.1 filename→local-proxy fallback. This must be resolved before Track
  A is considered complete.
  - **RESOLVED — Live test 2026-09-30: citation URL returned HTTP 409 PublicAccessNotPermitted
    — not openable without auth. The proxy fallback (§6.1) is now the required path, not a
    contingency.** (Case (b): bare blob URL with no SAS token; storage account has public access
    disabled. See §3 "Verified behavior".)
- **Filename→document matching reliability (only if fallback needed).** The fallback assumes
  the Foundry KB and local pgvector corpus share filenames. Duplicate basenames across folders
  or a KB doc absent from the local corpus would miss. Acceptable at POC scale; note as a
  limitation.
- **Non-viewable types (DOCX/PPTX/XLSX) — Track B:** served as downloads. Story says "where a
  viewable document is available," so download is compliant for the POC.
- **Large files — Track B:** proxy streams whole files through the app. Corpus is small; if
  large PDFs are added later, consider `StreamingResponse` + Range.
- **Duplicate blobs — Track B:** ingest dedups identical bytes across folders to a single
  `rag_documents` row (first-writer-wins), so a citation's `document_id` resolves to the
  canonical first-ingested copy. No action needed; noted.

## 11. Rough Effort Estimate

| Task | Est. |
|------|------|
| **Track A: live URL openability check + verify `citeHtml`/`fdCites` links** | 0.5 h |
| Track A fallback (filename→proxy: `get_document_id_by_basename` + endpoint rewrite) — *only if the URL check fails* | 1.5 h |
| Track B: `db.get_document_blob_name` + `search_chunks` `document_id` projection | 0.5 h |
| Track B: `service.open_document` + `format_sources` extension | 0.5 h |
| Track B: `open_document_endpoint` (content-type/disposition, errors) | 1.5 h |
| Track B: `Source` model update | 0.25 h |
| Track B front-end links (`sourcesHtml`/`srcMini`, `srcHtml`) | 1 h |
| Unit + API + jsdom tests (both tracks) | 2 h |
| Docker rebuild + manual smoke (both UIs) | 1 h |
| **Total (fallback not needed)** | **~1 day (7-8 h)** |
| **Total (fallback needed)** | **~1.5 days (9-10 h)** |
