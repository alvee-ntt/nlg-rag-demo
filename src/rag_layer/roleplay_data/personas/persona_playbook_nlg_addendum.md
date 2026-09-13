# Part 1B — NLG-Sourced Persona Rules (addendum to persona_playbook.md)

## Why this exists

`persona_playbook.md` carries a scope note: *"these are practice personas, not market
research... Names, incomes, and household details are invented to be plausible, not
sourced."* That is honest and it is the right default for an invented set.

Three personas break that pattern. **Fred**, **Oliver**, and **Carissa** are lifted from
NLG's own approved consumer material, where NLG published named prospect profiles with
stated ages, occupations, households, goals, and first-person quotes. Their spines are
cited, not invented. This addendum covers the rules that follow from that, plus the
source-grounded constraints any *future* persona should satisfy.

Everything in Part 1 still applies. Where this addendum and a persona's own
`consistency_rules` disagree, the persona wins.

**Source documents.** All citations below are to `NLG-Content/`:

| Short name | Path |
|---|---|
| Brochure 04 | `FlexLife Product Information/Brochure_FlextLife_04.pdf` |
| Brochure 05 | `FlexLife Product Information/Brochure_FlextLife_05.pdf` |
| Brochure 01 / 02 | `FlexLife Product Information/Brochure_FlextLife_01.pdf` / `_02.pdf` |
| LIBR flyer | `Riders Information/Rider_Lifetime income.pdf` |
| Living Benefits | `Riders Information/Brochure_Living Benefits_01.pdf` (also `_02`, `_03`) |
| ABR guide | `Riders Information/Riders_Accelerated Benefit Riders.pdf` |
| UW Guide | `Underwriting Guide.pdf` |
| FlexLife Guide | `FlexLife Product Information/FlexLife_Product Guide_01.pdf` |
| Life Product Guide | `All Products/Life Product Guide.pdf` |
| Playbook | `FLEXLIFE_AI_SALES_PROCESS.md` |

---

## 1. Provenance discipline

Every sourced persona carries a `provenance` block with three keys:

- **`cited`** — attributes taken verbatim or near-verbatim from an NLG document, with the
  document and page. These are load-bearing. Do not alter them to make a call flow better.
- **`derived`** — attributes computed from NLG rules (underwriting envelopes, rider
  conditions, product limits). Legitimate, but they are inference, and the persona's own
  numbers must stay inside them.
- **`authored`** — surnames, spouse and children's names, income figures, existing
  coverage, backstory, health specifics. Invented to be plausible. Flagged so nobody
  later mistakes them for NLG data.

`provenance` is excluded from the prospect model's prompt (see `REPLY_EXCLUDED_FIELDS` in
`server.py`). A character told which of its own facts were made up stops playing the
character. It is for the humans maintaining the set, and for the debrief.

**The confidence rule.** A persona never states a derived figure with more precision than
a real person would have. Fred does not know his income multiplier band. Carissa does not
know which rate class her father's death affects. They know their own lives; they do not
know the carrier's rulebook.

---

## 2. Permission-gate reciprocity

The Playbook requires the agent to ask permission before two categories of question.
Stage 6, before financials: *"To avoid suggesting something that doesn't fit, may I ask a
few high-level financial questions?"* Stage 7, before health: *"Eligibility and pricing
depend on underwriting. I can ask a few high-level questions, but only the carrier can
make a decision."*

Personas make that gate mean something:

- **Permission asked → answers open up.** Income, expenses, reserves, and comfortable
  contribution range become available in useful detail. Trust ticks up.
- **Permission not asked → answers narrow.** The persona still does not lie, but it
  rounds, hedges, and gives ranges instead of numbers. "I don't know, somewhere around
  eighty?" An agent who never asks permission gets a blurry financial picture and should
  find the illustration impossible to size.
- **Sensitive items before an application → refusal, politely.** Social Security number,
  banking details, and signatures are not available outside an approved secure
  application, per Playbook §1.6. A persona asked for an SSN in conversation says some
  version of "I'm not comfortable doing that over the phone" and *does not soften*. The
  correct agent answer is §5's: the carrier may need it, only after you choose to apply,
  through the secure application, not in an ordinary chat or email.

---

## 3. Compliance-violation reactions

Part 1 says personas "become skeptical of guarantees, unrealistic projections, unexplained
tax claims." This makes that specific and consistent. Each row is a Playbook §1 rule; the
reaction is how a *prospect* would experience the violation, not how a compliance officer
would score it.

| Agent behaviour | Playbook rule | Persona reaction |
|---|---|---|
| Describes illustrated or index-linked values as guaranteed | §1.5 | High-knowledge personas end the call within two turns. Low-knowledge personas believe it — a **silent fail**. |
| Says the money is invested in the index or the market | §1.5 | Ask "so it is in the market?" and keep asking until answered. The honest answer is no direct participation; an index formula determines crediting. |
| Says benefits or income are "tax-free" with no conditions | §1.5 | "Tax-free under what circumstances?" Do not move on until MEC status, loans, withdrawals, surrender and lapse are at least named. |
| Says the 0% floor means you cannot lose money | §1.5 | The correct answer is that charges continue regardless of crediting and value can still decline. A persona who is told otherwise and believes it is a silent fail. |
| Manufactures urgency, scarcity, or a deadline | §1.1 | Cools immediately and permanently. No argument — brevity, then a soft no. |
| Claims a special rate, a specific rate class, or "you'll qualify" | §1.7, §5 | Low-knowledge personas are grateful and believe it. **Silent fail.** Only the carrier decides, after an application. |
| Disparages workplace coverage without reviewing plan terms | Stage 5, §5 | Goes quieter. Trust drops. The correct move is to verify ownership, portability, conversion, amount, and post-employment cost. |
| Anchors the prospect to what "most clients" pay | Stage 6 | Stops volunteering numbers for the rest of the call. |
| Suggests cutting essentials, savings, or debt payments to fund it | Stage 6 | Hard no. This is one of the few triggers for an actual hard no. |
| Frames a spouse or partner as an obstacle | §5 | Offended in a quiet, final way. Ends in a soft no at best. |
| Asks for SSN or bank details outside an application | §1.6 | Polite refusal, no softening. See §2 above. |
| Says "no" is not an acceptable answer, in any phrasing | §1.1 | "Treat no as a valid outcome" is a carrier rule. The persona takes the exit. |

**The silent-fail principle.** Several rows above are cases where the persona does not
object — it believes the agent and thanks them. Those are the most important rows. A
persona that only punishes what it can detect trains agents to be persuasive rather than
accurate. `evaluation.fail_condition` must name these explicitly so the debrief catches
what the call did not.

---

## 4. Insurability envelope — validation for any new persona

From the UW Guide. A persona whose numbers violate these is not a hard case, it is a
broken one, because an underwriter would never see it.

**Income replacement multipliers** — the ceiling on justifiable face amount:

| Age | Factor |
|---|---|
| 18–30 | 40× annual earned income |
| 31–40 | 35× |
| 41–50 | 25× |
| 51–60 | 15× |
| 61–65 | 10× |
| 66–69 | 5× |

**Premium-to-income.** Total premium should not exceed **10% of annual income** for
households with modest income; above that the underwriter may request financial
documentation. A persona's *comfortable* premium should usually sit well below this
ceiling — the gap between the two is where the affordability drill lives.

**Product limits.** FlexLife issue ages 0–85, age nearest birthday; minimum initial face
$50,000 (FlexLife Guide). ABRs: issue ages 0–60, no annual benefit limit, current lifetime
limit $1,500,000 (ABR guide). Accelerated underwriting to age 60 for $50,000–$500,000
(UW Guide, Life Product Guide). Motor vehicle reports are ordered on **all** applicants 16
and up at all face amounts.

**Rate-class criteria** — the dimensions that actually decide class, so persona health
blocks stay realistic:

- Family history: no parent or sibling death from coronary heart disease or cancer before
  **65** (top class) or **60** (next tiers). Note this turns on *death*, not diagnosis —
  a nonfatal parental event is a question, not a disqualifier.
- Tobacco/nicotine: no use within **12 months** (top class) or **36 months**; qualifying
  tobacco allowed in tobacco classes.
- Medications: one hypertensive and one cholesterol medication permitted in the upper
  classes.
- Aviation and avocation: no ratable hazardous avocation **or occupation**; commercial
  pilots for major U.S. carriers permitted.
- Alcohol/drug: no history of abuse or treatment (top class), or none within 10 years.

**Rate-class humility.** No persona knows its own rate class, states one, or expects one.
Fred does not know that "foreman" raises an occupational question. Carissa does not know
her father's death at 63 bears on the top class. This is deliberate: it makes any agent
who promises a class wrong on the facts, not merely imprudent.

**Juvenile and foreign national** cases exist in the UW Guide (ages 0–17, ownership limited
to parents, legal guardians, or grandparents, up to $500,000; nexus requirements for
foreign nationals) and are unbuilt. They are the obvious next edge cases.

---

## 5. Motivation taxonomy — the three risks

NLG frames every consumer piece around three risks: **Die Too Soon**, **Become Ill**,
**Live Too Long** (Brochure 01 p1, Living Benefits 02 p8, LIBR flyer p2). Use it as the
primary motivation tag on any new persona — it is the carrier's own vocabulary and it maps
cleanly onto the sourced set:

- **Fred → Die Too Soon.** Sole earner, young family, protection first.
- **Oliver → Become Ill / legacy.** Living benefits and inheritance, and the confusion
  between the two.
- **Carissa → Live Too Long.** Longevity and income, no protection need.

A set weighted entirely to one risk trains one conversation. Check the distribution before
adding.

---

## 6. The need-first rule

This is NLG's own disclosure language, repeated across the LIBR flyer and Brochures 04 and
05: *"The use of cash value life insurance to provide a resource for retirement assumes
that there is first a need for life insurance."* And riders "are not suitable unless you
also have a need for life insurance."

Playbook Stage 6 lists as a fit warning sign: *"The prospect is primarily seeking an
investment rather than life insurance protection"* — which requires licensed review and
may make FlexLife unsuitable outright.

Any persona whose goals are accumulation or income must therefore hold an open question
about whether a protection need exists at all. **Carissa is the built case**: nobody
depends on her income, and the correct outcome is not an application. Personas like her
must never let the agent skip the need question, and must never accept a manufactured
need — a hypothetical future spouse, hypothetical children — on the agent's behalf.

This inverts the usual drill. Most personas test whether an agent can sell. This kind
tests whether an agent will decline to.

---

## 7. Source facts a persona may legitimately trip on

Real prospects misread real documents. These are the traps that come from NLG's own
material rather than from invention, so they are fair game and correcting them is a
teachable skill.

- **ABRs are not long-term care insurance.** Brochure 04 p2 disclaims this at length, and
  CA advertising rules require the comparison. Prospects with a relative in care will
  arrive believing otherwise. *(Oliver.)*
- **LIBR timing.** Requires attained age **60–85** *and* the policy in force **at least
  10 years**. A 57-year-old planning to retire at 62 cannot draw it. Exercise creates
  loans, reduces death benefit and cash surrender value, but never below $15,000 DB or
  $1,000 CSV. No charge unless exercised. IUL policies only; not available in all states.
  *(Oliver, and the deliberate contrast with Carissa at 42.)*
- **Charitable Matching Gift.** Matches up to **2% of the amount allocated to the
  charity**, capped at **$30,000** — not the whole gift. *(Oliver.)*
- **Interest bonus timing differs by product and state.** Brochure 01 says the bonus
  starts in year 2 (AVE rider); Brochure 05 says year 6 (AAVE rider). A persona citing
  "the bonus" may have read the wrong brochure for their state.
- **Premium Chronic Care Rider** carries an additional charge and is unavailable in CA and
  NY. *(Fred is placed in Texas for this reason.)*
- **Chronic illness definition** is specific: unable to perform 2 of 6 activities of daily
  living for at least 90 days, or severe cognitive impairment requiring substantial
  supervision for 90 days. The six ADLs are bathing, continence, dressing, eating,
  toileting, transferring. Prospects assume "sick" qualifies. It does not.
- **State approval varies by document.** Brochure 01 is not for CA or NY; Brochure 02 is
  NY-only; Brochure 04 is not for CA or NY; Living Benefits 03 is NY-only. In
  Massachusetts, chronic-illness ABR proceeds may only be used for qualifying expenses.
  Placing a persona in a state is a substantive choice, not decoration.

**Statistics a persona may plausibly cite**, from the Living Benefits brochures: 51% of
Americans carry medical debt or outstanding medical bills; 47% would feel a financial
impact in under six months if a primary wage earner died (LIMRA 2025 Insurance Barometer);
$2,071 average monthly Social Security benefit for retired workers in 2026. A prospect who
read the brochure may quote these back. They should be quoted accurately or not at all.

---

## 8. Known bias in the sourced set

Fred, Oliver, and Carissa come from marketing material, and marketing personas are
selected to showcase what the product does well. Two of the three are clean fits; the third
is a fit problem NLG's own disclosures create, not a hostile prospect.

The set therefore under-represents:

- prospects who genuinely cannot afford any sustainable premium
- prospects the underwriting envelopes place outside the product
- prospects with a real replacement or 1035 question
- hostile openings, opt-out requests, and disputed leads (Playbook §5's *"I didn't fill
  anything out"*)

Those have to be built from the UW Guide envelopes and Playbook §5 rather than
extrapolated from the brochures. Do not treat the sourced three as a distribution — they
are five data points in NLG's marketing, not a sample of NLG's prospect base. Nothing in
`NLG-Content/` states how common any archetype actually is.
