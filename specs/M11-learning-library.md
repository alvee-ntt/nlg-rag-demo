# M11 — Learning Library

## 1. Summary

The Learning Library is a **simple, static navigation page**: a predefined set of
NLG learning resources that the user can browse and open. It is deliberately the
opposite of the dynamic "mixes" the Learn tab already generates — no retrieval, no
LLM generation, no search, no personalization, no LMS. Each resource is a
hard-coded entry with a title, a short blurb, and a **link/navigation action**;
selecting it opens or navigates to the associated material.

The app already ships a close ancestor of this page: a hard-coded `VIDEO_LIBRARY`
catalogue rendered under "FlexLife video library" on the Learn home, with a
category detail view (`renderVideos`). The one thing it does **not** do is open
anything — every resource row currently just fires a "not connected in this demo
yet" toast (`ui/learn.html:1736`). M11 is therefore a small, bounded change:
attach real (hard-coded) destinations to the predefined resources and make
selecting a resource open/navigate to it, satisfying the story's acceptance
criteria without introducing any dynamic or LMS machinery.

## 2. User Story (verbatim)

**User Story: Learning Library** — As a FlexLife sales agent or support user, I
want access to a Learning Library of NLG training resources, so that I can quickly
find and open existing learning materials.

Description: The POC will provide a simple Learning Library page containing a
predefined set of links to NLG learning resources. The page is intended as a
straightforward navigation experience. Resource links may be hard coded for the
POC and should direct the user to the associated learning material when selected.
No dynamic content generation, recommendation logic, search, personalization, or
knowledge retrieval is required for this capability.

Acceptance criteria (source): The application includes a Learning Library page
accessible from the POC user interface; the page displays a predefined set of NLG
learning resources; each resource includes a link or navigation action to the
associated learning material; selecting a resource opens or navigates to the
expected destination; resource links may be hard coded for the POC; the page can
be demonstrated without integration with a learning management system or dynamic
content service.

Out of scope: Dynamic generation of learning content; personalized learning
recommendations; search or filtering of learning resources; tracking course
completion or learner progress; learning management system integration; automated
synchronization of resource links or metadata.

## 3. Current State — what exists

A hard-coded resource catalogue already exists in the Learn tab and is already
wired into the SPA router and navigation; what is missing is only that selecting a
resource opens nothing. Everything below is grounded in `ui/learn.html` (the Learn
feature is a single-page app inside that file). There is **no backend work** for
M11 — the resources are static client-side data.

### The existing catalogue (the thing M11 builds on)

- **`VIDEO_LIBRARY`** — `ui/learn.html:1710-1726`. A hard-coded array of four
  categories (`product-knowledge`, `professional-skills`, `sales-strategies`,
  `business-tools`), each with a `slug`, `name`, `blurb`, and a `videos` list of
  `[title, blurb, duration]` tuples. The leading comment (`ui/learn.html:1708-1709`)
  states plainly: *"Placeholder catalogue: the titles describe material that exists
  in the source library, but there is no video source wired up yet, so rows only
  toast."* This is the predefined resource set the story asks for — it simply has
  no destinations attached.
- **Rendered entry point on the Learn home** — `ui/learn.html:1678`. A "FlexLife
  video library" section lists each category as a `row-item` that navigates to
  `/learn/videos/{slug}`. So the library is already reachable from the POC UI
  (the Learn tab).
- **Category detail view `renderVideos(slug)`** — `ui/learn.html:1727-1737`.
  Renders a category's `videos` as `.vrow` rows (title, blurb, duration thumb).
  **The gap:** every `.vrow` click handler is
  `() => toast("Video playback isn't connected in this demo yet")`
  (`ui/learn.html:1736`) — there is no link, no navigation, nothing opens.
- **Router wiring** — `ui/learn.html:938-942`: the `learn` segment dispatches
  `id === "videos" && sub` to `renderVideos(decodeURIComponent(sub))`; the Learn
  tab itself is `renderLearn()` (`ui/learn.html:1596`). Adding or repurposing a
  library route is a one-line addition to this `route()` switch (`ui/learn.html:923-968`).

### What is NOT present

- **No destinations/links of any kind** on the resources — titles and blurbs only
  (`ui/learn.html:1710-1726`); selecting a resource opens nothing
  (`ui/learn.html:1736`).
- **No standalone "Learning Library" page** labelled as such. The catalogue lives
  as a section on the Learn home and a per-category detail page; there is no
  top-level library page. (Whether M11 needs one is an open question — see §10; the
  minimal reading is that the existing section + detail view already satisfy
  "a Learning Library page accessible from the POC UI.")
- **No backend route** — grep of `src/rag_layer/server.py` for the resources shows
  the Learn routes are all about dynamic mixes (`/v1/learn/*`,
  `src/rag_layer/server.py:661-863`); nothing serves a static resource list, and
  M11 does not need one.

### Reusable UI plumbing

- `go(h)` hash-navigation and the `route()` SPA router — `ui/learn.html:923-970`.
- `data-go` delegation pattern used throughout
  (`el.onclick = () => go(el.dataset.go)`), e.g. `ui/learn.html:1681, 1704, 1773`.
- `backHeader(title)` + `tabbar("learn")` page chrome — e.g. `ui/learn.html:1728,1730`.
- `esc()` HTML escaper — `ui/learn.html:847`; `toast()` — `ui/learn.html:855`.
- `coverClass(id)` for the tile art — `ui/learn.html:863`; icon set `I`
  (`I.video`, `I.play`, `I.chev`) used in the existing rows.

## 4. Scope

**In scope**

- Attach a hard-coded destination (an external URL and/or an in-app route) to each
  predefined NLG learning resource.
- Make selecting a resource **open or navigate to** that destination (external link
  opens in a new tab; in-app material navigates via the router) instead of firing
  the placeholder toast.
- Present the predefined resources as a clearly labelled Learning Library,
  reachable from the POC UI (reuse/relabel the existing "FlexLife video library"
  section + `renderVideos` detail, or promote it to a dedicated route).
- Keep the resource set defined as hard-coded client-side data in `ui/learn.html`.

**Out of scope** — see §9.

## 5. Functional Requirements

- **FR1 — Predefined resource set.** The Library displays a fixed, hard-coded set
  of NLG learning resources (the existing `VIDEO_LIBRARY`, extended with
  destinations, is the baseline). Content is static; nothing is generated or
  retrieved at runtime.
- **FR2 — Accessible from the POC UI.** The Library is reachable from the Learn tab
  (the existing "FlexLife video library" section at `ui/learn.html:1678` already
  provides this entry point; it may be relabelled "Learning Library").
- **FR3 — Each resource has a navigation action.** Every resource row exposes a
  link or tap action to its associated material (FR1's hard-coded destination).
- **FR4 — Selecting opens the destination.** Tapping a resource opens/navigates to
  the expected material: an external URL opens in a new browser tab
  (`window.open(url, "_blank", "noopener")` or an `<a target="_blank" rel="noopener">`);
  an in-app destination navigates via `go(...)`.
- **FR5 — Hard-coded links are acceptable.** Destinations may be literal URLs in
  the source; no link registry, CMS, or sync is required.
- **FR6 — Graceful handling of an unavailable resource.** A resource with no real
  destination yet (placeholder) must behave predictably — either hidden, visibly
  marked "coming soon," or kept as the existing informational toast — never a dead
  click that looks broken. (Which resources are real vs. placeholder is an open
  question — see §10.)
- **FR7 — No dynamic behavior.** No search box, no filtering, no personalization,
  no completion/progress tracking on this page (progress tracking for audio is a
  separate story, M13).

## 6. Technical Design

Frontend-only change in `ui/learn.html`. No backend, config, or DB change.

### 6.1 Extend the resource data with destinations

Give each resource a destination. Minimal shape change to `VIDEO_LIBRARY`
(`ui/learn.html:1710-1726`): make each video a `{ title, blurb, duration, url }`
object (or keep the tuple and add a 4th element), where `url` is a hard-coded
link to the material. Optionally promote the structure to a neutral
`LEARNING_LIBRARY` constant so the naming matches the story:

```js
// ui/learn.html — replace/rename VIDEO_LIBRARY (~1710)
const LEARNING_LIBRARY = [
  { slug: "product-knowledge", name: "Product knowledge",
    blurb: "...",
    resources: [
      { title: "FlexLife IUL", blurb: "...", duration: "5:23",
        url: "https://www.nationallife.com/..." },   // hard-coded, FR5
      // ...
    ] },
  // ...
];
```

A resource with no destination yet carries `url: null` (FR6 handling below).

### 6.2 Open the destination on select

In `renderVideos` (rename to `renderLibraryCategory` or leave as-is), replace the
blanket toast at `ui/learn.html:1736`:

```js
// was: view.querySelectorAll(".vrow").forEach(el => el.onclick =
//        () => toast("Video playback isn't connected in this demo yet"));
view.querySelectorAll(".vrow").forEach((el) => {
  const r = cat.resources[Number(el.dataset.v)];
  el.onclick = () => {
    if (r.url && /^https?:/i.test(r.url)) window.open(r.url, "_blank", "noopener");
    else if (r.url) go(r.url);                      // in-app route
    else toast("This resource isn't available in the demo yet");  // FR6
  };
});
```

The rows already carry `data-v="${i}"` (`ui/learn.html:1733`), so the index maps
straight back to the resource object. External links use
`window.open(..., "noopener")`; in-app destinations reuse the existing `go()`
router (`ui/learn.html:970`).

### 6.3 Labelling (optional, to match the story wording)

- Relabel the Learn-home section header "FlexLife video library"
  (`ui/learn.html:1678`) → "Learning Library" (and the catalogue var name), and
  the detail header (`ui/learn.html:1730`) accordingly. Purely cosmetic.
- If a dedicated top-level page is wanted (see §10), add one `route()` case (e.g.
  `seg === "library"` → `renderLibrary()`) mirroring the existing `videos`
  dispatch (`ui/learn.html:940`) and a tab/menu entry; this is optional and the
  heavier option.

### 6.4 Data-flow summary

Static `LEARNING_LIBRARY` array (hard-coded) → Learn-home section
(`ui/learn.html:1678`) → category detail `renderVideos`
(`ui/learn.html:1727`) → tap a resource → `window.open(url)` / `go(route)`. No
network call, no state, no persistence.

## 7. Acceptance Criteria (Given/When/Then)

- **AC1 (library page present / accessible; story ACs "includes a Learning Library
  page accessible from the POC UI").** *Given* the app is signed in on the Learn
  tab, *When* the user scrolls to the Learning Library section (or opens its page),
  *Then* the predefined resource catalogue is shown, reachable without any
  generation step.
- **AC2 (predefined set displayed; story AC "displays a predefined set of NLG
  learning resources").** *Given* the Library is open, *When* it renders, *Then* it
  lists the hard-coded NLG resources (the `LEARNING_LIBRARY`/`VIDEO_LIBRARY`
  entries) with title and blurb — no runtime-generated items.
- **AC3 (each resource has a navigation action; story AC "each resource includes a
  link or navigation action").** *Given* a resource row, *When* inspected, *Then*
  it exposes a tap/link action bound to that resource's hard-coded destination.
- **AC4 (selecting opens the destination; story AC "selecting a resource opens or
  navigates to the expected destination").** *Given* a resource with a real
  destination, *When* the user taps it, *Then* the associated material opens
  (external URL in a new tab, or in-app navigation) — it no longer fires the
  placeholder toast.
- **AC5 (hard-coded links acceptable; story AC "resource links may be hard coded").**
  *Given* the resources, *When* their destinations are reviewed, *Then* they are
  literal URLs/routes in `ui/learn.html` with no LMS/CMS/sync dependency.
- **AC6 (no LMS/dynamic dependency; story AC "demonstrable without LMS or dynamic
  content service").** *Given* the Library, *When* demonstrated, *Then* it works
  with the app alone — no learning-management-system or content-service
  integration, and no `/v1/*` call is required to render or navigate it.
- **AC7 (placeholder resource is graceful; FR6).** *Given* a resource that has no
  real destination yet, *When* tapped, *Then* it responds predictably (coming-soon
  note / hidden) rather than appearing broken.

## 8. Test Plan

**Frontend (jsdom, per the dev-loop note — the browser extension is on another
machine):**

- `LEARNING_LIBRARY` renders: the Learn-home section lists all categories, and a
  category detail lists each resource row with its title/blurb.
- A resource with an `http(s)` `url`: tapping it calls `window.open` with that URL
  and `"_blank"`/`noopener` (stub `window.open`, assert the argument), and makes no
  `fetch`/`api` call.
- A resource with an in-app `url`: tapping it calls `go(url)` and updates
  `location.hash`.
- A resource with `url: null`: tapping it shows the coming-soon toast and does not
  navigate.
- The Library is reachable from the Learn tab render (`renderLearn`) and the
  `/learn/videos/{slug}` route resolves to the detail view (`route()` dispatch).

**Manual smoke (rebuild the Docker image only if the HTML is baked into the image;
otherwise reload `/app/learn.html`):** open the Learn tab, enter the Learning
Library, tap a real resource (opens in a new tab), tap a placeholder (graceful
note). Verify via jsdom markup per the memory note since the browser is on another
machine.

## 9. Out of Scope

- Dynamic generation of learning content (that is the Learn "mixes" feature and
  M15), recommendation logic, or knowledge retrieval for the Library.
- Search or filtering of resources.
- Personalized recommendations.
- Tracking course completion or learner progress (audio resume is M13; it does not
  apply to these static links).
- Learning-management-system integration.
- Automated synchronization of resource links or metadata (links are hand-maintained).
- Hosting or serving the learning materials themselves; M11 only links to
  wherever they already live.

## 10. Dependencies & Open Questions

**Dependencies**

- None technical — this is a self-contained `ui/learn.html` change with no backend,
  config, or DB dependency, and no dependency on other milestones.

**Open questions**

1. **What are the real destination URLs?** The catalogue is explicitly a
   placeholder today (`ui/learn.html:1708-1709`). M11 needs the actual
   NLG-hosted resource links (e.g. the National Life agent portal / training site)
   to wire in. Until provided, resources stay as graceful placeholders (FR6).
   **Product-owner input required.**
2. **Which resources should the Library list?** Keep the existing four
   video-oriented categories, or curate a different predefined set of NLG learning
   resources (documents, portal pages, courses)? The story says "a predefined set
   of NLG learning resources," not specifically videos.
3. **Section vs. dedicated page.** Is the existing "video library" section on the
   Learn home (plus its detail view) sufficient to count as "a Learning Library
   page," or does the product owner want a distinct top-level page/tab labelled
   "Learning Library"? Recommendation for the POC: relabel + wire the existing
   section (minimal); promote to a dedicated route only if asked.
4. **Open behavior for non-web materials.** If some resources are PDFs or files
   rather than web pages, confirm "open in new tab" is acceptable (it is, for
   document-level navigation, consistent with M10's document-open approach).

## 11. Rough Effort Estimate

| Area | Work | Est. |
| --- | --- | --- |
| Frontend | Add `url` destinations to the resource data | ~0.25 day |
| Frontend | Open-on-select (external/in-app) + graceful placeholder | ~0.25 day |
| Frontend | Relabel to "Learning Library" (+ optional dedicated route) | ~0.25 day |
| Tests | jsdom render + open-behavior tests | ~0.25 day |
| Content | Collect/confirm real NLG resource URLs (PO-dependent) | ~0.25 day |

**Total: ~1–1.25 developer-days** (most of it trivial), **plus** the
product-owner task of supplying the real destination URLs. If the real links are
provided up front, the engineering work alone is ~0.5–0.75 day.
