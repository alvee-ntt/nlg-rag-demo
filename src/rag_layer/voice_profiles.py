"""Vocalization configuration: which voice and speech pattern the AI customer uses.

Two profiles exist for every Gender x Age Range combination (see voice_profiles.json).
Each carries its own credential env-var names, so one pattern can be pointed at a
different Speech resource from another without touching code.

    from src.rag_layer.voice_profiles import load_registry
    reg = load_registry()
    profile = reg.select(gender="female", age=61)        # -> female_senior_warm
    creds = reg.credentials(profile)                     # resolves env vars
    ssml = profile.to_ssml("Hello there.")

Validate against the live Speech resource (checks the voices exist, that no profile
sets a style its voice will silently ignore, and that every credential env var
resolves):

    python -m src.rag_layer.voice_profiles --check
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

REGISTRY_PATH = Path(__file__).resolve().parent / "roleplay_data" / "voice_profiles.json"


@dataclass(frozen=True)
class AgeRange:
    """Half-open [min_age, max_age): age 40 is 'mid', not 'young'."""

    id: str
    label: str
    min_age: int
    max_age: int

    def contains(self, age: int) -> bool:
        return self.min_age <= age < self.max_age


@dataclass(frozen=True)
class Credentials:
    """Env-var names, never values, so secrets stay in .env."""

    key_env: str
    region_env: str
    endpoint_env: str


@dataclass(frozen=True)
class ResolvedCredentials:
    key: str
    region: str
    endpoint: str
    key_env: str
    missing: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.missing


@dataclass(frozen=True)
class VoiceProfile:
    id: str
    label: str
    gender: str
    age_range: str
    voice_name: str
    credentials: Credentials
    output_format: str
    prosody: dict[str, str] = field(default_factory=dict)
    # DragonHD voices advertise no styles and silently ignore mstts:express-as, so a
    # style on one of those would look configured and do nothing. Recorded per profile
    # so validation can catch that rather than leaving it to be noticed by ear.
    supports_styles: bool = False
    style: str | None = None
    style_degree: float | None = None
    role: str | None = None
    notes: str = ""

    @property
    def effective_style(self) -> str | None:
        """The style that will actually be applied - None if the voice ignores styles."""
        return self.style if (self.style and self.supports_styles) else None

    def to_ssml(self, text: str, lang: str = "en-US") -> str:
        """Wrap text in the SSML this profile describes.

        Prosody is applied for every voice tier; express-as only where the voice
        genuinely supports it.
        """
        inner = escape(text)
        rate = self.prosody.get("rate")
        pitch = self.prosody.get("pitch")
        volume = self.prosody.get("volume")
        attrs = []
        if rate and rate not in {"0%", "default"}:
            attrs.append(f'rate="{rate}"')
        if pitch and pitch not in {"0%", "default"}:
            attrs.append(f'pitch="{pitch}"')
        if volume and volume != "default":
            attrs.append(f'volume="{volume}"')
        if attrs:
            inner = f"<prosody {' '.join(attrs)}>{inner}</prosody>"
        style = self.effective_style
        if style:
            bits = [f'style="{style}"']
            if self.style_degree is not None:
                bits.append(f'styledegree="{self.style_degree}"')
            if self.role:
                bits.append(f'role="{self.role}"')
            inner = f"<mstts:express-as {' '.join(bits)}>{inner}</mstts:express-as>"
        return (
            '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            'xmlns:mstts="http://www.w3.org/2001/mstts" '
            f'xml:lang="{lang}"><voice name="{self.voice_name}">{inner}</voice></speak>'
        )

    def as_public_dict(self) -> dict[str, Any]:
        """Safe to hand to the browser: describes the pattern, names no credentials."""
        return {
            "id": self.id,
            "label": self.label,
            "gender": self.gender,
            "age_range": self.age_range,
            "voice_name": self.voice_name,
            "prosody": dict(self.prosody),
            "style": self.effective_style,
            "style_degree": self.style_degree if self.effective_style else None,
            "supports_styles": self.supports_styles,
            "output_format": self.output_format,
            "notes": self.notes,
        }


@dataclass
class VoiceRegistry:
    version: int
    age_ranges: list[AgeRange]
    profiles: list[VoiceProfile]

    def range_for_age(self, age: int | None) -> AgeRange:
        for rng in self.age_ranges:
            if age is not None and rng.contains(int(age)):
                return rng
        # Unknown or out-of-band age falls in the middle band rather than failing: a
        # missing age must never leave the customer voiceless.
        return next((r for r in self.age_ranges if r.id == "mid"), self.age_ranges[0])

    def by_id(self, profile_id: str) -> VoiceProfile | None:
        return next((p for p in self.profiles if p.id == profile_id), None)

    def options(self, gender: str, age: int | None) -> list[VoiceProfile]:
        """Every profile for a Gender x Age Range combination, in registry order."""
        rng = self.range_for_age(age)
        g = (gender or "").strip().lower()
        matches = [p for p in self.profiles if p.gender == g and p.age_range == rng.id]
        if matches:
            return matches
        # Unknown gender: fall back to the age band alone so a voice is still chosen.
        return [p for p in self.profiles if p.age_range == rng.id]

    def select(
        self, gender: str, age: int | None, variant: int = 0
    ) -> VoiceProfile | None:
        """Pick one option for a combination. `variant` indexes into the options, so
        the same persona can be replayed with a different speech pattern."""
        opts = self.options(gender, age)
        if not opts:
            return self.profiles[0] if self.profiles else None
        return opts[variant % len(opts)]

    def credentials(self, profile: VoiceProfile) -> ResolvedCredentials:
        creds = profile.credentials
        key = os.getenv(creds.key_env, "")
        region = os.getenv(creds.region_env, "")
        endpoint = os.getenv(creds.endpoint_env, "")
        missing = tuple(
            name
            for name, value in (
                (creds.key_env, key),
                (creds.region_env, region),
            )
            if not value
        )
        return ResolvedCredentials(
            key=key, region=region, endpoint=endpoint,
            key_env=creds.key_env, missing=missing,
        )

    def grid(self) -> dict[str, dict[str, list[str]]]:
        """Coverage view: age range -> gender -> profile ids."""
        out: dict[str, dict[str, list[str]]] = {}
        for rng in self.age_ranges:
            out[rng.label] = {}
            for gender in sorted({p.gender for p in self.profiles}):
                out[rng.label][gender] = [
                    p.id for p in self.profiles
                    if p.age_range == rng.id and p.gender == gender
                ]
        return out


def load_registry(path: Path | None = None) -> VoiceRegistry:
    raw = json.loads((path or REGISTRY_PATH).read_text(encoding="utf-8"))
    defaults = raw.get("defaults") or {}
    default_creds = defaults.get("credentials") or {}
    default_prosody = defaults.get("prosody") or {}

    ranges = [
        AgeRange(id=r["id"], label=r["label"], min_age=int(r["min_age"]),
                 max_age=int(r["max_age"]))
        for r in raw["age_ranges"]
    ]
    profiles = []
    for p in raw["profiles"]:
        creds = {**default_creds, **(p.get("credentials") or {})}
        profiles.append(
            VoiceProfile(
                id=p["id"],
                label=p.get("label", p["id"]),
                gender=str(p["gender"]).lower(),
                age_range=p["age_range"],
                voice_name=p["voice_name"],
                credentials=Credentials(
                    key_env=creds["key_env"],
                    region_env=creds["region_env"],
                    endpoint_env=creds["endpoint_env"],
                ),
                output_format=p.get("output_format", defaults.get("output_format", "")),
                prosody={**default_prosody, **(p.get("prosody") or {})},
                supports_styles=bool(p.get("supports_styles", False)),
                style=p.get("style", defaults.get("style")),
                style_degree=p.get("style_degree", defaults.get("style_degree")),
                role=p.get("role", defaults.get("role")),
                notes=p.get("notes", ""),
            )
        )
    return VoiceRegistry(version=int(raw.get("version", 1)), age_ranges=ranges,
                         profiles=profiles)


# ------------------------------- validation ---------------------------------

def validate(
    registry: VoiceRegistry,
    live_voices: dict[str, Any] | None,
    check_credentials: bool = False,
) -> list[str]:
    """Return a list of problems. Empty means the registry is internally consistent
    and (if live_voices was supplied) matches what the Speech resource offers.

    `check_credentials` is opt-in because credential presence is a property of the
    environment, not of the registry. A CI job validating the registry before its
    deploy secrets are in scope would otherwise fail on every profile - which is
    exactly what happened the first time this ran in Actions.
    """
    problems: list[str] = []
    seen: set[str] = set()
    range_ids = {r.id for r in registry.age_ranges}

    for p in registry.profiles:
        if p.id in seen:
            problems.append(f"{p.id}: duplicate profile id")
        seen.add(p.id)
        if p.age_range not in range_ids:
            problems.append(f"{p.id}: unknown age_range {p.age_range!r}")
        if p.style and not p.supports_styles:
            problems.append(
                f"{p.id}: sets style={p.style!r} but supports_styles is false - the "
                "voice would silently ignore it"
            )
        if live_voices is not None:
            info = live_voices.get(p.voice_name)
            if info is None:
                problems.append(f"{p.id}: voice {p.voice_name!r} not offered by the resource")
            else:
                advertised = info.get("StyleList") or []
                if p.supports_styles and not advertised:
                    problems.append(
                        f"{p.id}: supports_styles is true but {p.voice_name} advertises "
                        "no styles"
                    )
                if not p.supports_styles and advertised:
                    problems.append(
                        f"{p.id}: {p.voice_name} does advertise styles - "
                        "supports_styles could be true"
                    )
                if p.style and advertised and p.style not in advertised:
                    problems.append(
                        f"{p.id}: style {p.style!r} not in {p.voice_name} styles "
                        f"({', '.join(advertised[:6])}...)"
                    )
                if info.get("Gender", "").lower() != p.gender:
                    problems.append(
                        f"{p.id}: gender {p.gender!r} but {p.voice_name} is "
                        f"{info.get('Gender')!r}"
                    )
        if check_credentials:
            creds = registry.credentials(p)
            if creds.missing:
                problems.append(f"{p.id}: unset env var(s) {', '.join(creds.missing)}")

    # Every combination should offer at least two patterns to choose between.
    for label, genders in registry.grid().items():
        for gender, ids in genders.items():
            if len(ids) < 2:
                problems.append(
                    f"coverage: {gender} / {label} has {len(ids)} profile(s), expected 2+"
                )
    return problems


def _fetch_live_voices() -> dict[str, Any] | None:
    """Ask the Speech resource what it actually offers. None if unreachable."""
    try:
        import requests
        import urllib3
        from .config import load_settings

        settings = load_settings()
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        region = settings.azure_speech_region
        response = requests.get(
            f"https://{region}.tts.speech.microsoft.com/cognitiveservices/voices/list",
            headers={"Ocp-Apim-Subscription-Key": settings.azure_speech_key},
            timeout=(10, 60),
            verify=settings.azure_storage_verify_ssl,
        )
        response.raise_for_status()
        return {v["ShortName"]: v for v in response.json()}
    except Exception as exc:  # noqa: BLE001 - offline validation is still useful
        print(f"(could not reach the Speech resource: {type(exc).__name__}: {exc})")
        return None


def main() -> None:
    import sys

    from .config import load_settings

    load_settings()  # populate os.environ from .env so credential checks are real
    registry = load_registry()
    args = set(sys.argv[1:])

    print(f"voice_profiles.json v{registry.version}: {len(registry.profiles)} profiles\n")
    print("COVERAGE (Gender x Age Range)")
    for label, genders in registry.grid().items():
        for gender, ids in genders.items():
            print(f"  {gender:<7} {label:<7} {len(ids)}  {', '.join(ids)}")

    print("\nPROFILES")
    for p in registry.profiles:
        creds = registry.credentials(p)
        style = p.effective_style or "-"
        pros = " ".join(f"{k}={v}" for k, v in p.prosody.items() if v not in {"0%", "default"})
        print(f"  {p.id:<26} {p.voice_name:<34} style={style:<12} {pros or 'no prosody change':<28} "
              f"key={p.credentials.key_env}{'' if creds.ok else ' [MISSING]'}")

    if "--check" in args:
        live = _fetch_live_voices()
        # This is the operator-facing tool, run where .env exists, so it does check
        # that each profile's credential env vars resolve.
        problems = validate(registry, live, check_credentials=True)
        print(f"\nVALIDATION ({'live' if live else 'offline'})")
        if problems:
            for problem in problems:
                print(f"  ! {problem}")
            sys.exit(1)
        print("  all profiles valid")

    if "--ssml" in args:
        sample = "I would need to know what this costs before we go any further."
        print("\nSAMPLE SSML")
        for p in registry.profiles[:2]:
            print(f"  {p.id}:\n    {p.to_ssml(sample)}")


if __name__ == "__main__":
    main()
