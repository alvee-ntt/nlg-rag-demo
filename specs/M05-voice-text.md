# M05 — Voice / Text

## 1. Summary

The Agent Navigator already lets the user **type** a question in the Ask
Navigator chat and read a **text** answer, so the "Text In → Text Out" mode of
this story is shipped. M05 adds the two voice modes the story requires on top of
that same chat — **Speech In → Speech Out** and **Speech In → Text Out** — plus
a **mode selector** so the user chooses how they interact.

The key fact that shapes this milestone: **speech is already half-built in this
repo, just not for the chat.** A complete browser Azure Speech SDK integration
(microphone STT + token-authorised TTS with pause/resume) already powers the
**Prepare-tab roleplay call** (`ui/learn.html:2517-2602`, `2687-2746`), fed by a
working server token-minting endpoint `POST /v1/speech/token`
(`src/rag_layer/server.py:1094-1103` → `Roleplay.speech_token`,
`src/rag_layer/roleplay.py:1220-1252`). Separately, a server-side REST TTS module
(`src/rag_layer/speech.py`) renders Learn-tab audio **mixes** to MP3 files. M05
does **not** build speech from scratch — it **re-points the existing
browser-SDK seam at the Ask Navigator chat**: speech-in is transcribed to text
and fed into the *existing* `navSend` → `POST /v1/foundry/chat` path unchanged,
so voice turns get the exact same grounding, citations, and follow-up behavior
as typed turns; speech-out synthesizes the text answer the chat already produces.

The actual gaps are narrow: (1) the chat's voice input today uses the
browser-native Web Speech API (`ui/learn.html:1240-1251`), not the Azure seam,
so it is Chrome/Edge-only and inconsistent with the rest of the app; (2) there
is **no TTS in the chat at all** — answers are text-only; (3) there is **no mode
selector**; and (4) the default English chat voice name is **not exposed to the
browser** (`/v1/speech/token` returns a voice only when a roleplay `profile_id`
is passed — `roleplay.py:1250-1251`). M05 is almost entirely frontend work in
`ui/learn.html` plus one small backend addition to surface the default voice.

## 2. User Story (verbatim)

**User Story: Support Voice and Text Interaction**

> As a FlexLife sales agent or support user,
> I want to interact with the application using either text or voice,
> so that I can choose the interaction method that is most convenient for my situation.

### Description

The POC will support both text and voice interaction for user questions and application responses.

Users should be able to select from the following interaction modes:

- Text input with text output.
- Speech input with speech output.
- Speech input with text output.

Voice input may be handled through a simple record-and-transcribe interaction rather than requiring a continuous real-time voice conversation.

Once speech is converted to text, the resulting request should follow the same conversational, retrieval, grounding, and follow-up behavior as a typed request.

For voice output, basic English text-to-speech capability is sufficient for the POC. Advanced voice customization and production-quality speech behavior are not required.

### Acceptance Criteria

- A user can submit a question or request using typed text.
- A user can submit a question or request using spoken English.
- Spoken input is converted into text that can be processed by the same application flow used for typed input.
- A user can receive a response as displayed text.
- A user can receive a response as synthesized speech.
- The application supports Text In → Text Out.
- The application supports Speech In → Speech Out.
- The application supports Speech In → Text Out.
- The user can select the supported interaction mode.
- Voice interactions support the same FlexLife knowledge grounding and conversational behavior as text interactions.
- A simple record → transcribe → respond → play interaction is sufficient for the POC.

### Out of Scope

- Continuous or always-listening voice conversation.
- Real-time conversational interruption or turn-taking.
- Multiple languages.
- Advanced voice selection, cloning, emotion, or speaking-style controls.
- Production-grade speech recognition tuning or accessibility optimization.

## 3. Current State — what exists

Speech is **partially built**. Everything below is real, shipped code. The
summary: the server-side token + SDK plumbing exists and is proven in the
roleplay call; the chat reuses *none* of it for output and uses a weaker,
browser-only path for input.

### Text In → Text Out (the chat) — DONE

- The Ask Navigator chat widget `mountChat(root, opts)`
  (`ui/learn.html:1157-1256`) renders typed input (`<input maxlength="1000">`,
  `ui/learn.html:1160`), sends on Enter/tap (`send`, `ui/learn.html:1186-1195`),
  and paints text answers as Markdown (`mdToHtml`, `ui/learn.html:1094`; rendered
  at `1174-1176`).
- The turn function `navSend(message, history)` (`ui/learn.html:1046-1063`)
  POSTs to `/v1/foundry/chat` and returns
  `{ text, citations, sources, domain, escalate, escalate_reason }`. This is the
  grounded, conversational path M05's voice modes must reuse verbatim — the
  hosted Foundry agent does retrieval/grounding, history is replayed for
  follow-ups (`foundry.chat`, `src/rag_layer/foundry.py:169-191`). **Text In →
  Text Out therefore already satisfies the first/fourth ACs.**
- Hosting surfaces: full-page `renderChat` (`ui/learn.html:1272-1292`), the
  pull-up sheet `openNavigator` (`ui/learn.html:1246-1267`), and the in-call Ask
  sheet `openAsk` (`ui/learn.html:2766+`). All three mount the same widget.

### Speech input in the chat — PARTIAL (browser Web Speech API only)

- `mountChat` has a mic button (`.micb`, `ui/learn.html:1160-1161`) wired to the
  **browser-native** `window.SpeechRecognition || window.webkitSpeechRecognition`
  (`ui/learn.html:1240-1251`). On result it fills the input and, on `onend`,
  auto-submits if non-empty (`ui/learn.html:1246-1247`) — already a
  "record → transcribe → send" gesture.
- Limits: Web Speech API is **Chrome/Edge-only** (Firefox/Safari show the toast
  "Voice input isn't available in this browser", `ui/learn.html:1243`), uses
  Google/Apple's recognizer rather than the configured Azure resource, and is
  inconsistent with the roleplay call which uses Azure. This is a working but
  second-class STT; M05 should standardise on the Azure seam (see §6) while
  keeping Web Speech API as a no-token fallback.

### Speech output in the chat — MISSING

- There is **no TTS in `mountChat`**. AI answers are text only. Nothing
  synthesizes a chat answer to audio. This is the single biggest new piece of
  user-visible work.

### Browser Azure Speech SDK seam (reusable) — DONE, but only for the roleplay call

The roleplay call flow contains a full, production-quality browser Speech SDK
integration that M05 will lift into the chat:

- **SDK loader** `loadSpeechSdk()` (`ui/learn.html:2522-2532`) — lazily injects
  `https://aka.ms/csspeech/jsbrowserpackageraw`, caching `window.SpeechSDK`.
- **Token fetch** `ensureSpeechToken()` (`ui/learn.html:2534-2540`) — `POST
  /v1/speech/token` with `{ profile_id }`, caches `{ token, region }`.
- **TTS** `getSynthesizer(voiceName)` (`ui/learn.html:2549-2566`) +
  `speak(text, voiceName, ssml)` (`ui/learn.html:2570-2593`). Builds a
  `SpeechConfig.fromAuthorizationToken(token, region)`, sets
  `speechSynthesisVoiceName`, routes to a `SpeakerAudioDestination` so
  pause/resume/stop work (`pauseAudio`/`resumeAudio`/`stopAudio`,
  `ui/learn.html:2595-2602`), and `speak()` resolves only after playback
  finishes.
- **STT** `ensureRecognizer()` (`ui/learn.html:2713-2736`) + `startListening()`
  (`ui/learn.html:2738-2746`). Builds a `SpeechRecognizer` from
  `AudioConfig.fromDefaultMicrophoneInput()`, language `en-US` by default
  (`ui/learn.html:2718`). Note: the roleplay uses **continuous** recognition
  (`startContinuousRecognitionAsync`) because a call is a running conversation;
  M05's chat wants a **single utterance** (`recognizeOnceAsync`), matching the
  story's "simple record → transcribe" and the Out-of-Scope "no continuous
  listening".

### Backend token endpoint — DONE

- `POST /v1/speech/token` (`src/rag_layer/server.py:1094-1103`), request model
  `SpeechTokenRequest { profile_id: str = "" }` (`server.py:920-921`), delegates
  to `Roleplay.speech_token(profile_id)` (`roleplay.py:1220-1252`). It mints a
  short-lived Azure STS token via `POST {endpoint}/sts/v1.0/issueToken`
  (`roleplay.py:1240-1245`) and returns `{ "token", "region" }`, plus
  `"profile"` **only when a roleplay `profile_id` is supplied**
  (`roleplay.py:1249-1251`). With an **empty** `profile_id` (what the chat would
  send) it uses the top-level settings credentials (`roleplay.py:1232-1235`) and
  returns **no voice name**. 401 → `PermissionError` → HTTP 502
  (`roleplay.py:1246-1247`, `server.py:1100-1101`).
- `GET /v1/speech/profiles` (`server.py:1106-1108` → `voice_profiles`,
  `roleplay.py:1254-1274`) returns the **roleplay** voice registry (gender×age
  DragonHD customer personas, `src/rag_layer/voice_profiles.py`). This is for
  Prepare-tab customers, not the Navigator's own voice; M05 does not need it.

### Server-side TTS for Learn mixes — DONE, but wrong tool for chat

- `src/rag_layer/speech.py` (`AzureSpeechClient`) renders **audio mixes** and
  **article narration** to **one MP3 file** over Azure's REST synthesis endpoint
  (`speech.py:97-171`), batching long scripts (`_TURNS_PER_REQUEST = 8`,
  `speech.py:32`). Used by Learn's `render-audio` route (`server.py:796-826`) and
  played back as a file via `/v1/learn/mixes/{id}/audio` in `renderAudio`
  (`ui/learn.html:1891-1969`) / `renderArticle` (`ui/learn.html:1971-2057`).
- This is **whole-document, high-latency, file-based** synthesis (cached MP3 per
  mix). It is **not** suited to a conversational chat answer that should start
  speaking promptly. M05 should use the **browser SDK** streaming synthesizer
  (the roleplay seam), not this module — though §10 notes a server-blob fallback
  that could reuse `AzureSpeechClient`.

### Config — DONE (Azure Speech resource)

- `Settings` fields `azure_speech_key`, `azure_speech_region`,
  `azure_speech_endpoint`, `azure_speech_voice_ava`, `azure_speech_voice_andrew`
  (`src/rag_layer/config.py:42-46`), loaded in `load_settings`
  (`config.py:135-139`). Defaults confirm the memory fact: region **`eastus`**
  (`config.py:136`), endpoint `https://eastus.api.cognitive.microsoft.com/`
  (`config.py:137`), voices **`en-US-Ava:DragonHDLatestNeural`** and
  **`en-US-Andrew:DragonHDLatestNeural`** (`config.py:138-139`) — DragonHD
  Ava/Andrew on a **separate `eastus` Speech resource**, distinct from the
  westus3 NLG-High Azure OpenAI used for chat/embeddings (`config.py:38-41`).
- `speech_configured(settings)` is `bool(key and region)` (`speech.py:46-47`),
  surfaced to the browser as `speech_configured` in `GET /v1/learn/status`
  (`server.py:664`) and `GET /v1/roleplay/status` (`server.py:943`).
- **Gap:** the default English voice name (`azure_speech_voice_ava`) is **not
  exposed to the browser anywhere** — only `speech_configured` is. M05 needs the
  voice name on the client to call `getSynthesizer(voiceName)`. See §6.1.

### Verified behavior (code audit — 2026-10-02)

- Typed question → grounded text answer: shipped (`mountChat` + `navSend`).
- Chat voice input: present but browser-native Web Speech API, Chrome/Edge-only
  (`ui/learn.html:1240-1251`).
- Chat speech output: **absent** — no synthesizer call anywhere in `mountChat`.
- Mode selector (text / speech-in-speech-out / speech-in-text-out): **absent**.
- Azure SDK STT+TTS + `/v1/speech/token`: fully working in the **roleplay call**
  only (`ui/learn.html:2517-2746`, `roleplay.py:1220-1252`).

## 4. Scope

**In scope**

- A **mode selector** in the Ask Navigator chat letting the user choose: **Text
  In → Text Out** (default, current behavior), **Speech In → Speech Out**, and
  **Speech In → Text Out**. Persisted in `localStorage` via `store`.
- **Speech In** standardised on the **browser Azure Speech SDK**
  (`recognizeOnceAsync`, single utterance), minted via the existing
  `POST /v1/speech/token` with an empty `profile_id`. The recognised text is fed
  into the **existing** `send()` → `navSend` → `/v1/foundry/chat` path unchanged,
  so grounding/conversation/follow-ups are identical to typed input.
- **Speech Out**: when the selected mode has speech output, synthesize the AI
  answer's plain text with the browser SDK synthesizer (reusing
  `getSynthesizer`/`speak`) using the default English voice, with a visible
  stop/replay control.
- A small **backend addition** so the chat knows which voice to speak: expose the
  default English voice name to the browser (recommended: add `voice` to the
  `/v1/speech/token` response for empty `profile_id`; see §6.1).
- Graceful fallback when Azure Speech is unconfigured or the SDK/mic is
  unavailable (fall back to the existing Web Speech API for input; disable speech
  output with an explanatory note), mirroring the roleplay's text-only fallback
  (`ui/learn.html:2687-2708`).

**Out of scope** — see §9.

## 5. Functional Requirements

- **FR1 — Text In → Text Out (unchanged).** The user can type a question and read
  a text answer exactly as today (`mountChat` + `navSend`). No regression.
- **FR2 — Mode selector.** A control in the chat foot lets the user pick one of
  three modes: `text` (default), `speech_speech`, `speech_text`. The choice
  persists across turns and reloads (`store.set`), and visibly reflects the
  active mode.
- **FR3 — Speech In (record → transcribe).** In either speech-in mode, tapping
  the mic records a **single utterance**, transcribes it to text via the Azure
  Speech SDK (`recognizeOnceAsync`) on a token from `POST /v1/speech/token`, and
  places the transcript where a typed question would go.
- **FR4 — Same flow as typed.** The transcript is submitted through the **same**
  `send()`/`navSend` path as typed input (`ui/learn.html:1186-1195`,
  `1046-1063`). Voice turns therefore get identical Foundry grounding, citations,
  history replay, and follow-up behavior — no separate voice backend.
- **FR5 — Text Out.** In `text` and `speech_text` modes, the answer is displayed
  as text exactly as today. (In `speech_speech` the text is still displayed *and*
  spoken — the transcript/answer stays on screen.)
- **FR6 — Speech Out.** In `speech_speech` mode, when an answer arrives, its
  plain-text form (Markdown and `[n]` citation markers stripped) is synthesized
  and played through the browser SDK synthesizer using the default English voice.
- **FR7 — English TTS is sufficient.** Use the configured default English
  DragonHD voice (`azure_speech_voice_ava`); no voice picker, styles, or SSML
  tuning (per story Out of Scope).
- **FR8 — Playback control.** The user can **stop** in-progress speech output
  (reuse `stopAudio`, `ui/learn.html:2597-2602`) and optionally **replay** an
  answer's audio. Starting a new turn stops any current playback.
- **FR9 — Fallbacks.** If Azure Speech is unconfigured (`speech_configured ===
  false`) or the SDK/token/mic fails: speech **input** falls back to the existing
  Web Speech API (`ui/learn.html:1240-1251`) where the browser supports it; speech
  **output** is unavailable and the UI shows a short note and reverts to a
  text-out mode. No crash, no blocked typing.
- **FR10 — Barge-in is NOT required.** Record and playback are sequential: the
  mic is not listening while the answer is being spoken (consistent with the
  story's "simple record → transcribe → respond → play" and Out-of-Scope
  "continuous / interruption"). A manual stop is enough.
- **FR11 — All three chat surfaces.** The modes work in the full-page chat
  (`renderChat`) and the pull-up sheet (`openNavigator`); both mount the same
  `mountChat`, so the feature is implemented once in the widget.

## 6. Technical Design

The design is "reuse the roleplay speech seam inside `mountChat`." Almost all
work is in `ui/learn.html`; backend change is one field.

### 6.1 Backend — expose the default chat voice

The chat needs a voice name for `getSynthesizer`. Today `/v1/speech/token`
returns a voice only for a roleplay `profile_id` (`roleplay.py:1249-1251`).
**Recommended:** have `Roleplay.speech_token("")` also return the default voice
when no profile is requested:

```python
# src/rag_layer/roleplay.py  — in speech_token, the no-profile branch (~1232-1235)
else:
    key = self.settings.azure_speech_key
    region = self.settings.azure_speech_region
    endpoint = self.settings.azure_speech_endpoint or f"https://{region}.api.cognitive.microsoft.com"
    default_voice = self.settings.azure_speech_voice_ava   # basic English TTS (FR7)
...
result: dict[str, Any] = {"token": response.text, "region": region}
if profile is not None:
    result["profile"] = profile.as_public_dict()
else:
    result["voice"] = default_voice        # NEW: chat speaks with this
return result
```

No new endpoint, no model change (the response is an untyped `dict`). The two
existing voices (`azure_speech_voice_ava`/`andrew`, `config.py:45-46,138-139`)
are reused as-is.

*Alternative (if a token response change is undesirable):* add `"voice":
settings.azure_speech_voice_ava` to `GET /v1/learn/status` (`server.py:661-669`)
next to `speech_configured`, and read it client-side at boot. Either is ~2 lines;
the token-response option keeps the voice next to the credential it is used with.

No change to the grounding/chat path — `/v1/foundry/chat`, `foundry.chat`,
retrieval, and history replay are all untouched (that is the whole point: voice
reuses the text path).

### 6.2 Frontend — mode state (`ui/learn.html`)

Add a mode constant and persisted state near the chat prefs block
(`ui/learn.html:1030-1038`, which already uses `store`):

```js
const VOICE_MODE_KEY = "voiceMode";                       // salesdj.voiceMode
const VOICE_MODES = ["text", "speech_speech", "speech_text"];
let voiceMode = VOICE_MODES.includes(store.get(VOICE_MODE_KEY, "text"))
  ? store.get(VOICE_MODE_KEY, "text") : "text";
const setVoiceMode = (m) => { voiceMode = m; store.set(VOICE_MODE_KEY, m); };
const speechIn  = () => voiceMode === "speech_speech" || voiceMode === "speech_text";
const speechOut = () => voiceMode === "speech_speech";
```

### 6.3 Frontend — promote the shared speech seam out of the roleplay closure

The `speech` object and helpers (`loadSpeechSdk`, `ensureSpeechToken`,
`getSynthesizer`, `speak`, `stopAudio`, and a single-utterance recognizer) live
today inside the roleplay section (`ui/learn.html:2517-2746`). They reference
only module-level utilities (`api`, `toast`) and their own `speech` state, so
they can be **used by `mountChat` as-is** — `mountChat` is defined earlier
(`1157`), so either (a) move the `speech` helpers above `mountChat`, or (b) keep
them where they are and have `mountChat` call them (JS function declarations
hoist; the `speech` object is in the same IIFE scope). Prefer (a) for clarity:
lift the `speech` object + `loadSpeechSdk`/`ensureSpeechToken`/`getSynthesizer`/
`speak`/`stopAudio` to a shared "Speech" section above `mountChat`. The roleplay
call keeps using them unchanged.

For the chat, add one **single-utterance** recognizer helper (distinct from the
roleplay's continuous one at `2713-2746`):

```js
// Record one utterance for the chat and return the transcript. Azure SDK path.
async function recognizeOnce() {
  const SDK = await loadSpeechSdk();
  await ensureSpeechToken();                       // POST /v1/speech/token {profile_id:""}
  const cfg = SDK.SpeechConfig.fromAuthorizationToken(speech.token, speech.region);
  cfg.speechRecognitionLanguage = "en-US";
  const rec = new SDK.SpeechRecognizer(cfg, SDK.AudioConfig.fromDefaultMicrophoneInput());
  try {
    const text = await new Promise((resolve, reject) =>
      rec.recognizeOnceAsync(r =>
        resolve(r && r.reason === SDK.ResultReason.RecognizedSpeech ? (r.text || "").trim() : ""),
        err => reject(new Error(String(err || "recognition failed")))));
    return text;
  } finally { try { rec.close(); } catch {} }
}
```

`ensureSpeechToken()` posts `{ profile_id: "" }` (empty), so it hits the
top-level-credentials branch and (after §6.1) also receives the default `voice`.
Store it: `if (data.voice) speech.chatVoice = data.voice;` in `ensureSpeechToken`
(`ui/learn.html:2534-2540`).

### 6.4 Frontend — wire the modes into `mountChat`

**Mode selector (FR2).** Add a small segmented control to the chat foot markup
(`ui/learn.html:1159-1160`), e.g. three pill buttons "Text / Voice / Voice→Text"
styled like the existing `.chip`s. On click, `setVoiceMode(m)` and repaint the
control's active state. Hide/disable the two speech options when
`learn_status.speech_configured === false` **and** the Web Speech API is absent
(input) — but keep "Speech In → Text Out" available whenever *either* STT path
works.

**Speech In (FR3/FR4).** Replace the mic handler (`ui/learn.html:1242-1251`) so
it prefers the Azure path and falls back to Web Speech API:

```js
const mic = async () => {
  if (!speechIn()) { /* keep current Web Speech API behavior for text mode's mic */ }
  if (statusSpeechConfigured) {
    micb.classList.add("on");
    try { const t = await recognizeOnce(); if (t) { input.value = t; submit(); } }
    catch (e) { toast(/permission|NotAllowed|microphone/i.test(e.message)
      ? "Microphone blocked — you can still type." : "Couldn't hear that. Try again."); }
    finally { micb.classList.remove("on"); }
  } else if (window.SpeechRecognition || window.webkitSpeechRecognition) {
    /* existing Web Speech API fallback, ui/learn.html:1245-1249 */
  } else { toast("Voice input isn't available in this browser"); }
};
```

Crucially, `submit()`/`send(q)` is the **unchanged** existing path
(`ui/learn.html:1186-1195`), so the transcribed question goes through `navSend`
and gets identical grounding (FR4). No new request shape.

**Speech Out (FR6/FR8).** In `send()` (`ui/learn.html:1186-1195`), after the
pending message resolves and `paint()` runs, if `speechOut()` and the turn
succeeded, speak the answer:

```js
// after: pending.pending = false; paint(); paintChips(); save();
if (speechOut() && !pending.failed && pending.text) {
  stopAudio();                                   // barge-out any prior playback (FR8/FR10)
  try { await loadSpeechSdk(); await ensureSpeechToken();
        await speak(plainForSpeech(pending.text), speech.chatVoice || DEFAULT_VOICE); }
  catch { toast("Couldn't play the spoken answer."); }
}
```

`plainForSpeech(md)` strips Markdown and `[n]` citation markers to feed the TTS
clean prose — a small helper (reuse the inverse of `mdToHtml`; simplest is a
regex strip of `[#*_`>]`, link syntax, and `\[\d+\]`). Each AI row in
`speech_speech` mode also gets a **Stop / Replay** affordance in the tools row
(`ui/learn.html:1171-1173`): Stop → `stopAudio()`; Replay → re-run the `speak`
block for that message's text.

**Teardown.** `mountChat`'s returned `destroy()` (`ui/learn.html:1255`) should
also `stopAudio()` so navigating away silences playback.

### 6.5 Data flow summary

```
Speech In  → recognizeOnce() [Azure SDK, token via POST /v1/speech/token]
           → transcript → input → send(q) → navSend → POST /v1/foundry/chat
           → {text, citations, ...}  [SAME grounding as typed]
Text Out   → mdToHtml(answer) rendered in the bubble (unchanged)
Speech Out → speak(plainForSpeech(answer), defaultVoice) [Azure SDK synthesizer]
```

The only backend call voice adds beyond the existing chat is
`POST /v1/speech/token` (token mint, already built). The Foundry chat round-trip
is byte-for-byte the typed one.

## 7. Acceptance Criteria (Given/When/Then)

- **AC1 (type a question — story "submit using typed text", "Text In → Text
  Out").** *Given* the chat in `text` mode, *When* the user types and sends,
  *Then* a grounded text answer renders — unchanged current behavior.
- **AC2 (speak a question — story "submit using spoken English").** *Given* a
  speech-in mode and a configured mic, *When* the user taps the mic and speaks
  one utterance, *Then* `recognizeOnce()` returns a transcript and it appears as
  the question.
- **AC3 (spoken input → same flow — story "converted into text processed by the
  same flow").** *Given* a transcript from AC2, *When* it is submitted, *Then* it
  is sent through the same `send`/`navSend`/`POST /v1/foundry/chat` path as typed
  input (no separate voice endpoint), and the answer is grounded with citations.
- **AC4 (text answer — story "response as displayed text").** *Given* any mode
  with text output (`text`, `speech_text`), *When* an answer arrives, *Then* it
  is displayed as text.
- **AC5 (spoken answer — story "response as synthesized speech").** *Given*
  `speech_speech` mode, *When* an answer arrives, *Then* its plain text is
  synthesized and played via the Azure SDK synthesizer in English.
- **AC6 (Text In → Text Out).** Covered by AC1.
- **AC7 (Speech In → Speech Out).** *Given* `speech_speech`, *When* the user
  speaks a question, *Then* they both see **and** hear the answer.
- **AC8 (Speech In → Text Out).** *Given* `speech_text`, *When* the user speaks a
  question, *Then* they see the answer as text and **no** audio plays.
- **AC9 (mode selection — story "user can select the mode").** *Given* the chat,
  *When* the user picks a mode in the selector, *Then* subsequent turns use it and
  the choice persists across reloads (`localStorage`).
- **AC10 (same grounding/conversation — story "same FlexLife knowledge grounding
  and conversational behavior").** *Given* a voice turn and an equivalent typed
  turn, *When* both are processed, *Then* they hit the identical Foundry path,
  share the same history replay for follow-ups, and return the same citations.
- **AC11 (record → transcribe → respond → play — story "simple … is
  sufficient").** *Given* `speech_speech`, *When* the user completes one speak →
  answer → playback cycle, *Then* the mic is not listening during playback
  (sequential, no continuous listening); a new turn or Stop halts playback.
- **AC12 (fallback / no crash).** *Given* Azure Speech unconfigured or mic
  blocked, *When* the user tries voice, *Then* speech input falls back to the Web
  Speech API where available (or a clear toast otherwise), speech output is
  disabled with a note, and typing still works.

## 8. Test Plan

Voice is inherently browser/mic-bound and the repo's UI is verified via **jsdom
without a real browser** (per the dev-loop memory note), so the plan leans on
**mockable seams + manual smoke**, plus backend unit coverage.

**Backend (pytest + FastAPI `TestClient`, following `tests/test_m09_handoff.py`
patterns):**

- `Roleplay.speech_token("")` returns `{token, region, voice}` with `voice ==
  settings.azure_speech_voice_ava`, and still returns `profile` (not `voice`)
  when a `profile_id` is passed. Monkeypatch `requests.post` to return a fake
  token string (no live Azure call).
- `POST /v1/speech/token` with `{"profile_id": ""}` → 200 and includes `voice`;
  a 401 from the STS mock → `PermissionError` → HTTP 502 (unchanged,
  `server.py:1100-1101`).
- Regression: `GET /v1/learn/status` / `/v1/roleplay/status` still report
  `speech_configured` correctly for key-present and key-absent settings.

**Frontend (jsdom, no browser — assert wiring, not audio):**

- Design the speech seam so STT/TTS are **injectable**: `recognizeOnce` and
  `speak` resolve via `window.SpeechSDK`, which jsdom tests replace with a stub
  (`window.SpeechSDK = { ... }`) and `api` is stubbed to return a fake token.
- `setVoiceMode` persists to `store`/`localStorage` and `speechIn()/speechOut()`
  derive correctly for all three modes.
- In a speech-in mode with a stubbed `recognizeOnce` returning "what is the
  cap?", tapping the mic calls `send("what is the cap?")`, which calls the
  stubbed `navSend`/`api("/v1/foundry/chat", …)` — proving voice reuses the text
  path (AC3/AC10). Assert the request body matches the typed-path body.
- In `speech_speech`, after a stubbed successful answer, `speak` (stubbed) is
  invoked once with the Markdown-stripped text; in `speech_text` and `text`,
  `speak` is **not** called (AC5/AC7/AC8).
- `plainForSpeech("**Caps** work like `[1]`")` strips Markdown and `[1]` markers.
- Fallback: with `window.SpeechSDK` absent and no Web Speech API, the mic shows
  the unavailable toast and typing still submits (AC12).

**Manual smoke (rebuild the Docker image per the dev loop, open
`/app/learn.html#/chat` in Chrome/Edge on a mic-equipped machine):**

- Pick each mode; confirm: type→read (text), speak→see+hear (speech_speech),
  speak→see only (speech_text).
- Confirm a spoken follow-up ("and the floor?") resolves via history replay
  (same as typed), proving AC10.
- Stop playback mid-answer; start a new turn and confirm prior audio stops.
- Set `AZURE_SPEECH_KEY` empty, rebuild, confirm graceful fallback/notes and that
  typing is unaffected.

## 9. Out of Scope

- Continuous / always-listening conversation and real-time barge-in or
  turn-taking (the chat is sequential record → respond → play; the roleplay
  call's continuous recognizer is **not** reused for chat).
- Languages other than English (the DragonHD chat voice is English-only; STT
  locale fixed to `en-US`).
- Voice selection UI, cloning, emotion/style controls, SSML prosody tuning
  (basic default English voice only).
- Production-grade STT tuning, custom speech models, or accessibility
  optimization.
- Server-side conversation/audio storage; no recording is persisted.
- Changing the Learn-tab audio-mix pipeline (`speech.py`, `render-audio`) or the
  roleplay call flow — both remain as-is.
- Any change to the chat grounding/retrieval path (`/v1/foundry/chat`,
  `foundry.chat`) — voice explicitly reuses it unchanged.

## 10. Dependencies & Open Questions

**Dependencies**

- **Azure Speech resource** must be configured for the Azure STT/TTS path:
  `AZURE_SPEECH_KEY` + `AZURE_SPEECH_REGION` (`config.py:135-136`;
  `speech_configured`, `speech.py:46-47`). This is the separate **`eastus`**
  DragonHD resource (`config.py:136-139`), not the westus3 Azure OpenAI used for
  chat. Without it, STT degrades to the Web Speech API and TTS is unavailable
  (FR9).
- **`POST /v1/speech/token`** (`server.py:1094-1103`, `roleplay.py:1220-1252`) —
  already built; M05 adds one `voice` field to its no-profile response (§6.1).
- **Browser Speech SDK** loaded from `https://aka.ms/csspeech/jsbrowserpackageraw`
  (`ui/learn.html:2526`) — external CDN; already relied on by the roleplay call.
- **The Foundry chat path** (`navSend` → `/v1/foundry/chat`) provides the
  grounded answers voice wraps; unchanged. (M02 domain-gate / M03 citations ride
  along for free since voice reuses the same response.)
- Docker image rebuild to ship the backend `voice` field (per the dev-loop
  memory note).

**Open questions**

1. **STT path for the chat: Azure SDK vs. the existing Web Speech API.** The
   roleplay-proven Azure SDK path is cross-browser (any browser that runs the
   JS SDK + mic), uses the configured resource, and is consistent with the rest
   of the app — but costs a token round-trip and an SDK download. The current
   chat Web Speech API path (`ui/learn.html:1240-1251`) is zero-infra but
   Chrome/Edge-only and uses the browser vendor's recognizer. **Recommendation:**
   prefer Azure SDK when `speech_configured`, fall back to Web Speech API
   otherwise. Confirm this is acceptable (vs. keeping Web Speech API as primary
   to avoid the token/SDK cost).
2. **Which default voice, and should it be user-pickable?** The story says a
   basic English voice is sufficient and Out-of-Scope forbids a voice picker, so
   the recommendation is a single fixed voice (`azure_speech_voice_ava`,
   `config.py:138`). Confirm Ava vs. Andrew for the Navigator, and confirm no
   picker is wanted for the POC. (Also confirm where to surface the voice name —
   `/v1/speech/token` response vs. `/v1/learn/status`; §6.1.)
3. **TTS mechanism: browser SDK streaming vs. server MP3 blob.** Recommendation
   is the browser SDK synthesizer (low latency, reuses the roleplay `speak`
   seam). A server-side option could reuse `AzureSpeechClient` (`speech.py`) to
   return an answer MP3, which works in browsers where the SDK can't synthesize,
   but is higher-latency (whole-answer batch). Keep the server blob only as a
   documented fallback, or skip it for the POC?
4. **Markdown → speech cleanup fidelity.** Answers are Markdown with `[n]`
   citation markers; `plainForSpeech` must strip these so the voice doesn't read
   "open bracket one close bracket". A simple regex strip is proposed; confirm no
   richer SSML handling is wanted (Out of Scope says no).
5. **Token lifetime.** Azure STS tokens are short-lived (~10 min). The roleplay
   re-mints on cancel (`ui/learn.html:2731`); the chat should re-mint on
   synth/recognize failure (clear `speech.token` and retry once). Acceptable for
   a POC?

## 11. Rough Effort Estimate

| Area | Work | Est. |
| --- | --- | --- |
| Backend | `voice` field on `/v1/speech/token` no-profile response (§6.1) | ~0.25 day |
| Frontend | Lift `speech` seam above `mountChat`; add `recognizeOnce` single-utterance helper | ~0.5 day |
| Frontend | Mode selector UI + persisted `voiceMode` state (§6.2, §6.4) | ~0.5 day |
| Frontend | Speech-in wiring (Azure + Web Speech fallback) into the mic handler | ~0.5 day |
| Frontend | Speech-out in `send()` + Stop/Replay + `plainForSpeech` + teardown | ~0.75 day |
| Frontend | Fallbacks / unconfigured + no-mic handling, toasts | ~0.25 day |
| Tests | backend pytest (token `voice`) + jsdom seam-mock tests | ~0.5 day |
| Manual | Docker rebuild + mic smoke across the three modes | ~0.5 day |

**Total: ~3.75 days.** The load-bearing reason it is not larger: the Azure STT/TTS
SDK integration and the token endpoint already exist (proven in the roleplay
call), and the grounding/chat path is reused verbatim — M05 is mostly re-pointing
and a mode selector, not new speech infrastructure.
