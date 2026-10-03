import pytest

from src.rag_layer.learn import _llm_json


def test_llm_json_surfaces_json_mode_compatibility_error_without_downgrade():
    calls = []

    class Client:
        def post(self, path, payload, timeout):
            calls.append((path, dict(payload), timeout))
            raise RuntimeError("json_object is not supported by this deployment")

    settings = type("Settings", (), {"azure_openai_chat_deployment": "gpt-test"})()

    with pytest.raises(RuntimeError, match="json_object is not supported"):
        _llm_json(Client(), settings, "Return JSON")

    assert len(calls) == 1
    assert calls[0][0] == "/responses"
    assert calls[0][1]["text"] == {"format": {"type": "json_object"}}
