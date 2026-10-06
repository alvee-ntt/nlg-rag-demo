"""Client facts and underwriting-guide lookups for Ask Navigator (M16).

Two things live here, both pure and free of I/O beyond reading one JSON file:

- the **rulebook**: what the FlexLife underwriting guide treats as material, extracted
  once from the guide (``underwriting_data/flexlife_rulebook.json``);
- the **case sheet**: the client facts gathered so far in one chat thread. The browser
  holds it and sends it back each turn, so nothing here is stored server-side.

Everything the UI shows about a client (what is still missing, which questions to ask,
what the guide says for the known facts) is computed from those two by plain lookups.
The app collects and explains the information underwriting needs; it never states
whether an applicant will be approved or at what rate class.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_ROOT = Path(__file__).resolve().parent / "underwriting_data"

CASE_VERSION = 1
MAX_CONDITIONS = 6
UNKNOWN = "unknown"

BEST_OFFER_LABELS = {
    "better_than_standard": "Better than Standard",
    "standard": "Standard",
    "substandard": "Substandard",
}


@lru_cache(maxsize=1)
def load_rulebook() -> dict[str, Any]:
    with (DATA_ROOT / "flexlife_rulebook.json").open(encoding="utf-8") as handle:
        return json.load(handle)


# --- fact specs --------------------------------------------------------------------


def fact_spec(key: str, rulebook: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The rulebook entry for a case-sheet key, or None if the key is not one we hold.

    Plain keys ("age") are client facts. Dotted keys ("diabetes_type_2.when") are the
    per-condition follow-ups, resolved against the shared ``condition_facts`` entries.
    """
    rulebook = rulebook or load_rulebook()
    if key in rulebook["facts"]:
        return rulebook["facts"][key]
    condition_id, _, sub = key.partition(".")
    condition = rulebook["conditions"].get(condition_id)
    base = rulebook["condition_facts"].get(sub)
    if not condition or not base or sub not in condition["needs"]:
        return None
    name = condition["label"]
    return {
        **base,
        "condition_label": name,
        "ask": base["ask"].replace("{condition}", _prose_name(name)),
        "page": base.get("page", condition["page"]),
        "group": "condition",
        "guide_text": condition["guide_text"],
    }


def _prose_name(label: str) -> str:
    """A guide index label as it reads mid-sentence: "Diabetes, Type 2" -> "type 2
    diabetes". Acronyms, single letters and possessive names keep their capitals."""
    head, _, tail = label.partition(", ")
    if tail and len(tail.split()) <= 2:
        label = f"{tail} {head}"
    words = [
        word if word.isupper() or "'" in word or "’" in word else word.lower()
        for word in label.split()
    ]
    return " ".join(words)


def _money(value: float) -> str:
    if value >= 1_000_000:
        millions = value / 1_000_000
        return f"${millions:g}M"
    if value >= 1_000:
        return f"${value / 1_000:g}K"
    return f"${value:,.0f}"


def _to_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower().replace(",", "").replace("$", "")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(k|m|mm|million|thousand)?", text)
    if not match:
        return None
    scale = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6}.get(match.group(2) or "", 1)
    return float(match.group(1)) * scale


def normalize_value(spec: dict[str, Any], raw: Any) -> dict[str, Any] | None:
    """Coerce one raw value to ``{"value", "display"}`` for its fact type, or None.

    Tolerant on purpose: the value comes either from the model's extraction or from the
    browser's stepper, and anything that does not fit the type is simply not recorded.
    """
    if raw is None or raw == "":
        return None
    kind = spec["type"]
    if isinstance(raw, str) and raw.strip().lower() in {UNKNOWN, "not sure", "not sure yet"}:
        return {"value": UNKNOWN, "display": "Not sure yet"}
    if kind == "number":
        number = _to_number(raw)
        if number is None or not 0 <= number <= 120:
            return None
        return {"value": int(number), "display": str(int(number))}
    if kind == "amount":
        number = _to_number(raw)
        if number is None or number < 0:
            return None
        for option in spec.get("options", []):
            if option["value"] == number:
                return {"value": number, "display": option["label"]}
        return {"value": number, "display": _money(number)}
    if kind == "choice":
        text = str(raw).strip().lower()
        for option in spec["options"]:
            if text in {str(option["value"]).lower(), option["label"].lower()}:
                return {"value": option["value"], "display": option["label"]}
        return None
    if kind == "yes_no":
        if isinstance(raw, bool):
            return {"value": raw, "display": "Yes" if raw else "No"}
        text = str(raw).strip().lower()
        if text in {"yes", "y", "true"}:
            return {"value": True, "display": "Yes"}
        if text in {"no", "n", "false"}:
            return {"value": False, "display": "No"}
        return None
    if kind == "height_weight":
        if not isinstance(raw, dict):
            return None
        height, weight = _to_number(raw.get("height_in")), _to_number(raw.get("weight_lb"))
        if height is None or weight is None or not (36 <= height <= 96) or not (50 <= weight <= 700):
            return None
        height, weight = int(round(height)), int(round(weight))
        return {
            "value": {"height_in": height, "weight_lb": weight},
            "display": f"{height // 12}'{height % 12}\", {weight} lb",
        }
    text = re.sub(r"\s+", " ", str(raw)).strip()[:120]
    return {"value": text, "display": text[:1].upper() + text[1:]} if text else None


# --- case sheet --------------------------------------------------------------------


def empty_case() -> dict[str, Any]:
    return {"v": CASE_VERSION, "conditions": [], "facts": {}, "asked": []}


def clean_case(raw: Any, rulebook: dict[str, Any] | None = None) -> dict[str, Any]:
    """Rebuild a case sheet from untrusted input, keeping only what the rulebook knows.

    The sheet round-trips through the browser, so it is re-validated on every turn. The
    whitelist is also what keeps names, SSNs and the like out: there is no key for them.
    """
    rulebook = rulebook or load_rulebook()
    case = empty_case()
    if not isinstance(raw, dict):
        return case
    for condition_id in raw.get("conditions") or []:
        if condition_id in rulebook["conditions"] and condition_id not in case["conditions"]:
            case["conditions"].append(condition_id)
    case["conditions"] = case["conditions"][:MAX_CONDITIONS]
    facts = raw.get("facts") if isinstance(raw.get("facts"), dict) else {}
    for key, entry in facts.items():
        spec = _spec_for_case(key, case, rulebook)
        if not spec or not isinstance(entry, dict):
            continue
        if entry.get("status") == "skipped":
            case["facts"][key] = {"status": "skipped"}
            continue
        normalized = normalize_value(spec, entry.get("value"))
        if normalized:
            case["facts"][key] = normalized
    case["asked"] = [
        key for key in dict.fromkeys(raw.get("asked") or [])
        if isinstance(key, str) and _spec_for_case(key, case, rulebook)
    ]
    return case


def _spec_for_case(key: str, case: dict[str, Any], rulebook: dict[str, Any]) -> dict[str, Any] | None:
    spec = fact_spec(key, rulebook)
    if not spec or spec.get("group") != "condition":
        return spec
    if key.partition(".")[0] not in case["conditions"]:
        return None
    # With one condition on the sheet the label needs no qualifier; with several it does.
    if len(case["conditions"]) > 1:
        return {**spec, "label": f"{spec['label']} ({spec['condition_label']})"}
    return spec


def merge_extracted(
    case: dict[str, Any],
    *,
    conditions: list[str] | None = None,
    facts: dict[str, Any] | None = None,
    rulebook: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fold newly extracted conditions and facts into a (clean) case sheet.

    A new value replaces an old one, so "actually he's 47" corrects the sheet. Unknown
    keys and values that do not fit their type are dropped silently.
    """
    rulebook = rulebook or load_rulebook()
    merged = {**case, "conditions": list(case["conditions"]), "facts": dict(case["facts"])}
    for condition_id in conditions or []:
        if (
            condition_id in rulebook["conditions"]
            and condition_id not in merged["conditions"]
            and len(merged["conditions"]) < MAX_CONDITIONS
        ):
            merged["conditions"].append(condition_id)
    for key, raw in (facts or {}).items():
        spec = _spec_for_case(str(key), merged, rulebook)
        normalized = normalize_value(spec, raw) if spec else None
        if normalized:
            merged["facts"][str(key)] = normalized
    return merged


def has_content(case: dict[str, Any] | None) -> bool:
    return bool(case and (case.get("conditions") or case.get("facts")))


def fact_value(case: dict[str, Any], key: str) -> Any:
    """The recorded value for a fact, or None when absent, skipped or "not sure"."""
    value = (case["facts"].get(key) or {}).get("value")
    return None if value == UNKNOWN else value


def _when_holds(when: dict[str, Any] | None, case: dict[str, Any]) -> bool:
    if not when:
        return True
    value = fact_value(case, when["fact"])
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    if "gte" in when and value < when["gte"]:
        return False
    if "lte" in when and value > when["lte"]:
        return False
    return True


def required_keys(case: dict[str, Any], rulebook: dict[str, Any] | None = None) -> list[str]:
    """Facts the guide treats as material for this client, most important first."""
    rulebook = rulebook or load_rulebook()
    universal = [
        (spec.get("priority", 99), key)
        for key, spec in rulebook["facts"].items()
        if spec.get("group") == "universal" and _when_holds(spec.get("required_when"), case)
    ]
    keys = [key for _, key in sorted(universal)]
    for condition_id in case["conditions"]:
        keys.extend(f"{condition_id}.{need}" for need in rulebook["conditions"][condition_id]["needs"])
    return keys


def missing_keys(case: dict[str, Any], rulebook: dict[str, Any] | None = None) -> list[str]:
    return [key for key in required_keys(case, rulebook) if key not in case["facts"]]


def unasked_missing(case: dict[str, Any], rulebook: dict[str, Any] | None = None) -> list[str]:
    """Missing facts the agent has not been asked for yet. Each fact may hold up an
    answer once; after that the app answers with what it has."""
    asked = set(case["asked"])
    return [key for key in missing_keys(case, rulebook) if key not in asked]


def mark_asked(case: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    return {**case, "asked": list(dict.fromkeys([*case["asked"], *keys]))}


# --- what the UI renders -----------------------------------------------------------


def card_rows(case: dict[str, Any], rulebook: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Rows for the facts card: what was picked up, then what is still missing."""
    rulebook = rulebook or load_rulebook()
    rows: list[dict[str, Any]] = []
    if case["conditions"]:
        rows.append({
            "key": "conditions",
            "label": "Condition" if len(case["conditions"]) == 1 else "Conditions",
            "value": ", ".join(rulebook["conditions"][c]["label"] for c in case["conditions"]),
            "status": "known",
        })
    required = required_keys(case, rulebook)
    ordered = list(dict.fromkeys([*required, *case["facts"]]))
    known, missing = [], []
    for key in ordered:
        spec = _spec_for_case(key, case, rulebook)
        if not spec:
            continue
        entry = case["facts"].get(key)
        if entry is None:
            missing.append({"key": key, "label": spec["label"], "value": None, "status": "missing"})
        elif entry.get("status") == "skipped":
            known.append({"key": key, "label": spec["label"], "value": "Skipped", "status": "skipped"})
        else:
            known.append({"key": key, "label": spec["label"], "value": entry["display"], "status": "known"})
    return [*rows, *known, *missing]


def questions_for(case: dict[str, Any], keys: list[str], rulebook: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Stepper questions for the given fact keys, in the order given."""
    rulebook = rulebook or load_rulebook()
    questions = []
    for key in keys:
        spec = _spec_for_case(key, case, rulebook)
        if not spec:
            continue
        questions.append({
            "key": key,
            "label": spec["label"],
            "prompt": spec["ask"],
            "why": spec.get("why", ""),
            "guide_text": spec.get("guide_text"),
            "type": spec["type"],
            "options": spec.get("options", []),
            "page": spec.get("page"),
        })
    return questions


def editable_keys(case: dict[str, Any], rulebook: dict[str, Any] | None = None) -> list[str]:
    """Every fact the Edit sheet offers: the required ones, then the optional context."""
    rulebook = rulebook or load_rulebook()
    context = [key for key, spec in rulebook["facts"].items() if spec.get("group") == "context"]
    return list(dict.fromkeys([*required_keys(case, rulebook), *context, *case["facts"]]))


def summary_text(case: dict[str, Any], rulebook: dict[str, Any] | None = None) -> str:
    """Plain-text version of the facts card. This is the message text, so it is what
    later turns (and the handoff draft) see in the conversation history."""
    rows = card_rows(case, rulebook)
    missing = [row["label"].lower() for row in rows if row["status"] == "missing"]
    known = [f"{row['label']}: {row['value']}" for row in rows if row["status"] == "known"]
    count = len(missing)
    if count:
        noun = "one more fact" if count == 1 else f"{count} more facts"
        lead = f"Here's what I picked up. The underwriting guide treats {noun} as relevant here"
        return f"{lead}: {', '.join(missing)}. Known so far: {'; '.join(known) or 'nothing yet'}."
    return f"Here's what I picked up: {'; '.join(known)}."


# --- guide lookups -----------------------------------------------------------------


def _age_band_index(age: int, rulebook: dict[str, Any]) -> int:
    for index, band in enumerate(rulebook["requirements_grid"]["age_bands"]):
        if band["max"] is None or age <= band["max"]:
            return index
    return len(rulebook["requirements_grid"]["age_bands"]) - 1


def requirement_code(age: int, face_amount: float, rulebook: dict[str, Any] | None = None) -> str:
    """The p.23 requirements-grid cell for an age and coverage amount, e.g. "A" or "D/APS"."""
    rulebook = rulebook or load_rulebook()
    grid = rulebook["requirements_grid"]
    column = _age_band_index(age, rulebook)
    for row in grid["rows"]:
        if row["up_to"] is None or face_amount <= row["up_to"]:
            return row["cells"][column]
    return grid["rows"][-1]["cells"][column]


def describe_code(code: str, rulebook: dict[str, Any] | None = None) -> str:
    """Spell out a grid cell: "D/APS" -> "application, exam, blood profile, urine, plus
    an attending physician statement"."""
    rulebook = rulebook or load_rulebook()
    codes = rulebook["requirements_grid"]["codes"]
    parts = code.split("/")
    text = codes[parts[0]].lower()
    if parts[0] == "A":
        text = "application only, with no exam or labs"
    if "APS" in parts[1:]:
        text += ", plus an attending physician statement (medical records)"
    return text


def requirements_by_amount(age: int, rulebook: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The whole grid column for an age, with equal neighbouring rows collapsed."""
    rulebook = rulebook or load_rulebook()
    grid = rulebook["requirements_grid"]
    column = _age_band_index(age, rulebook)
    bands: list[dict[str, Any]] = []
    lower = 0
    for row in grid["rows"]:
        code = row["cells"][column]
        if bands and bands[-1]["code"] == code:
            bands[-1]["up_to"] = row["up_to"]
        else:
            bands.append({"over": lower, "up_to": row["up_to"], "code": code})
        lower = row["up_to"]
    return bands


def financial_requirements(
    age: int | None, face_amount: float, state: str | None = None, rulebook: dict[str, Any] | None = None
) -> list[str]:
    """Financial documents the p.23 table lists for a coverage amount, as descriptions."""
    rulebook = rulebook or load_rulebook()
    table = rulebook["financial_requirements"]
    is_ny = bool(state) and state.strip().lower() in {"ny", "new york"}
    codes: list[str] = []
    for rule in table["rules"]:
        if rule.get("state") == "NY" and not is_ny:
            continue
        if face_amount <= rule["face_over"] and rule.get("state") != "NY":
            continue
        if "min_age" in rule and (age is None or age < rule["min_age"]):
            continue
        if rule["code"] not in codes:
            codes.append(rule["code"])
    return [table["codes"][code] for code in codes]


def income_multiple(age: int, rulebook: dict[str, Any] | None = None) -> int | None:
    rulebook = rulebook or load_rulebook()
    for band in rulebook["income_multiples"]["bands"]:
        if band["min_age"] <= age <= band["max_age"]:
            return band["multiple"]
    return None


def _amount_range(band: dict[str, Any]) -> str:
    if band["up_to"] is None:
        return f"above {_money(band['over'])}"
    if band["over"] == 0:
        return f"up to {_money(band['up_to'])}"
    return f"above {_money(band['over'])} up to {_money(band['up_to'])}"


def findings_for(case: dict[str, Any], rulebook: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """What the guide lays out for the facts on the sheet, each line with its page.

    These are lookups, not judgments: requirements, documented rules and the guide's own
    best-case wording for a condition. Nothing here says what an underwriter will decide.
    """
    rulebook = rulebook or load_rulebook()
    out: list[dict[str, Any]] = []

    def add(kind: str, text: str, page: int) -> None:
        out.append({"kind": kind, "text": text, "page": page})

    age = fact_value(case, "age")
    face = fact_value(case, "face_amount")
    grid_page = rulebook["requirements_grid"]["page"]
    if age is not None and face is not None:
        code = requirement_code(age, face, rulebook)
        add(
            "requirements",
            f"Medical requirements at age {age} for {case['facts']['face_amount']['display']} of FlexLife: "
            f"{describe_code(code, rulebook)}.",
            grid_page,
        )
        financial = financial_requirements(age, face, fact_value(case, "state"), rulebook)
        if financial:
            add("financial", "Financial requirements at this amount: " + ", ".join(financial) + ".", grid_page)
    elif age is not None:
        parts = [f"{_amount_range(band)}: {describe_code(band['code'], rulebook)}" for band in requirements_by_amount(age, rulebook)]
        add("requirements", f"Medical requirements at age {age} depend on the coverage amount. " + "; ".join(parts) + ".", grid_page)

    for rule in rulebook["rules"]:
        if rule.get("when") and _when_holds(rule["when"], case):
            add("rule", rule["text"], rule["page"])

    note = rulebook["conditions_note"]
    for condition_id in case["conditions"]:
        condition = rulebook["conditions"][condition_id]
        add(
            "condition",
            f"{condition['label']}: the guide lists a potential best offer of "
            f"{BEST_OFFER_LABELS[condition['best_offer']]}, assuming: {condition['guide_text']}.",
            condition["page"],
        )
        trigger = rulebook["aps_condition_ids"].get(condition_id)
        if trigger:
            add("records", f"Physician records (an APS) may be required regardless of coverage amount for: {trigger}.", rulebook["routine_aps"]["page"])
        if condition_id in rulebook["elite_preferred_excluded_ids"]:
            add(
                "class_criteria",
                f"A personal health history of {_prose_name(condition['label'])} is outside the Elite and Preferred criteria.",
                rulebook["rate_classes"]["criteria"]["page"],
            )
    if case["conditions"]:
        add("caveat", "These best-offer lines assume the condition is fully evaluated and optimally controlled. "
            "Multiple conditions may alter the offer, and the final rating depends on the individual case.", note["page"])

    diabetic = [c for c in case["conditions"] if c.startswith("diabetes_type")]
    tobacco = fact_value(case, "tobacco")
    if diabetic and (
        tobacco == "within_12m" or any(fact_value(case, f"{c}.insulin") is True for c in diabetic)
    ):
        add("records", "Diabetes treated by insulin, or combined with tobacco use, may require physician records (an APS) regardless of coverage amount.", rulebook["routine_aps"]["page"])

    if tobacco is not None:
        criteria = rulebook["rate_classes"]["criteria"]
        if tobacco == "within_12m":
            add("tobacco", "With tobacco or nicotine use in the last 12 months, the tobacco classes apply (Preferred Tobacco or Standard Tobacco).", rulebook["tobacco"]["page"])
        else:
            add(
                "tobacco",
                "Nicotine-free periods the non-tobacco classes ask for: "
                f"Elite {criteria['Elite']['tobacco_free_months']} months, "
                f"Preferred {criteria['Preferred']['tobacco_free_months']} months, "
                f"Select {criteria['Select']['tobacco_free_months']} months. Current lab testing must be negative for nicotine.",
                criteria["page"],
            )

    if fact_value(case, "build") is not None:
        table = rulebook["build_table"]
        add("build", "Height and weight are checked against the guide's build table. It is a guideline; other factors, including age and body proportions, may affect the final decision.", table["page"])

    income = fact_value(case, "earned_income")
    if age is not None and income:
        multiple = income_multiple(age, rulebook)
        if multiple:
            cap = multiple * income
            text = f"Income replacement at age {age} is considered up to {multiple}x annual earned income, about {_money(cap)} here."
            if face is not None:
                text += " The requested amount is within that." if face <= cap else " The requested amount is above that, so expect to justify it."
            add("income", text, rulebook["income_multiples"]["page"])
    return out


def fit_signals_for(case: dict[str, Any]) -> list[dict[str, Any]]:
    """The sales-side facts shown under an answer. Never required, only surfaced."""
    signals = []
    for key, label, absent in (
        ("need", "Need", "Not shared yet"),
        ("budget", "Budget", "Not shared yet"),
        ("existing_coverage", "Existing coverage", "Not asked yet"),
    ):
        entry = case["facts"].get(key)
        if entry and "display" in entry:
            signals.append({"label": label, "value": entry["display"], "status": "known"})
        else:
            signals.append({"label": label, "value": absent, "status": "missing"})
    return signals


def context_text(case: dict[str, Any], findings: list[dict[str, Any]], rulebook: dict[str, Any] | None = None) -> str:
    """The block handed to the hosted agent on a client turn: the facts, what the guide
    lookup already established, and the line it must not cross."""
    rows = card_rows(case, rulebook)
    known = "; ".join(f"{row['label']}: {row['value']}" for row in rows if row["status"] == "known")
    missing = ", ".join(row["label"].lower() for row in rows if row["status"] == "missing")
    lines = [f"The agent is asking about a specific client. Known client facts: {known or 'none'}."]
    if missing:
        lines.append(f"Not yet known: {missing}.")
    if findings:
        lines.append(
            "Already established from the underwriting guide and shown to the agent separately "
            "(do not repeat these line by line): " + " | ".join(f["text"] for f in findings)
        )
    lines.append(
        "Explain only what the approved material documents: rules, requirements, conditions, limits "
        "and what to prepare for the application. Do NOT decide or predict whether this client will "
        "be approved or declined, and do NOT state a rate class for them. If the message is not a "
        "question, briefly say what the agent should know and gather next. If the sources do not "
        "establish something, say so and point to NLG Support."
    )
    return "\n".join(lines)
