from bizharness.engine import _reasoning_model, model_settings_for_effort
from bizharness.profile import coerce_effort


def test_openai_chat_spec_uses_responses_api(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from pydantic_ai.models.openai import OpenAIResponsesModel

    model, settings = _reasoning_model("openai-chat:gpt-5.6-luna")
    assert isinstance(model, OpenAIResponsesModel)
    assert settings is not None
    assert settings["openai_reasoning_summary"] == "detailed"
    assert settings["openai_reasoning_effort"] == "medium"


def test_openai_spec_honours_requested_effort(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _, settings = _reasoning_model("openai:gpt-5.6-luna", effort="high")
    assert settings["openai_reasoning_effort"] == "high"


def test_test_model_passthrough():
    model, settings = _reasoning_model("test")
    assert model == "test"
    assert settings is None


def test_model_settings_for_effort_pins_openai_and_unified():
    assert model_settings_for_effort("high") == {
        "openai_reasoning_effort": "high",
        "thinking": "high",
    }
    assert model_settings_for_effort("nope")["openai_reasoning_effort"] == "medium"


def test_coerce_effort_defaults_to_medium():
    assert coerce_effort(None) == "medium"
    assert coerce_effort("HIGH") == "high"
    assert coerce_effort("xhigh") == "xhigh"
    assert coerce_effort("minimal") == "medium"
