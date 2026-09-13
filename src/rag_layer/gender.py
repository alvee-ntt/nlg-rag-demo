"""Infer a persona's gender for voice selection.

Used when a persona does not state it: generated personas whose description never
mentioned it, and older ones created before the field existed. Getting it wrong means
a male prospect speaks with a female voice, which is immediately obvious in a demo.

Context beats the name, because a name can be ambiguous or unfamiliar while "she
mentioned her husband" is not. Order of evidence:

    1. an explicit gender already on the persona
    2. pronouns and relationship words in the description or summary
    3. a first-name lookup
    4. a coin flip, reported as such

infer_gender returns the reason as well as the answer, so a wrong guess can be traced
rather than silently shaping the voice.
"""

from __future__ import annotations

import random
import re

# Common first names, biased toward the US life-insurance prospect this demo models,
# with a spread of names the built-in personas already use. Deliberately not
# exhaustive: context and the explicit field carry most cases, and the reason string
# makes a name-based guess visible.
FEMALE_NAMES = {
    "ava", "aria", "emma", "olivia", "sophia", "isabella", "mia", "charlotte", "amelia",
    "harper", "evelyn", "abigail", "emily", "elizabeth", "sofia", "ella", "grace",
    "chloe", "victoria", "riley", "aubrey", "zoey", "hannah", "lily", "nora", "layla",
    "helen", "maria", "linda", "patricia", "barbara", "susan", "jessica", "sarah",
    "karen", "nancy", "betty", "margaret", "sandra", "ashley", "kimberly", "donna",
    "michelle", "carol", "amanda", "melissa", "deborah", "stephanie", "rebecca",
    "laura", "sharon", "cynthia", "kathleen", "amy", "angela", "shirley", "anna",
    "brenda", "pamela", "nicole", "ruth", "katherine", "virginia", "rachel", "diane",
    "priya", "aisha", "fatima", "mei", "yuki", "ingrid", "sofie", "elena", "carmen",
    "rosa", "lucia", "keisha", "tanya", "monica", "denise", "gloria", "teresa",
    "janet", "catherine", "christine", "marie", "janice", "kelly", "tammy", "joan",
}
MALE_NAMES = {
    "andrew", "anton", "kevin", "marcus", "frank", "james", "robert", "john",
    "michael", "david", "william", "richard", "joseph", "thomas", "charles",
    "christopher", "daniel", "matthew", "anthony", "mark", "donald", "steven", "paul",
    "brian", "george", "kenneth", "edward", "ronald", "timothy", "jason", "jeffrey",
    "ryan", "jacob", "gary", "nicholas", "eric", "jonathan", "stephen", "larry",
    "justin", "scott", "brandon", "benjamin", "samuel", "gregory", "alexander",
    "patrick", "jack", "dennis", "jerry", "tyler", "aaron", "jose", "adam", "henry",
    "nathan", "douglas", "zachary", "peter", "kyle", "walter", "ethan", "jeremy",
    "harold", "carl", "keith", "roger", "gerald", "arthur", "terry", "lawrence",
    "raj", "amir", "hassan", "wei", "hiroshi", "dmitri", "pedro", "carlos", "miguel",
    "darnell", "malik", "tony", "vincent", "louis", "russell", "philip", "johnny",
}
# Names that are genuinely common for both; never decide on these.
AMBIGUOUS_NAMES = {
    "alex", "jordan", "taylor", "casey", "riley", "morgan", "avery", "quinn", "jamie",
    "cameron", "reese", "rowan", "sage", "skyler", "parker", "charlie", "sam", "chris",
    "pat", "robin", "kai", "dana", "jesse", "drew", "blake", "harley", "shiloh",
}

# Signals about the SUBJECT. Spouse and parent words are deliberately absent: "wife"
# implies the subject is male, not female, and "supports her mother" describes a
# relative rather than the subject. Spouse words are handled separately below, and
# parent words are left out entirely because they are too often about someone else.
# "widow" is a female noun; "widowed" is not gendered, and \b keeps them apart.
_FEMALE_CONTEXT = (
    r"\bshe\b", r"\bher\b", r"\bhers\b", r"\bherself\b", r"\bwidow\b",
    r"\bmrs\.?\b", r"\bms\.?\b", r"\bstay-at-home mom\b", r"\bmaternity\b",
)
_MALE_CONTEXT = (
    r"\bhe\b", r"\bhim\b", r"\bhis\b", r"\bhimself\b", r"\bwidower\b",
    r"\bmr\.?\b", r"\bstay-at-home dad\b",
)


def _score(text: str, patterns: tuple[str, ...]) -> int:
    return sum(len(re.findall(p, text, re.IGNORECASE)) for p in patterns)


def infer_gender(name: str = "", *context: str) -> tuple[str, str]:
    """Return (gender, reason). Gender is always "female" or "male".

    A persona describing a wife implies a male subject and vice versa, so those words
    are counted for the opposite side - which is why "his wife stays home" resolves to
    male rather than cancelling out.
    """
    blob = " ".join(t for t in context if t)

    # A named spouse indicates the subject is the opposite gender: "his wife stays
    # home" is evidence for male, not female. Counted here and nowhere else, so there
    # is no double counting.
    spouse_male = len(re.findall(r"\bwife\b", blob, re.IGNORECASE))
    spouse_female = len(re.findall(r"\bhusband\b", blob, re.IGNORECASE))

    female = _score(blob, _FEMALE_CONTEXT) + spouse_female
    male = _score(blob, _MALE_CONTEXT) + spouse_male

    if female > male:
        return "female", f"context ({female} female vs {male} male signals)"
    if male > female:
        return "male", f"context ({male} male vs {female} female signals)"

    first = re.split(r"[\s,]+", (name or "").strip())[0].lower().strip(".")
    if first and first not in AMBIGUOUS_NAMES:
        if first in FEMALE_NAMES:
            return "female", f"first name {first!r}"
        if first in MALE_NAMES:
            return "male", f"first name {first!r}"
    if first in AMBIGUOUS_NAMES:
        return random.choice(["female", "male"]), (
            f"random - {first!r} is used for both and context gave no signal"
        )
    return random.choice(["female", "male"]), (
        "random - no usable name or context signal"
    )


def persona_gender(persona: dict) -> tuple[str, str]:
    """Gender for a persona, stated or inferred, with the reason."""
    stated = str(persona.get("gender") or "").strip().lower()
    if stated in {"female", "male"}:
        return stated, "stated on the persona"
    return infer_gender(
        str(persona.get("display_name") or ""),
        str(persona.get("source_notes") or ""),
        str(persona.get("summary") or ""),
        str((persona.get("fixed_facts") or {}).get("household") or ""),
    )
