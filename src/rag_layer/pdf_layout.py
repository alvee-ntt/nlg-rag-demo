"""Layout-aware PDF extraction.

``pypdf.extract_text()`` emits text in content-stream order -- the order the authoring
tool happened to write objects into the file -- which for designed documents bears no
relation to visual reading order. This module rebuilds reading order geometrically and
recovers the structure (title, headings, tables, disclosures) that a flat string loses.

The only engine-aware code is ``_load_pages``. Everything downstream operates on the
normalized ``Block`` type, so swapping PyMuPDF for another backend is a local change.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

try:  # pragma: no cover - exercised only before dependencies are installed
    import pymupdf
except ImportError:  # pragma: no cover
    try:
        import fitz as pymupdf  # PyMuPDF < 1.24 only exposed the `fitz` name
    except ImportError:
        pymupdf = None


BOLD_MARKERS = ("bold", "black", "heavy", "semibold", "demi")

# Blocks starting below this fraction of page height are running footers.
FOOTER_BAND = 0.95
# A heading must be this much larger than body text.
HEADING_RATIO = 1.1
# Small print (footnotes, legal disclosures) is at most this fraction of body size.
# Deliberately conservative: misfiling real content as boilerplate is far more costly
# than leaving boilerplate in the body, because the former is silent. Slide decks in
# particular mix type sizes freely and punish an aggressive threshold.
FOOTNOTE_RATIO = 0.7
# A title must be this much larger than body text.
TITLE_RATIO = 1.5
# Only the bottom of a page can anchor a disclosure zone.
DISCLOSURE_BAND = 0.6
# An anchor must carry real text. A stray superscript glyph left over as its own block
# ("9" at 5.2pt) must not be allowed to declare half a page to be legal boilerplate.
MIN_ANCHOR_CHARS = 40
# Short text repeated on at least this share of pages is furniture, wherever it sits.
FURNITURE_PAGE_SHARE = 0.5
FURNITURE_MAX_CHARS = 120
# A bold run-in heading at body size must be followed by at least this much prose.
RUN_IN_MIN_BODY = 40

MAX_CUT_DEPTH = 12

# Legal boilerplate is not reliably distinguishable by font size (on these documents
# the disclosures are 10.0pt condensed against 10.5pt body), so anchor on the phrases
# that are stable across the whole issuer corpus, plus the numbered-footnote pattern.
BOILERPLATE_MARKERS = re.compile(
    r"products issued by"
    r"|is a trade name of"
    r"|form series"
    r"|underwritten by"
    r"|no bank or credit union"
    r"|not fdic"
    r"|not a deposit"
    r"|guarantees are dependent upon"
    r"|solely responsible for its own financial condition",
    re.IGNORECASE,
)
FOOTNOTE_LIST = re.compile(r"^\d{1,2}\s+[A-Z(]")

# "Doing Business with National Life Group . . . . . . . . 2" -- leader dots (usually
# letter-spaced) and a trailing page number. Contents listings are purely navigational
# and fully redundant with the heading paths this module now extracts, so they are
# dropped: left in, they match a wide range of questions *about* a document while
# containing no answer to any of them.
TOC_LEADER = re.compile(r"(?:[.·•…]\s*){4,}")
TOC_PAGE_MIN_ENTRIES = 5
TOC_HEADING = re.compile(r"^(table of\s+)?contents\b", re.IGNORECASE)


@dataclass(frozen=True)
class Block:
    """A paragraph-level run of text with its geometry and dominant font."""

    page: int
    bbox: tuple[float, float, float, float]
    text: str
    size: float
    bold: bool
    # Populated when the block is a single table row (cells laid out side by side).
    cells: tuple[str, ...] = ()

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y1(self) -> float:
        return self.bbox[3]


@dataclass(frozen=True)
class Section:
    """A run of text under one heading, carrying its provenance."""

    text: str
    page: int
    heading_path: tuple[str, ...]
    zone: str = "body"

    @property
    def breadcrumb(self) -> str:
        return " > ".join(self.heading_path)


@dataclass
class _Page:
    number: int
    width: float
    height: float
    blocks: list[Block]
    line_height: float


# --------------------------------------------------------------------------- engine


def _is_bold(font: str) -> bool:
    lowered = font.lower()
    return any(marker in lowered for marker in BOLD_MARKERS)


def _dominant_font(raw_block: dict) -> tuple[float, bool]:
    """Font size and weight of the block, weighted by how many characters use it."""
    weights: dict[tuple[float, bool], int] = {}
    for line in raw_block.get("lines", []):
        for span in line.get("spans", []):
            length = len(span.get("text", "").strip())
            if not length:
                continue
            key = (round(span.get("size", 0.0), 1), _is_bold(span.get("font", "")))
            weights[key] = weights.get(key, 0) + length
    if not weights:
        return 0.0, False
    return max(weights.items(), key=lambda item: item[1])[0]


@dataclass(frozen=True)
class _Line:
    bbox: tuple[float, float, float, float]
    text: str
    size: float
    bold: bool


def _block_lines(raw_block: dict) -> list[_Line]:
    lines = []
    for line in raw_block.get("lines", []):
        text = "".join(span.get("text", "") for span in line.get("spans", []))
        if not text.strip():
            continue
        weights: dict[tuple[float, bool], int] = {}
        for span in line.get("spans", []):
            length = len(span.get("text", "").strip())
            if not length:
                continue
            key = (round(span.get("size", 0.0), 1), _is_bold(span.get("font", "")))
            weights[key] = weights.get(key, 0) + length
        size, bold = max(weights.items(), key=lambda item: item[1])[0] if weights else (0.0, False)
        lines.append(_Line(bbox=tuple(line["bbox"]), text=text, size=size, bold=bold))
    return lines


def _split_leading_heading(lines: list[_Line], base: float, line_height: float):
    """Separate a run-in heading from the body copy that shares its block.

    These documents set section headings as the first line of the same text frame as
    the paragraph beneath them, so block-level font stats average the heading away.
    Requires a genuine size step up, not just bold -- inline bold lead-ins like
    "The **Premium Chronic Care Rider**, available ..." must not be promoted.
    """
    if len(lines) < 2 or not base:
        return None
    head, rest = lines[0], lines[1:]
    if not (head.bold and head.size >= base * HEADING_RATIO):
        return None
    if any(line.bold and line.size >= base * HEADING_RATIO for line in rest):
        return None
    if head.bbox[3] - head.bbox[1] > line_height * 2.5:
        return None
    return [head], rest


def _row_cells(lines: list[_Line], line_height: float) -> tuple[str, ...]:
    """Return the cells if these lines form one horizontal row, else ``()``.

    Designed tables in these documents come back as one block per row, with each cell
    as a separate "line" at the same y. Detecting that keeps the row intact.
    """
    if len(lines) < 2:
        return ()
    tops = [line.bbox[1] for line in lines]
    if max(tops) - min(tops) > line_height * 0.6:
        return ()
    ordered = sorted(lines, key=lambda line: line.bbox[0])
    for left, right in zip(ordered, ordered[1:]):
        if right.bbox[0] - left.bbox[2] < 5.0:
            return ()
    cells = tuple(line.text.strip() for line in ordered)
    return cells if all(cells) else ()


def _assemble_text(lines: list[_Line], line_height: float) -> str:
    """Join a block's lines in reading order, banding by y then sorting by x."""
    ordered = sorted(lines, key=lambda line: (line.bbox[1], line.bbox[0]))
    bands: list[list[_Line]] = []
    for line in ordered:
        if bands and line.bbox[1] - bands[-1][0].bbox[1] <= line_height * 0.5:
            bands[-1].append(line)
        else:
            bands.append([line])
    rows = []
    for band in bands:
        band.sort(key=lambda line: line.bbox[0])
        rows.append(" ".join(line.text.strip() for line in band))
    return "\n".join(row for row in rows if row.strip())


def _make_block(page_number: int, lines: list[_Line], line_height: float) -> Block | None:
    if not lines:
        return None
    cells = _row_cells(lines, line_height)
    text = " ".join(cells) if cells else _assemble_text(lines, line_height)
    if not text.strip():
        return None
    weights: dict[tuple[float, bool], int] = {}
    for line in lines:
        weights[(line.size, line.bold)] = weights.get((line.size, line.bold), 0) + len(line.text.strip())
    size, bold = max(weights.items(), key=lambda item: item[1])[0]
    return Block(
        page=page_number,
        bbox=(
            min(line.bbox[0] for line in lines),
            min(line.bbox[1] for line in lines),
            max(line.bbox[2] for line in lines),
            max(line.bbox[3] for line in lines),
        ),
        text=text,
        size=size,
        bold=bold,
        cells=cells,
    )


def _load_pages(content: bytes) -> list[_Page]:
    """Engine adapter -- the only PyMuPDF-aware function in this module.

    Runs in two passes: materialize blocks, measure body text size, then re-split any
    block whose first line is a run-in heading (which needs the body size to detect).
    """
    if pymupdf is None:  # pragma: no cover
        raise RuntimeError("PyMuPDF (pymupdf) is required for PDF extraction")

    staged: list[tuple[_Page, list[list[_Line]]]] = []
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        for number, page in enumerate(doc, start=1):
            raw = page.get_text("dict")
            heights = [
                line["bbox"][3] - line["bbox"][1]
                for raw_block in raw.get("blocks", [])
                if raw_block.get("type") == 0
                for line in raw_block.get("lines", [])
            ]
            line_height = statistics.median(heights) if heights else 12.0

            line_groups = []
            for raw_block in raw.get("blocks", []):
                if raw_block.get("type") != 0:
                    continue
                lines = _block_lines(raw_block)
                if lines:
                    line_groups.append(lines)

            page_obj = _Page(
                number=number,
                width=page.rect.width,
                height=page.rect.height,
                blocks=[],
                line_height=line_height,
            )
            for lines in line_groups:
                block = _make_block(number, lines, line_height)
                if block:
                    page_obj.blocks.append(block)
            staged.append((page_obj, line_groups))

    pages = [page for page, _ in staged]
    base = body_size(pages)

    for page, line_groups in staged:
        rebuilt: list[Block] = []
        for lines in line_groups:
            split = _split_leading_heading(lines, base, page.line_height)
            parts = split if split else [lines]
            for part in parts:
                block = _make_block(page.number, part, page.line_height)
                if block:
                    rebuilt.append(block)
        page.blocks = rebuilt
    return pages


# -------------------------------------------------------------------- reading order


def _best_gap(blocks: list[Block], axis: int, min_gap: float) -> tuple[float, float] | None:
    """Widest whitespace gap along ``axis`` (0 = x, 1 = y) that separates the blocks.

    Returns ``(cut_position, gap_width)`` or ``None`` if nothing clears ``min_gap``.
    """
    intervals = sorted((b.bbox[axis], b.bbox[axis + 2]) for b in blocks)
    best: tuple[float, float] | None = None
    reach = intervals[0][1]
    for low, high in intervals[1:]:
        gap = low - reach
        if gap >= min_gap and (best is None or gap > best[1]):
            best = (reach + gap / 2.0, gap)
        reach = max(reach, high)
    return best


def _xy_cut(blocks: list[Block], min_h_gap: float, min_v_gap: float, depth: int = 0) -> list[Block]:
    """Recursive XY-cut: split on the widest whitespace gap, recurse, concatenate.

    Horizontal cuts read top-to-bottom, vertical cuts left-to-right. Preferring the
    *wider* gap is what keeps true multi-column pages from interleaving, while still
    letting side-by-side callouts be read as columns rather than as rows.
    """
    if len(blocks) <= 1 or depth >= MAX_CUT_DEPTH:
        return sorted(blocks, key=lambda b: (b.y0, b.x0))

    horizontal = _best_gap(blocks, axis=1, min_gap=min_h_gap)
    vertical = _best_gap(blocks, axis=0, min_gap=min_v_gap)

    if horizontal and (vertical is None or horizontal[1] >= vertical[1]):
        cut = horizontal[0]
        above = [b for b in blocks if b.y1 <= cut]
        below = [b for b in blocks if b.y1 > cut]
        return _xy_cut(above, min_h_gap, min_v_gap, depth + 1) + _xy_cut(
            below, min_h_gap, min_v_gap, depth + 1
        )

    if vertical:
        cut = vertical[0]
        left = [b for b in blocks if b.x1 <= cut]
        right = [b for b in blocks if b.x1 > cut]
        return _xy_cut(left, min_h_gap, min_v_gap, depth + 1) + _xy_cut(
            right, min_h_gap, min_v_gap, depth + 1
        )

    return sorted(blocks, key=lambda b: (b.y0, b.x0))


def _footer_signature(text: str) -> str:
    """Digit-insensitive signature so 'Page 1 of 9' and 'Page 2 of 9' match."""
    collapsed = re.sub(r"\s+", " ", re.sub(r"\d+", "#", text)).strip()
    return " ".join(collapsed.split()[:3])


def _strip_running_furniture(pages: list[_Page]) -> None:
    """Drop page numbers, TC codes and other furniture repeated across pages."""
    seen: dict[str, set[int]] = {}
    for page in pages:
        for block in page.blocks:
            seen.setdefault(_footer_signature(block.text), set()).add(page.number)

    repeated = {sig for sig, page_numbers in seen.items() if len(page_numbers) > 1}
    # Short text on most pages is furniture regardless of where it sits. Slide decks
    # place their "For Agent Use Only ..." strap well above a print footer's band.
    pervasive = {
        sig
        for sig, page_numbers in seen.items()
        if len(page_numbers) >= max(2, len(pages) * FURNITURE_PAGE_SHARE)
    }

    for page in pages:
        kept = []
        for block in page.blocks:
            signature = _footer_signature(block.text)
            short = len(block.text.strip()) <= FURNITURE_MAX_CHARS
            if block.y0 > page.height * FOOTER_BAND:
                continue
            if len(pages) > 1 and signature in repeated and block.y0 > page.height * 0.85:
                continue
            if len(pages) > 2 and short and signature in pervasive:
                continue
            kept.append(block)
        page.blocks = kept


def reading_order(pages: list[_Page]) -> list[Block]:
    """Blocks across the whole document in visual reading order."""
    ordered: list[Block] = []
    for page in pages:
        if not page.blocks:
            continue
        min_h_gap = max(6.0, page.line_height * 0.6)
        min_v_gap = max(9.0, page.line_height * 0.8)
        ordered.extend(_xy_cut(page.blocks, min_h_gap, min_v_gap))
    return ordered


# ------------------------------------------------------------------- classification


def _is_toc_block(block: Block) -> bool:
    """Whether this block is a table-of-contents listing rather than content."""
    return len(TOC_LEADER.findall(block.text)) >= 2


def _toc_cutoffs(pages: list[_Page]) -> dict[int, float]:
    """For each contents page, the y at which the listing starts.

    Everything below the cutoff is dropped. Working page-wide rather than line-by-line
    matters because entries arrive as one block per line and a wrapped entry ("Fully
    Underwritten Elite/Preferred/Select Criteria") carries no leader dots at all, so
    dropping only matching lines strands the fragments. Cutting from the heading down
    rather than dropping the page keeps cover material -- the rider guide's contents
    page also carries the subtitle naming every rider the document covers.
    """
    cutoffs: dict[int, float] = {}
    for page in pages:
        # Count leader runs rather than "entry ... N" lines: the three parts of an entry
        # (text, dots, page number) are separate columns, so they interleave and the
        # number often lands mid-line -- "Quick Reference Guide . . 2 . . . . . .".
        leaders = sum(len(TOC_LEADER.findall(b.text)) for b in page.blocks)
        if leaders < TOC_PAGE_MIN_ENTRIES:
            continue
        anchors = [b.y0 for b in page.blocks if TOC_HEADING.match(b.text.strip())]
        if not anchors:
            anchors = [b.y0 for b in page.blocks if TOC_LEADER.search(b.text)]
        cutoffs[page.number] = min(anchors) if anchors else 0.0
    return cutoffs


def _is_boilerplate_text(text: str) -> bool:
    """Unambiguous legalese only -- safe to use when deciding what body size even is.

    Deliberately excludes the numbered-footnote pattern, which needs a size check to be
    trustworthy (see :func:`_is_boilerplate`).
    """
    return bool(BOILERPLATE_MARKERS.search(text.strip()))


def body_size(pages: list[_Page]) -> float:
    """Modal font size of body copy, weighted by character count.

    Blocks that are lexically identifiable as legal boilerplate are excluded from the
    vote -- on these flyers the disclosures out-number the real body copy by character
    count and would otherwise define "body size" as their own size. The exclusion is
    lexical, so this stays non-circular: it never consults font size to decide.
    """
    weights: dict[float, int] = {}
    for page in pages:
        for block in page.blocks:
            if _is_boilerplate_text(block.text):
                continue
            weights[block.size] = weights.get(block.size, 0) + len(block.text)
    if not weights:
        return 10.0
    return max(weights.items(), key=lambda item: item[1])[0]


def _is_boilerplate(block: Block, base: float) -> bool:
    if block.size and base and block.size < base * FOOTNOTE_RATIO:
        return True
    if _is_boilerplate_text(block.text):
        return True
    # A leading number only implies a footnote list if the type is also smaller than
    # body copy. Superscript footnote *references* flatten into the text, so ordinary
    # body lines routinely start with a digit -- "9 Provides a one-time accumulated
    # value credit ..." is product copy, not a disclosure.
    return bool(
        FOOTNOTE_LIST.match(block.text.strip())
        and block.size
        and base
        and block.size < base
    )


def _disclosure_floor(page: _Page, base: float) -> float:
    """The y below which everything on the page is legal small print.

    Extends downward only. An earlier version also walked *upward* through contiguous
    blocks to pick up the "Products issued by ..." lockup above the footnotes; on
    evenly-spaced layouts that walk never terminated and swallowed whole pages of real
    content. Blocks above the floor are still caught individually by
    :func:`_is_boilerplate`, which handles that lockup by phrase instead.
    """
    anchors = [
        b
        for b in page.blocks
        if b.y0 > page.height * DISCLOSURE_BAND
        and len(b.text.strip()) >= MIN_ANCHOR_CHARS
        and _is_boilerplate(b, base)
    ]
    if not anchors:
        return float("inf")
    return min(b.y0 for b in anchors)


def _heading_text(block: Block) -> str:
    """Collapse a wrapped heading onto one line."""
    return " ".join(block.text.split())


def _is_heading(block: Block, base: float, following: Block | None = None) -> bool:
    text = block.text.strip()
    # A reconstructed table row is never a heading, whatever it is set in.
    if not text or block.cells or len(text) >= 100:
        return False
    # Allow a single wrap: "Fully Underwritten Elite/Preferred/Select Criteria
    # (Term Products)" is one heading set over two lines.
    if text.count("\n") > 1:
        return False
    # Section openers are often set large but light rather than bold -- the 26pt green
    # "Life Underwriting Requirements" opening each product section is not bold.
    if base and block.size >= base * TITLE_RATIO:
        return True
    if not (block.bold and base and block.size >= base):
        return False
    if block.size > base * HEADING_RATIO:
        return True
    # Bold at body size still reads as a heading -- product guides label each entry with
    # a bold run-in at the same point size as the copy beneath it ("Balanced Trend",
    # "U.S. Pacesetter Index"), and those labels are the disambiguating term. But table
    # cells are bold at body size too. The difference is that a heading introduces
    # prose, so require the next block to be ordinary body copy. Without this, a rate
    # table's cells ("S&P 500", "Year", "265.00%") each become a section.
    if following is None:
        return True
    return not following.bold and len(following.text.strip()) >= RUN_IN_MIN_BODY


def _heading_flags(ordered: list[Block], base: float) -> list[bool]:
    """Heading test for every block, with its successor available for the run-in rule."""
    return [
        _is_heading(block, base, ordered[index + 1] if index + 1 < len(ordered) else None)
        for index, block in enumerate(ordered)
    ]


def _heading_levels(ordered: list[Block], base: float, title: str | None) -> dict[float, int]:
    """Map each heading font size to a nesting depth.

    Only sizes used for headings on **more than one page** define a level. A heading
    style confined to a single page is not organising the document -- on the Premium
    Chronic Care flyer the 18pt stat callouts ("90%", "$118,104") are display type, and
    treating them as parents would file "Monthly payouts" underneath "$118,104". Those
    become leaves at the deepest level instead.
    """
    pages_by_size: dict[float, set[int]] = {}
    for block, is_heading in zip(ordered, _heading_flags(ordered, base)):
        if not is_heading or (title and _heading_text(block) == title):
            continue
        pages_by_size.setdefault(block.size, set()).add(block.page)

    structural = sorted(
        (size for size, pages in pages_by_size.items() if len(pages) > 1), reverse=True
    )
    levels = {size: index + 1 for index, size in enumerate(structural)}
    leaf = len(structural) or 1
    for size in pages_by_size:
        levels.setdefault(size, leaf)
    return levels


# ------------------------------------------------------------------------ rendering


def _render_table(rows: list[Block]) -> str:
    header, *body = rows
    width = len(header.cells)
    lines = [
        "| " + " | ".join(header.cells) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    for row in body:
        lines.append("| " + " | ".join(row.cells) + " |")
    return "\n".join(lines)


def _group_tables(blocks: list[Block]) -> list[Block | list[Block]]:
    """Collapse runs of >=2 consecutive same-width row blocks into table groups."""
    grouped: list[Block | list[Block]] = []
    run: list[Block] = []

    def flush() -> None:
        if len(run) >= 2:
            grouped.append(list(run))
        else:
            grouped.extend(run)
        run.clear()

    for block in blocks:
        if block.cells and (not run or len(run[0].cells) == len(block.cells)):
            run.append(block)
            continue
        flush()
        if block.cells:
            run.append(block)
        else:
            grouped.append(block)
    flush()
    return grouped


def extract_sections(content: bytes) -> list[Section]:
    """Extract a PDF as ordered, heading-scoped sections with page provenance."""
    pages = _load_pages(content)
    _strip_running_furniture(pages)

    if not any(page.blocks for page in pages):
        return []

    base = body_size(pages)
    floors = {page.number: _disclosure_floor(page, base) for page in pages}
    ordered = reading_order(pages)

    title = _find_title(ordered, base)
    # Levels are inferred from body content only. Boilerplate is set in its own styles
    # -- the 18pt "Products issued by" lockup repeats on every back page and would
    # otherwise masquerade as a document-wide structural heading level.
    body_blocks = [
        b
        for b in ordered
        if b.y0 < floors[b.page] and not _is_boilerplate(b, base)
    ]
    levels = _heading_levels(body_blocks, base, title)
    root: tuple[str, ...] = (title,) if title else ()

    body_sections: list[Section] = []
    disclosures: list[Block] = []

    stack: list[str] = []
    path: tuple[str, ...] = root
    buffer: list[Block] = []
    section_page = ordered[0].page

    def emit() -> bool:
        """Emit the buffered blocks as a section. Returns whether anything was written."""
        if not buffer:
            return False
        text = _render_blocks(buffer)
        buffer.clear()
        if not text.strip():
            return False
        body_sections.append(Section(text=text, page=section_page, heading_path=path))
        return True

    toc_cutoffs = _toc_cutoffs(pages)
    for block, is_heading in zip(ordered, _heading_flags(ordered, base)):
        if block.y0 >= toc_cutoffs.get(block.page, float("inf")) or _is_toc_block(block):
            continue
        if block.y0 >= floors[block.page] or _is_boilerplate(block, base):
            disclosures.append(block)
            continue
        if title and _heading_text(block) == title:
            continue

        if is_heading:
            if not emit() and len(path) > len(root):
                # A heading with no body beneath it is still content -- standalone
                # notices like "This flyer is not for use in CA or NY" are set as bold
                # callouts. Demote it to body text under its parent rather than drop it.
                body_sections.append(
                    Section(text=path[-1], page=section_page, heading_path=path[:-1])
                )
            heading = _heading_text(block)
            depth = levels.get(block.size, len(stack) + 1)
            stack = stack[: depth - 1] + [heading]
            path = root + tuple(stack)
            section_page = block.page
            continue

        # Close the section at a page boundary so provenance stays exact. Without this
        # a section that runs across pages keeps the page it started on, and citations
        # point readers many pages away from the text they are reading.
        if buffer and block.page != section_page:
            emit()
            section_page = block.page

        if not buffer:
            section_page = block.page
        buffer.append(block)
    emit()

    sections = body_sections
    if disclosures:
        disclosure_path = (title, "Disclosures") if title else ("Disclosures",)
        for page_number in sorted({b.page for b in disclosures}):
            page_blocks = [b for b in disclosures if b.page == page_number]
            sections.append(
                Section(
                    text=_render_blocks(page_blocks),
                    page=page_number,
                    heading_path=disclosure_path,
                    zone="disclosure",
                )
            )
    return [s for s in sections if s.text.strip()]


def _render_blocks(blocks: list[Block]) -> str:
    parts = []
    for item in _group_tables(blocks):
        if isinstance(item, list):
            parts.append(_render_table(item))
        else:
            parts.append(item.text)
    return "\n\n".join(parts)


def _find_title(ordered: list[Block], base: float) -> str | None:
    candidates = [b for b in ordered if b.page == 1 and len(b.text) < 120 and "\n" not in b.text]
    if not candidates:
        return None
    largest = max(candidates, key=lambda b: b.size)
    if largest.size < base * TITLE_RATIO:
        return None
    return largest.text.strip()


def render_markdown(sections: list[Section]) -> str:
    """Flatten sections back to markdown -- used by the plain ``extract_text`` path."""
    out: list[str] = []
    seen_path: tuple[str, ...] | None = None
    for section in sections:
        if section.heading_path != seen_path:
            for depth, heading in enumerate(section.heading_path, start=1):
                out.append(f"{'#' * min(depth, 6)} {heading}")
            seen_path = section.heading_path
        out.append(section.text)
    return "\n\n".join(out).strip()
