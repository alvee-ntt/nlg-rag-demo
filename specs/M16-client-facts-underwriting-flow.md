# M16 — Client Facts & Underwriting Guidance Flow

Build-ready plan for the F2.1–F2.4 Ask Navigator screens (facts card, missing-fact
stepper, "What the underwriting guide says" answer card, replacement escalation).

## 1. Summary

When an agent starts describing a client in Ask Navigator ("Client is 46, takes
metformin for type 2 diabetes. Looking at FlexLife"), the app:

1. picks the client facts out of the conversation and shows them back as a card, with
   the facts the underwriting guide treats as material but that are still missing;
2. optionally walks the agent through those missing facts in a tap-through stepper;
3. answers from the guide with citations, fit signals and a "guidance only" disclaimer;
4. recognises a replacement and routes it to NLG Support instead of giving a talk track.

M16 is the UI-rich delivery of **M08 (guiding prompts)** and **M07 (underwriting
technicals)**, and reuses **M09 (handoff)**. It supersedes the chat-only designs in
those two specs where they differ; see §9.

The design rests on three structures:

- a static **rulebook** extracted once from the underwriting guide and reviewed by a
  human;
- a small **case sheet** per chat thread that fills in as the agent talks;
- a few new **response fields** on `/v1/foundry/chat` that the UI renders as cards.

The model does not generate UI and does not decide what is material. One extraction
call per turn turns free text into rulebook keys; everything else (what is missing,
which questions to ask, what the requirements grid says) is deterministic lookup.

The Figma screens are inspiration, not a specification. The underwriting guide is the
source of truth: the facts shown, the questions asked and their answer options all come
from what the guide actually branches on, even where that differs from the screens.

There is no "case mode". A case sheet only comes into existence once the agent mentions
a client fact; until then the chat behaves exactly as it does today.

## 2. Source material and what it does and does not support

Source: **Life Insurance Underwriting Guide, Cat No 62797(0126)**, 41 pages. Indexed as
`From NLG/Underwriting Guide.pdf` (doc #59).

What the guide treats as material for almost any FlexLife question:

| Fact | Why the guide cares | Page |
|---|---|---|
| Age and face amount | Sets exam, labs and APS requirements (the requirements grid) | 23 |
| Tobacco or nicotine, and time since last use | 12, 36 and 60 months are the class cut-offs | 27–29 |
| Height and weight | Build bands per rate class | 32 |
| Age 60+: physical in the last 24 months | Otherwise the application is declined | 4, 34 |
| Earned income | Coverage capped at a multiple by age (25x at 41–50) | 13 |
| State (New York) | $2M limit, CFQ on every application | 12, 23 |
| Existing coverage being replaced | Required application question | 4 |

Per condition: about 90 conditions on pp.35–37, each with one line of qualifiers and a
"potential best offer" tier (better than standard, standard, substandard). Plus the
non-qualifying list (p.34), routine APS triggers (p.8) and the Elite, Preferred and
Select criteria (pp.28–31).

The checkmark tables on p.23 and pp.35–37 are dropped by text extraction (see
`tests/underwriting_guide_questions.md`), so the indexed chunks do not carry them. The
rulebook is built by reading the pages directly, which recovers them.

What the guide does **not** contain, and the app therefore does not ask or assert:

- **A1C thresholds.** The only mention is "normal A1c" for gestational diabetes, so the
  A1C question drawn on the F2.2 screen is not used. For diabetes the app asks what the
  guide does branch on: type, diagnosis date, stability, complications, insulin,
  tobacco.
- **Condition depth in general.** One line per condition. For more, the guide sends
  agents to XRAE, NLG's quote tool, which it describes as having "impairment-based
  reflexive questions" (p.39).
- **Replacement rules by state.** Replacement appears only as a required application
  question (p.4).

## 3. Data structures

### 3.1 Rulebook (static)

`src/rag_layer/underwriting_data/flexlife_rulebook.json`. Committed, generated from the
guide, reviewed before it drives the bot. Lives under `src/` so the Dockerfile's
`COPY src ./src` ships it, like `roleplay_data`.

```json
{
  "source": {"title": "Life Insurance Underwriting Guide", "cat_no": "62797(0126)",
             "blob": "From NLG/Underwriting Guide.pdf"},
  "facts": {
    "age":         {"label": "Age", "type": "number", "universal": true, "priority": 1,
                    "ask": "How old is the client?",
                    "why": "Sets exam and lab requirements.", "page": 23, "source": "guide"},
    "face_amount": {"label": "Coverage amount", "type": "choice", "universal": true, "priority": 2,
                    "ask": "How much coverage are they looking at?",
                    "options": ["Up to $250K", "$250K to $1M", "$1M to $2M", "$2M to $3M",
                                "$3M to $5M", "$5M to $10M", "Over $10M"],
                    "why": "With age, decides whether an exam or records are needed.",
                    "page": 23, "source": "guide"},
    "tobacco":     {"label": "Tobacco", "type": "choice", "universal": true, "priority": 3,
                    "ask": "Any tobacco or nicotine use?",
                    "options": ["Never or 5+ years ago", "3 to 5 years ago",
                                "1 to 3 years ago", "Within the last year"],
                    "why": "The non-tobacco classes use 12, 36 and 60 month cut-offs.",
                    "page": 28, "source": "guide"},
    "build":       {"label": "Height and weight", "type": "height_weight", "universal": true,
                    "priority": 4, "page": 32, "source": "guide"},
    "diabetes.insulin": {"label": "Uses insulin", "type": "yes_no", "page": 8, "source": "guide"}
  },
  "conditions": {
    "diabetes_type_2": {
      "label": "Type 2 diabetes", "best_offer": "standard", "page": 35,
      "guide_text": "depends on age & disease duration, stable, no complications",
      "needs": ["diagnosis_date", "stable", "complications", "diabetes.insulin"]
    }
  },
  "knockouts": [
    {"id": "age60_physical", "page": 34,
     "text": "Applicants age 60 and over must have routine health care and a physical exam within the past 24 months; otherwise the application will be declined."}
  ],
  "aps_triggers": [],
  "requirements_grid": {
    "product": "flexlife", "page": 23,
    "age_bands": ["0-17", "18-30", "31-40", "41-50", "51-60", "61-65", "66-69", "70+"],
    "codes": {"A": "Application", "D": "Application, exam, blood profile, urine",
              "M": "Application, exam, blood profile, urine, mature assessment",
              "ME": "Application, exam, blood profile, urine, EKG, mature assessment",
              "APS": "Attending physician statement"},
    "rows": [
      {"up_to": 250000,  "cells": ["A", "A", "A", "A", "A", "A", "D", "ME/APS"]},
      {"up_to": 1000000, "cells": ["A", "A", "A", "A", "A", "D", "D", "ME/APS"]}
    ]
  },
  "build_table": [],
  "income_multiples": [],
  "class_criteria": {}
}
```

Rules for the file:

- Every option list uses the guide's own cut-offs. No invented buckets.
- Every entry carries a `page`.
- Every fact carries `source`: `guide` or `nlg_supplied`. The second is the slot for
  anything NLG provides beyond the guide (A1C thresholds, XRAE's question set).
- `needs` may only reference keys that exist in `facts`.

### 3.2 Case sheet (dynamic, per thread)

Held on the chat thread in the browser (`t.case`, persisted with the thread in
localStorage) and round-tripped on every `/v1/foundry/chat` call. The server is
stateless with respect to it.

```json
{
  "v": 1,
  "conditions": ["diabetes_type_2"],
  "facts": {
    "age":        {"value": 46, "display": "46"},
    "medication": {"value": "metformin", "display": "Metformin"},
    "tobacco":    {"status": "skipped"}
  },
  "asked": ["build", "tobacco", "diagnosis_date"]
}
```

- Only keys present in the rulebook are accepted; the server drops anything else. There
  is no slot for a name, SSN or bank detail.
- "Missing" is not stored. It is computed each turn as: universal facts, plus `needs` of
  each condition, minus facts that are known or skipped.
- `asked` records which facts have already been put to the agent, so each fact can
  block an answer at most once.

### 3.3 Request and response fields

`FoundryChatRequest` gains:

```python
case: dict | None = None
case_action: Literal["answer_now"] | None = None
```

`FoundryChatResponse` gains, all defaulted so existing turns are unchanged:

| Field | Shape | Drives |
|---|---|---|
| `case` | case sheet | Updated sheet, echoed back for the browser to keep |
| `case_card` | `{rows: [{key, label, value, status}], questions: [{key, prompt, why, type, options, page}], note}` | F2.1 card and the F2.2 stepper |
| `findings` | `[{kind, text, page, document_id}]` | F2.3 "What the underwriting guide says" |
| `fit_signals` | `[{label, value, status}]` | F2.3 Need, Budget, Existing coverage |
| `disclaimer` | `str` | "Guidance only. NLG underwriting decides." |
| `banner` | `str` | "Replacement rules can vary by state." |
| `actions` | `[{id, label}]` | Chips: Walk through questions, Edit facts, Ask NLG support, Back to the case |
| `underwriting` | `"rule" \| "case" \| None` | M07's machine-readable signal |

## 4. Turn flow (`service.chat_foundry`)

1. **Domain gate** (M02). Unchanged.
2. **Extract.** `extract_case_facts(client, settings, message, history, case, rulebook)`
   makes one JSON call and returns `{facts, conditions, flags}`. Keys are validated
   against the rulebook and merged into the sheet. Fails open: on any error or garbage it
   returns nothing and the turn proceeds as today.
3. **No sheet and nothing extracted.** Existing path, untouched.
4. **Facts card turn.** If facts are missing that are not yet in `asked`, and
   `case_action` is not `answer_now`: return `case_card` with `status: "guiding"`,
   `escalate: false`, no citations, and **do not call Foundry**. Add the missing keys to
   `asked`. Short-circuiting here keeps the M03 zero-citation abstention in
   `foundry.chat` from replacing the card with the support message.
5. **Answer turn.** Compute deterministic findings from the rulebook (requirements grid
   cell, knockouts, condition line, APS triggers). Pass a `case_context` block to
   `foundry.chat`: the known facts plus "explain documented rules only; do not decide or
   predict whether this applicant will be approved or at what class; if the sources do
   not establish it, say so and point to NLG Support". Return the Foundry answer with
   `findings`, `fit_signals`, `disclaimer`, `underwriting: "rule"`.
6. **Foundry abstains but findings exist.** Show the findings (cited to the guide page
   via `/v1/documents/{id}/open`) rather than a bare referral; keep `escalate: true` so
   the handoff button still shows.
7. **Replacement flag.** Set `banner`, `escalate: true`,
   `escalate_reason: "case_specific"`, `underwriting: "case"`. The existing M09 CTA
   ("Ask NLG for a decision") and draft flow fire with no handoff changes.

Card-only turns are not passed to `track_question`, so Question Insights does not count
them as answered questions.

## 5. Backend changes

| File | Change |
|---|---|
| `src/rag_layer/underwriting_data/flexlife_rulebook.json` | New. The extraction (§3.1). |
| `src/rag_layer/underwriting.py` | New, pure functions: `load_rulebook`, `validate_case`, `merge_facts`, `missing_facts`, `questions_for`, `card_rows`, `findings_for` (grid, knockouts, condition line), `fit_signals_for`, `case_context_text`. |
| `src/rag_layer/embeddings.py` | `extract_case_facts`, same tolerant-parse, fail-open style as `classify_domain`. |
| `src/rag_layer/service.py` | Steps 2–7 in `chat_foundry`; same tagging on the local fallback branch. |
| `src/rag_layer/foundry.py` | `case_context: str = ""` on `chat` and `_current_user_content`, appended to the preamble `lines`; recorded in the request trace inputs. |
| `src/rag_layer/config.py` | `UNDERWRITING_DISCLAIMER` constant and `Settings` field with env override. |
| `src/rag_layer/server.py` | Request and response fields (§3.3); skip `track_question` when `status == "guiding"`. |

## 6. Frontend changes (`ui/learn.html`)

- **State.** Each thread gets `t.case`. `navSend` takes the thread so it can send
  `case` and store the returned one. Callers at `openNavigator` and `renderChat` pass
  it. The in-call Ask sheet has its own `send` and is untouched.
- **`navSend` return.** Pass through `case_card`, `findings`, `fit_signals`,
  `disclaimer`, `banner`, `actions`, `underwriting`.
- **`mountChat.paint()`.** A message with `case_card` renders the rows inside the
  bubble, with a `Missing` pill for missing facts and the privacy note underneath. A
  message with `findings` renders the "What the underwriting guide says" block, the fit
  signals table and the disclaimer box. `banner` renders as a pill above the text.
- **`mountChat.paintChips()`.** When the last AI message has `actions`, render those
  chips instead of follow-ups. `walk` opens the stepper, `edit` opens the editor,
  `handoff` reuses `openHandoff`.
- **Stepper.** A full-height `.sheet-wrap` driven by `case_card.questions`: progress
  ("Question 1 of 3"), one question per screen, radio options plus "Not sure yet", the
  `why` box, Next and "Skip for now". No server call per step. On finish it writes the
  answers into `t.case` and sends one summary message ("5'6", 190 lb · Non-smoker"),
  which becomes the user bubble in F2.3.
- **Edit facts.** The same sheet as a single form over all known and missing facts.
- **Styles.** Card, pills, stepper and disclaimer box, taken from the Figma chat-flow
  frames.

## 7. Test plan

- **Rulebook integrity** (`tests/test_m16_rulebook.py`): file loads; every `needs` key
  exists in `facts`; every entry has a page; grid rows cover every age band.
- **Grid lookups against known answers** from `tests/underwriting_guide_questions.md`
  rows 1–6: age 45 at $1.5M is `A`; age 55 at $1.5M is `D`; age 35 at $4M is `D`; age
  70+ is `ME/APS` at every amount.
- **`underwriting.py` units:** merge drops unknown keys; missing shrinks as facts
  arrive; skipped facts are not re-asked; `asked` prevents a second block.
- **`chat_foundry` branches** (monkeypatch `classify_domain`, `extract_case_facts`,
  `foundry.chat`, mirroring `tests/test_m02_domain_gate.py`): card turn does not call
  Foundry; `answer_now` does; no-facts turn is byte-identical to today; extraction error
  fails open; replacement sets `case_specific`; abstention with findings keeps findings.
- **Preamble** (extend `tests/test_foundry_prompt.py`): `case_context` appears in the
  provider message only when set.
- **Endpoint** (`TestClient`): responses validate against the updated model.
- **Frontend** (jsdom): card rows and pills render; action chips replace follow-ups;
  stepper completes and sends one message with the sheet updated.
- **Eval:** run `tests/run_foundry_eval.py` before and after to confirm plain rule
  questions are unaffected; add multi-turn client scenarios.

## 8. Phases

| # | Work | Est. |
|---|---|---|
| 0 | Rulebook JSON, loader, lookup tests | 1.5 d |
| 1 | Extraction, sheet merge, missing logic, response fields | 2 d |
| 2 | Facts card, action chips, Edit facts | 1.5 d |
| 3 | Stepper | 1.5 d |
| 4 | Answer turn: preamble, findings, fit signals, disclaimer | 1.5 d |
| 5 | Replacement banner and escalation | 0.5 d |
| 6 | Eval, tuning, Docker rebuild and jsdom check | 1.5 d |

About 10 developer-days.

## 9. Relationship to M07 and M08

- **M08** has the model derive clarifying questions per turn from retrieved chunks. M16
  replaces that with the rulebook, so questions and options are stable and reviewable.
  M08's rules still hold: no mandatory questionnaire, no re-asking, fail open.
- **M07** refers any turn that describes a specific applicant to NLG. Under M16 that
  would refuse the mock's opening message. The rule becomes: client facts are used to
  select which documented rules to show; the app still never states an approval,
  decline or rate class for the individual. M07's disclaimer, `underwriting` signal and
  `case_specific` reason are kept as specified.

## 10. Open decisions

1. **Build-table band.** The answer card shows everything the guide establishes for the
   known facts: requirements, knockouts, APS triggers, the condition's qualifiers and
   its "potential best offer" tier quoted with the guide's own caveat. The one item held
   back is the height and weight band (p.32), because naming a class for an individual
   reads like the rate-class prediction M07 forbids. Confirm whether to show it.
2. **Facts from outside the guide.** The `nlg_supplied` slot stays empty unless NLG
   provides more (for example XRAE's question set).
3. **Privacy copy.** The sheet stays in the browser, but chat text is still written to
   the Foundry request traces and `ask_question_log`. Either the "I don't save names,
   SSNs or banking details" copy changes or those two need redaction for client turns.
4. **Replacement content.** The guide has no state replacement rules; confirm which
   approved document the F2.4 answer should cite.
