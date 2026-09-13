"""The salesDJ starter curriculum for new FlexLife agents.

Each entry is a *pinned* mix: instead of semantic search, the generator reads the
exact document sections listed under ``sources`` and is handed a coverage ``brief``
of the facts the lesson must land. Tier 1 is the day-one essentials, tier 2 goes
deeper in the same plain-English register, tier 3 is how to sell it.

Source specs match documents by a case-insensitive substring of the blob name
(``doc``), optionally narrowed to PDF ``pages`` or a ``chunks`` index range. A spec
with neither pulls the whole document (fine for the short transcripts).

The curriculum is written for the non-New-York version of FlexLife. NY caps, riders
and bonus timing differ; NY material is deliberately excluded from every pinned list.
"""

from __future__ import annotations

from typing import Any, TypedDict


class SourceSpec(TypedDict, total=False):
    doc: str
    pages: list[int]
    chunks: list[int]  # [first, last] inclusive chunk_index range


class CurriculumItem(TypedDict):
    key: str
    tier: int
    kind: str
    length: str
    prompt: str
    brief: str
    sources: list[SourceSpec]


# Document handles (substrings of blob names in the index).
QUICK_REF = "FlexLife_Product Quick Ref Guide"
LIBR_GUIDE = "FlexLife_Product Quick Guide"  # this file is the LIBR quick reference
PRODUCT_PAGE = "FlexLife product page"
TRAINING = "FlexLife Agent Training Voice Over"
PRODUCT_GUIDE = "FlexLife_Product Guide_01"
BROCHURE_1 = "Brochure_FlextLife_01"
BROCHURE_3 = "Brochure_FlextLife_03"
BROCHURE_5 = "Brochure_FlextLife_05"
IUL_ARTICLE = "Indexed Universal Life Insurance_ Upside Potential"
INDEX_GUIDE = "Product_Index Guide_01"
ABR_GUIDE = "Riders_Accelerated Benefit Riders"
LB_BROCHURE = "Brochure_Living Benefits_01"
PCC_DECK = "Premium Chronic Care Training Deck"
PCC_FLYER = "Rider_Premium Chronic Care"
LIBR_FLYER = "Rider_Lifetime income"
LB_VIDEO_1 = "NLG living benefits 1"
LB_VIDEO_2 = "NLG living benefits 2"
LB_VIDEO_3 = "NLG Living benefits 3"
EZ_UW = "EZ Underwriting Program"
UW_GUIDE = "Underwriting Guide.pdf"
SALE_1 = "NLG Art of the sale 1"
SALE_2 = "NLG Art of the Sale 2"
SALE_3 = "NLG Art of the Sale 3"
SALE_4 = "NLG Art of the Sale 4"
KEY_TO_SALE = "NLG KeyToTheSale"
PRESENTING = "NLG presenting to prospects"
QUESTIONING = "guide to conversational questioning"
STATE_OF_MIND = "financial state of mind"

TIER_TITLES = {1: "Day one: the essentials", 2: "Going deeper, still plain English", 3: "Selling it"}

CURRICULUM: list[CurriculumItem] = [
    # ------------------------------------------------------------------ Tier 1
    {
        "key": "t1-what-is-flexlife",
        "tier": 1, "kind": "article", "length": "short",
        "prompt": "What FlexLife is, on one page",
        "brief": (
            "Cover: FlexLife is National Life Group's flagship indexed universal life policy, issued by Life "
            "Insurance Company of the Southwest outside New York. Who it is for: emerging affluent clients who "
            "need death benefit protection and may use the policy for income later, plus business-owner uses. "
            "Issue ages 0 to 85, $50,000 minimum face amount, flexible premiums. The three questions the product "
            "answers: die too soon (death benefit), become ill (living benefits), live too long (Lifetime Income "
            "Benefit Rider). What it is NOT: not an investment, does not directly participate in the market, "
            "income is not a policy feature (income comes from loans, withdrawals or LIBR if there is enough cash "
            "value). The 'flexibility' claim: adjust premiums and coverage as needs change."
        ),
        "sources": [
            {"doc": QUICK_REF, "pages": [1, 2]},
            {"doc": PRODUCT_PAGE},
            {"doc": TRAINING, "pages": [2, 3, 5, 6]},
            {"doc": PRODUCT_GUIDE, "pages": [2, 7]},
        ],
    },
    {
        "key": "t1-three-questions",
        "tier": 1, "kind": "audio", "length": "short",
        "prompt": "The three questions every FlexLife conversation answers",
        "brief": (
            "Structure the episode around National Life's own framing: What if you die too soon? (permanent death "
            "benefit, level Option A vs increasing Option B, Benefit Distribution Option, Death Benefit Protection "
            "Rider). What if you become ill? (Accelerated Benefit Riders for terminal, chronic, critical illness, "
            "critical injury and Alzheimer's, at no extra premium but paid on a discounted basis; the optional "
            "Premium Chronic Care Rider that pays dollar-for-dollar). What if you live too long? (Lifetime Income "
            "Benefit Rider: guaranteed income for life once exercised, between ages 60 and 85 after 10 policy "
            "years). Andrew should ask what 'discounted' means and Ava should answer simply. Close with why this "
            "triad is the elevator pitch."
        ),
        "sources": [
            {"doc": TRAINING, "pages": [7, 8, 9, 10, 11, 12, 24, 25, 26]},
            {"doc": BROCHURE_1, "pages": [1, 2, 3, 4]},
            {"doc": QUICK_REF, "pages": [1]},
        ],
    },
    {
        "key": "t1-cash-value-101",
        "tier": 1, "kind": "article", "length": "short",
        "prompt": "Cash value 101: floor, cap, and participation rate",
        "brief": (
            "Explain the three levers using the consumer article's exact examples: the floor (the least you are "
            "ever credited is 0%); the cap (if the index grows 10% but the cap is 6%, you are credited 6%; not all "
            "strategies are capped); the participation rate (index gains 8%, participation 140%, credited 11.20% "
            "with no cap; at 60% credited 4.80%). Interest is credited point-to-point after one year. Answer 'will "
            "it grow as much as the stock market?' honestly: not necessarily. The trap every rookie misses: the "
            "0% floor protects index credits, but monthly deductions (policy fee, expense charge, cost of "
            "insurance, rider charges) still come out, so cash value can fall in a flat year. Interest grows tax "
            "deferred. Participation rates and caps can change annually."
        ),
        "sources": [
            {"doc": IUL_ARTICLE},
            {"doc": PRODUCT_GUIDE, "pages": [6, 8, 9]},
            {"doc": BROCHURE_1, "pages": [3]},
        ],
    },
    {
        "key": "t1-by-the-numbers",
        "tier": 1, "kind": "flashcards", "length": "long",
        "prompt": "FlexLife by the numbers",
        "brief": (
            "Cards must include: issue ages 0 to 85 (age nearest birthday); minimum face $50,000 initial and "
            "$25,000 for increases; minimum premium $10; surrender schedule 10 years; Policy Protection Period 10 "
            "years; premium load 8% in years 1-10 and 5% after; monthly policy fee $6 (guaranteed max $8); fixed "
            "account guaranteed 1.75%; Cap Focus guaranteed minimum cap 3.1% with participation at least 100%; "
            "Participation Focus at least 110% participation with minimum cap 3.0%; 1% Floor option minimum cap "
            "2.1%; Balanced Trend and US Pacesetter: participation at least 50%, no cap, 0% floor, not in NY; "
            "accumulation value bonus 0.25% beginning year 2; the four loan types; loans and withdrawals available "
            "after year one, $500 minimum withdrawal; rate bands ($250,000, $1,000,000, $3,000,000 breakpoints); "
            "full medical underwriting above $3,000,000; death benefit Option A level vs Option B increasing."
        ),
        "sources": [
            {"doc": QUICK_REF, "pages": [1, 2, 3]},
            {"doc": TRAINING, "pages": [6, 29]},
            {"doc": PRODUCT_GUIDE, "pages": [3, 4, 18]},
        ],
    },
    {
        "key": "t1-living-benefits-plain",
        "tier": 1, "kind": "article", "length": "short",
        "prompt": "Living benefits in plain English",
        "brief": (
            "Cover the six Accelerated Benefit Riders: Terminal Illness (expected death within 24 months, no waiting "
            "period), Chronic Illness (unable to do 2 of 6 activities of daily living for 90 days, or severe "
            "cognitive impairment; 30-day waiting period; monthly benefit), Critical Illness (the covered list, "
            "e.g. heart attack, stroke, cancer, ALS), Critical Injury (coma, paralysis, severe burns, traumatic "
            "brain injury), Alzheimer's (issue ages 0-60; not available with a first-degree family history), and "
            "the optional Premium Chronic Care Rider. Key ideas: no extra premium for the ABRs; the benefit is a "
            "DISCOUNTED portion of the death benefit, not dollar-for-dollar; using it reduces the death benefit and "
            "cash value; cash can be used for anything, no receipts; lifetime maximums $1,500,000 for terminal, "
            "chronic and Alzheimer's and $1,000,000 for critical, per insured across all policies; critical claims "
            "must be filed within 365 days. The sentence that keeps an agent licensed: these riders are not "
            "long-term care insurance and must never be described as an alternative to it. Benefits may be taxable "
            "and can affect public assistance eligibility."
        ),
        "sources": [
            {"doc": ABR_GUIDE, "pages": [2, 3, 4, 5, 6, 7, 8, 9]},
            {"doc": TRAINING, "pages": [9, 10, 11, 12, 13, 14, 15]},
            {"doc": LB_BROCHURE, "pages": [1, 2, 3, 5]},
        ],
    },
    {
        "key": "t1-living-benefits-facts",
        "tier": 1, "kind": "flashcards", "length": "long",
        "prompt": "Living benefits: the facts you must not get wrong",
        "brief": (
            "Cards must include: the six ADLs (bathing, continence, dressing, eating, toileting, transferring); "
            "chronic trigger is 2 of 6 for at least 90 days or cognitive impairment; terminal = 24 months (12 in "
            "some states); waiting periods (terminal none; chronic, critical, Alzheimer's 30 days); critical claim "
            "filing within 365 days; lifetime caps $1.5M terminal/chronic/Alzheimer's and $1M critical, aggregate "
            "per insured across all policies; Alzheimer's issue ages 0-60 and first-degree family history "
            "exclusion; Alzheimer's and Premium Chronic Care cannot be added after issue; Express Standard "
            "Non-Tobacco 2 and table ratings above 250% get only the terminal rider; benefits are discounted based "
            "on life expectancy, remaining premiums, fees and loans; partial acceleration reduces the death benefit "
            "proportionally; always contact Agent Services before quoting a benefit; two-year contestability; "
            "the issuing state's rules follow the policy if the client moves; not long-term care insurance."
        ),
        "sources": [
            {"doc": ABR_GUIDE, "pages": [3, 4, 5, 6, 8, 9, 11, 12, 13]},
            {"doc": LB_VIDEO_1},
            {"doc": LB_VIDEO_2},
            {"doc": LB_VIDEO_3},
        ],
    },
    # ------------------------------------------------------------------ Tier 2
    {
        "key": "t2-premium-dollar",
        "tier": 2, "kind": "article", "length": "long",
        "prompt": "How a dollar of premium actually moves through a FlexLife policy",
        "brief": (
            "Walk one premium dollar through the machine: premium load comes off first (8% years 1-10, 5% after); "
            "the net goes into the Basic Strategy; on the sweep date (the 14th of the month) it moves into the "
            "chosen crediting options and starts a 12-month segment; index interest is calculated point-to-point "
            "at the end of the segment; money taken out before the segment ends earns nothing for that segment; "
            "allocation changes only take effect at segment end. Meanwhile the monthly deduction comes out every "
            "month regardless of crediting: cost of insurance (rate times net amount at risk), monthly expense "
            "charge, $6 policy fee, 0.04% of accumulated value charge, rider charges. Explain net amount at risk "
            "simply. The 10-year Policy Protection Period keeps the policy from lapsing if cumulative minimum "
            "premiums are paid. Surrender charges for 10 years, and each face increase starts a new 10-year "
            "schedule. Why monthly premiums do not guarantee an advantage over annual. Why max-funding matters for "
            "cash value and why minimum funding will not build significant value."
        ),
        "sources": [
            {"doc": PRODUCT_GUIDE, "pages": [6, 8, 9, 10, 11, 13, 17, 18]},
        ],
    },
    {
        "key": "t2-crediting-options",
        "tier": 2, "kind": "audio", "length": "short",
        "prompt": "Cap Focus vs Participation Focus vs 1% Floor: which option and why",
        "brief": (
            "Ava explains the S&P 500 options with the product guide's worked example (9% index growth, 100% "
            "participation, 7% cap credits 7%), then Participation Focus (participation at least 110%, lower cap), "
            "the 1% Floor option (guaranteed 1% but lower cap), the uncapped Balanced Trend and US Pacesetter "
            "options with participation at least 50%, and the Fixed account. Andrew asks the rookie question: "
            "which one is best? Ava must say plainly that there is no way to know which option will perform best, "
            "past performance is not an indicator, and that is why many clients split allocations. Explain why "
            "caps move: the company buys one-year options to hedge, and when options get expensive or general "
            "account yields fall, caps come down. Mention the accumulation value bonus from year 2. Never claim "
            "an illustrated rate is a prediction."
        ),
        "sources": [
            {"doc": PRODUCT_GUIDE, "pages": [4, 8, 9, 10, 11]},
            {"doc": INDEX_GUIDE, "pages": [1, 2, 3]},
            {"doc": QUICK_REF, "pages": [3]},
        ],
    },
    {
        "key": "t2-pcc-vs-chronic",
        "tier": 2, "kind": "article", "length": "short",
        "prompt": "Premium Chronic Care vs the free Chronic Illness rider",
        "brief": (
            "Same trigger (2 of 6 ADLs or cognitive impairment), very different check. Chronic Illness ABR: no "
            "extra premium, benefit is discounted at claim, shares the $1.5M lifetime cap, 30-day waiting period, "
            "issue ages 0-85, benefit depends on funding and cash value performance. Premium Chronic Care Rider "
            "(new for 2025 FlexLife): optional with an extra charge, pays 2% or 4% of the death benefit per month "
            "with no discounting, chosen at issue (4% can be reduced to 2% later but never increased), separate "
            "$3,000,000 lifetime cap so up to $4.5M of chronic protection when combined, no waiting period, issue "
            "ages 18-75, subject to the IRS per diem limit, 12-month benefit period then recertification, all "
            "policy charges waived during the benefit period if accumulation value is exhausted, must be used "
            "instead of the chronic ABR if it is on the policy, one PCC policy per customer, no policy changes "
            "during a benefit period. Use the deck's cost framing (about 95 cents a day for more than 50% more "
            "chronic benefit in its example) but label it a hypothetical. Not available in CA or NY."
        ),
        "sources": [
            {"doc": PCC_DECK},
            {"doc": PCC_FLYER},
            {"doc": TRAINING, "pages": [16, 17, 18, 19, 20]},
            {"doc": PRODUCT_GUIDE, "pages": [20]},
        ],
    },
    {
        "key": "t2-libr",
        "tier": 2, "kind": "article", "length": "short",
        "prompt": "The Lifetime Income Benefit Rider: guaranteed income for life, and the fine print",
        "brief": (
            "Cover: LIBR is automatically added to eligible FlexLife policies at no cost; the charge (0.65% "
            "annually, deducted monthly) only applies once income is activated. To exercise: policy in force at "
            "least 10 years, insured between 60 and 85, no outstanding loans, death benefit ratio within the "
            "maximum, benefit at least $100, not issued Express Standard Non-Tobacco 2 or rated above 250%. Income "
            "is paid as fixed net cost policy loans, so it is not taxable for a non-MEC policy; once the cash value "
            "threshold is reached, payments continue for life and the death benefit never drops below $15,000 nor "
            "cash surrender value below $1,000. Payout choices: level, or increasing 3% a year; a ratchet every "
            "fifth anniversary can raise the income base but never lower it. Premiums stop once exercised; income "
            "can be suspended and resumed once per policy year up to age 85. The benefit is not guaranteed until "
            "exercised. Explain why this is the 'live too long' answer and use the Sara story as a hypothetical."
        ),
        "sources": [
            {"doc": LIBR_GUIDE},
            {"doc": LIBR_FLYER},
            {"doc": TRAINING, "pages": [24, 25, 26]},
            {"doc": PRODUCT_GUIDE, "pages": [31, 32]},
        ],
    },
    {
        "key": "t2-loans-withdrawals",
        "tier": 2, "kind": "audio", "length": "long",
        "prompt": "Loans, withdrawals, and how people accidentally lapse a policy",
        "brief": (
            "Ava walks Andrew through the four loan types: Participating Declared (not in NY), Participating "
            "Variable, Participating Fixed (5% set at issue) and Standard; what 'participating' means (the "
            "collateral keeps earning index credits) and the 'upside down' risk when the loan rate exceeds what "
            "the collateral earns. Loans available after year one; loan type can be switched once per policy "
            "year. Withdrawals after year one, $500 minimum, and under Option A the face amount drops by the "
            "withdrawal. Tax order: withdrawals up to basis are tax-free, then people switch to loans; if the "
            "policy lapses with loans outstanding the gain becomes taxable income, which is how people get hurt. "
            "MEC rules if too much premium goes in. Overloan Protection Rider as the safety net: policy in force "
            "15 years, insured 75 or older, loans at 95% of cash value, becomes a paid-up policy with a fee. "
            "Andrew should ask 'so is the tax-free retirement thing real?' and Ava should give the compliant answer: "
            "only if there is first a need for life insurance, only loans and withdrawals, and it depends on funding "
            "and performance, not guaranteed."
        ),
        "sources": [
            {"doc": PRODUCT_GUIDE, "pages": [14, 15, 16, 33]},
            {"doc": QUICK_REF, "pages": [2, 3]},
            {"doc": PRODUCT_PAGE},
        ],
    },
    {
        "key": "t2-options-and-tests",
        "tier": 2, "kind": "flashcards", "length": "short",
        "prompt": "Option A vs Option B, GPT vs CVAT",
        "brief": (
            "Cards: Option A level death benefit = face amount (or accumulated value times corridor if greater); "
            "Option B increasing = face amount plus accumulated value; switching A to B or B to A and what happens "
            "to the death benefit at the moment of change; Option B generally allows larger premiums; MEC warning "
            "when switching from increasing to level; Guideline Premium Test caps premiums; Cash Value Accumulation "
            "Test has no premium cap but higher corridor factors; the test is chosen at issue and is irrevocable; "
            "LIBR requires GPT; how a withdrawal reduces face under Option A."
        ),
        "sources": [
            {"doc": PRODUCT_GUIDE, "pages": [12, 13, 16, 17]},
            {"doc": LIBR_GUIDE, "pages": [1]},
        ],
    },
    {
        "key": "t2-underwriting",
        "tier": 2, "kind": "article", "length": "short",
        "prompt": "Getting the client approved: EZ Underwriting and what delays a case",
        "brief": (
            "Cover: EZ Underwriting can approve without exams or labs when criteria are met, but it is not "
            "guaranteed issue. Who qualifies: applicants 50 and under with a routine physical in the past 12 months; "
            "the face amount tiers by age (quote them exactly from the flyer). What gets checked: MIB on every "
            "application, prescription history back seven years, motor vehicle report at 16+, medical claims data. "
            "Rate classes from Elite to Express Standard and what moves a client (tobacco-free months, BMI, family "
            "history, medications, driving record). Applicants 60 and over need a physical within 24 months or the "
            "file is declined. The commonly missed application questions (other applications, replacements, "
            "DUI/misdemeanors, physician name, medications, upcoming appointments). A complete medical history can "
            "save up to five business days. Application good for 6 months; two-year contestability; "
            "misrepresentation can void the policy. Set expectations: 'may be approved immediately' or the agent is "
            "contacted for more."
        ),
        "sources": [
            {"doc": EZ_UW},
            {"doc": UW_GUIDE, "pages": [3, 4, 5, 9, 17, 18, 27, 28, 34]},
            {"doc": QUICK_REF, "pages": [2]},
        ],
    },
    # ------------------------------------------------------------------ Tier 3
    {
        "key": "t3-first-conversation",
        "tier": 3, "kind": "audio", "length": "long",
        "prompt": "Your first FlexLife conversation with a prospect",
        "brief": (
            "Ava coaches Andrew through a first meeting using the Art of the Sale material: prepare a 30-second "
            "value proposition and know your why; own what you sell; honor the referral and align on the meeting "
            "goal; ask permission to get personal; watch communication styles and body language; find the real "
            "decision maker. Then the discovery interview: a conversation, not an interrogation ('tell me about your "
            "family' instead of 'do you have kids'); think like a detective; facts and feelings; the customer buys "
            "for their reasons; product case vs strategy case (sometimes the right answer is a term policy). Use "
            "the conversational questioning guide's four tracks (current state, future state, money mindset, "
            "readiness for the unexpected) with a few verbatim questions, and trial closes like 'if there was a "
            "solution to that, would you want to learn more?'. Role-play a short exchange between the hosts."
        ),
        "sources": [
            {"doc": SALE_1},
            {"doc": SALE_2},
            {"doc": QUESTIONING, "chunks": [0, 14]},
        ],
    },
    {
        "key": "t3-discovery-questions",
        "tier": 3, "kind": "flashcards", "length": "long",
        "prompt": "Discovery questions that actually work",
        "brief": (
            "Front: the situation or track (e.g. 'Opening the protection track', 'Client mentions retirement', "
            "'Find out about group coverage'). Back: the verbatim question(s) from the questioning guide or the "
            "customer interview transcript, plus the follow-up ('tell me more', 'why is this important to you?'). "
            "Include the four question types (data, biographical, thought-provoking, intuitive), the four tracks, "
            "the permission question, the 1-to-10 self-rating question, 'how much of your coverage is through "
            "work?', and the referral questions from the follow-up session. Use the customer's words back to them."
        ),
        "sources": [
            {"doc": QUESTIONING},
            {"doc": SALE_2},
            {"doc": STATE_OF_MIND, "chunks": [0, 6]},
            {"doc": SALE_4},
        ],
    },
    {
        "key": "t3-living-benefits-story",
        "tier": 3, "kind": "audio", "length": "short",
        "prompt": "The living benefits story: castle and moat, YouFundYou",
        "brief": (
            "Ava retells Mike Marino's approach from the presenting-to-prospects panel: the first appointment with "
            "only the living benefits brochure, the financial castle-and-moat analogy (one dollar builds a moat of "
            "death benefit, chronic, critical illness, critical injury and terminal protection), going to the "
            "critical illness page first because it is the real differentiator, 'illustrations are a beauty "
            "contest', and against competitors going straight to what they don't have. 'Not GoFundMe, YouFundYou'. "
            "Prospecting angles: elder-law attorneys, accountants, key-person and buy-sell cases. Andrew pushes back "
            "on whether this is too salesy and Ava anchors it in the compliance lines: benefits are discounted, "
            "never quote an amount, not long-term care insurance, business-owned policies may make benefits taxable."
        ),
        "sources": [
            {"doc": PRESENTING},
            {"doc": LB_BROCHURE, "pages": [2, 3, 4, 5, 6]},
        ],
    },
    {
        "key": "t3-objections",
        "tier": 3, "kind": "audio", "length": "long",
        "prompt": "Objections: the sale starts when you hear no",
        "brief": (
            "From the KeyToTheSale notes and Art of the Sale 3: an objection is an unanswered question; the five "
            "buckets (no hurry, no worry, no time, no need, no money); the physiology (pause, breathe, talk less; "
            "top reps pause longer and talk less); the mistakes (answering every objection, 'yes, but', giving up: "
            "most customers say no four times and most salespeople quit after four). The 10-step process and the "
            "METOR framework (minimize, explain, transform, outweigh, reinforce). Then role-play the big ones with "
            "the taught responses: 'it costs too much' (minimize yearly to monthly to daily, 'if we could make it "
            "affordable would you move ahead?'), 'I can get it cheaper on TV', 'I want to think about it', 'not "
            "now' (the opportunity-cost close: one diagnosis away from losing the riders), 'I need to talk to my "
            "spouse', and the final-objection probe."
        ),
        "sources": [
            {"doc": KEY_TO_SALE},
            {"doc": SALE_3, "chunks": [14, 34]},
        ],
    },
    {
        "key": "t3-objection-scripts",
        "tier": 3, "kind": "flashcards", "length": "long",
        "prompt": "Objection scripts",
        "brief": (
            "Front: the objection in the client's words. Back: the taught response, as close to verbatim as the "
            "sources allow, plus the technique name (teeter-totter, minimization, question-becomes-the-answer, "
            "METOR, assumptive close, opportunity-cost close). Include the five buckets, the four objection types by "
            "frequency, the 10-step process, and the pause/talk-less statistics as recall cards."
        ),
        "sources": [
            {"doc": KEY_TO_SALE},
            {"doc": SALE_3, "chunks": [14, 34]},
        ],
    },
    {
        "key": "t3-illustration-and-close",
        "tier": 3, "kind": "article", "length": "long",
        "prompt": "Walking a client through the illustration and closing",
        "brief": (
            "From Art of the Sale 3: keep it super simple; show only the illustration pages you need; be "
            "transparent that it is life insurance with fees and funding requirements; illustrations are "
            "projections, not guarantees. The page order: overview, key feature detail, the National Life story, "
            "the total-package page (start here for numbers people), the accelerated benefit projection page, the "
            "guaranteed ledger (worst case), the non-guaranteed ledger. Reading interest and moving to close. The "
            "four ways to answer a question. The three closes (assumptive, question, opportunity cost) with "
            "wording. Roadblocks: 'think about it', 'not now', hidden decision maker. Always ask for the sale, "
            "book the follow-up before leaving, and if you lose, ask the exit-door question."
        ),
        "sources": [
            {"doc": SALE_3},
        ],
    },
    {
        "key": "t3-after-the-sale",
        "tier": 3, "kind": "article", "length": "short",
        "prompt": "After the sale: delivery, reviews, and referrals",
        "brief": (
            "From Art of the Sale 4 and the underwriting guide: monitor submitted business on the portal so it "
            "does not die on the vine; the delivery checklist (re-explain the policy, confirm payments, ask whether "
            "there has been any new diagnosis since application because of contestability, verify beneficiaries, "
            "beneficiary cards, portal login); deliver in person as soon as possible. Service cadence: annual "
            "review at anniversary, birthdays, holidays, at least one personal call a year, don't let technology "
            "make you impersonal. Referrals: if you don't ask you won't get them; the 1-to-10 satisfaction "
            "question, the five business cards question, the 'you mentioned...' question."
        ),
        "sources": [
            {"doc": SALE_4},
            {"doc": UW_GUIDE, "pages": [4, 5]},
        ],
    },
    {
        "key": "t3-never-say",
        "tier": 3, "kind": "flashcards", "length": "long",
        "prompt": "What you can never say about FlexLife",
        "brief": (
            "Front: a tempting claim a rookie might make. Back: why it is wrong and the compliant way to say it. "
            "Cards must include: calling ABRs long-term care insurance or an alternative to it; comparing ABRs to "
            "Aflac, disability, medical or Medicare coverage; quoting an accelerated benefit amount without an "
            "illustration or Agent Services; 'guaranteed growth' or predicting index performance; 'you can't lose "
            "money' (charges still come out); calling it an investment or saying it participates in the market; "
            "'tax-free retirement' without a life insurance need first; giving tax or legal advice; promising the "
            "bonus or caps will not change; saying riders are free when Premium Chronic Care has a charge; saying "
            "income is a policy feature; ignoring that benefits may be taxable or affect public assistance; using "
            "LSW material in New York; editing approved email templates."
        ),
        "sources": [
            {"doc": ABR_GUIDE, "pages": [2, 4]},
            {"doc": LB_VIDEO_1},
            {"doc": LB_VIDEO_3},
            {"doc": PRODUCT_GUIDE, "pages": [2, 8, 10]},
            {"doc": PRODUCT_PAGE},
            {"doc": BROCHURE_1, "pages": [8]},
        ],
    },
]

CURRICULUM_BY_KEY: dict[str, CurriculumItem] = {item["key"]: item for item in CURRICULUM}


def curriculum_outline() -> list[dict[str, Any]]:
    """Tiered listing for the API (no briefs; those are generation-side)."""
    tiers: dict[int, dict[str, Any]] = {}
    for item in CURRICULUM:
        tier = tiers.setdefault(item["tier"], {"tier": item["tier"], "title": TIER_TITLES[item["tier"]], "items": []})
        tier["items"].append(
            {"key": item["key"], "kind": item["kind"], "length": item["length"], "prompt": item["prompt"]}
        )
    return [tiers[t] for t in sorted(tiers)]
