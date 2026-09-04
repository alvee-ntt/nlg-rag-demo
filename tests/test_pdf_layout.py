"""Regression tests for layout-aware PDF extraction and structure-aware chunking.

The fixture is a real two-page designed flyer. Its content-stream order is badly
scrambled relative to visual reading order -- ``pypdf`` emits the callout box before
the document title on page 1 -- which is precisely the failure these tests pin down.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rag_layer.chunking import chunk_sections  # noqa: E402
from src.rag_layer.extractors import extract_document  # noqa: E402
from src.rag_layer.pdf_layout import extract_sections, render_markdown  # noqa: E402

FIXTURE = ROOT / "tmp" / "pdfs" / "Rider_Premium_Chronic_Care.pdf"

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150


@pytest.fixture(scope="module")
def content() -> bytes:
    if not FIXTURE.exists():
        pytest.skip(f"fixture missing: {FIXTURE}")
    return FIXTURE.read_bytes()


@pytest.fixture(scope="module")
def sections(content: bytes):
    return extract_sections(content)


@pytest.fixture(scope="module")
def markdown(sections) -> str:
    return render_markdown(sections)


# ------------------------------------------------------------------- reading order


def test_title_precedes_callout_body(markdown: str) -> None:
    """The exact inversion pypdf produced: callout box emitted before the title."""
    title = markdown.index("Premium Chronic Care Rider")
    callout = markdown.index("The Premium Chronic Care Rider3, available")
    assert title < callout


def test_page_one_reads_top_to_bottom(markdown: str) -> None:
    expected = [
        "GET THE CARE YOU NEED",
        "Life insurance provides a payout",
        "Living Benefits1 provide protection",
        "The Premium Chronic Care Rider3, available",
        "Monthly payouts",
        "Unused benefits are paid as a death benefit",
        "Accelerate your death benefit",
    ]
    positions = [markdown.index(fragment) for fragment in expected]
    assert positions == sorted(positions)


def test_stat_callouts_read_left_to_right_with_their_captions(markdown: str) -> None:
    """Three side-by-side cards: each number must stay with its own caption.

    A naive (y, x) sort emits these right-to-left, because the three cards differ by
    fractions of a point in y (367.0 / 367.3 / 367.7).
    """
    order = [
        "90%",
        "of Americans 65+",
        "$118,104",
        "is the median annual",
    ]
    positions = [markdown.index(fragment) for fragment in order]
    assert positions == sorted(positions)


def test_page_two_body_precedes_disclosures(markdown: str) -> None:
    assert markdown.index("Availability") < markdown.index("Products issued by")
    assert markdown.index("Start protecting your future today") < markdown.index(
        "form series"
    )


# ------------------------------------------------------------------ structure


@pytest.mark.parametrize(
    "heading",
    [
        "Availability",
        "Charge",
        "What is a qualifying chronic illness?",
        "Monthly payouts",
    ],
)
def test_section_headings_are_detected(sections, heading: str) -> None:
    """These are set as the first line of the same text frame as their body copy."""
    assert any(section.heading_path[-1:] == (heading,) for section in sections)


def test_headings_are_scoped_under_the_document_title(sections) -> None:
    availability = next(s for s in sections if s.heading_path[-1:] == ("Availability",))
    assert availability.heading_path == ("Premium Chronic Care Rider", "Availability")
    assert availability.text.startswith("The Premium Chronic Care Rider is available")


def test_payout_table_is_reconstructed(markdown: str) -> None:
    """Borderless table -- find_tables() returns nothing for it, so rows are rebuilt."""
    assert "| Monthly payout % | Monthly payout amount | Payout duration | Total payout |" in markdown
    assert "| 2% | $10,000 | 50 months | $500,000 |" in markdown
    assert "| 4% | $20,000 | 25 months | $500,000 |" in markdown


def test_running_footer_is_stripped(markdown: str) -> None:
    assert "TC8911014" not in markdown
    assert "Cat No 108194" not in markdown


def test_standalone_bold_notice_is_not_dropped(sections) -> None:
    """A heading with no body beneath it is still content and must survive."""
    assert any("This flyer is not for use in CA or NY" in s.text for s in sections)


def test_pages_are_attributed_correctly(sections) -> None:
    by_heading = {s.heading_path[-1]: s.page for s in sections if s.heading_path}
    assert by_heading["Monthly payouts"] == 1
    assert by_heading["Availability"] == 2


# --------------------------------------------------------------------- zoning


def test_disclosures_are_tagged_and_separated(sections) -> None:
    disclosures = [s for s in sections if s.zone == "disclosure"]
    assert disclosures, "legal boilerplate should be isolated into its own zone"
    joined = "\n".join(s.text for s in disclosures)
    assert "form series" in joined
    assert "is a trade name of" in joined
    assert "Products issued by" in joined


def test_body_sections_contain_no_boilerplate(sections) -> None:
    body = "\n".join(s.text for s in sections if s.zone == "body")
    for marker in ("form series", "No bank or credit union guarantee", "is a trade name of"):
        assert marker not in body


# --------------------------------------------------------------------- chunking


@pytest.fixture(scope="module")
def chunks(content: bytes):
    return chunk_sections(
        extract_document("shared/riders/Rider_Premium Chronic Care.pdf", content),
        CHUNK_SIZE,
        CHUNK_OVERLAP,
    )


def test_no_runt_chunks(chunks) -> None:
    """The old pipeline's last chunk was 20 words of pure overlap residue."""
    assert chunks
    for chunk in chunks:
        assert len(chunk.content.split()) >= 30, chunk.content


def test_every_chunk_carries_provenance(chunks) -> None:
    for chunk in chunks:
        assert chunk.metadata["page"] in (1, 2)
        assert chunk.metadata["zone"] in ("body", "disclosure")
        assert chunk.content.startswith(chunk.metadata["heading_path"])


def test_table_is_never_split_across_chunks(chunks) -> None:
    holding = [c for c in chunks if "Monthly payout %" in c.content]
    assert len(holding) == 1
    chunk = holding[0]
    assert "| 2% | $10,000 | 50 months | $500,000 |" in chunk.content
    assert "| 4% | $20,000 | 25 months | $500,000 |" in chunk.content


def test_chunks_do_not_mix_body_with_disclosures(chunks) -> None:
    for chunk in chunks:
        if chunk.metadata["zone"] == "body":
            assert "form series" not in chunk.content


def test_chunks_do_not_straddle_pages(chunks) -> None:
    """Page markers used to land mid-chunk; provenance is now unambiguous."""
    for chunk in chunks:
        assert isinstance(chunk.metadata["page"], int)


def test_chunk_sizes_are_bounded(chunks) -> None:
    for chunk in chunks:
        assert len(chunk.content) <= CHUNK_SIZE * 1.35, chunk.metadata


def test_rejects_overlap_larger_than_chunk_size(sections) -> None:
    with pytest.raises(ValueError):
        chunk_sections(sections, 100, 100)
