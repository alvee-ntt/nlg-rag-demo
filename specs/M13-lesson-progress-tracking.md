# M13 — Lesson Progress Tracking (Resume Audio Learning Materials)

## 1. Summary

M13 lets a learner stop an **audio** learning material and later resume it at
approximately the saved position, across separate app sessions, without manually
noting where they left off. The generated material and its progress should carry a
**fixed expiration** for cleanup; once expired the material may be removed and
would need to be regenerated.

The stop/resume half of this story is **already implemented** in the Learn tab's
audio players: playback position is written to `localStorage` on every
`timeupdate` and restored on `loadedmetadata`, keyed by mix id, and the Learn
home surfaces a "Continue listening…" card from it. What is **not** implemented is
the story's second requirement — a **fixed expiration period for generated
materials and their progress**. The `learn_mixes` table never expires rows (the
only age-based job, `mark_stale_mixes`, just fails *stuck* generations after 12
minutes; it does not clean up finished mixes), and the client-side `progress`
map is never pruned, so entries for deleted/expired mixes accumulate forever.

M13's real work is therefore: (1) harden and document the existing resume
mechanism so it clearly satisfies the acceptance criteria (approximate-position
semantics, cross-session persistence), and (2) add a **fixed expiration** for
generated mixes + their progress, reconciled with the existing mix lifecycle. The
story explicitly rates expiration as *secondary* to demonstrating stop-and-resume.

## 2. User Story (verbatim)

**User Story: Resume Audio Learning Materials** — As a FlexLife learner, I want to
stop an audio learning session and continue it later, so that I can complete
longer learning materials over multiple sessions.

Description: The POC will allow generated audio learning materials to be paused or
stopped and resumed at a later time. The application should retain enough progress
information to return the user to approximately the same playback position when
they resume the material. For the POC, any reasonable persistence approach is
acceptable. The capability does not need to demonstrate sophisticated
synchronization across devices or production-grade learner progress management.
Generated learning materials and their associated progress should have a fixed
expiration period for cleanup purposes. Once expired, the saved material may be
removed and the user would need to generate it again if needed. Expiration
behavior is secondary to the primary POC goal of demonstrating stop-and-resume
capability.

Acceptance criteria (source): A user can stop or leave an audio learning session
before it is complete; the application retains the user's approximate playback
position; the user can later return to the saved learning material and resume from
approximately where they stopped; resume behavior works across separate
application sessions using a reasonable POC persistence mechanism; generated
learning materials and associated progress have a fixed expiration period; expired
materials may be removed and are no longer required to be resumable; the POC does
not require the user to manually record or remember their previous position.

Out of scope: Cross-device synchronization requirements; production-grade learner
progress tracking; user-configurable expiration periods; expiration warnings or
countdown displays; long-term archival of generated learning materials; recovery
of expired materials.

## 3. Current State — what exists

Grounded in `ui/learn.html`, `src/rag_layer/learn.py`, and `src/rag_layer/db.py`.
The Learn tab (salesDJ) generates "mixes" — bite-sized training content in three
formats, `audio` (a two-host MP3 episode), `article` (with an on-demand "Listen"
narration), and `flashcards` — and the two audio players already persist and
restore playback position.

### The mix lifecycle (M13's backbone — document precisely)

- **Table `learn_mixes`** — `src/rag_layer/db.py:47-70`. Columns: `id, kind,
  prompt, length, status, title, summary, duration_seconds, content JSONB,
  sources JSONB, audio BYTEA, audio_mime, error, recommended, created_at,
  updated_at` + later `tier, curriculum_key`. The rendered MP3 bytes live in
  `audio`; audio sub-state lives in `content.audio_status`.
- **Status flow:** `queued → generating → ready | failed`. `create_mix`
  (`db.py:331-350`) inserts at the default `status='queued'`; `run_generation`
  (`learn.py:363-451`) flips to `generating`, then `ready` (with content/sources/
  audio) or `failed`; `retry_mix_endpoint` (`src/rag_layer/server.py:837-851`)
  re-enqueues; `delete_mix` (`db.py:398-401`) hard-deletes a row.
- **Audio sub-status** (`content.audio_status`, set in `learn.py`): `pending` /
  `ready` / `failed` / `unconfigured` at generation (`learn.py:424-442`), and
  `rendering` during an on-demand (re)render (`learn.py:508`). Audio is produced
  at creation when Speech is configured, or later via
  `POST /v1/learn/mixes/{id}/render-audio` (`server.py:792-818`) /
  `render-pending` (`server.py:821-834`).
- **The only age-based job is NOT expiry.** `mark_stale_mixes(conn,
  older_than_minutes=12)` — `db.py:404-419`, called at the top of
  `list_mixes_endpoint` (`server.py:677`) — flips rows **stuck in `queued`/
  `generating`** past 12 minutes to `failed` ("Generation was interrupted"). It
  **does not** delete or expire finished (`ready`) mixes. **There is no TTL,
  retention, or cleanup of generated materials anywhere** (grep for
  `expire/ttl/cleanup/retention` finds only `mark_stale_mixes` and SAS-expiry
  code unrelated to mixes). Mixes live until a user manually deletes them.
- **Endpoints** — `server.py:661-863`: `GET /v1/learn/status` (661), `GET/POST
  /v1/learn/mixes` (672/683), `POST /v1/learn/seed` (696), `GET
  /v1/learn/curriculum` (727), `GET /v1/learn/mixes/{id}` (732), `GET
  /v1/learn/mixes/{id}/audio` (744), `POST .../render-audio` (792), `POST
  /v1/learn/audio/render-pending` (821), `POST .../retry` (837), `DELETE
  .../{id}` (854). None read or write a playback position — progress is purely
  client-side today.

### Audio playback today

- **`GET /v1/learn/mixes/{id}/audio`** — `server.py:744-789`. Streams the stored
  MP3 and **honors HTTP `Range` requests (206 + `Accept-Ranges: bytes`)** so the
  browser treats `<audio>` as seekable; the code comment (`server.py:757-759`)
  notes this is exactly what makes `currentTime`, the skip-15 buttons, and the
  scrub bar work. This is the seek primitive resume relies on.
- **Audio episode player `renderAudio(m)`** — `ui/learn.html:1891-1970`. Creates
  `new Audio(.../audio)` with `preload="metadata"` (`1945-1946`); wires a scrub
  slider, play/pause, ±15s skip (`1964-1965`), and playback-rate cycling.
- **Article "Listen" narration player** — `ui/learn.html:2023-2057`. Same
  pattern for article narration (`#lseek`, segment highlighting), with its own
  `start` offsets from `content.narration`.
- `flashcards` have no audio and are out of M13's scope.

### Resume position today (ALREADY BUILT, localStorage)

- **Storage:** a single `localStorage` map `salesdj.progress`, read/written via
  `store.get("progress", {})` / `store.set("progress", progress)` (store helper,
  `ui/learn.html:850-853`), keyed by **mix id**, value `{ position, duration }`
  (seconds).
- **Save:** on every `timeupdate`, both players write
  `progress[m.id] = { position: audio.currentTime, duration: ... }`
  (`ui/learn.html:1958` audio, `2048` narration).
- **Restore (approximate):** on `loadedmetadata`, each player sets
  `audio.currentTime = saved.position` **only if `saved.position < audio.duration
  - 5`** (`ui/learn.html:1957, 2047`) — i.e. it resumes a few seconds' tolerance
  short of the end and ignores a near-complete position. This is the
  "approximately where they stopped" semantics.
- **Reset on finish:** on `ended`, position is reset to `0`
  (`ui/learn.html:1961, 2051`) so a completed episode restarts rather than
  resuming at the very end.
- **Surfacing:** `lastAudio` is stored on open (`ui/learn.html:1892`); the Learn
  home renders a "Continue listening…" card with a progress bar computed from
  `progress[last.id]` (`ui/learn.html:1631-1641`), and `mixCard` draws a per-card
  progress bar (`ui/learn.html:1487-1489`).
- **Persistence scope:** `localStorage` persists across reloads and **separate
  app sessions on the same browser/device** (not across devices — acceptable per
  the story's "no cross-device sync" out-of-scope). No login/profile is attached
  (one shared demo login, `config.py:76-77`), so progress is per-browser.

### The gap (what M13 must add)

- **No fixed expiration of generated materials.** `learn_mixes` rows (and their
  stored MP3 bytes) never expire; only manual `DELETE` removes them (`db.py:398`,
  `server.py:854`). The story requires a **fixed expiration period** after which
  the material may be removed.
- **No expiration/pruning of progress.** The `salesdj.progress` map grows
  unbounded: entries for deleted or (future-)expired mixes are never removed
  (`ui/learn.html:1958, 2048` only ever add/update keys). Progress must expire
  with — or alongside — its material.
- **Resume is sound but undocumented as an AC.** The `-5s` tolerance and
  `ended→0` reset already implement "approximate position," but there is no test
  or spec pinning them to the acceptance criteria.

## 4. Scope

**In scope**

- Confirm and (lightly) harden the existing stop/resume for both audio players so
  it demonstrably meets the ACs: stop/leave mid-session, approximate-position
  retention, cross-session resume, no manual position entry.
- Add a **fixed expiration period** for generated mixes (material) and their
  progress, with a cleanup mechanism, reconciled with the existing lifecycle.
- Prune/expire the client-side `progress` map so it does not retain entries for
  materials that no longer exist.

**Decision needed (see §10):** whether expiry is enforced **server-side** (an
age-based cleanup of `learn_mixes` by `created_at`/`updated_at`, the authoritative
option) or kept **client-side only** (expire `progress` entries by stored
timestamp; mixes are not auto-deleted). Recommendation below is server-side
material expiry + client progress pruning that follows it.

**Out of scope** — see §9.

## 5. Functional Requirements

- **FR1 — Stop/leave mid-session.** The user can pause or navigate away from an
  audio material before completion with no explicit "save" step. (Already true:
  pause toggles `audio.pause()` `ui/learn.html:1963`; leaving the page runs
  `cleanup` which pauses/releases the element `ui/learn.html:1969, 2057`.)
- **FR2 — Retain approximate position automatically.** The app continuously
  records the current playback position per material without the user recording
  it (`timeupdate` → `store.set`, `ui/learn.html:1958, 2048`).
- **FR3 — Resume approximately.** On reopening a material, playback starts at
  approximately the saved position (within a few seconds), not the beginning —
  except when the saved position is within the end tolerance, in which case it
  starts over (`-5s` guard + `ended→0`, `ui/learn.html:1957/1961, 2047/2051`).
- **FR4 — Across separate sessions.** Resume works after a full app
  close/reopen/reload on the same device via a reasonable POC persistence
  mechanism (`localStorage`, `ui/learn.html:850-853`). Cross-device sync is not
  required.
- **FR5 — No manual position tracking.** The user never types, bookmarks, or
  remembers a timestamp; the app does it.
- **FR6 — Fixed expiration of materials.** Generated mixes have a **fixed**
  expiration period (not user-configurable). After it elapses, the material is
  eligible for removal and need not remain resumable; a removed material can be
  regenerated by the user.
- **FR7 — Progress expires with its material.** Progress for an expired/removed
  material is cleaned up (server-side rows cascade with the mix; the client
  `progress` map drops entries whose mix no longer exists or whose own timestamp
  is past the expiry).
- **FR8 — Expiration is secondary / non-intrusive.** No expiration warnings,
  countdowns, or user prompts (explicitly out of scope); cleanup happens quietly.

## 6. Technical Design

Two independent pieces: resume (mostly done — frontend) and expiration (new —
backend-leaning). They share a single configurable expiry constant.

### 6.1 Resume — keep, harden, and make it robust (frontend, `ui/learn.html`)

The core resume already works (`ui/learn.html:1945-1969` audio, `2023-2057`
narration). Recommended hardening, all small:

- **Throttle writes.** `timeupdate` fires ~4×/sec; writing the whole `progress`
  map to `localStorage` each time is wasteful. Debounce the `store.set("progress",
  …)` to ~every 5s (and once on `pause`/`beforeunload`/`cleanup`) so a stop always
  flushes the latest position. Keeps FR2/FR4 while cutting write churn.
- **Record a timestamp per entry** (needed by FR7 client pruning): change the
  stored value to `{ position, duration, at: Date.now() }` at
  `ui/learn.html:1958, 2048`. The Home/`mixCard` readers (`1487, 1637`) ignore
  unknown keys, so this is backward compatible.
- **Keep the approximate semantics as-is:** the `-5s` end tolerance
  (`ui/learn.html:1957, 2047`) and `ended→0` reset (`1961, 2051`) are the
  "approximately where they stopped" behavior — no change, just covered by tests
  (§7 AC2/AC3).
- **Optional:** also persist `playbackRate`/last segment for article narration —
  not required by the ACs.

No change is needed to the audio endpoint — `Range` support
(`server.py:744-789`) already makes `currentTime` seek correctly.

### 6.2 Expiration of materials — recommended: server-side age-based cleanup

Add a **fixed TTL** on generated mixes, keyed off `created_at` (or `updated_at` to
let active use extend life — decide in §10). Reuse the existing
`mark_stale_mixes` pattern (`db.py:404-419`) and its call site
(`list_mixes_endpoint`, `server.py:672-680`) so cleanup is opportunistic and needs
no scheduler/cron (a POC-appropriate, zero-infra approach consistent with the
app's design).

**Config** (`src/rag_layer/config.py`, following the `_int`/`os.getenv` pattern,
`config.py:100-102, 115-159`):

```python
# Settings dataclass (~config.py:71, with the other defaulted fields)
learn_mix_ttl_days: int = 30     # fixed expiry for generated mixes + progress

# load_settings()
learn_mix_ttl_days=_int("LEARN_MIX_TTL_DAYS", 30),
```

**DB cleanup** (`src/rag_layer/db.py`, next to `mark_stale_mixes`):

```python
def expire_old_mixes(conn, *, older_than_days: int) -> int:
    """Delete generated mixes past the fixed TTL so stored audio/content do not
    accumulate. Progress cleanup is client-side (see service note). Returns rows removed."""
    cur = conn.execute(
        """DELETE FROM learn_mixes
           WHERE created_at < now() - make_interval(days => %s)""",
        (older_than_days,),
    )
    conn.commit()
    return cur.rowcount
```

**Wire it** into the already-existing stale sweep in `list_mixes_endpoint`
(`server.py:672-680`), gated on the TTL being set:

```python
with connect(request.app.state.settings) as conn:
    mark_stale_mixes(conn)
    if settings.learn_mix_ttl_days > 0:
        expire_old_mixes(conn, older_than_days=settings.learn_mix_ttl_days)
    rows = list_mixes(conn)
```

Because `audio` (BYTEA) and `content` live on the same row, the `DELETE` reclaims
the MP3 bytes too (FR6). **Open decision:** protect `recommended`/curriculum
starter lessons from expiry (they are the shared learning path, not ephemeral
user-generated material) — likely add `AND recommended = false AND curriculum_key
IS NULL` to the `DELETE`. See §10.

**Alternative (client-only, lighter):** skip server deletion; expire only the
client `progress` entries by their `at` timestamp (§6.3). Mixes then linger in the
DB but progress "expires," partly satisfying FR6/FR7. Weaker (storage never
reclaimed) and does not truly "remove the material," so server-side is
recommended; the client pruning in §6.3 is wanted either way.

### 6.3 Progress pruning (frontend, `ui/learn.html`)

With a per-entry `at` timestamp (§6.1) and the mix list already fetched on the
Learn home (`ui/learn.html:1609`), prune the `progress` map so it does not retain
dead entries (FR7):

```js
// after mixes load in renderLearn (~ui/learn.html:1631)
const TTL_MS = 30 * 864e5;              // mirror LEARN_MIX_TTL_DAYS
const live = new Set(mixes.map(m => m.id));
const progress = store.get("progress", {}); let changed = false;
for (const [id, p] of Object.entries(progress)) {
  const gone = !live.has(Number(id));               // mix deleted/expired server-side
  const stale = p.at && (Date.now() - p.at) > TTL_MS;
  if (gone || stale) { delete progress[id]; changed = true; }
}
if (changed) store.set("progress", progress);
```

This ties client progress to server truth: once a mix is expired/deleted, its
resume entry disappears on the next Learn-home visit, and any orphaned/stale
entries self-clean. (Entries with no `at` — written before this change — are kept
until their mix disappears, then pruned as `gone`.)

### 6.4 Data-flow summary

Playback → `timeupdate` (debounced) → `localStorage salesdj.progress[id] =
{position,duration,at}` → reopen → `loadedmetadata` restores `currentTime`
(approx, `-5s` guard) → Learn home "Continue listening…" (`ui/learn.html:1636`).
Expiry → opportunistic `expire_old_mixes` on `GET /v1/learn/mixes`
(`server.py:677`) removes aged rows (material + audio) → next Learn-home visit
prunes the matching `progress` entries (§6.3).

## 7. Acceptance Criteria (Given/When/Then)

- **AC1 (stop mid-session; story AC "stop or leave before complete").** *Given* an
  audio material is playing, *When* the user pauses or navigates away, *Then*
  playback stops cleanly (`cleanup`, `ui/learn.html:1969/2057`) and the latest
  position is persisted with no explicit save.
- **AC2 (approximate position retained; story ACs "retains approximate playback
  position", "does not require the user to manually record").** *Given* playback
  reached ~t seconds, *When* it is stopped, *Then* `salesdj.progress[id].position`
  ≈ t (within the `timeupdate`/debounce granularity), written automatically.
- **AC3 (resume approximately; story AC "resume from approximately where they
  stopped").** *Given* a saved position not within the end tolerance, *When* the
  user reopens the material, *Then* playback starts at ≈ the saved position (the
  `-5s` guard, `ui/learn.html:1957/2047`); *and given* the saved position was at/near
  the end, *Then* it starts over (`ended→0`, `1961/2051`).
- **AC4 (cross-session; story AC "works across separate application sessions").**
  *Given* a saved position, *When* the app is fully closed and reopened (or
  reloaded) on the same device, *Then* the position persists via `localStorage`
  and resume still works.
- **AC5 (no manual tracking; story AC "does not require the user to manually record
  or remember").** *Given* the whole flow, *When* exercised, *Then* the user never
  enters or bookmarks a timestamp.
- **AC6 (fixed expiration of materials; story AC "fixed expiration period",
  "expired materials may be removed").** *Given* a generated mix older than
  `LEARN_MIX_TTL_DAYS`, *When* `GET /v1/learn/mixes` runs, *Then*
  `expire_old_mixes` removes the row (and its stored audio) and it no longer
  appears in the library; the TTL is fixed, not user-set.
- **AC7 (progress expires with material; story AC "associated progress" +
  "no longer required to be resumable").** *Given* an expired/removed mix, *When*
  the Learn home next loads, *Then* its `progress` entry is pruned (§6.3), so no
  dangling resume state remains; a stale entry past TTL also self-cleans.
- **AC8 (non-intrusive; out-of-scope "no warnings/countdowns").** *Given*
  expiration, *When* it occurs, *Then* there is no warning, countdown, or prompt —
  cleanup is silent.

## 8. Test Plan

**Backend (pytest):**

- `expire_old_mixes` deletes rows whose `created_at` is older than the TTL and
  leaves newer rows (insert rows with back-dated `created_at`; assert `rowcount`
  and remaining rows). If the starter-lesson carve-out is adopted, assert
  `recommended`/`curriculum_key` rows survive.
- `list_mixes_endpoint` integration (FastAPI `TestClient`): with
  `LEARN_MIX_TTL_DAYS` set, an aged mix is gone from the `mixes` list; a fresh one
  remains; `mark_stale_mixes` behavior (12-min stuck→failed) is unchanged.
- TTL disabled (`0`) → no deletion occurs.

**Frontend (jsdom, per the dev-loop note):**

- On `loadedmetadata` with a saved position < duration−5, `audio.currentTime` is
  set to the saved position; with a near-end saved position it is not (starts at 0).
- `timeupdate` writes `{position,duration,at}` to `salesdj.progress[id]`
  (debounced: assert a flush on `pause`/cleanup).
- `ended` resets the stored position to 0.
- Progress pruning: given a `progress` map with an id absent from the fetched
  `mixes` (deleted) and an entry with an `at` older than TTL, both are removed on
  Learn-home render; a live, fresh entry is kept.
- "Continue listening…" card renders the correct percentage from a saved entry
  (`ui/learn.html:1637`).

**Manual smoke (rebuild the Docker image per the dev loop):** play an audio mix,
leave at ~0:30, reload `/app/learn.html`, reopen the mix → resumes near 0:30 and
the Home "Continue listening…" bar reflects it. For expiry, back-date a mix's
`created_at` in Postgres (via `!`/allowed rule per the local-DB memory note),
reload the Learn tab, confirm it disappears and its progress entry is pruned.

## 9. Out of Scope

- Cross-device / cross-browser synchronization of position (localStorage is
  per-browser; this is accepted by the story).
- Production-grade learner progress tracking, analytics, or a server-side
  per-user progress store (there is one shared demo login; `config.py:76-77`).
- User-configurable expiration periods (TTL is a fixed config constant).
- Expiration warnings, countdowns, or "expires in N days" displays.
- Long-term archival or recovery of expired/removed materials.
- Resume for non-audio content (flashcards; article *reading* position beyond the
  existing narration player).
- A background scheduler/cron for cleanup — expiry is opportunistic on the
  existing list endpoint, matching the POC's zero-infra style.

## 10. Dependencies & Open Questions

**Dependencies**

- The Learn mixes feature (generation + the two audio players) and the
  `Range`-aware audio endpoint (`server.py:744-789`) — all present.
- A Docker image rebuild to ship backend (config/DB) changes, per the dev-loop
  memory note. The resume-only hardening is frontend and ships with the HTML.

**Open questions**

1. **Server-side material expiry vs. client-only progress expiry.** Recommended:
   server-side `expire_old_mixes` (truly removes material + reclaims audio bytes,
   fully satisfying FR6) **plus** client progress pruning (FR7). Confirm the
   product owner wants materials actually deleted, not just hidden.
2. **TTL length and anchor.** Default proposed `LEARN_MIX_TTL_DAYS = 30`. Confirm
   the fixed period, and whether to anchor on `created_at` (hard expiry from
   generation) or `updated_at`/last-access (sliding — but mixes aren't "touched"
   on play today, so sliding would require writing a `last_played_at`, extra
   work). Recommendation: anchor on `created_at` for simplicity.
3. **Protect the curriculum/starter lessons from expiry?** The seeded
   `recommended`/`curriculum_key` lessons (`server.py:696-724`) are the shared
   learning path, not ephemeral user content; expiring them would silently empty
   the path. Recommendation: exclude `recommended=true`/`curriculum_key IS NOT
   NULL` from `expire_old_mixes` so only user-generated custom mixes expire.
   Needs PO confirmation.
4. **Should play extend a material's life?** If a learner is mid-way through a
   long episode when TTL elapses, a hard `created_at` expiry could delete it under
   them. For the POC this is acceptable (expiration is "secondary"), but a
   `last_played_at` touch on open would avoid it if desired (defer).
5. **Pre-existing progress entries without `at`.** Handled gracefully (kept until
   their mix disappears, §6.3), but note that until a material is reopened and
   re-saved, its entry lacks a timestamp.

## 11. Rough Effort Estimate

| Area | Work | Est. |
| --- | --- | --- |
| Frontend | Debounce + `at` timestamp on progress writes (both players) | ~0.25 day |
| Frontend | Progress-map pruning on Learn home | ~0.25 day |
| Config | `learn_mix_ttl_days` field + env | ~0.1 day |
| Backend | `expire_old_mixes` + wire into list endpoint (+ carve-out) | ~0.25 day |
| Tests | backend pytest + jsdom (resume + expiry + pruning) | ~0.5 day |
| Manual/demo | Docker rebuild, back-date smoke test, tuning | ~0.25 day |

**Total: ~1.5–1.75 developer-days.** The stop/resume core is already built, so
most of the effort is the fixed-expiration piece and locking the resume semantics
down with tests. Resume-hardening alone (no expiry) is ~0.5 day.
