"""salesDJ "Learn" mixes: bite-sized training content generated from the corpus.

A *mix* is one topic rendered in one of three formats:

- ``article``    - a short, plain-language lesson with a talk track
- ``flashcards`` - a question/answer deck for drilling
- ``audio``      - a two-host coaching conversation, rendered to MP3 with Azure Speech
                   (articles can also be narrated on demand: the "Listen" option)

Every mix is grounded the same way: plan a few retrieval queries from the agent's
prompt, pull the matching chunks from pgvector, and ask the chat model for a
structured JSON result constrained to those excerpts. Generation runs in the
background; the row in ``learn_mixes`` moves queued -> generating -> ready|failed.
"""

from __future__ import annotations

import json
import re
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests

from .config import Settings
from .curriculum import CURRICULUM, CURRICULUM_BY_KEY, CurriculumItem
from .db import connect, get_chunks_for_source, get_mix, update_mix
from .embeddings import AzureOpenAIClient
from .service import format_sources, retrieve_contexts
from .speech import HOSTS, estimate_seconds, get_speech_client, speech_configured, OUTPUT_MIME

KINDS = ("audio", "article", "flashcards")
LENGTHS = ("short", "long")

# What "short" and "long" mean per format. Words are the model's target; minutes
# are what the UI shows before the real duration is known.
LENGTH_SPECS: dict[str, dict[str, dict[str, Any]]] = {
    "audio": {
        "short": {"label": "5 min", "minutes": 5, "words": 750, "turns": "14-20"},
        "long": {"label": "10 min", "minutes": 10, "words": 1500, "turns": "28-40"},
    },
    "article": {
        "short": {"label": "5 min read", "minutes": 5, "words": 900, "sections": "3-4"},
        "long": {"label": "10 min read", "minutes": 10, "words": 1800, "sections": "5-7"},
    },
    "flashcards": {
        "short": {"label": "10 cards", "minutes": 5, "cards": 10},
        "long": {"label": "20 cards", "minutes": 10, "cards": 20},
    },
}

# Tier-1 curriculum items are what the Learn home recommends.
RECOMMENDED_MIXES: list[dict[str, str]] = [
    {"kind": item["kind"], "length": item["length"], "prompt": item["prompt"]}
    for item in CURRICULUM
    if item["tier"] == 1
]

# Generation runs off-request. A small pool keeps Azure OpenAI/Speech rate limits
# comfortable while still finishing the 21-mix starter pack in a few minutes.
_POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="learn-gen")
# Pinned sources are bounded so a long curriculum item cannot blow the context.
_MAX_PINNED_CHUNKS = 90


def enqueue(fn, *args) -> None:
    _POOL.submit(fn, *args)

_PERSONA = (
    "You are a senior sales coach at National Life Group creating training content for a "
    "brand-new life insurance agent who has never sold FlexLife (an indexed universal life "
    "policy) before. Write in plain, friendly English. Explain every industry term the first "
    "time it appears in one short clause. Use ONLY the source excerpts provided: never invent "
    "numbers, rates, caps, participation rates, rider names, ages, or guarantees. If the "
    "excerpts do not cover something, say so briefly instead of guessing. Keep it compliant: "
    "no promises about market performance, no 'guaranteed growth', no tax advice beyond what "
    "the sources say."
)


def length_spec(kind: str, length: str) -> dict[str, Any]:
    return LENGTH_SPECS[kind][length]


# --- Model helpers ---------------------------------------------------------------


def _response_text(data: dict[str, Any]) -> str:
    if data.get("output_text"):
        return data["output_text"]
    parts: list[str] = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                parts.append(content["text"])
    return "\n".join(parts).strip()


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def _parse_json(text: str) -> dict[str, Any]:
    cleaned = _FENCE.sub("", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def _llm_json(
    client: AzureOpenAIClient,
    settings: Settings,
    prompt: str,
    *,
    effort: str | None = None,
) -> dict[str, Any]:
    """Ask for a JSON object; tolerate models/deployments without json_object mode."""
    payload: dict[str, Any] = {
        "model": settings.azure_openai_chat_deployment,
        "input": prompt,
        "text": {"format": {"type": "json_object"}},
    }
    if effort:
        payload["reasoning"] = {"effort": effort}
    try:
        data = client.post("/responses", payload, timeout=(10, 300))
    except requests.HTTPError as error:
        status = getattr(error.response, "status_code", None)
        if status != 400:
            raise
        payload.pop("text", None)
        data = client.post("/responses", payload, timeout=(10, 300))
    text = _response_text(data)
    try:
        return _parse_json(text)
    except json.JSONDecodeError:
        retry = client.post(
            "/responses",
            {
                "model": settings.azure_openai_chat_deployment,
                "input": f"Convert the following into a single valid JSON object and return ONLY the JSON:\n\n{text}",
            },
            timeout=(10, 300),
        )
        return _parse_json(_response_text(retry))


def _context_block(contexts: list[dict[str, Any]]) -> str:
    from .db import citation

    return "\n\n".join(
        f"[{index + 1}] Source: {citation(item)}\n{item['content']}"
        for index, item in enumerate(contexts)
    )


# --- Retrieval ------------------------------------------------------------------


def plan_queries(client: AzureOpenAIClient, settings: Settings, prompt: str, kind: str) -> list[str]:
    """Turn a short learner prompt into 3 focused retrieval queries."""
    result = _llm_json(
        client,
        settings,
        f"""A new life insurance agent wants to learn about: "{prompt}"
The material will become a {kind} lesson about selling FlexLife (an indexed universal life product from National Life Group).
The searchable library contains: the FlexLife product guide and quick reference, FlexLife brochures and agent training scripts, IUL and index crediting guides, an underwriting guide, living-benefits and rider brochures (accelerated benefit riders, chronic care, lifetime income), and sales-skills transcripts (building trust, customer interview, presenting and closing, objections, follow-up).

Write 3 short search queries (5-12 words each) that together cover the product facts AND the selling angle for this topic.
Return JSON: {{"queries": ["...", "...", "..."]}}""",
        effort="low",
    )
    queries = [q.strip() for q in result.get("queries", []) if isinstance(q, str) and q.strip()]
    return (queries or [prompt])[:3]


def gather_contexts(
    settings: Settings,
    client: AzureOpenAIClient,
    queries: list[str],
    *,
    total: int,
) -> list[dict[str, Any]]:
    """Union of the top chunks for each query, deduplicated, best similarity first."""
    per_query = max(4, total // len(queries) + 2)
    seen: dict[tuple[str, int], dict[str, Any]] = {}
    for query in queries:
        for row in retrieve_contexts(settings=settings, client=client, text=query, limit=per_query):
            key = (row["blob_name"], row["chunk_index"])
            if key not in seen or row["similarity"] > seen[key]["similarity"]:
                seen[key] = row
    ranked = sorted(seen.values(), key=lambda r: r["similarity"], reverse=True)
    return ranked[:total]


def gather_pinned_contexts(settings: Settings, item: CurriculumItem) -> list[dict[str, Any]]:
    """The exact document sections a curriculum item is written from, in reading order."""
    seen: set[tuple[str, int]] = set()
    rows: list[dict[str, Any]] = []
    with connect(settings) as conn:
        for spec in item["sources"]:
            for row in get_chunks_for_source(
                conn, doc_like=spec["doc"], pages=spec.get("pages"), chunk_range=spec.get("chunks")
            ):
                key = (row["blob_name"], row["chunk_index"])
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
    return rows[:_MAX_PINNED_CHUNKS]


def _brief_block(brief: str | None) -> str:
    if not brief:
        return ""
    return (
        "\nCoverage checklist (the lesson must land every point below that the excerpts support; "
        "if an excerpt contradicts the checklist, the excerpt wins):\n" + brief.strip() + "\n"
    )


# --- Generators -----------------------------------------------------------------


def generate_article(client, settings, prompt: str, length: str, contexts, brief: str | None = None) -> dict[str, Any]:
    spec = length_spec("article", length)
    return _llm_json(
        client,
        settings,
        f"""{_PERSONA}

Write a bite-sized training article on: "{prompt}"
{_brief_block(brief)}
Target about {spec['words']} words in {spec['sections']} sections. Structure it so a rookie can read it once and then actually use it in a client conversation: what it is, why a client cares, how to explain it simply, and what NOT to say.

Return JSON with exactly these keys:
{{
  "title": "punchy headline, under 70 characters, phrased as the question or promise a new agent has",
  "subtitle": "one sentence on what the reader will be able to do afterwards",
  "sections": [
    {{"heading": "short heading", "paragraphs": ["2-4 sentence paragraph", "..."], "bullets": ["optional short bullet", "..."]}}
  ],
  "key_takeaways": ["3-5 one-line takeaways"],
  "say_it_like_this": ["2-4 short, natural sentences the agent can say to a client, verbatim"],
  "watch_outs": ["1-3 things the agent must not claim or promise, based on the sources"]
}}
"bullets" may be an empty list. Use plain text only, no markdown.

Source excerpts:
{_context_block(contexts)}""",
    )


def generate_flashcards(client, settings, prompt: str, length: str, contexts, brief: str | None = None) -> dict[str, Any]:
    spec = length_spec("flashcards", length)
    return _llm_json(
        client,
        settings,
        f"""{_PERSONA}

Create a deck of exactly {spec['cards']} flashcards on: "{prompt}"
{_brief_block(brief)}
Mix the card types: definitions a rookie must know, specific product facts from the sources, "why does this matter to a client" reasoning, and "how would you answer if a client asked..." practice. Fronts are a single clear question. Backs are 1-3 sentences, concrete, and answerable from the sources. Order the deck from foundational to advanced.

Return JSON with exactly these keys:
{{
  "title": "deck title, under 60 characters",
  "cards": [
    {{"front": "question", "back": "answer", "source": "the bracketed source number(s) that support it, e.g. [2]"}}
  ]
}}

Source excerpts:
{_context_block(contexts)}""",
    )


def generate_audio_script(client, settings, prompt: str, length: str, contexts, brief: str | None = None) -> dict[str, Any]:
    spec = length_spec("audio", length)
    ava, andrew = HOSTS
    return _llm_json(
        client,
        settings,
        f"""{_PERSONA}

Write the script for a short coaching podcast episode on: "{prompt}"
{_brief_block(brief)}
Two hosts. {ava} is the veteran sales coach who explains things and shares what works in real conversations. {andrew} is the co-host who has just started selling FlexLife; he asks the questions a rookie would actually ask, pushes back when something sounds too good, and summarizes what he learned. They are warm, direct, and occasionally funny. This is spoken audio, so:
- Write the way people talk: contractions, short sentences, no lists, no headings.
- Spell numbers and symbols out in words as they should be spoken (say "percent", "one hundred and ten percent", "zero point five").
- Each turn is 15-70 words. Aim for {spec['turns']} turns and about {spec['words']} words total.
- Open with a quick welcome and what the episode covers, close with {andrew} recapping two or three things a new agent should do next.
- Only use facts that appear in the source excerpts; when the sources do not settle something, have the hosts say so and point the listener to the product guide.

Return JSON with exactly these keys:
{{
  "title": "episode title, under 60 characters",
  "description": "one or two sentences describing the episode",
  "turns": [{{"speaker": "{ava}" or "{andrew}", "text": "what they say"}}]
}}

Source excerpts:
{_context_block(contexts)}""",
    )


# --- Worker ---------------------------------------------------------------------


def _trim_sources(contexts: list[dict[str, Any]], keep: int = 8) -> list[dict[str, Any]]:
    """Sources shown to the learner: one entry per document page, previews shortened."""
    sources: list[dict[str, Any]] = []
    seen: set[tuple[str, Any]] = set()
    for source in format_sources(contexts):
        key = (source["blob_name"], source["page"])
        if key in seen:
            continue
        seen.add(key)
        source["preview"] = source["preview"][:220]
        sources.append(source)
        if len(sources) >= keep:
            break
    return sources


def _words(text: str) -> int:
    return len(text.split())


def friendly_doc_name(blob_name: str) -> str:
    """'flexlife/raw/FlexLife_Product Guide_01.pdf' -> 'FlexLife Product Guide 01'."""
    base = blob_name.rsplit("/", 1)[-1]
    base = re.sub(r"\.(pdf|docx?|html?|txt|pptx?)$", "", base, flags=re.IGNORECASE)
    base = re.sub(r"\s*\(transcribed on[^)]*\)", "", base, flags=re.IGNORECASE)
    return re.sub(r"[_\s]+", " ", base).strip()


def excerpt_refs(contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The numbered excerpt list exactly as the model saw it, so a card's "[3]" can be
    resolved to a document, page and passage in the UI."""
    from .db import citation

    refs = []
    for index, row in enumerate(contexts):
        meta = row.get("metadata") or {}
        refs.append(
            {
                "n": index + 1,
                "doc": friendly_doc_name(row["blob_name"]),
                "page": meta.get("page"),
                "heading": (meta.get("heading_path") or "")[:120],
                "citation": citation(row),
                "preview": row["content"][:320],
            }
        )
    return refs


def run_generation(settings: Settings, client: AzureOpenAIClient, mix_id: int) -> None:
    """Background job: fill in a queued mix. Never raises; failures land on the row."""
    with connect(settings) as conn:
        mix = get_mix(conn, mix_id)
        if not mix:
            return
        update_mix(conn, mix_id, status="generating", error=None)

    kind, length, prompt = mix["kind"], mix["length"], mix["prompt"]
    item = CURRICULUM_BY_KEY.get(mix.get("curriculum_key") or "")
    try:
        brief: str | None = None
        if item:
            queries = [f"pinned:{spec['doc']}" for spec in item["sources"]]
            contexts = gather_pinned_contexts(settings, item)
            brief = item["brief"]
            if not contexts:
                raise RuntimeError("None of this lesson's pinned source sections are in the index.")
        else:
            queries = plan_queries(client, settings, prompt, kind)
            total = 18 if length == "long" else 12
            contexts = gather_contexts(settings, client, queries, total=total)
            if not contexts:
                raise RuntimeError("No documents are indexed yet, so nothing can be generated.")
        sources = _trim_sources(contexts, keep=12 if item else 8)

        fields: dict[str, Any] = {"sources": sources}
        refs = excerpt_refs(contexts)
        if kind == "article":
            content = generate_article(client, settings, prompt, length, contexts, brief)
            body_words = sum(
                _words(" ".join(section.get("paragraphs", []) + section.get("bullets", [])))
                for section in content.get("sections", [])
            )
            fields.update(
                title=content.get("title") or prompt,
                summary=content.get("subtitle") or "",
                duration_seconds=max(60, int(body_words / 200 * 60)),
                content={**content, "queries": queries, "refs": refs},
            )
        elif kind == "flashcards":
            content = generate_flashcards(client, settings, prompt, length, contexts, brief)
            cards = content.get("cards", [])
            fields.update(
                title=content.get("title") or prompt,
                summary=f"{len(cards)} cards",
                duration_seconds=max(60, len(cards) * 30),
                content={**content, "queries": queries, "refs": refs},
            )
        elif kind == "audio":
            content = generate_audio_script(client, settings, prompt, length, contexts, brief)
            turns = [
                {"speaker": t.get("speaker") or HOSTS[0], "text": (t.get("text") or "").strip()}
                for t in content.get("turns", [])
                if (t.get("text") or "").strip()
            ]
            script_words = sum(_words(t["text"]) for t in turns)
            fields.update(
                title=content.get("title") or prompt,
                summary=content.get("description") or "",
                duration_seconds=max(60, int(script_words / 150 * 60)),
                content={**content, "turns": turns, "queries": queries, "refs": refs, "audio_status": "pending"},
            )
            if speech_configured(settings):
                try:
                    audio = get_speech_client(settings).synthesize_dialogue(turns)
                    fields.update(
                        audio=audio,
                        audio_mime=OUTPUT_MIME,
                        duration_seconds=estimate_seconds(audio),
                    )
                    fields["content"]["audio_status"] = "ready"
                except Exception as speech_error:  # noqa: BLE001 - transcript still useful
                    fields["content"]["audio_status"] = "failed"
                    fields["content"]["audio_error"] = f"{type(speech_error).__name__}: {speech_error}"[:400]
            else:
                fields["content"]["audio_status"] = "unconfigured"
                fields["content"]["audio_error"] = (
                    "Azure Speech is not configured. Set AZURE_SPEECH_KEY and AZURE_SPEECH_REGION to render audio."
                )
        else:
            raise ValueError(f"Unknown mix kind: {kind}")

        with connect(settings) as conn:
            update_mix(conn, mix_id, status="ready", error=None, **fields)
    except Exception as error:  # noqa: BLE001 - background job must not crash the server
        traceback.print_exc()
        with connect(settings) as conn:
            update_mix(conn, mix_id, status="failed", error=f"{type(error).__name__}: {error}"[:500])


NARRATED_KINDS = ("audio", "article")


def article_narration(content: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn a generated article into read-aloud segments, in reading order.

    Each segment maps to one block on the article page (``id`` matches the DOM
    anchor the reader highlights while that part plays). Sources are skipped: a
    list of citations is noise when spoken.
    """
    segments: list[dict[str, Any]] = []

    def clean(items: list[Any]) -> list[str]:
        return [str(p).strip() for p in items if str(p or "").strip()]

    def add(seg_id: str, label: str, lead_in: str, body: list[Any]) -> None:
        """A block is only spoken if it has real content; the lead-in alone does not count."""
        body = clean(body)
        if body:
            segments.append({"id": seg_id, "label": label, "paragraphs": clean([lead_in, *body])})

    title = (content.get("title") or "").strip()
    add("intro", title or "Introduction", "", [title, content.get("subtitle") or ""])
    for index, section in enumerate(content.get("sections") or []):
        heading = (section.get("heading") or "").strip()
        body = [*(section.get("paragraphs") or []), *(section.get("bullets") or [])]
        add(f"sec-{index}", heading or f"Section {index + 1}", heading, body)
    add("takeaways", "Key takeaways", "Key takeaways.", content.get("key_takeaways") or [])
    add("say", "Say it like this", "Here is how you can say it to a client.", content.get("say_it_like_this") or [])
    add("watch", "Watch outs", "A few things to watch out for.", content.get("watch_outs") or [])
    return segments


def run_audio_render(settings: Settings, mix_id: int) -> None:
    """Background job: (re)render just the MP3 for a finished mix.

    Audio episodes: re-synthesize the two-host script (used after Azure Speech is
    configured, so already-written episodes do not have to be regenerated).
    Articles: render the "Listen" narration on demand. Articles are never
    narrated at creation time, so only the ones people actually listen to cost
    speech minutes.
    """
    with connect(settings) as conn:
        mix = get_mix(conn, mix_id)
    if not mix or mix["kind"] not in NARRATED_KINDS:
        return
    content = dict(mix.get("content") or {})
    kind = mix["kind"]
    turns = content.get("turns") or []
    segments = article_narration(content) if kind == "article" else []
    if kind == "audio" and not turns:
        return
    if kind == "article" and not segments:
        return
    content["audio_status"] = "rendering"
    content.pop("audio_error", None)
    with connect(settings) as conn:
        update_mix(conn, mix_id, content=content)
    try:
        if not speech_configured(settings):
            raise RuntimeError(
                "Azure Speech is not configured. Set AZURE_SPEECH_KEY and AZURE_SPEECH_REGION to render audio."
            )
        client = get_speech_client(settings)
        content["audio_status"] = "ready"
        if kind == "article":
            audio, starts = client.synthesize_narration(segments)
            # Keep duration_seconds as the read time; the narration has its own length.
            content["narration"] = [
                {"id": seg["id"], "label": seg["label"], "start": start}
                for seg, start in zip(segments, starts)
            ]
            content["audio_seconds"] = estimate_seconds(audio)
            fields: dict[str, Any] = {"audio": audio, "audio_mime": OUTPUT_MIME, "content": content}
        else:
            audio = client.synthesize_dialogue(turns)
            fields = {
                "audio": audio, "audio_mime": OUTPUT_MIME,
                "duration_seconds": estimate_seconds(audio), "content": content,
            }
        with connect(settings) as conn:
            update_mix(conn, mix_id, **fields)
    except Exception as error:  # noqa: BLE001 - keep the transcript, record why audio failed
        traceback.print_exc()
        content["audio_status"] = "failed"
        content["audio_error"] = f"{type(error).__name__}: {error}"[:400]
        with connect(settings) as conn:
            update_mix(conn, mix_id, content=content)


def serialize_mix(row: dict[str, Any], *, include_content: bool = False) -> dict[str, Any]:
    spec = length_spec(row["kind"], row["length"])
    content = row.get("content") or {}
    audio_status = content.get("audio_status") if row["kind"] in NARRATED_KINDS else None
    data = {
        "id": row["id"],
        "kind": row["kind"],
        "prompt": row["prompt"],
        "length": row["length"],
        "length_label": spec["label"],
        "status": row["status"],
        "title": row.get("title") or row["prompt"],
        "summary": row.get("summary") or "",
        "duration_seconds": row.get("duration_seconds") or spec["minutes"] * 60,
        "recommended": bool(row.get("recommended")),
        "tier": row.get("tier"),
        "curriculum_key": row.get("curriculum_key"),
        "has_audio": bool(row.get("has_audio")),
        "audio_status": audio_status,
        "error": row.get("error"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }
    if include_content:
        data["content"] = content
        data["sources"] = row.get("sources") or []
    return data
