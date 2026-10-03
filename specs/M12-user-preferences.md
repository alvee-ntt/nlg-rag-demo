# M12 — User Preferences (Clarification Memo)

> **Status:** No user story defined. `USER_STORIES.md:489-493` lists M12 only as
> a title with an open question: *"How is this different from
> [M06: Explain Like…]?"* and the note *"No user story defined yet — needs
> clarification / possible merge with M06."* This memo analyzes the overlap and
> recommends a scope. It is **not** a full spec.

## 1. The question

M06 ("Support User Response Preferences") already lets a user configure how
answers are presented and persists that choice. M12 is named "User Preferences"
with no story. The risk is that M12 and M06 are the same feature under two names.
This memo answers: **does M12 have any scope M06 does not already cover, and
should it merge into M06 or stand alone?**

## 2. What M06 already owns (so M12 must not duplicate)

M06 covers everything about **how a generated answer is presented**, and it is
largely built today:

- **Length / Format / Tone / Plain Language** preferences, chosen in the "Answer
  preferences" sheet — `ui/learn.html:1410-1445` (controls at
  `ui/learn.html:1417-1420`).
- Persisted per browser in localStorage (`chatPrefs`, `ui/learn.html:1033-1038`)
  and sent on every turn by `navSend` (`ui/learn.html:1046-1063`).
- Applied to the answer via a style preamble on the Foundry path
  (`foundry._current_user_content`, `src/rag_layer/foundry.py:173-225`), with
  backend models at `src/rag_layer/server.py:111-124`.

So anything about response length, format, tone, plain language, or "always cite
sources" is **M06's** and should not be re-scoped into M12.

## 3. Candidate distinct scope for M12 (non-response preferences)

There *are* real, user-settable preferences in the app that are **not** about how
an answer is phrased. These are genuinely outside M06:

- **Default voice & interaction mode (M05).** The app has STT/TTS and DragonHD
  voices (`/v1/speech/profiles`, `server.py:1106-1108`; voices in
  `config.py:138-139`). "Which voice, and do I default to text or
  speech in/out?" is a preference M05 exposes per-use but nobody persists as a
  default. Not presentation-of-answer-text → not M06.
- **Default app language & practice language.** Already persisted server-side in
  the profile — `SETTING_KEYS = ("app_language","practice_language")`
  (`src/rag_layer/profile.py:109`), via `PUT /v1/profile/settings`
  (`server.py:1082-1088`). This is the one piece of "user preferences" that
  already lives in the **profile** (not localStorage), and it is squarely
  non-response. A natural anchor for M12.
- **Default landing tab / start screen.** Learn vs. Prepare vs. Coach; today
  navigation is ad hoc (`navStack`, `ui/learn.html:913-948`). No persisted
  "open here by default."
- **Retention / expiration settings.** M13 (Lesson Progress) defines a fixed
  expiration for generated materials but explicitly makes it non-configurable
  ("User-configurable expiration periods" is Out of Scope for M13). If a user
  were ever to tune retention/cleanup, it would be a *preference*, not a
  response style — i.e. M12 territory, though currently de-scoped by M13.
- **Notification preferences.** No notification system exists today; listed only
  as a potential future home, not a recommendation to build one.

The common thread: M12 = **account/experience preferences**; M06 = **answer
presentation preferences**. They are different axes.

## 4. Options

**Option A — Merge M12 into M06.** Treat "User Preferences" as the umbrella and
fold everything into one settings story. *Pro:* one settings surface, no
ambiguity. *Con:* M06's story is specifically scoped to *response presentation*
("without changing the underlying factual content… the supported preferences are
Length/Format/Tone/Plain Language"). Merging dilutes that and leaves
non-response prefs (voice/mode/tab/language) homeless and undefined. It also
re-opens an already-nearly-done milestone.

**Option B — M12 is a distinct "account & experience preferences" story.** M06
owns answer presentation; M12 owns the non-response settings: **default
app/practice language** (already partly built, `profile.py:109`), **default
voice & interaction mode** (M05 defaults), **default landing tab**, and — only if
the product wants it — **retention** (de-scoped by M13 today). *Pro:* clean,
non-overlapping axes; gives the already-server-persisted language settings a home
and a reason to extend the same `app_settings` store. *Con:* needs its own story
written and may pull in M05/M13 dependencies.

**Option C — Close M12 as a duplicate.** Declare M06 the single "user
preferences" milestone and drop M12. *Pro:* least work. *Con:* loses a real gap —
the non-response prefs above are not covered by any other milestone and would go
undefined.

## 5. Recommendation

**Option B — keep M12 as a distinct milestone for non-response ("account &
experience") preferences, with M06 explicitly owning answer-presentation
preferences.** Concretely:

1. **Draw the boundary in `USER_STORIES.md`:** M06 = *how answers are
   presented* (Length/Format/Tone/Plain Language); M12 = *how the app behaves for
   me* (default language, default voice & interaction mode, default landing tab;
   retention only if the product reverses M13's de-scope).
2. **Anchor M12 on what already exists:** the profile `app_settings` store and
   `GET /v1/profile` / `PUT /v1/profile/settings` (`profile.py:109,129-138`,
   `server.py:1076-1088`) already persist `app_language`/`practice_language`
   server-side. M12's natural first increment is to surface those in a settings
   screen and add **default voice/mode** (M05) persistence the same way.
3. **Share the plumbing, not the scope:** M12 reuses the same server-side profile
   store that M06 §6.1 proposes extending for response prefs. One settings area
   in the UI can host both groups under separate headings, but they remain two
   stories with two scopes.
4. **If the product owner wants minimum footprint,** fall back to Option C
   (close M12, note the non-response prefs as future work) rather than Option A —
   merging would muddy M06's deliberately narrow, nearly-complete scope.

This keeps M06 shippable and well-defined, gives the orphaned non-response
preferences a clear owner, and leans on infrastructure that already exists.

## 6. Proposed M12 story (draft, if Option B is accepted)

> As a FlexLife sales agent or support user, I want to set account and experience
> preferences — my default app and practice language, my default voice and
> interaction mode, and my default landing tab — so that the app opens and behaves
> the way I prefer each time without reconfiguring it.

Acceptance (sketch): a user can set and persist a default app language and
practice language (already backed by `profile.py:109`); a default voice and
interaction mode (text/speech in/out) from the M05 options; a default landing
tab; preferences persist across sessions via the profile store; preferences
restore on next load; nothing here changes answer *content* or *presentation*
(that is M06).

**Explicitly out of M12:** everything in M06 (Length/Format/Tone/Plain Language);
building a notification system from scratch; per-user accounts (one shared login
today, `config.py:76-77`).

## 7. Open questions for the product owner

1. Confirm the M06/M12 split above, or choose Option C (close M12).
2. For M12 scope: are **default voice/mode** (M05) and **default landing tab**
   wanted, or is M12 just "surface the already-persisted language settings"?
3. Does the product want **user-configurable retention** (which M13 currently
   de-scopes)? If yes, it belongs in M12.
4. Is server-side (profile) persistence required for M12, given the POC's single
   shared login makes "per-user" preferences effectively deployment-wide
   (`profile.py` docstring)?
