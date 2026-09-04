from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:  # pragma: no cover - exercised only before dependencies are installed
    RecursiveCharacterTextSplitter = None

from .pdf_layout import Section

# A chunk shorter than this fraction of chunk_size carries too little signal to embed
# well on its own and is merged into an adjacent chunk.
RUNT_RATIO = 0.4


@dataclass(frozen=True)
class Chunk:
    """A unit of text to embed, with the provenance needed to cite it."""

    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


SEPARATORS = [
    "\n\n",
    "\n",
    ". ",
    "? ",
    "! ",
    "; ",
    ": ",
    ", ",
    " ",
    "",
]


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    clean = normalize_text(text)
    if not clean:
        return []
    if chunk_size <= overlap:
        raise ValueError("CHUNK_SIZE must be larger than CHUNK_OVERLAP")

    if RecursiveCharacterTextSplitter is not None:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=overlap,
            separators=SEPARATORS,
            keep_separator="end",
        )
        return [chunk.strip() for chunk in splitter.split_text(clean) if chunk.strip()]

    return _fallback_chunk_text(clean, chunk_size, overlap)


def _fallback_chunk_text(clean: str, chunk_size: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(clean):
        end = min(start + chunk_size, len(clean))
        if end < len(clean):
            boundary, separator = max(
                (clean.rfind(separator, start, end), separator)
                for separator in SEPARATORS
                if separator
            )
            if boundary > start + chunk_size // 2:
                end = boundary + len(separator)
        chunk = clean[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(clean):
            break
        start = max(0, end - overlap)
    return chunks


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ------------------------------------------------------------- structure-aware path


def chunk_sections(sections: list[Section], chunk_size: int, overlap: int) -> list[Chunk]:
    """Chunk on the document's own structure rather than on a blind character window.

    Sections already respect heading boundaries, so splitting only happens *within* an
    oversized section. Each chunk is prefixed with its heading breadcrumb, which gives
    the embedding the context that a mid-document window would otherwise lack.
    """
    if chunk_size <= overlap:
        raise ValueError("CHUNK_SIZE must be larger than CHUNK_OVERLAP")

    pieces: list[tuple[str, Section]] = []
    for section in sections:
        text = normalize_text(section.text)
        if not text:
            continue
        # Reserve room for the breadcrumb that gets prepended to every chunk.
        prefix = len(section.breadcrumb) + 2 if section.breadcrumb else 0
        budget = max(chunk_size - prefix, chunk_size // 2)
        for piece in _pack_units(_atomic_units(text), budget, overlap):
            pieces.append((piece, section))

    return [
        Chunk(
            content=f"{section.breadcrumb}\n\n{text}" if section.breadcrumb else text,
            metadata={
                "page": section.page,
                "zone": section.zone,
                "heading_path": section.breadcrumb,
            },
        )
        for text, section in _merge_runts(pieces, chunk_size)
    ]


def _atomic_units(text: str) -> list[str]:
    """Blank-line-separated units. A markdown table arrives as one unit and stays one."""
    return [unit.strip() for unit in text.split("\n\n") if unit.strip()]


def _pack_units(units: list[str], budget: int, overlap: int) -> list[str]:
    """Greedily fill chunks with whole units so tables and paragraphs stay intact."""
    packed: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            packed.append("\n\n".join(current))
            current.clear()

    for unit in units:
        if len(unit) > budget:
            flush()
            packed.extend(_split_oversized(unit, budget, overlap))
            continue
        projected = sum(len(u) for u in current) + len(unit) + 2 * len(current)
        if current and projected > budget:
            flush()
        current.append(unit)
    flush()
    return packed


def _split_oversized(unit: str, budget: int, overlap: int) -> list[str]:
    if RecursiveCharacterTextSplitter is not None:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=budget,
            chunk_overlap=min(overlap, budget // 4),
            separators=SEPARATORS,
            keep_separator="end",
        )
        return [piece.strip() for piece in splitter.split_text(unit) if piece.strip()]
    return _fallback_chunk_text(unit, budget, min(overlap, budget // 4))


def _merge_runts(
    pieces: list[tuple[str, Section]], chunk_size: int
) -> list[tuple[str, Section]]:
    """Fold undersized chunks into their neighbour.

    Restricted to the same page and zone: consolidating is a size optimisation, and
    blending body copy with legal disclosures, or the end of one page with the start of
    the next, costs more retrieval precision than the extra chunk does.
    """
    minimum = int(chunk_size * RUNT_RATIO)
    # Absorbing a runt is worth a little overflow -- a 150-character orphan retrieves
    # far worse than a slightly oversized but complete chunk.
    ceiling = int(chunk_size * 1.1)
    merged: list[tuple[str, Section]] = []
    for text, section in pieces:
        if merged:
            previous, previous_section = merged[-1]
            mergeable = (
                previous_section.zone == section.zone
                and previous_section.page == section.page
                and (len(text) < minimum or len(previous) < minimum)
                and len(previous) + len(text) + 2 <= ceiling
            )
            if mergeable:
                joined = previous
                depth = len(section.heading_path)
                is_ancestor = section.heading_path == previous_section.heading_path[:depth]
                if section.heading_path and not is_ancestor:
                    # Keep the absorbed section's own heading inline so it is not lost.
                    joined += f"\n\n{section.heading_path[-1]}"
                merged[-1] = (f"{joined}\n\n{text}", previous_section)
                continue
        merged.append((text, section))
    return merged

