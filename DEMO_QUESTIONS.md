# FlexLife POC — Demo Script (M02, M03, M09, M10)

Run in Ask Navigator at `/app/learn.html`, as one continuous chat, in the order below.
In-corpus questions come from `tests/underwriting_guide_questions.md`, so the expected
answers are known. Do a dry run before the demo — these have not been run against the
live app.

---

## M02 — Natural Language Search

### 1. Plain question, synthesized answer

```
What are the EZ Underwriting face limits on FlexLife by age?
```

Expect: ages 18–50 up to $5M, 51–60 up to $3M, 61–65 up to $250K.

### 2. Follow-up without restating context (also shows M04)

```
A 45-year-old wants $1.5M of FlexLife. Do they need an exam?
```

```
Same case, but the client is 55?
```

Expect: first answer is application only; second is exam, blood and urine unless they
had a routine physical in the last 12 months.

### 3. Beyond simple lookup: a comparison

```
Compare what's required for Elite versus Preferred on blood pressure and cholesterol.
```

Expect: Elite needs 135/85 and a cholesterol ratio of 4.5 or less; Preferred allows 140/90.

### 4. Out-of-domain decline

```
Write me a haiku about Python.
```

```
Who won the World Series last year?
```

Expect: "I'm the FlexLife Navigator, so I can only help with FlexLife products, riders,
and approved wording…" with no sources.

---

## M03 — Citations

### 5. Answer with a source list

```
Does Zyn, vaping, hookah or betel nut count as tobacco?
```

Expect: yes, all of them. A "Sources (n)" link appears under the answer with at most 4
sources. Click it to expand.

### 6. Multiple sources supporting one answer

```
A 38-year-old earning $100K wants $5M of coverage. Is that OK, and what income multiple applies?
```

Expect: no, the max is about $3.5M (35× earned income), with sources listed.

### 7. Abstention when the corpus can't support an answer

```
What is the FlexLife commission schedule for agents in Guam in 2027?
```

Expect: "I couldn't find enough approved FlexLife material to answer that confidently.
Please reach out to NLG support…" with no sources. Leave this on screen for step 8.

---

## M09 — Handoff to NLG Support

### 8. Automatic handoff after an abstention

No prompt. Click the green **Prepare a request to NLG Support** button under the answer
from step 7.

Expect: an editable draft with To, Subject and Message, written in first person. Edit a
line, click **Send to NLG Support**, and show the "simulated, nothing was sent" toast.

### 9. Manual handoff for a case-specific decision

```
My client is 58 with type 2 diabetes and a heart stent from 2021. Will he be approved for FlexLife?
```

Then click **Ask NLG Support** in the action row under the answer.

Expect: a draft that summarizes the client details from the chat. The app does not
detect "case-specific" on its own, so use the manual button.

---

## M10 — Provide Appropriate Links

### 10. Open the source document from a citation

```
What's required for anyone 70 or older applying for FlexLife?
```

Expand Sources and click a title with the ↗ arrow.

Expect: the Underwriting Guide PDF opens in a new tab.

Known issue: this only works on turns answered by the local pipeline. Foundry-answered
turns return `PublicAccessNotPermitted`. If links fail in the dry run, show this step
from the Coach console at `/app/index.html`, where sources always use the working route.

---

## Dry-run checklist

- Step 7: if the app finds something for the Guam question, swap in another on-topic
  question the corpus won't cover.
- Step 10: click a link in whichever screen you plan to demo from.
- If document links return 502 instead, the storage SAS in `.env` has likely expired.

---

## Story status

Judged against the acceptance criteria in `USER_STORIES.md` from reading the code, not
from running the live app.

| Story | Status | What's left |
|---|---|---|
| M02 Natural Language Search | Done in code (9 of 9 criteria) | 3 stale tests; out-of-domain gate only covers Ask Navigator, not the Coach console |
| M03 Citations | Done in code (7 of 7 criteria) | 4 stale tests; relevance threshold only applies on the local pipeline, not Foundry |
| M09 Handoff | Mostly done (9 of 10 criteria) | App does not detect "needs a case-specific decision" on its own; manual button only |
| M10 Provide Appropriate Links | Partly done | Links fail on Foundry-answered turns (`PublicAccessNotPermitted`); they work on local-pipeline turns and in the Coach console |

Notes:

- M10 is the one most likely to bite in the demo, since Foundry is the main path in Ask
  Navigator. Fallback: show step 10 from the Coach console.
- The 7 stale tests (44 of 51 pass) are leftovers from the `CleanInputs` merge and don't
  indicate broken features, but the suite shows red if anyone runs it.
