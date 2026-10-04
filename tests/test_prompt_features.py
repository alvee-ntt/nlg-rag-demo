import pytest

from src.rag_layer.prompt_features import (
    PromptFeatureDefinition,
    features_using_component,
    get_prompt_feature,
    prompt_recipe,
    validate_component_template,
)
from src.rag_layer.prompt_runtime import PromptTemplateError


def test_ask_registry_accepts_only_the_effective_optional_recipe():
    recipe = prompt_recipe(
        "ask",
        [
            {"key": "ask.navigator", "version": 2},
            {"key": "ask.about_me", "version": 3},
        ],
    )
    assert recipe == {"ask.navigator": 2, "ask.about_me": 3}


def test_ask_registry_does_not_require_absent_optional_components():
    feature = get_prompt_feature("ask")
    assert feature is not None
    feature.validate_recipe({"ask.navigator": 1})


def test_component_contract_validates_existing_ask_placeholder_syntax():
    assert validate_component_template("ask.about_me", "About me: {{about_me}}") == {
        "about_me"
    }
    with pytest.raises(PromptTemplateError, match="missing required"):
        validate_component_template("ask.about_me", "About me follows")


def test_registry_reports_shared_component_usage():
    assert features_using_component("ask.navigator") == ["ask"]


def test_mutually_exclusive_component_group_requires_exactly_one():
    feature = PromptFeatureDefinition(
        key="reply",
        name="Reply",
        purpose="test",
        required_components=("shared",),
        mutually_exclusive_components=(("callback", "meeting"),),
    )
    feature.validate_recipe({"shared": 1, "callback": 1})
    with pytest.raises(ValueError, match="exactly one"):
        feature.validate_recipe({"shared": 1})
    with pytest.raises(ValueError, match="exactly one"):
        feature.validate_recipe({"shared": 1, "callback": 1, "meeting": 1})
