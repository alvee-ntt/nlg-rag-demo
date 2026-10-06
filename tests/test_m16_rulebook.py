"""M16 — the underwriting rulebook and the case-sheet logic built on it.

Pure lookups, no LLM, no network. The grid expectations come from the answer key in
``tests/underwriting_guide_questions.md`` (rows 1-6), which was written from the guide
independently of the rulebook.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rag_layer import underwriting as uw  # noqa: E402

RB = uw.load_rulebook()


# --- rulebook integrity ------------------------------------------------------------

def test_every_fact_has_a_page_a_question_and_a_source():
    for key, spec in {**RB["facts"], **RB["condition_facts"]}.items():
        assert spec["ask"] and spec["why"] and spec["source"] == "guide", key
    for key, spec in RB["facts"].items():
        assert isinstance(spec["page"], int), key


def test_choice_facts_have_options():
    for key, spec in {**RB["facts"], **RB["condition_facts"]}.items():
        if spec["type"] == "choice":
            assert len(spec["options"]) >= 2, key


def test_condition_needs_reference_known_condition_facts():
    assert len(RB["conditions"]) == 93
    for key, condition in RB["conditions"].items():
        assert condition["best_offer"] in uw.BEST_OFFER_LABELS, key
        assert condition["guide_text"] and condition["page"] in (35, 36, 37), key
        for need in condition["needs"]:
            assert need in RB["condition_facts"], (key, need)


def test_condition_mappings_point_at_real_conditions():
    for condition_id in [*RB["aps_condition_ids"], *RB["elite_preferred_excluded_ids"]]:
        assert condition_id in RB["conditions"], condition_id


def test_grid_rows_cover_every_age_band():
    grid = RB["requirements_grid"]
    assert len(grid["rows"]) == 7
    for row in grid["rows"]:
        assert len(row["cells"]) == len(grid["age_bands"]) == 8
        for cell in row["cells"]:
            assert all(part in grid["codes"] for part in cell.split("/")), cell


def test_build_table_is_complete_and_contiguous():
    rows = RB["build_table"]["rows"]
    assert [r["height_in"] for r in rows] == list(range(56, 81))
    for row in rows:
        assert len(row["weights"]) == 6
        for (_, high), (low, _) in zip(row["weights"], row["weights"][1:]):
            assert low == high + 1, row


def test_best_offer_spot_checks_against_the_guide():
    expect = {
        "diabetes_type_2": "standard", "diabetes_type_1": "substandard", "anxiety": "better_than_standard",
        "copd": "substandard", "hypertension": "better_than_standard", "sleep_apnea": "better_than_standard",
        "stroke_cva": "substandard", "melanoma": "standard", "varicose_veins": "better_than_standard",
        "myasthenia_gravis": "substandard", "ulcerative_colitis": "standard",
    }
    for condition_id, tier in expect.items():
        assert RB["conditions"][condition_id]["best_offer"] == tier, condition_id


# --- grid lookups (answer key: underwriting_guide_questions.md rows 1-6) -----------

@pytest.mark.parametrize(
    "age, face, code",
    [
        (45, 1_500_000, "A"),        # 1: application only (ages 41-50 up to $2M)
        (55, 1_500_000, "D"),        # 2: exam, blood and urine
        (35, 4_000_000, "D"),        # 3: exam and labs
        (72, 100_000, "ME/APS"),     # 6: 70+ at every face amount
        (72, 20_000_000, "ME/APS"),
        (46, 2_000_000, "A"),        # band edges are inclusive on the upper bound
        (46, 2_000_001, "A/APS"),
        (10, 5_000_000, "A/APS"),
        (67, 250_000, "D"),
    ],
)
def test_requirement_code(age, face, code):
    assert uw.requirement_code(age, face) == code


def test_describe_code_spells_out_requirements():
    assert "no exam" in uw.describe_code("A")
    assert "exam" in uw.describe_code("D/APS") and "physician statement" in uw.describe_code("D/APS")
    assert "ekg" in uw.describe_code("ME/APS")


def test_requirements_by_amount_collapses_equal_rows():
    bands = uw.requirements_by_amount(46)
    assert [b["code"] for b in bands] == ["A", "A/APS", "D/APS"]
    assert bands[0] == {"over": 0, "up_to": 2_000_000, "code": "A"}
    assert bands[-1]["up_to"] is None


def test_financial_requirements():
    assert uw.financial_requirements(46, 1_000_000) == []
    assert uw.financial_requirements(46, 3_000_000) == ["E-Inspection"]
    six = uw.financial_requirements(46, 6_000_000)
    assert six == ["E-Inspection", "Confidential Financial Questionnaire (Form 1392)"]
    assert "Third Party-Verified Financials" in uw.financial_requirements(72, 6_000_000)
    assert "Income Verification" in uw.financial_requirements(46, 12_000_000)
    # New York needs the CFQ at every face amount.
    assert uw.financial_requirements(46, 500_000, "NY") == ["Confidential Financial Questionnaire (Form 1392)"]


def test_income_multiple_by_age():
    assert uw.income_multiple(25) == 40
    assert uw.income_multiple(46) == 25
    assert uw.income_multiple(68) == 5
    assert uw.income_multiple(75) is None


# --- normalizing values ------------------------------------------------------------

def test_normalize_value_by_type():
    facts = RB["facts"]
    assert uw.normalize_value(facts["age"], "46") == {"value": 46, "display": "46"}
    assert uw.normalize_value(facts["age"], 400) is None
    assert uw.normalize_value(facts["face_amount"], "1.5m")["value"] == 1_500_000
    assert uw.normalize_value(facts["face_amount"], 2_000_000)["display"] == "$1M to $2M"
    assert uw.normalize_value(facts["tobacco"], "Within the last 12 months")["value"] == "within_12m"
    assert uw.normalize_value(facts["tobacco"], "sometimes") is None
    assert uw.normalize_value(facts["physical_24m"], "yes")["value"] is True
    build = uw.normalize_value(facts["build"], {"height_in": 66, "weight_lb": 190})
    assert build["display"] == "5'6\", 190 lb"
    assert uw.normalize_value(facts["build"], "tall") is None
    assert uw.normalize_value(facts["tobacco"], "Not sure yet")["value"] == uw.UNKNOWN


# --- case sheet --------------------------------------------------------------------

def _mock_client():
    """The client from the F2.1 screen: 46, type 2 diabetes on metformin."""
    return uw.merge_extracted(
        uw.empty_case(),
        conditions=["diabetes_type_2"],
        facts={"age": 46, "medication": "metformin", "need": "family protection"},
    )


def test_merge_drops_unknown_keys_and_bad_values():
    case = uw.merge_extracted(
        uw.empty_case(),
        conditions=["diabetes_type_2", "made_up_condition"],
        facts={"age": 46, "ssn": "123-45-6789", "client_name": "Pat", "tobacco": "maybe",
               "copd.when": "2019", "diabetes_type_2.when": "2019"},
    )
    assert case["conditions"] == ["diabetes_type_2"]
    assert set(case["facts"]) == {"age", "diabetes_type_2.when"}


def test_clean_case_revalidates_what_the_browser_sends_back():
    dirty = {
        "v": 1, "conditions": ["diabetes_type_2", "nope"],
        "facts": {"age": {"value": 46, "display": "<b>46</b>"}, "name": {"value": "Pat"},
                  "tobacco": {"status": "skipped"}, "build": {"value": "big"}},
        "asked": ["age", "name", "tobacco"],
    }
    case = uw.clean_case(dirty)
    assert case["conditions"] == ["diabetes_type_2"]
    assert case["facts"] == {"age": {"value": 46, "display": "46"}, "tobacco": {"status": "skipped"}}
    assert case["asked"] == ["age", "tobacco"]
    assert uw.clean_case(None) == uw.empty_case()
    assert uw.clean_case("garbage") == uw.empty_case()


def test_missing_shrinks_as_facts_arrive_and_skips_count_as_handled():
    case = _mock_client()
    missing = uw.missing_keys(case)
    assert missing[:3] == ["face_amount", "tobacco", "build"]
    assert "diabetes_type_2.insulin" in missing
    assert "physical_24m" not in missing  # only asked from age 60
    assert "need" not in missing and "earned_income" not in uw.required_keys(case)  # context never blocks

    case = uw.merge_extracted(case, facts={"tobacco": "none_60m", "face_amount": 1_000_000})
    case["facts"]["build"] = {"status": "skipped"}
    assert not {"face_amount", "tobacco", "build"} & set(uw.missing_keys(case))


def test_age_sixty_adds_the_physical_question():
    case = uw.merge_extracted(uw.empty_case(), facts={"age": 62})
    assert "physical_24m" in uw.required_keys(case)


def test_each_fact_holds_up_an_answer_only_once():
    case = _mock_client()
    first = uw.unasked_missing(case)
    assert first
    case = uw.mark_asked(case, first)
    assert uw.unasked_missing(case) == []
    # A newly mentioned condition brings new, not-yet-asked facts.
    case = uw.merge_extracted(case, conditions=["sleep_apnea"])
    assert uw.unasked_missing(case) == ["sleep_apnea.severity", "sleep_apnea.treatment"]


def test_card_rows_show_known_then_missing():
    rows = uw.card_rows(_mock_client())
    assert rows[0] == {"key": "conditions", "label": "Condition", "value": "Diabetes, Type 2", "status": "known"}
    statuses = [r["status"] for r in rows]
    assert statuses.index("missing") > max(i for i, s in enumerate(statuses) if s == "known")
    by_key = {r["key"]: r for r in rows}
    assert by_key["age"]["value"] == "46"
    assert by_key["medication"]["value"] == "Metformin"
    assert by_key["build"]["status"] == "missing"


def test_questions_carry_prompt_why_options_and_page():
    case = _mock_client()
    questions = uw.questions_for(case, ["tobacco", "diabetes_type_2.insulin", "diabetes_type_2.stable"])
    tobacco, insulin, stable = questions
    assert tobacco["type"] == "choice" and len(tobacco["options"]) == 4 and tobacco["page"] == 27
    assert insulin["type"] == "yes_no" and insulin["page"] == 8
    assert stable["prompt"] == "Is the type 2 diabetes stable and well controlled?"
    assert stable["guide_text"].startswith("depends on age")


def test_condition_fact_labels_are_qualified_only_when_there_are_several_conditions():
    one = {r["key"]: r["label"] for r in uw.card_rows(_mock_client())}
    assert one["diabetes_type_2.when"] == "When diagnosed"
    two = uw.merge_extracted(_mock_client(), conditions=["sleep_apnea"])
    labels = {r["key"]: r["label"] for r in uw.card_rows(two)}
    assert labels["diabetes_type_2.when"] == "When diagnosed (Diabetes, Type 2)"
    assert labels["sleep_apnea.treatment"] == "Treatment (Sleep Apnea)"


def test_summary_text_names_what_is_missing():
    text = uw.summary_text(_mock_client())
    assert "Age: 46" in text and "height and weight" in text


# --- findings ----------------------------------------------------------------------

def test_findings_for_the_mock_client():
    case = uw.merge_extracted(
        _mock_client(),
        facts={"face_amount": 1_000_000, "tobacco": "none_60m", "build": {"height_in": 66, "weight_lb": 190},
               "diabetes_type_2.insulin": False},
    )
    findings = uw.findings_for(case)
    by_kind = {}
    for f in findings:
        assert isinstance(f["page"], int) and f["text"]
        by_kind.setdefault(f["kind"], []).append(f["text"])
    assert "application only" in by_kind["requirements"][0]
    assert "potential best offer of Standard" in by_kind["condition"][0]
    assert "outside the Elite and Preferred" in by_kind["class_criteria"][0]
    assert "Elite 60 months" in by_kind["tobacco"][0]
    assert "financial" not in by_kind and "records" not in by_kind
    # Height and weight is captured and explained, never turned into a named class.
    assert not any(c in by_kind["build"][0] for c in RB["build_table"]["columns"])


def test_findings_never_state_an_outcome_for_the_individual():
    case = uw.merge_extracted(
        _mock_client(),
        facts={"face_amount": 3_000_000, "tobacco": "within_12m", "earned_income": 80_000,
               "build": {"height_in": 66, "weight_lb": 260}, "diabetes_type_2.insulin": True},
    )
    text = " ".join(f["text"] for f in uw.findings_for(case)).lower()
    for phrase in ("will be approved", "will be declined", "will qualify", "your client qualifies"):
        assert phrase not in text
    assert "insulin" in text and "e-inspection" in text and "25x" in text and "above that" in text


def test_findings_with_only_an_age_lay_out_the_whole_column():
    case = uw.merge_extracted(uw.empty_case(), facts={"age": 62})
    kinds = {f["kind"]: f["text"] for f in uw.findings_for(case)}
    assert "depend on the coverage amount" in kinds["requirements"]
    assert "physical exam within the past 24 months" in kinds["rule"]


def test_fit_signals_and_context_text():
    case = _mock_client()
    signals = {s["label"]: s for s in uw.fit_signals_for(case)}
    assert signals["Need"]["value"] == "Family protection"
    assert signals["Budget"]["status"] == "missing"
    context = uw.context_text(case, uw.findings_for(case))
    assert "Age: 46" in context and "Do NOT decide or predict" in context
