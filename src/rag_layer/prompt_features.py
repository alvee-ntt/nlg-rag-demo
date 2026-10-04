from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .prompt_runtime import validate_prompt_template


@dataclass(frozen=True)
class PromptComponentDefinition:
    key: str
    required_placeholders: frozenset[str] = frozenset()
    allowed_placeholders: frozenset[str] = frozenset()

    def validate(self, template: str) -> set[str]:
        return validate_prompt_template(
            template,
            required=self.required_placeholders,
            allowed=self.allowed_placeholders,
        )


@dataclass(frozen=True)
class PromptFeatureDefinition:
    key: str
    name: str
    purpose: str
    required_components: tuple[str, ...]
    optional_components: tuple[str, ...] = ()
    mutually_exclusive_components: tuple[tuple[str, ...], ...] = ()

    @property
    def components(self) -> tuple[str, ...]:
        keys = [*self.required_components, *self.optional_components]
        for group in self.mutually_exclusive_components:
            keys.extend(group)
        return tuple(dict.fromkeys(keys))

    def validate_recipe(self, recipe: dict[str, int]) -> None:
        actual = set(recipe)
        required = set(self.required_components)
        allowed = set(self.components)
        missing = sorted(required - actual)
        unknown = sorted(actual - allowed)
        if missing or unknown:
            details = []
            if missing:
                details.append(f"missing {', '.join(missing)}")
            if unknown:
                details.append(f"unexpected {', '.join(unknown)}")
            raise ValueError(f"Invalid recipe for {self.key}: {'; '.join(details)}")
        for group in self.mutually_exclusive_components:
            selected = actual.intersection(group)
            if len(selected) != 1:
                raise ValueError(
                    f"Invalid recipe for {self.key}: choose exactly one of {', '.join(group)}"
                )
        invalid_versions = sorted(key for key, version in recipe.items() if int(version) < 1)
        if invalid_versions:
            raise ValueError(
                f"Invalid recipe for {self.key}: versions must be positive for "
                + ", ".join(invalid_versions)
            )


_COMPONENTS: dict[str, PromptComponentDefinition] = {
    "ask.navigator": PromptComponentDefinition(key="ask.navigator"),
    "ask.about_me": PromptComponentDefinition(
        key="ask.about_me",
        required_placeholders=frozenset({"about_me"}),
        allowed_placeholders=frozenset({"about_me"}),
    ),
    "ask.memories": PromptComponentDefinition(
        key="ask.memories",
        required_placeholders=frozenset({"memories"}),
        allowed_placeholders=frozenset({"memories"}),
    ),
    "ask.relevance": PromptComponentDefinition(
        key="ask.relevance",
        required_placeholders=frozenset({"history", "message"}),
        allowed_placeholders=frozenset({"history", "message"}),
    ),
    "ask.support_email": PromptComponentDefinition(
        key="ask.support_email",
        required_placeholders=frozenset({"history", "question", "reason_note", "source_context"}),
        allowed_placeholders=frozenset({"history", "question", "reason_note", "source_context"}),
    ),
}

_FEATURES: dict[str, PromptFeatureDefinition] = {
    "ask": PromptFeatureDefinition(
        key="ask",
        name="Ask",
        purpose="Answer a learner question through the hosted Foundry knowledge agent",
        required_components=("ask.navigator",),
        optional_components=("ask.about_me", "ask.memories"),
    ),
    "ask-relevance": PromptFeatureDefinition(
        key="ask-relevance",
        name="Ask relevance",
        purpose="Classify one Ask turn without invoking the Ask agent",
        required_components=("ask.relevance",),
    ),
    "ask-support-email": PromptFeatureDefinition(
        key="ask-support-email",
        name="Ask support email",
        purpose="Draft model output for an NLG Support handoff without sending it",
        required_components=("ask.support_email",),
    ),
}


def get_prompt_component(key: str) -> PromptComponentDefinition | None:
    return _COMPONENTS.get(key)


def get_prompt_feature(key: str) -> PromptFeatureDefinition | None:
    return _FEATURES.get(key)


def list_prompt_features() -> list[PromptFeatureDefinition]:
    return list(_FEATURES.values())


def features_using_component(component_key: str) -> list[str]:
    return [feature.key for feature in _FEATURES.values() if component_key in feature.components]


def validate_component_template(component_key: str, template: str) -> set[str]:
    component = get_prompt_component(component_key)
    # Definitions are added incrementally as features migrate. Existing unregistered prompt
    # definitions retain their current behavior until their feature enters the registry.
    return component.validate(template) if component else set()


def prompt_recipe(feature_key: str, prompts: Iterable[dict]) -> dict[str, int]:
    feature = get_prompt_feature(feature_key)
    if feature is None:
        raise ValueError(f"Unknown prompt feature: {feature_key}")
    recipe = {str(prompt["key"]): int(prompt["version"]) for prompt in prompts}
    feature.validate_recipe(recipe)
    return recipe
