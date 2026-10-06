"""M16 — the client-facts flow on the Foundry chat path.

Covers the turn classifier and extractor in ``embeddings`` and every branch of
``service.chat_foundry`` that M16 adds: facts card, answer with findings, skip, hand-filled
answers, replacement, and the fail-open paths. All LLM / Foundry calls are mocked; no
network, no DB, no Azure.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rag_layer import embeddings, foundry, service  # noqa: E402
from src.rag_layer import underwriting as uw  # noqa: E402

_SETTINGS = SimpleNamespace(foundry_agent_name="KnowledgeBase", rag_search_limit=6)
MOCK_MESSAGE = "Client is 46, takes metformin for type 2 diabetes. Looking at FlexLife for family protection."
MOCK_FOUND = {
    "conditions": ["diabetes_type_2"],
    "facts": {"age": 46, "medication": "Metformin", "need": "Family protection"},
}
FULL_FACTS = {
    "face_amount": 1_000_000, "tobacco": "none_60m", "build": {"height_in": 66, "weight_lb": 190},
    "diabetes_type_2.when": "2019", "diabetes_type_2.stable": "yes",
    "diabetes_type_2.complications": "none", "diabetes_type_2.insulin": False,
}
AGENT_REPLY = {
    "answer": "Have the diagnosis date and current medications ready for the application [1].",
    "citations": [{"n": 1, "title": "Underwriting Guide.pdf", "url": "https://x/guide.pdf"}],
    "agent": "KnowledgeBase", "model": "gpt-5", "response_id": "resp_1", "status": "completed",
    "escalate": False, "escalate_reason": None,
}
ABSTAIN_REPLY = {
    "answer": "I couldn't find enough approved FlexLife material.", "citations": [],
    "agent": "KnowledgeBase", "model": "gpt-5", "response_id": "resp_2", "status": "completed",
    "escalate": True, "escalate_reason": "no_citations",
}


def _turn(client=False, replacement=False, domain="IN_DOMAIN"):
    return lambda *a, **k: {"domain": domain, "client": client, "replacement": replacement}


@pytest.fixture
def wired(monkeypatch):
    """chat_foundry with every outside call replaced; ``calls`` records what ran."""
    calls = {"extract": 0, "foundry": []}

    def extract(*a, **k):
        calls["extract"] += 1
        return dict(MOCK_FOUND)

    def agent(**kwargs):
        calls["foundry"].append(kwargs)
        return dict(calls.get("reply", AGENT_REPLY))

    monkeypatch.setattr(service, "classify_turn", _turn(client=True))
    monkeypatch.setattr(service, "extract_case_facts", extract)
    monkeypatch.setattr(service.foundry, "chat", agent)
    monkeypatch.setattr(service, "_guide_document_url", lambda *a, **k: "/v1/documents/59/open")
    return calls


def _chat(message=MOCK_MESSAGE, **kwargs):
    return service.chat_foundry(settings=_SETTINGS, client=None, message=message, history=[], **kwargs)


def _full_case():
    case = uw.merge_extracted(uw.empty_case(), conditions=MOCK_FOUND["conditions"], facts=MOCK_FOUND["facts"])
    return uw.merge_extracted(case, facts=FULL_FACTS)


# --- embeddings.classify_turn ------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("IN_DOMAIN NO_CLIENT NO_REPLACEMENT", ("IN_DOMAIN", False, False)),
        ("IN_DOMAIN CLIENT NO_REPLACEMENT", ("IN_DOMAIN", True, False)),
        ("in_domain client replacement", ("IN_DOMAIN", True, True)),
        ("OUT_OF_DOMAIN NO_CLIENT NO_REPLACEMENT", ("OUT_OF_DOMAIN", False, False)),
        ("IN_DOMAIN", ("IN_DOMAIN", False, False)),
        ("who knows", ("IN_DOMAIN", False, False)),
    ],
)
def test_classify_turn_parses_tokens(monkeypatch, raw, expected):
    monkeypatch.setattr(embeddings, "_generate", lambda *a, **k: raw)
    turn = embeddings.classify_turn(None, None, "msg", [])
    assert (turn["domain"], turn["client"], turn["replacement"]) == expected


def test_classify_turn_fails_open_to_an_ordinary_turn(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(embeddings, "_generate", boom)
    assert embeddings.classify_turn(None, None, "msg", []) == {
        "domain": "IN_DOMAIN", "client": False, "replacement": False,
    }


# --- embeddings.extract_case_facts -------------------------------------------------

def test_extract_case_facts_parses_json_and_offers_the_rulebook_keys(monkeypatch):
    seen = {}

    def fake(client, settings, prompt):
        seen["prompt"] = prompt
        return '```json\n{"conditions": ["diabetes_type_2"], "facts": {"age": 46}}\n```'

    monkeypatch.setattr(embeddings, "_generate", fake)
    found = embeddings.extract_case_facts(None, None, MOCK_MESSAGE, [], uw.empty_case(), uw.load_rulebook())
    assert found == {"conditions": ["diabetes_type_2"], "facts": {"age": 46}}
    for expected in ("- age:", "- tobacco:", "- diabetes_type_2: Diabetes, Type 2", "- insulin:", MOCK_MESSAGE):
        assert expected in seen["prompt"]
    assert "never record names" in seen["prompt"]


@pytest.mark.parametrize("raw", ["not json at all", '{"conditions": "x", "facts": []}'])
def test_extract_case_facts_fails_open_on_garbage(monkeypatch, raw):
    monkeypatch.setattr(embeddings, "_generate", lambda *a, **k: raw)
    found = embeddings.extract_case_facts(None, None, "msg", [], uw.empty_case(), uw.load_rulebook())
    assert found["facts"] == {} and found["conditions"] in ([], ["x"]) or found == {"conditions": [], "facts": {}}


def test_extract_case_facts_fails_open_on_exception(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(embeddings, "_generate", boom)
    found = embeddings.extract_case_facts(None, None, "msg", [], uw.empty_case(), uw.load_rulebook())
    assert found == {"conditions": [], "facts": {}}


# --- foundry preamble --------------------------------------------------------------

def test_case_context_reaches_the_provider_message_only_when_set():
    plain = foundry._current_user_content(question="What applies?")
    assert "Known client facts" not in plain and plain.endswith("What applies?")
    with_case = foundry._current_user_content(question="What applies?", case_context="Known client facts: Age: 46.")
    assert "Known client facts: Age: 46." in with_case and with_case.endswith("What applies?")


# --- chat_foundry: ordinary turns are untouched ------------------------------------

def test_ordinary_turn_does_not_extract_or_add_case_fields(wired, monkeypatch):
    monkeypatch.setattr(service, "classify_turn", _turn(client=False))
    result = _chat("What are the EZ underwriting face limits?")
    assert wired["extract"] == 0
    assert "case_context" not in wired["foundry"][0]
    for field in ("case", "case_card", "findings", "actions", "disclaimer", "underwriting"):
        assert field not in result
    assert result["answer"] == AGENT_REPLY["answer"]


def test_ordinary_turn_in_a_thread_with_a_sheet_stays_ordinary(wired, monkeypatch):
    monkeypatch.setattr(service, "classify_turn", _turn(client=False))
    result = _chat("How do caps and floors work?", case=_full_case())
    assert wired["extract"] == 0 and "case" not in result
    assert "case_context" not in wired["foundry"][0]


def test_client_turn_with_nothing_extracted_falls_through(wired, monkeypatch):
    monkeypatch.setattr(service, "extract_case_facts", lambda *a, **k: {"conditions": [], "facts": {}})
    result = _chat("What about my client?")
    assert "case" not in result and len(wired["foundry"]) == 1


# --- chat_foundry: facts card ------------------------------------------------------

def test_first_client_message_returns_the_facts_card_without_calling_the_agent(wired):
    result = _chat()
    assert wired["foundry"] == []
    assert result["status"] == "guiding" and result["escalate"] is False and result["citations"] == []
    assert result["case"]["conditions"] == ["diabetes_type_2"]
    card = result["case_card"]
    assert card["show"] is True
    rows = {r["key"]: r for r in card["rows"]}
    assert rows["age"]["value"] == "46" and rows["medication"]["value"] == "Metformin"
    assert rows["build"]["status"] == "missing" and rows["tobacco"]["status"] == "missing"
    assert [q["key"] for q in card["questions"]][:3] == ["face_amount", "tobacco", "build"]
    assert {q["key"] for q in card["edit_questions"]} >= {"age", "need", "state", "build"}
    assert [a["id"] for a in result["actions"]] == ["walk", "edit", "answer_now"]
    assert "Age: 46" in result["answer"]
    # Everything shown as missing is now marked asked, so it will not hold up the next turn.
    assert set(q["key"] for q in card["questions"]) <= set(result["case"]["asked"])


def test_second_client_turn_answers_instead_of_asking_again(wired):
    first = _chat()
    second = _chat("Does he need an exam?", case=first["case"])
    assert len(wired["foundry"]) == 1
    assert second["status"] == "completed" and second["case_card"]["show"] is False
    assert second["actions"][0] == {"id": "walk", "label": "Add the missing facts"}


def test_follow_up_about_the_same_client_does_not_repeat_the_guide_lookup(wired):
    first = _chat()
    second = _chat("Does he need an exam?", case=first["case"])
    assert second["findings"] == [] and second["fit_signals"] == []
    assert "Known client facts" in wired["foundry"][0]["case_context"]  # the agent still knows
    assert second["disclaimer"]


def test_show_brings_the_facts_card_back_without_calling_the_agent(wired, monkeypatch):
    def must_not_classify(*a, **k):
        raise AssertionError("a UI button press is not classified")

    monkeypatch.setattr(service, "classify_turn", must_not_classify)
    result = _chat("Back to the client.", case=_full_case(), case_action="show")
    assert wired["foundry"] == [] and wired["extract"] == 0
    assert result["status"] == "guiding" and result["case_card"]["show"] is True
    assert [a["id"] for a in result["actions"]] == ["edit", "answer_now"]
    assert result["actions"][1]["label"] == "What does the guide say?"


def test_a_new_condition_brings_the_card_back(wired, monkeypatch):
    first = _chat()
    monkeypatch.setattr(service, "extract_case_facts", lambda *a, **k: {"conditions": ["sleep_apnea"], "facts": {}})
    second = _chat("He also has sleep apnea.", case=first["case"])
    assert second["status"] == "guiding"
    assert second["case"]["conditions"] == ["diabetes_type_2", "sleep_apnea"]


def test_answer_now_skips_the_card(wired):
    result = _chat("Answer with what I have", case=_chat()["case"], case_action="answer_now")
    assert wired["extract"] == 1  # only the first turn extracted
    assert result["status"] == "completed" and result["findings"]


# --- chat_foundry: answer turn -----------------------------------------------------

def test_hand_filled_answers_go_straight_to_an_answer_with_findings(wired):
    result = _chat("$250K to $1M · Non-smoker · 5'6\", 190 lb", case=_full_case(), case_action="answers")
    assert wired["extract"] == 0
    sent = wired["foundry"][0]
    assert "Known client facts" in sent["case_context"] and "Do NOT decide or predict" in sent["case_context"]
    assert result["answer"] == AGENT_REPLY["answer"] and result["citations"] == AGENT_REPLY["citations"]
    assert result["underwriting"] == "rule" and result["disclaimer"] == "Guidance only. NLG underwriting decides."
    kinds = [f["kind"] for f in result["findings"]]
    assert kinds[0] == "requirements" and "condition" in kinds
    assert result["findings"][0]["url"] == "/v1/documents/59/open#page=23"
    assert [s["label"] for s in result["fit_signals"]] == ["Need", "Budget", "Existing coverage"]
    assert result["actions"] == [{"id": "edit", "label": "Edit facts"}]
    assert result["case_card"]["questions"] == []


def test_agent_abstention_still_shows_what_the_guide_lookup_found(wired):
    wired["reply"] = ABSTAIN_REPLY
    result = _chat("anything else?", case=_full_case(), case_action="answers")
    assert result["escalate"] is False and result["status"] == "rulebook"
    assert result["answer"] == service.RULEBOOK_ANSWER
    assert result["citations"] == [{"n": 1, "title": "Underwriting Guide.pdf", "url": "/v1/documents/59/open"}]
    assert result["findings"]


def test_untrusted_sheet_is_revalidated(wired):
    dirty = {"conditions": ["diabetes_type_2"], "facts": {"age": {"value": 46}, "ssn": {"value": "123"}}, "asked": ["ssn"]}
    result = _chat("ok", case=dirty, case_action="answer_now")
    assert "ssn" not in result["case"]["facts"] and result["case"]["asked"] == []


def test_foundry_failure_falls_back_to_local_and_keeps_the_case_fields(wired, monkeypatch):
    def down(**k):
        raise RuntimeError("foundry down")

    monkeypatch.setattr(service.foundry, "chat", down)
    monkeypatch.setattr(service, "chat", lambda **k: {"answer": "local answer", "sources": [], "insufficient_support": False})
    result = _chat("ok", case=_full_case(), case_action="answers")
    assert result["source_engine"] == "local" and result["answer"] == "local answer"
    assert result["findings"] and result["disclaimer"]


# --- chat_foundry: replacement -----------------------------------------------------

def test_replacement_escalates_as_case_specific_with_banner(wired, monkeypatch):
    monkeypatch.setattr(service, "classify_turn", _turn(client=True, replacement=True))
    monkeypatch.setattr(service, "extract_case_facts", lambda *a, **k: {"conditions": [], "facts": {"existing_coverage": "Whole life policy"}})
    wired["reply"] = ABSTAIN_REPLY
    result = _chat("They also have a whole life policy they want to cash out to pay for this.", case=_full_case())
    assert "Do NOT give a sales talk track" in wired["foundry"][0]["case_context"]
    assert result["escalate"] is True and result["escalate_reason"] == "case_specific"
    assert result["underwriting"] == "case" and result["banner"] == service.REPLACEMENT_BANNER
    assert result["answer"] == service.REPLACEMENT_FALLBACK
    assert [a["id"] for a in result["actions"]] == ["handoff", "back"]
    assert result["findings"][0]["page"] == 4
    assert result["case"]["facts"]["existing_coverage"]["display"] == "Whole life policy"


def test_replacement_without_a_client_on_file_still_escalates(wired, monkeypatch):
    monkeypatch.setattr(service, "classify_turn", _turn(client=False, replacement=True))
    result = _chat("Can someone cash out a whole life policy to fund FlexLife?")
    assert result["escalate"] is True and result["banner"]
    assert result["answer"] == AGENT_REPLY["answer"]  # a grounded answer is kept
    assert [a["id"] for a in result["actions"]] == ["handoff"] and "case" not in result


def test_back_to_the_client_does_not_re_enter_the_replacement_branch(wired, monkeypatch):
    monkeypatch.setattr(service, "classify_turn", _turn(client=True, replacement=True))
    result = _chat("Back to the client", case=_full_case(), case_action="answer_now")
    assert "banner" not in result and result["underwriting"] == "rule"
