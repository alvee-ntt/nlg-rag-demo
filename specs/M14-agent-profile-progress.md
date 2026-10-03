# M14 — Agent Profile and Learnings / Progress / Tracking (Clarification Memo)

> **This is a scoping memo, not a build spec.** M14 has **no user story**. `USER_STORIES.md`
> (lines 538-543) flags an open discussion: *"We need to discuss what we are tracking
> against. It seems to contradict dynamic materials with some kind of longer-term lesson
> plan / syllabus."* This memo frames that tension, surveys what already exists in code,
> lays out candidate scopes with tradeoffs, and recommends a direction and the questions to
> close before any spec is written.

## 1. The tension, stated plainly

The POC has committed to **dynamic, on-demand learning** (M15): a user asks for any topic in
natural language, the app generates a one-off lesson grounded in the corpus, and generated
lessons are **ephemeral** — they have a fixed expiration and can be removed (M13,
`USER_STORIES.md:513-515`, `523`). There is deliberately **no catalog, no learning path, no
sequencing** (M15 out-of-scope, `USER_STORIES.md:583-584`).

"Progress / tracking against a syllabus" assumes the opposite: a **fixed, enumerable set of
things to complete**, against which a percentage or a next-step can be computed. You cannot
meaningfully track "percent complete" against an infinite, user-defined, expiring set of
dynamic lessons. **That is the contradiction the note names.** So the real M14 question is
not "how do we track progress" but **"what is the stable thing we track against, given
lessons are dynamic and disposable?"**

## 2. What already exists (so M14 does not reinvent it)

M14 is less greenfield than the empty user story suggests — there are already three progress
surfaces in the code:

- **Roleplay-derived profile (server-persisted, durable).** `profile.build_profile`
  (`src/rag_layer/profile.py:153-173`) returns a real profile: a **level** that is a pure
  function of finished calls (`level_for`, `profile.py:28-51`), **skill scores** averaged
  over graded roleplay calls on three fixed skills — living benefits, illustration design,
  objection handling (`SKILLS`, `profile.py:57-65`) — and **stats** (sessions, last practice)
  from `roleplay_sessions` (`profile.py:153-161`). Served at `GET /v1/profile`
  (`server.py:1076-1079`), rendered on the Profile screen (`ui/learn.html:2201+`). This is a
  durable "tracking against" axis **that already works** — and notably it tracks against
  *practice performance*, not a syllabus.
- **Profile settings (server-persisted).** `app_settings` key-value store holds language
  preferences only (`profile.py:109`, `PUT /v1/profile/settings`, `server.py:1082-1088`).
  M06 answer preferences currently live client-side (`chatPrefs` localStorage,
  `ui/learn.html:1033-1038`), not in the profile yet.
- **Learn progress (client-side, ephemeral).** A fixed `CURRICULUM` with tiers exists
  (`curriculum.py`), seeded as recommended mixes (`/v1/learn/seed`, `server.py:696-724`), and
  the UI already computes **curriculum completion** and a **"Continue / Start next"** nudge
  from a `localStorage` `viewed` map (`ui/learn.html:1758-1771`). Audio **resume position**
  per mix is persisted in `localStorage` `progress` (M13; `ui/learn.html:1957-1958`,
  `2047-2048`). Generated-lesson rows live in `learn_mixes` (`db.py:47-70`) with
  `created_at` for expiry (`mark_stale_mixes`, `db.py:404`).

So two durable axes (roleplay skills/level; language settings) and one ephemeral,
client-only axis (what Learn content was viewed/resumed) already exist. M14's job is to
decide which of these to **consolidate, persist, and surface as "the agent's learnings /
progress"** — not to build tracking from nothing.

## 3. Candidate scopes

### Scope A — "Learning activity log" (descriptive; no syllabus) — RECOMMENDED

Track **what the agent actually did**, not progress against a fixed plan. Promote the
existing client-side Learn progress into the server: persist per-lesson
**viewed/completed + resume position** on the `learn_mixes` row (or a small
`learn_progress` table), and surface a **"Your learning"** list on the Profile — recent
lessons, which were finished, resume where you left off — alongside the existing
roleplay level/skills.

- **Tracks against:** the agent's own activity (lessons generated, opened, finished;
  practice calls). A log, not a percentage.
- **Pros:** No contradiction with dynamic materials — a log naturally accommodates
  infinite, expiring, user-defined lessons. Mostly consolidation of code that already
  exists (M13 resume + `viewed` + roleplay stats). Honest to the POC's "no syllabus"
  stance. Small build.
- **Cons:** No "you are 60% through your training" number (which some stakeholders may
  expect from the word "progress"). No prescriptive "what to learn next" beyond the existing
  curriculum nudge.

### Scope B — "Curriculum progress" (prescriptive; a real syllabus)

Track completion against the **existing fixed `CURRICULUM`** tiers (`curriculum.py`): percent
complete per tier, badges, next recommended item. This is a genuine syllabus and yields a
clean progress bar.

- **Tracks against:** the seeded curriculum (a fixed set).
- **Pros:** Satisfies a literal "progress against a plan"; the curriculum and the
  completion math already exist client-side (`ui/learn.html:1758-1771`).
- **Cons:** **This is exactly the "longer-term lesson plan / syllabus" the open note warns
  contradicts dynamic materials.** It only covers the ~21 seeded items, not the dynamic
  lessons that are M15's whole point, so it risks presenting a curriculum as "the training"
  and sidelining on-demand learning. If pursued, it should be a **separate, clearly-bounded
  "Starter curriculum" tracker**, not conflated with dynamic-lesson tracking.

### Scope C — "Adaptive / skill-gap recommendations" (bridges practice + learning)

Derive **what to learn next** from the roleplay **skill scores** that already exist
(`profile.py:57-65`): a low "objection handling" average suggests a dynamic M15 lesson on
objections. Track against *skill improvement over time*, and close the loop by deep-linking
into M15 lesson generation (the Create screen already accepts a `?topic=` prefill,
`ui/learn.html:1794`).

- **Tracks against:** roleplay skill scores (durable, already computed) — improvement, not
  completion.
- **Pros:** No fixed syllabus, so no contradiction; makes dynamic lessons feel *targeted*
  ("based on needs" — the literal M15 title); reuses the richest durable signal already in
  the DB.
- **Cons:** More product/LLM design (mapping skills → topics), and the recommendation quality
  depends on roleplay volume. Larger build than A.

## 4. Recommendation

**Adopt Scope A as the M14 core, with a thin layer of Scope C, and explicitly defer Scope
B.**

- **A** resolves the contradiction directly: track *activity and performance*, not progress
  against a plan. It is mostly consolidating existing M13/roleplay signals into a durable,
  profile-attached "Your learning" view, so it is low-risk and honest to the "dynamic,
  disposable lessons" design.
- **C** adds the one genuinely valuable "tracking against" axis the codebase already
  supports — **roleplay skill scores** — and turns them into targeted M15 lesson suggestions.
  This is what makes "learnings / progress" meaningful without inventing a syllabus.
- **Defer B.** A fixed curriculum tracker is the precise thing the open note flags as
  contradictory. If stakeholders still want it, scope it as a separate, optional "Starter
  curriculum" progress widget, kept distinct from dynamic-lesson tracking so it does not
  reframe the product around a syllabus.

Net: M14 becomes **"the agent's profile = durable practice performance (roleplay
level/skills, already built) + a learning-activity log (promote M13/viewed to the server) +
skill-gap-driven lesson suggestions (reuse roleplay skills → M15)."** No syllabus, no
completion-of-infinite-set paradox.

## 5. Questions to resolve before an M14 spec

1. **What does "progress" mean to the product owner** — a completion percentage (implies a
   fixed set → Scope B), an activity history (Scope A), or skill improvement over time
   (Scope C)? This single answer picks the scope.
2. **Is a fixed syllabus actually wanted,** given M15 is explicitly catalog-free? If yes, is
   it the seeded `CURRICULUM`, and is it acceptable for it to cover only seeded (not dynamic)
   lessons?
3. **Persistence boundary:** should Learn activity (viewed/completed/resume) move from
   `localStorage` to the server/DB, or is client-side persistence acceptable for the POC (as
   M13 already treats it)? This determines whether M14 needs schema work.
4. **Single-user reality:** the POC has one shared login and one profile
   (`profile.py:1-8`); all "tracking" is deployment-wide, not per-user. Is that acceptable
   for the M14 demo, with per-user scoping deferred to real accounts (the documented
   migration path, `profile.py:4-8`)?
5. **Relationship to M06 and M13:** M06 should own moving answer preferences into the
   profile; M13 owns resume/expiration. M14 should *consume/surface* these, not re-own them —
   confirm this division so M14 does not duplicate M06/M13 work.
6. **Expiration vs. history:** M13 expires generated lessons (`USER_STORIES.md:513`). If the
   activity log references expired lessons, do we keep a lightweight **record** (title, date,
   completed) after the lesson content itself is removed? (Recommended: yes — the log is
   metadata, cheap to keep, and avoids the log emptying out as lessons expire.)
