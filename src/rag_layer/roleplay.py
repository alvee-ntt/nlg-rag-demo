"""Prepare tab: a spoken roleplay call against an AI prospect, with live coaching and an
end-of-call report.

Ported from the standalone voice demo so the whole salesDJ app runs as one process.
What changed in the move:

- Retrieval and fact-checking call ``service.search`` / ``service.fact_check`` directly
  instead of going over HTTP to this same API.
- Custom scenarios (personas generated from an agent's description) are stored in
  Postgres (``roleplay_personas``) rather than on the container filesystem.
- A finished call (transcript, outcome, coaching report) is written to
  ``roleplay_sessions`` when its feedback is generated, which is what powers the
  history list and the streak/minutes stats. In-flight sessions stay in memory: the
  voice turn is latency-bound and the deployment is a single replica.

Speech never touches this process's audio path. The browser transcribes the agent's
speech and speaks the customer's reply with the Azure Speech SDK on a short-lived
token minted here; the server only exchanges text.
"""

from __future__ import annotations

import json
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from .config import Settings
from .db import (
    connect,
    delete_roleplay_persona,
    insert_roleplay_session,
    list_roleplay_personas,
    upsert_roleplay_persona,
)
from .embeddings import AzureOpenAIClient
from .gender import infer_gender, persona_gender
from .profile import language, normalize_feedback_scores
from .service import fact_check as rag_fact_check
from .service import search as rag_search
from .voice_profiles import VoiceProfile, VoiceRegistry, load_registry

DATA_ROOT = Path(__file__).resolve().parent / "roleplay_data"
PERSONA_ROOT = DATA_ROOT / "personas"

# Azure's multilingual voices pick up the language from the text itself; the curated
# DragonHD voices in voice_profiles.json are English-only.
MULTILINGUAL_VOICES = {"female": "en-US-AvaMultilingualNeural", "male": "en-US-AndrewMultilingualNeural"}


def multilingual_voice(gender: str) -> str:
    return MULTILINGUAL_VOICES["male" if (gender or "").lower().startswith("m") else "female"]


def language_info(code: str | None) -> dict[str, str]:
    return language(code)


def _read(path: Path, fallback: str = "") -> str:
    return path.read_text(encoding="utf-8") if path.exists() else fallback


# sales_playbook.md answers "what should the agent do next" (live coaching and Ask
# Navigator). feedback_playbook.md is the same process rewritten as grading criteria
# for the end-of-session review. persona_playbook.md governs how a prospect behaves
# and is injected into the spoken reply prompt; every persona is written against it.
SALES_PLAYBOOK = _read(DATA_ROOT / "sales_playbook.md")
FEEDBACK_PLAYBOOK = _read(DATA_ROOT / "feedback_playbook.md", SALES_PLAYBOOK)
PERSONA_PLAYBOOK = _read(PERSONA_ROOT / "persona_playbook.md")


# ----------------------------- latency logging -----------------------------
# Every measured phase prints one greppable line on stdout:
#     [lat] sess=88cce9ba turn=3 server.llm.customer_reply=1840ms ...

SLOW_MS = 1500


def _now_iso() -> str:
    epoch = time.time()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(epoch))
    return f"{stamp}.{int((epoch % 1) * 1000):03d}"


def _log(message: str) -> None:
    print(f"[{_now_iso()}] [roleplay] {message}", flush=True)


@dataclass
class Lat:
    """Latency context for one user-visible operation. `kind` separates the sources
    that show up interleaved in one log: `server` (the blocking voice path), `bg`
    (background verification), `client` (browser-reported STT/TTS), `ask`, `feedback`."""

    sess: str = "-"
    turn: int = 0
    kind: str = "server"
    phases: dict[str, float] = field(default_factory=dict)

    def log(self, phase: str, ms: float, note: str = "") -> None:
        self.phases[phase] = ms
        flag = "  <-- SLOW" if ms >= SLOW_MS else ""
        suffix = f"  {note}" if note else ""
        print(
            f"[{_now_iso()}] [lat] sess={self.sess} turn={self.turn} "
            f"{self.kind}.{phase}={ms:.0f}ms{suffix}{flag}",
            flush=True,
        )

    def span(self, phase: str, note: str = ""):
        return _Span(self, phase, note)


class _Span:
    def __init__(self, lat: Lat, phase: str, note: str) -> None:
        self.lat, self.phase, self.note = lat, phase, note

    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        extra = (self.note + " FAILED").strip() if exc_type else self.note
        self.lat.log(self.phase, (time.perf_counter() - self.start) * 1000.0, extra)
        return False


def transcript_entry(speaker: str, text: str, turn: int) -> dict[str, Any]:
    return {"speaker": speaker, "text": text, "turn": turn, "epoch": time.time(), "ts": _now_iso()}


@dataclass
class SessionState:
    session_id: str
    persona: dict[str, Any]
    custom_notes: str = ""
    turns: list[dict[str, Any]] = field(default_factory=list)
    verification_events: list[dict[str, Any]] = field(default_factory=list)
    turn_seq: int = 0
    timings: dict[int, dict[str, float]] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    voice_profile_id: str = ""
    # Practice & roleplay language (profile setting); "en" unless the agent changed it.
    language: str = "en"
    ended: bool = False
    # Which of the playbook's four endings the prospect took, once they have taken one.
    outcome: str = ""
    # Set once the coaching report has been produced and stored.
    history_id: int | None = None
    # Guards turns / verification_events against the background verification threads.
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)


# ------------------------------- personas -------------------------------


def load_builtin_personas() -> dict[str, dict[str, Any]]:
    personas: dict[str, dict[str, Any]] = {}
    for path in sorted(PERSONA_ROOT.glob("*.json")):
        persona = json.loads(path.read_text(encoding="utf-8"))
        persona.pop("duration", None)
        persona["custom"] = False
        personas[persona["id"]] = persona
    return personas


def resolve_voice_profile(registry: VoiceRegistry, persona: dict[str, Any]) -> VoiceProfile | None:
    """The speech pattern for a persona: an explicit voice_profile wins; otherwise one is
    selected from gender and age, with voice_variant picking the second option."""
    explicit = str(persona.get("voice_profile") or "").strip()
    if explicit:
        profile = registry.by_id(explicit)
        if profile is not None:
            return profile
        _log(f"Unknown voice_profile {explicit!r} on persona {persona.get('id')}; "
             "falling back to gender/age selection")
    facts = persona.get("fixed_facts") or {}
    try:
        variant = int(persona.get("voice_variant", 0))
    except (TypeError, ValueError):
        variant = 0
    gender, reason = persona_gender(persona)
    if not persona.get("gender"):
        _log(f"Persona {persona.get('id')} has no gender; inferred {gender} ({reason})")
    return registry.select(gender, facts.get("age"), variant)


# Fields that describe how the run should be *judged* rather than who the character is.
# The prospect must not see these; coaching and feedback do get them.
REPLY_EXCLUDED_FIELDS = (
    "evaluation", "practicing", "generation", "custom", "blurb", "scenario_title",
    "difficulty", "voice_name", "voice_env", "voice_profile", "voice_variant",
    "provenance", "source_notes",
)


def persona_for_reply(persona: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in persona.items() if k not in REPLY_EXCLUDED_FIELDS}


# ------------------------------- scene modes -------------------------------
# A persona's `mode` decides where the call starts.
#   callback      the original voice-demo scene: a lead-form callback, the agent opens
#                 cold and the prospect works an objection ladder (the persona playbook).
#   presentation  ported from the nlg-roleplay chat app: the fact-find is finished, the
#                 agent has just turned toward how FlexLife applies, and the prospect
#                 reacts from known_background + hidden_customer_state.
#   discovery     also from nlg-roleplay: the fact-find is finished and the agent turns
#                 to what happens if the prospect ever needed care. The prospect reveals
#                 their situation one layer at a time (disclosure_ladder), and only when
#                 the agent's question or empathy earns it.

MODE_CALLBACK = "callback"
MODE_PRESENTATION = "presentation"
MODE_DISCOVERY = "discovery"
SCENE_MODES = (MODE_PRESENTATION, MODE_DISCOVERY)


def persona_mode(persona: dict[str, Any]) -> str:
    mode = str(persona.get("mode") or "").strip().lower()
    return mode if mode in SCENE_MODES else MODE_CALLBACK


PRESENTATION_SCENE = """SCENE - you are already mid-conversation. The fact-find is finished, which is why the agent knows everything under KNOWN BACKGROUND, and they have just turned the conversation toward FlexLife and how it would apply to your situation. Behave accordingly:
- No greetings, no small talk, no "good to see you," no asking what they have prepared. You are several minutes in and the recommendation is already on the table between you.
- Do not re-introduce yourself or re-explain your situation. You already walked them through all of it and you expect them to use it.
- If they ask discovery questions you already answered, or describe FlexLife in generic terms that could apply to anyone, say so: politely the first time, less patiently after that.
- What you are trying to get out of this stretch of the conversation is whether this actually fits your family: what it covers that you do not already have, why that amount, and what it does to your monthly cash flow."""

PRESENTATION_BEHAVIOR = """HOW TO USE THE HIDDEN DETAILS:
- Raise the likely objection early if the agent has not already handled it, in your own words rather than verbatim.
- Warm up and lean in when the agent hits the positive signal.
- Cool off, get guarded and slow the conversation down if the agent does the trust-breaking thing. Do not reward it.
- Do not agree to anything until the decision requirement has actually been met."""

DISCOVERY_SCENE = """SCENE - you are already mid-conversation. The fact-find is finished and you have talked through protecting your family and keeping your retirement on track. The agent has just turned toward one more area: what happens if YOU ever needed care yourself. Nothing has been pitched. Behave accordingly:
- No greetings, no small talk. You are several minutes in.
- This is not something you have thought much about, and you do not see it as a problem yet. Do not arrive already worried about it.
- Answer the question you are actually asked and little more. Do not volunteer deeper worries, do not run ahead to solutions, and do not lay your whole situation out. Let the agent draw it out of you.
- If the agent jumps to recommending something before you have felt why it matters, stay flat and a little unconvinced rather than getting on board."""

DISCOVERY_BEHAVIOR = """HOW YOU OPEN UP - you reveal your situation in layers, and only when the agent earns it. Never hand over a lower layer until their question or their empathy actually reaches for it. Never name a layer or say you are "revealing" anything. Just answer like a person.

{disclosure_ladder}

RULES FOR REVEALING:
- Start at the top layer and stay there. Give a short, slightly unreflective answer to the first care question.
- Only move down a layer when the agent asks a question that genuinely probes it, or shows real empathy. A generic or leading question does not earn the next layer.
- At most one layer per turn. Do not skip ahead, and do not stack two layers into one answer.
- If the agent pitches a product or a rider before you have reached the money and family layers, do not get on board. Say some version of "I'm not sure I really need that" and make them do the work.
- Only warm toward including it once you have said the value back in your own words.

HOW YOU SOUND - plain, spoken, a little in denial early on. Vary your wording; do not reuse a phrase you have already used. Match the REGISTER of these; do not recite them:
{style_anchors}"""


def scene_block(persona: dict[str, Any]) -> str:
    """The scene + behaviour text for a presentation / discovery persona; empty for the
    callback scene, which the persona playbook already describes."""
    mode = persona_mode(persona)
    if mode == MODE_PRESENTATION:
        return f"{PRESENTATION_SCENE}\n\n{PRESENTATION_BEHAVIOR}"
    if mode == MODE_DISCOVERY:
        behavior = DISCOVERY_BEHAVIOR.format(
            disclosure_ladder=str(persona.get("disclosure_ladder") or "").strip() or "(no ladder supplied: reveal gradually, one detail per turn)",
            style_anchors=str(persona.get("style_anchors") or "").strip() or "(no anchors supplied: keep it plain and spoken)",
        )
        return f"{DISCOVERY_SCENE}\n\n{behavior}"
    return ""


def scenario_card(persona: dict[str, Any]) -> dict[str, Any]:
    """The home-screen shape: enough to render a card and start a session."""
    facts = persona.get("fixed_facts") or {}
    return {
        "persona_id": persona["id"],
        "name": persona.get("display_name") or persona["id"].title(),
        "age": facts.get("age"),
        "gender": persona.get("gender", ""),
        "occupation": facts.get("occupation", ""),
        "scenario_title": persona.get("scenario_title", ""),
        "blurb": persona.get("blurb") or persona.get("summary", "")[:160],
        "summary": persona.get("summary", ""),
        "custom": bool(persona.get("custom")),
        "agent_knows": (persona.get("lead_context") or {}).get("agent_knows", ""),
        "practicing": persona.get("practicing") or (persona.get("evaluation") or {}).get("primary_skill", ""),
        "difficulty": persona.get("difficulty", ""),
        "mode": persona_mode(persona),
        "known_background": persona.get("known_background", ""),
    }


def session_progress(session: SessionState) -> dict[str, Any]:
    elapsed = max(0.0, time.time() - session.created_at)
    return {
        "phase": "ended" if session.ended else "active",
        "elapsed_seconds": int(elapsed),
    }


# ------------------------------- model calls -------------------------------

# Set the first time a deployment rejects `temperature` / `reasoning`, so the parameter
# is dropped for the rest of the process instead of costing a wasted round trip per call.
_TEMPERATURE_UNSUPPORTED = False
_REASONING_UNSUPPORTED = False


def _response_text(data: dict[str, Any]) -> str:
    if data.get("output_text"):
        return str(data["output_text"]).strip()
    parts: list[str] = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                parts.append(content["text"])
    return "\n".join(parts).strip()


def _http_error_text(exc: requests.HTTPError) -> str:
    body = ""
    if exc.response is not None:
        try:
            body = exc.response.text or ""
        except Exception:  # noqa: BLE001
            body = ""
    return f"{exc} {body}"


def model_text(
    client: AzureOpenAIClient,
    settings: Settings,
    prompt: str,
    lat: Lat | None = None,
    label: str = "llm",
    reasoning_effort: str = "",
) -> str:
    """Every roleplay model call funnels through here, so timing it once covers the
    customer reply, coaching, feedback, and persona generation."""
    global _TEMPERATURE_UNSUPPORTED, _REASONING_UNSUPPORTED
    payload: dict[str, Any] = {"model": settings.azure_openai_chat_deployment, "input": prompt}
    if not _TEMPERATURE_UNSUPPORTED:
        payload["temperature"] = 0.4
    if reasoning_effort and not _REASONING_UNSUPPORTED:
        payload["reasoning"] = {"effort": reasoning_effort}
    start = time.perf_counter()
    try:
        data = client.post("/responses", payload)
    except requests.HTTPError as exc:
        message = _http_error_text(exc)
        if "reasoning" in message and "reasoning" in payload and not _REASONING_UNSUPPORTED:
            _REASONING_UNSUPPORTED = True
            _log("This deployment rejects the `reasoning` parameter; omitting it from now on.")
            payload.pop("reasoning", None)
            data = client.post("/responses", payload)
        elif "temperature" in message and not _TEMPERATURE_UNSUPPORTED:
            _TEMPERATURE_UNSUPPORTED = True
            _log("This deployment rejects the `temperature` parameter; omitting it from now on.")
            payload.pop("temperature", None)
            data = client.post("/responses", payload)
        else:
            raise
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    if lat is not None:
        usage = data.get("usage") or {}
        note = f"prompt_chars={len(prompt)}"
        for key in ("output_tokens", "completion_tokens"):
            if key in usage:
                note += f" out_tokens={usage[key]}"
                break
        lat.log(label, elapsed_ms, note)
    return _response_text(data)


def parse_json_object(text: str, fallback: dict[str, Any]) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I | re.M)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    fallback["raw"] = text
    return fallback


def recent_transcript(turns: list[dict[str, Any]], limit: int = 12) -> str:
    if not turns:
        return "No prior turns."
    return "\n".join(f"{turn['speaker']}: {turn['text']}" for turn in turns[-limit:])


def source_payload(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "citation": s.get("citation", ""),
            "blob_name": s.get("blob_name", ""),
            "chunk_index": s.get("chunk_index"),
            "page": s.get("page"),
            "zone": s.get("zone", "body"),
            "similarity": float(s.get("similarity", 0.0)),
            "preview": s.get("preview", ""),
        }
        for s in sources
    ]


def context_block(sources: list[dict[str, Any]]) -> str:
    return "\n\n".join(
        f"Source: {s.get('citation', '')}\n{s.get('preview', '')}" for s in sources
    )


VERDICT_MAP = {
    "SUPPORTED": "supported",
    "CONTRADICTED": "unsupported",
    "NOT ADDRESSED": "not_enough_context",
    "UNKNOWN": "not_checked",
}


def _report_summary(report: str) -> str:
    for label in ("Reasoning:", "Evidence:"):
        for line in report.splitlines():
            if line.strip().lower().startswith(label.lower()):
                value = line.split(":", 1)[1].strip()
                if value and value.lower() != "none":
                    return value[:300]
    return ""


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


# How the prospect signals that they have ended the call. It travels inside the reply
# because the reply is the only thing the voice path waits on.
END_CALL_RE = re.compile(r"\[+\s*END[_ ]?CALL\s*(?::\s*([A-Za-z_ ]+?))?\s*\]+", re.I)
VALID_OUTCOMES = ("applied", "second_appointment", "soft_no", "hard_no")
OUTCOME_LABELS = {
    "applied": "Applied on the call",
    "second_appointment": "Second appointment booked",
    "soft_no": "Soft no",
    "hard_no": "Hard no",
    "": "Call did not reach an ending",
}

_DASH_RE = re.compile(r"\s*[‒–—―]+\s*")
_ELLIPSIS_RE = re.compile(r"\s*(?:…|\.\s*\.\s*\.)+")


def speechify(text: str) -> str:
    """Rewrite punctuation that turns into dead air when spoken: dashes and ellipses
    become sentence breaks. Plain hyphens are left alone."""
    out = _ELLIPSIS_RE.sub(". ", text)
    out = _DASH_RE.sub(". ", out)
    out = re.sub(r"([.!?])\s+([a-z])", lambda m: f"{m.group(1)} {m.group(2).upper()}", out)
    out = re.sub(r"\.\s*\.(\s|$)", r".\1", out)
    out = re.sub(r"\s*\n+\s*", " ", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\.\s+([,;:?!])", r"\1", out)
    out = re.sub(r"([,;:?!])\.", r"\1", out)
    return out.strip()


def split_end_marker(text: str) -> tuple[str, str]:
    """Pull the end-of-call marker out of a reply, returning (spoken text, outcome)."""
    outcome = ""
    for match in END_CALL_RE.finditer(text):
        raw = (match.group(1) or "").strip().lower().replace(" ", "_")
        if raw in VALID_OUTCOMES:
            outcome = raw
        elif not outcome:
            outcome = "soft_no"
    cleaned = END_CALL_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, outcome


# ------------------------------- prompts -------------------------------


def generate_customer_reply(
    client: AzureOpenAIClient,
    settings: Settings,
    session: SessionState,
    latest_agent_text: str,
    avoid_text: str = "",
    lat: Lat | None = None,
    label: str = "llm.customer_reply",
) -> str:
    avoid_note = ""
    if avoid_text:
        avoid_note = (
            "\n\nIMPORTANT: Do not repeat your previous reply, which was: "
            f'"{avoid_text}". Say something different that directly responds to the '
            "agent's latest message. If you are unsure what they said, ask them to repeat it."
        )
    mode = persona_mode(session.persona)
    if mode == MODE_CALLBACK:
        opening = """You are roleplaying as an insurance prospect on a callback.

How to play a prospect (the persona playbook - these rules govern your behaviour
unless a rule in your own persona overrides them):
{playbook}

Your persona:
{persona}
"""
        scene_rules = """- Greet only once, at the very beginning of the call. If the recent transcript shows you have already spoken at least once, never begin your reply with "Hi", "Hello", "Hey", or any greeting - answer the agent directly instead.
- If this is the very first exchange and the agent only greets you or makes small talk (no question asked and no topic raised), reply with a brief, natural, neutral greeting and stop. Do not volunteer FlexLife, IUL, product objections, financial details, or any fixed facts.
- Confirm who you are only once. After you have already said you are the person the agent asked for, never restate your name or say "yes, this is <name>" again; just continue the conversation.
- This is a callback, not a cold call: you filled in a lead form some days ago after
  seeing the ad in lead_context. You are not hostile, but you may half-remember doing
  it and need the agent to re-anchor why you raised your hand.
- Reveal fixed_facts only when directly asked or clearly relevant to what the agent
  just said. Everything in hidden_facts you answer accurately if asked directly and
  never volunteer. If the agent never asks, it never comes up - that is the point.
- Your root_goal is the thing you have not said out loud, and possibly have not
  admitted to yourself. Never state it plainly. It surfaces only after the agent has
  earned trust and asked something personal, and even then indirectly.
- Work through objection_ladder in order and do not skip ahead. The surface objection
  is a polite deflection, not the real issue. The real objection comes out only if the
  agent asks a second question instead of immediately rebutting the surface one. If
  the agent answers the surface objection with a script and moves on, stay polite and
  go cold - that is a valid outcome and you should let it happen.
- Stay consistent with fixed_facts and consistency_rules. The consistency rules
  outrank everything else here, including the playbook.
- Let personality.speech_signature shape the wording of every reply.
- Keep the reply short: usually one to three sentences, and most replies well under
  25 words. Go longer only about your own family, job, or a story you want to tell.
- You may end the call early only when one of your consistency_rules explicitly
  requires it - for example a rule that says you end the call if the agent claims
  something is guaranteed. If that happens, say your goodbye in character and put the
  outcome on its own final line, exactly like [[END_CALL:hard_no]]. Never use that
  marker for any other reason: not because the agent is doing badly, not out of
  impatience. By default you stay on the call."""
    else:
        # Presentation / discovery: the scene text replaces the playbook's callback
        # framing (lead form, greeting, re-anchoring). The playbook's speech engine,
        # honesty and emotional-physics rules still apply.
        opening = """You are roleplaying as a life insurance prospect in a sales meeting, for training. Stay in character.

How to play a prospect (the persona playbook). Its speech engine, honesty rules and
emotional physics apply to you. Its callback and lead-form framing does NOT: you did not
just pick up a phone call, you are already several minutes into a meeting, and the SCENE
below replaces that framing wherever the two conflict:
{playbook}

Your persona:
{persona}

KNOWN BACKGROUND - you already told the agent all of this (the known_background field).
HIDDEN DETAILS - these drive your reactions (the hidden_customer_state field). Never
recite or summarise them.

{scene}

WHAT THE AGENT IS TRYING TO DO in this meeting: {objective}
PRODUCT IN SCOPE: FlexLife.
PRODUCT FACTS: you have not been given any. Do not invent mechanics, rates or guarantees.
If the agent claims something about the product you may question it or take it at face
value, but never add product details of your own.
"""
        scene_rules = """- No greetings, ever. If the agent greets you or makes small talk as if the meeting were just starting, treat it as slightly odd: you are several minutes in. Answer briefly and steer back to what was on the table.
- Do not restate your name or re-explain your situation. Everything in known_background has already been said.
- Raise one thing at a time. Do not quote the agent's words back at them, and do not stack multiple worries into one turn.
- Only treat a concern as settled if the agent actually addressed it.
- Let personality.speech_signature shape the wording of every reply.
- Keep the reply to one to three sentences of ordinary spoken language, most replies well under 25 words.
- You may end the meeting early only for one of two reasons: you have genuinely decided to go ahead because your decision requirement was met and the agent asked you to (outcome applied), or trust has broken badly after the trust breaker happened and the agent kept going (outcome hard_no). If so, say your goodbye in character and put the outcome on its own final line, exactly like [[END_CALL:hard_no]]. Never use that marker for any other reason. By default you stay in the meeting."""

    language_note = ""
    lang = language_info(session.language)
    if lang["code"] != "en":
        language_note = (
            f"- This practice call is in {lang['name']}. Speak only {lang['name']}, as a native "
            f"speaker would, including the greeting. Keep names and product names as they are. "
            f"If the agent writes in another language, answer in {lang['name']} anyway.\n"
        )
    prompt = opening.format(
        playbook=PERSONA_PLAYBOOK or "None provided.",
        persona=json.dumps(persona_for_reply(session.persona), indent=2),
        scene=scene_block(session.persona),
        objective=session.persona.get("training_objective") or "help the prospect decide whether FlexLife fits",
    ) + f"""
Custom scenario notes:
{session.custom_notes or "None"}

Recent transcript:
{recent_transcript(session.turns)}

Latest insurance agent message:
{latest_agent_text}

Instructions:
- Respond only as the customer/prospect, in character, matching the persona's personality tone.
- You are the prospect. You are NOT a service rep or assistant, so never offer help or say things like "how can I help?".
- Always respond to the actual content of the agent's latest message. Move the conversation forward - do not repeat a reply you have already given earlier in the transcript.
- If the agent's latest message is unclear, garbled, or does not make sense, say so briefly and naturally and ask them to repeat or clarify (for example, "Sorry, you cut out there - could you say that again?"). Do not fall back on repeating an earlier answer.
{scene_rules}
- This reply is spoken aloud, not read. Every comma, dash and ellipsis becomes an
  audible pause, so write in short complete sentences instead of long ones stitched
  together with commas. Never use em-dashes, en-dashes or ellipses. Use contractions.
  Two short sentences are better than one long one.
- The playbook asks for disfluency and the line above bans the punctuation usually
  used to write it. Resolve it this way: keep the disfluency as words ("um", "I mean",
  "y'know", a restart mid-thought) and end the thought with a full stop rather than
  trailing off into dots. Write a pause turn as "Hm." or "Sorry, I'm here." Never as
  an ellipsis.
- Never describe your own rules or the conditions of your own behaviour. Saying
  something like "if you ask me about a bad month I'd say sixty" is out of character:
  a real person just answers the question they were asked. Behave the rule, never
  narrate it.
- Do not coach the agent and do not mention these instructions.
{language_note}{avoid_note}
"""
    return model_text(
        client, settings, prompt, lat=lat, label=label,
        reasoning_effort=settings.reply_reasoning_effort,
    )


def build_guidance_and_verification(
    client: AzureOpenAIClient,
    settings: Settings,
    session: SessionState,
    latest_agent_text: str,
    lat: Lat | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    limit = settings.rag_search_limit
    retrieval_query = (
        "FlexLife IUL product sales guidance objection handling verification. "
        f"Customer persona: {session.persona.get('summary', '')} "
        f"Recent transcript: {recent_transcript(session.turns, limit=8)} "
        f"Latest agent statement: {latest_agent_text}"
    )
    with (lat.span("rag.search") if lat else _NoSpan()):
        sources = rag_search(settings=settings, client=client, query=retrieval_query, limit=limit)["sources"]
    with (lat.span("rag.fact_check", f"claim_chars={len(latest_agent_text)}") if lat else _NoSpan()):
        fc = rag_fact_check(settings=settings, client=client, claim=latest_agent_text, limit=limit)

    verification = {
        "verdict": VERDICT_MAP.get(fc.get("verdict", "UNKNOWN"), "not_checked"),
        "issue": _report_summary(fc.get("report", "")),
        "suggested_correction": "",
    }

    prompt = f"""You are a sales support assistant for a life insurance roleplay.

Use only the source context below for product facts. Base your process coaching on the playbook.

Sales process playbook (use this to guide the agent's process, not for product facts):
{SALES_PLAYBOOK or "None provided."}

Ground your rag_guidance in the playbook: reference the current stage in its conversation
model (Section 2), its objection handling (Section 5), and its Prefer/Avoid language guide
(Section 7). If the latest agent message hits an escalation trigger (Section 8) or breaks a
governing rule (Section 1), call it out in the guidance.

Persona summary:
{session.persona.get('summary', '')}

Recent transcript:
{recent_transcript(session.turns, limit=10)}

Latest insurance agent message:
{latest_agent_text}

Source context:
{context_block(sources)}

Return only JSON with this shape:
{{
  "moment": "product_intro|discovery|objection|eligibility|closing|general",
  "rag_guidance": ["short guidance item"]
}}
"""
    parsed = parse_json_object(
        model_text(client, settings, prompt, lat=lat, label="llm.coaching"),
        {"moment": "general", "rag_guidance": []},
    )
    return parsed, verification, sources


class _NoSpan:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def generate_feedback(
    client: AzureOpenAIClient, settings: Settings, session: SessionState, lat: Lat | None = None
) -> dict[str, Any]:
    prompt = f"""You are a sales roleplay coach. Review this session and grade the agent
against the evaluation instruction set below. Follow it exactly: it defines what counts
as a finding, how to rank findings, and - importantly - what must not be reported.

Evaluation instruction set:
{FEEDBACK_PLAYBOOK or "None provided."}

Persona (including the `evaluation` block, which the customer was never shown):
{json.dumps(session.persona, indent=2)}

How the call ended: {session.outcome or "the call did not reach a declared ending"}

Transcript:
{recent_transcript(session.turns, limit=80)}

Verification events (RAG fact-check verdicts on the agent's own claims; `not_checked`
means the check did not run, which is not a finding):
{json.dumps(session.verification_events, indent=2)}

Every bullet must be grounded in what actually happened in this transcript.

Return only JSON with this shape:
{{
  "what_went_well": ["exactly 3 bullets: good things to keep doing"],
  "what_to_improve": ["exactly 3 bullets: concrete fixes"],
  "what_to_avoid": ["up to 3 bullets: things the agent did that the playbook says not to do; empty if none"],
  "score": <integer 0-100: overall quality of the agent's performance on this call; 50 is an average rookie, 85+ is a call you would show a new hire>,
  "skills": {{
    "living_benefits": <integer 0-100 for how well the agent explained and positioned living benefits, or null if the call never touched them>,
    "illustration_design": <integer 0-100 for how well the agent handled numbers, premiums, funding and illustration design, or null if the call never got there>,
    "objection_handling": <integer 0-100 for how well the agent handled the prospect's objections, or null if none came up>
  }}
}}
"""
    report = parse_json_object(
        model_text(client, settings, prompt, lat=lat, label="llm.feedback"),
        {
            "what_went_well": [],
            "what_to_improve": ["Feedback generation did not return structured JSON."],
            "what_to_avoid": [],
        },
    )
    return normalize_feedback_scores(report)


def answer_navigator_question(
    client: AzureOpenAIClient,
    settings: Settings,
    session: SessionState,
    question: str,
    lat: Lat | None = None,
) -> dict[str, Any]:
    """Answer the agent's in-session "Ask Navigator" question, grounded in the source
    documents and the running transcript."""
    try:
        with (lat.span("rag.search") if lat else _NoSpan()):
            sources = rag_search(settings=settings, client=client, query=question, limit=settings.rag_search_limit)["sources"]
    except Exception as exc:  # noqa: BLE001 - retrieval failing must not kill the answer
        _log(f"Ask Navigator retrieval failed: {type(exc).__name__}: {exc}")
        sources = []

    prompt = f"""You are Navigator, a live sales coach whispering to an insurance agent
who is mid-roleplay with a customer. Answer the agent's question directly, briefly, and
practically (2-4 sentences). Use the playbook for process and approved wording, and the
source context for product facts. If the sources do not cover a product specific, say so
and give safe general guidance without inventing product details. Never promise guarantees.

Sales process playbook:
{SALES_PLAYBOOK or "None provided."}

Customer persona summary:
{session.persona.get('summary', '')}

Recent transcript:
{recent_transcript(session.turns, limit=12)}

Agent's question to their coach:
{question}

Source context:
{context_block(sources)}
"""
    answer = model_text(client, settings, prompt, lat=lat, label="llm.ask_navigator")
    return {"answer": answer, "sources": source_payload(sources)}


# ------------------------------- persona generation -------------------------------


def persona_prompt(notes: str) -> str:
    return f"""You are building a roleplay prospect for insurance sales practice.

The persona must fit the practice set's playbook, which is how every prospect in this
tool behaves. Read it before you write anything:
---
{PERSONA_PLAYBOOK or "None provided."}
---

The agent described the prospect they want to practise with:
---
{notes}
---

Return ONLY a JSON object with exactly this shape:

{{
  "display_name": "first name only",
  "full_name": "first and last name",
  "gender": "female or male",
  "scenario_title": "4-8 word label for this scenario",
  "blurb": "one sentence, max 140 chars, describing the prospect for a browse card",
  "summary": "2-3 sentences describing who they are and what they want",
  "difficulty": "foundational, intermediate or advanced",
  "buying_pattern": "one or two of: towards, away-from, internal, external, self, others, possibilities, necessities",
  "lead_context": {{
    "ad": "the paid social ad they responded to, as a short quoted line",
    "form": "what they typed or selected on the lead form",
    "days_since": "how long ago they filled it in, and whether they remember doing it",
    "agent_knows": "the handful of facts the agent has before dialling"
  }},
  "fixed_facts": {{
    "age": 0,
    "occupation": "",
    "household": "",
    "household_income": "",
    "current_coverage": "",
    "retirement_savings": "",
    "monthly_budget_comfort": "a real ceiling, and what they say when pushed",
    "financial_goals": ["three short goals"],
    "insurance_familiarity": "",
    "iul_familiarity": "",
    "financial_picture": "2-4 sentences of concrete numbers: what comes in, what goes out, what is left",
    "stated_goal": "what they say they want, in their own words"
  }},
  "root_goal": "the real reason they clicked, which they have not said out loud and may not have admitted to themselves",
  "personality": {{
    "tone": "",
    "conversation_style": "",
    "speech_signature": ["3-5 specific verbal habits: fillers, tics, what they do when uncomfortable"]
  }},
  "objection_ladder": {{
    "surface": ["the polite deflection offered early, usually price or timing; not the real issue"],
    "real": "the substantive concern, voiced only if the agent asks a second question instead of rebutting the surface objection",
    "root": "the thing they have not said out loud, surfacing only after real trust"
  }},
  "objection_path": ["exactly 5 stages, in order, from how they open to what wins them over"],
  "consistency_rules": ["5-7 rules that keep the character stable across turns, including a hard budget ceiling and at least one thing that loses them outright"],
  "hidden_facts": ["3-5 facts they answer accurately if asked directly and never volunteer"],
  "evaluation": {{
    "right_answer": "what a good agent should actually recommend, which may be term, or nothing at all",
    "primary_skill": "the one skill this scenario drills",
    "win_condition": "what the agent must do to have handled this well",
    "fail_condition": "what a failure looks like, including an apparent close that was the wrong sale"
  }}
}}

Rules:
- Use every detail the agent gave. Do not contradict anything they specified.
- Invent the rest so it is realistic and internally consistent (income should fit the
  occupation, coverage should fit the household, the budget ceiling should fit what is
  left after the outgoings, and so on).
- This is a warm inbound lead being called back, never a cold call and never a service
  rep. They filled in a form and are expecting the call.
- The three objection layers must be genuinely different concerns, not the same worry
  reworded. The surface one should be about price or timing.
- root_goal must be something the prospect would not say to a stranger on the phone.
- The right answer does not have to be a FlexLife IUL. If the description implies a
  prospect who should not buy one, say so in evaluation.right_answer and build the
  persona to expose the wrong sale.
- Keep it a life-insurance / IUL sales context.
- Return only the JSON object, no commentary.
"""


def unique_persona_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "scenario"
    return f"{slug}-{uuid.uuid4().hex[:4]}"


def complete_persona(
    generated: dict[str, Any],
    notes: str,
    registry: VoiceRegistry,
    settings: Settings,
) -> dict[str, Any]:
    """Fill anything the model omitted with plausible random values, recording per field
    whether it came from the model or a fallback so a generated persona can be audited."""
    provenance: dict[str, str] = {}

    def pick(path: str, value: Any, fallback: Any) -> Any:
        empty = (
            value is None
            or (isinstance(value, str) and not value.strip())
            or (isinstance(value, (list, dict, tuple)) and not value)
        )
        if empty:
            provenance[path] = "fallback"
            return fallback() if callable(fallback) else fallback
        provenance[path] = "model"
        return value

    facts = generated.get("fixed_facts")
    if not isinstance(facts, dict):
        facts = {}

    name = pick("display_name", str(generated.get("display_name") or "").strip(),
                lambda: random.choice(["Jordan", "Casey", "Morgan", "Riley", "Avery", "Quinn", "Taylor"]))

    raw_age = facts.get("age")
    valid_age = raw_age if isinstance(raw_age, int) and 18 <= raw_age <= 80 else None
    age = pick("fixed_facts.age", valid_age, lambda: random.randint(28, 58))

    goals = facts.get("financial_goals")
    goals = pick(
        "fixed_facts.financial_goals",
        [str(g) for g in goals][:4] if isinstance(goals, list) and goals else None,
        lambda: random.sample([
            "protect the family if something happens to them",
            "build cash value they can draw on later",
            "keep the monthly premium predictable",
            "understand the trade-offs before committing",
            "find tax-advantaged growth outside a 401(k)",
            "cover the mortgage if their income stops",
        ], 3),
    )

    raw_path = generated.get("objection_path")
    path_ok = [str(x) for x in raw_path][:6] if isinstance(raw_path, list) and len(raw_path) >= 3 else None
    objection_path = pick("objection_path", path_ok, [
        "They open guarded and want to know why the agent is calling.",
        "They ask what the product actually does before any numbers.",
        "Their first objection is cost against their other commitments.",
        "Their second objection is whether they could do better elsewhere.",
        "They warm up when the agent ties it to a goal they named themselves.",
    ])

    raw_rules = generated.get("consistency_rules")
    rules_ok = [str(x) for x in raw_rules][:7] if isinstance(raw_rules, list) and raw_rules else None
    consistency_rules = pick("consistency_rules", rules_ok, lambda: [
        f"Never change {name}'s age, household, income, or current coverage.",
        "Disclose fixed facts naturally when asked, not all at once.",
        f"Only speak as {name}, the customer.",
        "Stay consistent with the objection path and do not skip ahead.",
    ])

    personality = generated.get("personality")
    if not isinstance(personality, dict):
        personality = {}
    raw_sig = personality.get("speech_signature")
    speech_signature = pick(
        "personality.speech_signature",
        [str(x) for x in raw_sig][:6] if isinstance(raw_sig, list) and raw_sig else None,
        lambda: random.sample([
            "one filler per few turns: 'um', 'I mean', 'y'know'",
            "restarts a sentence when the question lands awkwardly",
            "repeats a number back wrong before getting it right",
            "goes quiet and says 'okay' when given too much at once",
            "answers a short question with a short story",
            "apologises for background noise once during the call",
        ], 4),
    )

    raw_ladder = generated.get("objection_ladder")
    if not isinstance(raw_ladder, dict):
        raw_ladder = {}
    raw_surface = raw_ladder.get("surface")
    if isinstance(raw_surface, str):
        raw_surface = [raw_surface]
    objection_ladder = {
        "surface": pick(
            "objection_ladder.surface",
            [str(x) for x in raw_surface][:3] if isinstance(raw_surface, list) and raw_surface else None,
            lambda: [random.choice([
                "How much is this going to cost me a month?",
                "I'd need to think about the timing on this.",
                "How did you get my information?",
            ])],
        ),
        "real": pick(
            "objection_ladder.real",
            str(raw_ladder.get("real") or "").strip() or None,
            "They are not sure this is the right thing to be spending money on right now, "
            "and want the agent to be honest about priority.",
        ),
        "root": pick(
            "objection_ladder.root",
            str(raw_ladder.get("root") or "").strip() or None,
            "They are afraid of committing to something they may have to cancel, and have not said so.",
        ),
    }

    raw_hidden = generated.get("hidden_facts")
    hidden_facts = pick(
        "hidden_facts",
        [str(x) for x in raw_hidden][:6] if isinstance(raw_hidden, list) and raw_hidden else None,
        lambda: [
            "The coverage they already have through work, and how little it is.",
            "A family member who is financially dependent on them.",
            "A debt they are quietly paying down.",
        ],
    )

    lead = generated.get("lead_context")
    if not isinstance(lead, dict):
        lead = {}
    lead_context = {
        "ad": pick("lead_context.ad", str(lead.get("ad") or "").strip() or None,
                   "A paid social ad about protecting your family and building savings at the same time."),
        "form": pick("lead_context.form", str(lead.get("form") or "").strip() or None,
                     "Name, phone, age, and 'I'm interested in learning more.'"),
        "days_since": pick("lead_context.days_since", str(lead.get("days_since") or "").strip() or None,
                           lambda: random.choice([
                               "filled the form a couple of hours ago and remembers it clearly",
                               "filled the form three days ago and half-forgot about it",
                               "filled the form a week ago and needs reminding what it was",
                           ])),
        "agent_knows": pick("lead_context.agent_knows", str(lead.get("agent_knows") or "").strip() or None,
                            lambda: f"Name, phone, age {age}, and which ad they responded to."),
    }

    evaluation_in = generated.get("evaluation")
    if not isinstance(evaluation_in, dict):
        evaluation_in = {}
    evaluation = {
        "right_answer": pick("evaluation.right_answer", str(evaluation_in.get("right_answer") or "").strip() or None,
                             "Depends on discovery: size the recommendation to the need the agent uncovers, not to the product."),
        "primary_skill": pick("evaluation.primary_skill", str(evaluation_in.get("primary_skill") or "").strip() or None,
                              "Needs discovery before recommending"),
        "win_condition": pick("evaluation.win_condition", str(evaluation_in.get("win_condition") or "").strip() or None,
                              "Agent reaches the real objection rather than rebutting the surface one, surfaces the hidden facts, and agrees a specific next step."),
        "fail_condition": pick("evaluation.fail_condition", str(evaluation_in.get("fail_condition") or "").strip() or None,
                               "Agent answers the surface objection with a script and closes on a budget the prospect cannot actually sustain."),
    }

    stated = str(generated.get("gender") or "").strip().lower()
    inferred_reason = ""

    def _infer_gender() -> str:
        nonlocal inferred_reason
        value, inferred_reason = infer_gender(name, notes, str(generated.get("summary") or ""))
        return value

    gender = pick("gender", stated if stated in {"female", "male"} else None, _infer_gender)
    if inferred_reason:
        provenance["gender"] = f"inferred: {inferred_reason}"
    profile = registry.select(gender, age, random.randint(0, 1))
    difficulty = str(generated.get("difficulty") or "").strip().lower()
    persona = {
        "id": unique_persona_id(name),
        "display_name": name,
        "full_name": pick("full_name", str(generated.get("full_name") or "").strip() or None, name),
        "gender": gender,
        "voice_profile": profile.id if profile else "",
        "voice_name": profile.voice_name if profile else settings.azure_speech_voice_ava,
        "scenario_title": pick("scenario_title", str(generated.get("scenario_title") or "").strip()[:80] or None, "Custom scenario"),
        "blurb": pick("blurb", str(generated.get("blurb") or "").strip()[:160] or None, notes[:160]),
        "difficulty": pick("difficulty", difficulty if difficulty in {"foundational", "intermediate", "advanced"} else None, "intermediate"),
        "buying_pattern": pick("buying_pattern", str(generated.get("buying_pattern") or "").strip() or None,
                               lambda: random.choice(["away-from / others", "towards / possibilities", "internal / necessities", "external / others"])),
        "summary": pick("summary", str(generated.get("summary") or "").strip() or None,
                        lambda: f"{name} is a {age}-year-old prospect. {notes}"),
        "lead_context": lead_context,
        "fixed_facts": {
            "age": age,
            "occupation": pick("fixed_facts.occupation", facts.get("occupation"),
                               lambda: random.choice(["operations manager", "nurse", "small business owner", "teacher", "sales director", "civil engineer"])),
            "household": pick("fixed_facts.household", facts.get("household"),
                              lambda: random.choice(["married, two children", "single, no dependants", "married, one child", "partnered, expecting their first child"])),
            "household_income": pick("fixed_facts.household_income", facts.get("household_income"),
                                     lambda: f"${random.randrange(70, 320, 10)},000 household income"),
            "current_coverage": pick("fixed_facts.current_coverage", facts.get("current_coverage"),
                                     lambda: random.choice(["group life through work only", "no coverage at all", "a small term policy bought years ago"])),
            "retirement_savings": pick("fixed_facts.retirement_savings", facts.get("retirement_savings"),
                                       lambda: random.choice(["contributes enough for the employer match", "maxes their 401(k)", "has a modest IRA and little else"])),
            "monthly_budget_comfort": pick("fixed_facts.monthly_budget_comfort", facts.get("monthly_budget_comfort"),
                                           lambda: f"${random.randrange(100, 700, 50)} to ${random.randrange(750, 1200, 50)} if the value is clear"),
            "financial_goals": goals,
            "insurance_familiarity": pick("fixed_facts.insurance_familiarity", facts.get("insurance_familiarity"),
                                          lambda: random.choice(["low", "moderate", "moderate to high"])),
            "iul_familiarity": pick("fixed_facts.iul_familiarity", facts.get("iul_familiarity"),
                                    lambda: random.choice(["has never heard of it", "has heard the term only", "has read a little about it"])),
            "financial_picture": pick("fixed_facts.financial_picture", facts.get("financial_picture"),
                                      lambda: f"Money is accounted for month to month. Roughly ${random.randrange(100, 600, 50)} of genuine slack, and they will overstate it if asked directly."),
            "stated_goal": pick("fixed_facts.stated_goal", facts.get("stated_goal"), "Just wanted to see what's out there."),
        },
        "root_goal": pick("root_goal", str(generated.get("root_goal") or "").strip() or None,
                          "Something specific happened to someone close to them that they have not mentioned, and it is the real reason they filled in the form."),
        "personality": {
            "tone": pick("personality.tone", personality.get("tone"),
                         lambda: random.choice(["guarded but polite", "warm and curious", "blunt and time-pressed", "friendly, easily distracted"])),
            "conversation_style": pick("personality.conversation_style", personality.get("conversation_style"),
                                       "asks practical questions and wants plain answers"),
            "speech_signature": speech_signature,
        },
        "objection_ladder": objection_ladder,
        "objection_path": objection_path,
        "consistency_rules": consistency_rules,
        "hidden_facts": hidden_facts,
        "evaluation": evaluation,
        "custom": True,
        "source_notes": notes,
    }
    from_model = sum(1 for v in provenance.values() if v == "model")
    persona["generation"] = {
        "created_at": _now_iso(),
        "model": settings.azure_openai_chat_deployment,
        "fields_from_model": from_model,
        "fields_total": len(provenance),
        "provenance": provenance,
    }
    return persona


# ------------------------------- the service -------------------------------


class Roleplay:
    """Everything the Prepare tab needs, held on ``app.state``. Personas and the voice
    registry load once; sessions live in ``self.sessions`` for the life of the process."""

    def __init__(self, settings: Settings, client: AzureOpenAIClient) -> None:
        self.settings = settings
        self.client = client
        self.voices = load_registry()
        self.personas: dict[str, dict[str, Any]] = {}
        self.sessions: dict[str, SessionState] = {}
        self._lock = threading.Lock()
        self.model_warm = threading.Event()
        self.reload_personas()

    # --- personas ---

    def reload_personas(self) -> None:
        personas = load_builtin_personas()
        try:
            with connect(self.settings) as conn:
                for persona in list_roleplay_personas(conn):
                    persona["custom"] = True
                    persona.pop("duration", None)
                    if persona["id"] not in personas:
                        personas[persona["id"]] = persona
        except Exception as exc:  # noqa: BLE001 - built-ins still work without the table
            _log(f"Could not load custom personas from Postgres: {type(exc).__name__}: {exc}")
        with self._lock:
            self.personas = personas

    def scenarios(self) -> dict[str, Any]:
        cards = [scenario_card(p) for p in self.personas.values()]
        cards.sort(key=lambda c: (c["custom"], c["name"]))
        return {"scenarios": cards}

    def inspect_persona(self, persona_id: str) -> dict[str, Any] | None:
        persona = self.personas.get(persona_id)
        if persona is None:
            return None
        generation = persona.get("generation") or {}
        provenance = generation.get("provenance") or {}
        return {
            "persona_id": persona["id"],
            "card": scenario_card(persona),
            "custom": bool(persona.get("custom")),
            "source_notes": persona.get("source_notes", ""),
            "generation": generation,
            "fields_from_model": sorted(k for k, v in provenance.items() if v == "model"),
            "fields_randomly_filled": sorted(k for k, v in provenance.items() if v == "fallback"),
            "persona": persona,
        }

    def create_custom(self, notes: str) -> dict[str, Any]:
        notes = notes.strip()
        if not notes:
            raise ValueError("Describe the customer first")
        lat = Lat(kind="scenario")
        with lat.span("generate", f"notes_chars={len(notes)}"):
            generated = parse_json_object(
                model_text(self.client, self.settings, persona_prompt(notes), lat=lat, label="llm.persona"),
                {},
            )
        persona = complete_persona(generated, notes, self.voices, self.settings)
        with connect(self.settings) as conn:
            upsert_roleplay_persona(conn, persona)
        with self._lock:
            self.personas[persona["id"]] = persona
        gen = persona.get("generation") or {}
        _log(f"Custom scenario created: {persona['id']} "
             f"[{gen.get('fields_from_model')}/{gen.get('fields_total')} fields from the model]")
        return {"scenario": scenario_card(persona), "persona": persona}

    def delete_custom(self, persona_id: str) -> bool:
        persona = self.personas.get(persona_id)
        if persona is None or not persona.get("custom"):
            return False
        with connect(self.settings) as conn:
            delete_roleplay_persona(conn, persona_id)
        with self._lock:
            self.personas.pop(persona_id, None)
        return True

    # --- speech ---

    def speech_token(self, profile_id: str = "") -> dict[str, Any]:
        """Issue a short-lived Speech token. A profile_id issues it from that profile's
        credentials (env var names on the profile), otherwise the top-level settings."""
        profile = self.voices.by_id(profile_id) if profile_id else None
        if profile_id and profile is None:
            raise ValueError(f"Unknown voice profile: {profile_id}")
        if profile is not None:
            creds = self.voices.credentials(profile)
            if creds.missing:
                raise ValueError(f"Voice profile {profile.id} needs env var(s): {', '.join(creds.missing)}")
            key, region = creds.key, creds.region
            endpoint = creds.endpoint or f"https://{region}.api.cognitive.microsoft.com"
        else:
            key = self.settings.azure_speech_key
            region = self.settings.azure_speech_region
            endpoint = self.settings.azure_speech_endpoint or f"https://{region}.api.cognitive.microsoft.com"
        if not key:
            raise ValueError("AZURE_SPEECH_KEY is not configured, so the browser cannot use the microphone or hear the customer")
        lat = Lat(kind="server")
        with lat.span("speech.token_fetch", f"profile={profile.id if profile else '-'}"):
            response = requests.post(
                f"{endpoint.rstrip('/')}/sts/v1.0/issueToken",
                headers={"Ocp-Apim-Subscription-Key": key},
                timeout=(10, 30),
                verify=self.settings.azure_storage_verify_ssl,
            )
        if response.status_code == 401:
            raise PermissionError("Azure Speech rejected the key (HTTP 401). Check AZURE_SPEECH_KEY and AZURE_SPEECH_REGION.")
        response.raise_for_status()
        result: dict[str, Any] = {"token": response.text, "region": region}
        if profile is not None:
            result["profile"] = profile.as_public_dict()
        return result

    def voice_profiles(self) -> dict[str, Any]:
        registry = self.voices
        profiles = []
        for profile in registry.profiles:
            creds = registry.credentials(profile)
            profiles.append({
                **profile.as_public_dict(),
                "credentials": {
                    "key_env": profile.credentials.key_env,
                    "region_env": profile.credentials.region_env,
                    "endpoint_env": profile.credentials.endpoint_env,
                    "configured": creds.ok,
                    "missing": list(creds.missing),
                },
            })
        return {
            "version": registry.version,
            "age_ranges": [{"id": r.id, "label": r.label, "min_age": r.min_age, "max_age": r.max_age} for r in registry.age_ranges],
            "coverage": registry.grid(),
            "profiles": profiles,
        }

    # --- sessions ---

    def get_session(self, session_id: str) -> SessionState:
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError("Unknown or expired session")
        return session

    def start(self, persona_id: str, custom_notes: str = "", language: str = "en") -> dict[str, Any]:
        persona = self.personas.get(persona_id)
        if persona is None:
            raise ValueError(f"Unknown scenario: {persona_id}")
        lang = language_info(language)
        curated = resolve_voice_profile(self.voices, persona)
        session = SessionState(
            session_id=uuid.uuid4().hex,
            persona=persona,
            custom_notes=custom_notes.strip(),
            voice_profile_id=curated.id if curated else "",
            language=lang["code"],
        )
        profile = self._voice_for(session)
        self.sessions[session.session_id] = session
        return {
            "session_id": session.session_id,
            "scenario": scenario_card(persona),
            "transcript": session.turns,
            "voice_profile": profile.as_public_dict() if profile else None,
            "voice_name": profile.voice_name if profile else persona.get("voice_name", ""),
            "language": lang["code"],
            "language_name": lang["name"],
            "stt_locale": lang["stt"],
            **session_progress(session),
        }

    def _voice_for(self, session: SessionState) -> VoiceProfile | None:
        """The persona's curated voice, swapped for Azure's multilingual voice of the
        same gender when the call is not in English (the DragonHD voices are English-only)."""
        profile = self.voices.by_id(session.voice_profile_id) if session.voice_profile_id else None
        if profile is None or session.language == "en":
            return profile
        return replace(profile, voice_name=multilingual_voice(profile.gender))

    def turn(self, session_id: str, agent_text: str) -> dict[str, Any]:
        session = self.get_session(session_id)
        agent_text = agent_text.strip()
        if not agent_text:
            raise ValueError("agent_text is required")
        with session.lock:
            session.turn_seq += 1
            turn_no = session.turn_seq
        lat = Lat(sess=session.session_id[:8], turn=turn_no, kind="server")
        if not self.model_warm.is_set():
            _log(f"sess={session.session_id[:8]} turn={turn_no}: turn started before the keep-warm ping finished")
        turn_start = time.perf_counter()

        with session.lock:
            session.turns.append(transcript_entry("agent", agent_text, turn_no))
            prev_customer = next((t["text"] for t in reversed(session.turns) if t["speaker"] == "customer"), "")
        customer_reply = generate_customer_reply(self.client, self.settings, session, agent_text, lat=lat)
        customer_reply, outcome = split_end_marker(customer_reply)
        customer_reply = speechify(customer_reply)
        if prev_customer and _norm(customer_reply) == _norm(prev_customer):
            customer_reply = generate_customer_reply(
                self.client, self.settings, session, agent_text,
                avoid_text=prev_customer, lat=lat, label="llm.customer_reply.retry",
            )
            customer_reply, retry_outcome = split_end_marker(customer_reply)
            customer_reply = speechify(customer_reply)
            outcome = outcome or retry_outcome
        if outcome:
            with session.lock:
                session.outcome = outcome
                session.ended = True
            _log(f"sess={session.session_id[:8]} turn={turn_no}: customer ended the call: {outcome}")
            progress = session_progress(session)
        with session.lock:
            session.turns.append(transcript_entry("customer", customer_reply, turn_no))
            transcript = list(session.turns)

        self._spawn_verification(session, agent_text, turn_no)

        total_ms = (time.perf_counter() - turn_start) * 1000.0
        llm_ms = sum(v for k, v in lat.phases.items() if k.startswith("llm."))
        lat.log("turn_total", total_ms, f"llm={llm_ms:.0f}ms overhead={total_ms - llm_ms:.0f}ms reply_chars={len(customer_reply)}")
        with session.lock:
            session.timings[turn_no] = dict(lat.phases)

        profile = self._voice_for(session)
        return {
            "session_id": session.session_id,
            "turn": turn_no,
            "customer_reply": customer_reply,
            "ssml": profile.to_ssml(customer_reply, lang=language_info(session.language)["stt"] or "en-US") if profile else "",
            "voice_profile_id": session.voice_profile_id,
            "voice_name": profile.voice_name if profile else session.persona.get("voice_name", ""),
            "transcript": transcript,
            "outcome": session.outcome,
            **session_progress(session),
        }

    def _spawn_verification(self, session: SessionState, agent_text: str, turn_no: int) -> None:
        """Run coaching + fact-check off the voice path; its only consumer is the
        end-of-session report via session.verification_events."""
        bg = Lat(sess=session.session_id[:8], turn=turn_no, kind="bg")
        bg_start = time.perf_counter()

        def worker() -> None:
            try:
                support, verification, sources = build_guidance_and_verification(
                    self.client, self.settings, session, agent_text, lat=bg,
                )
                payload_sources = source_payload(sources)
            except Exception as exc:  # noqa: BLE001 - a background turn must never crash the server
                _log(f"sess={session.session_id[:8]} turn={turn_no}: verification failed: {type(exc).__name__}: {exc}")
                support = {"moment": "general"}
                verification = {"verdict": "not_checked", "issue": f"{type(exc).__name__}", "suggested_correction": ""}
                payload_sources = []
            event = {
                "turn": turn_no,
                "agent_text": agent_text,
                "moment": support.get("moment", "general"),
                "verification": verification,
                "sources": payload_sources,
            }
            with session.lock:
                session.verification_events.append(event)
            bg.log("total", (time.perf_counter() - bg_start) * 1000.0)

        threading.Thread(target=worker, daemon=True, name=f"verify-{session.session_id[:8]}").start()

    def ask(self, session_id: str, question: str) -> dict[str, Any]:
        session = self.get_session(session_id)
        question = question.strip()
        if not question:
            raise ValueError("question is required")
        lat = Lat(sess=session.session_id[:8], turn=session.turn_seq, kind="ask")
        with lat.span("total", f"question_chars={len(question)}"):
            return answer_navigator_question(self.client, self.settings, session, question, lat=lat)

    def feedback(self, session_id: str) -> dict[str, Any]:
        """Generate the coaching report, persist the finished call, and return both."""
        session = self.get_session(session_id)
        with session.lock:
            session.ended = True
        lat = Lat(sess=session.session_id[:8], turn=session.turn_seq, kind="feedback")
        with lat.span("total", f"turns={len(session.turns)}"):
            feedback = generate_feedback(self.client, self.settings, session, lat=lat)
        elapsed = int(max(0.0, time.time() - session.created_at))
        history_id = session.history_id
        try:
            with connect(self.settings) as conn:
                history_id = insert_roleplay_session(
                    conn,
                    session_key=session.session_id,
                    persona_id=session.persona["id"],
                    persona_name=session.persona.get("display_name", ""),
                    scenario_title=session.persona.get("scenario_title", ""),
                    custom=bool(session.persona.get("custom")),
                    outcome=session.outcome,
                    elapsed_seconds=elapsed,
                    turn_count=session.turn_seq,
                    turns=session.turns,
                    feedback=feedback,
                    verification_events=session.verification_events,
                    started_at=datetime.fromtimestamp(session.created_at, tz=timezone.utc),
                    ended_at=datetime.now(tz=timezone.utc),
                )
            session.history_id = history_id
        except Exception as exc:  # noqa: BLE001 - a storage failure must not hide the report
            _log(f"Could not store session {session.session_id[:8]}: {type(exc).__name__}: {exc}")
        return {
            "session_id": session.session_id,
            "history_id": history_id,
            "feedback": feedback,
            "outcome": session.outcome,
            "outcome_label": OUTCOME_LABELS.get(session.outcome, session.outcome),
            "elapsed_seconds": elapsed,
            "turn_count": session.turn_seq,
            "transcript": session.turns,
            "verification_events": session.verification_events,
            "scenario": scenario_card(session.persona),
        }

    # Phases the browser measures, in the order they occur within one turn.
    _CLIENT_PHASES = (
        ("stt_recognize_ms", "stt.recognize"),
        ("http_turn_ms", "http.turn"),
        ("tts_first_byte_ms", "tts.first_byte"),
        ("tts_total_ms", "tts.synthesis"),
        ("tts_audio_length_ms", "tts.audio_length"),
    )

    def client_metrics(self, payload: dict[str, Any]) -> None:
        """Receive the browser's STT/TTS timings and print the combined critical path,
        so one stdout log carries the whole mouth-to-ear number."""
        session_id = str(payload.get("session_id", ""))
        turn_no = int(payload.get("turn") or 0)
        lat = Lat(sess=(session_id[:8] or "-"), turn=turn_no, kind="client")
        for key, phase in self._CLIENT_PHASES:
            value = payload.get(key)
            if isinstance(value, (int, float)) and value >= 0:
                lat.log(phase, float(value))
        critical = {p: lat.phases.get(p, 0.0) for p in ("stt.recognize", "http.turn", "tts.first_byte")}
        measured = sum(critical.values())
        if measured <= 0 or critical["http.turn"] <= 0:
            return
        reported = payload.get("mouth_to_ear_ms")
        mouth_to_ear = float(reported) if isinstance(reported, (int, float)) and reported > 0 else measured
        worst = max(critical, key=critical.get)
        parts = " ".join(f"{k}={v:.0f}ms" for k, v in critical.items())
        print(f"[{_now_iso()}] [lat] sess={lat.sess} turn={lat.turn} SUMMARY mouth_to_ear={mouth_to_ear:.0f}ms | {parts} | bottleneck={worst}", flush=True)

    # --- keep-warm ---

    def start_keep_warm(self) -> None:
        """Ping the chat deployment at boot and periodically so the first turn of a
        session avoids the deployment cold start (measured at 15-35 s)."""
        interval = self.settings.model_warm_interval_seconds
        if interval <= 0:
            self.model_warm.set()
            return

        def loop() -> None:
            first = True
            warm_client = AzureOpenAIClient(self.settings)
            while True:
                start = time.perf_counter()
                try:
                    warm_client.post(
                        "/responses",
                        {"model": self.settings.azure_openai_chat_deployment, "input": "ready"},
                        timeout=(10, 180 if first else 60),
                    )
                    ms = (time.perf_counter() - start) * 1000.0
                    if first:
                        self.model_warm.set()
                        _log(f"Chat deployment warm after {ms:.0f}ms (pinging every {interval}s).")
                        first = False
                except Exception as exc:  # noqa: BLE001 - best effort
                    _log(f"Keep-warm ping failed: {type(exc).__name__}: {exc}")
                time.sleep(interval)

        threading.Thread(target=loop, daemon=True, name="roleplay-keep-warm").start()
