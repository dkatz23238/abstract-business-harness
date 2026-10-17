from abstract.engine import _reasoning_model, model_settings_for_effort, run_model
from abstract.profile import coerce_effort


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


def test_openai_base_url_defaults_to_openai(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    model, _ = _reasoning_model("openai:gpt-5.6-luna")
    assert model.base_url.rstrip("/") == "https://api.openai.com/v1"


def test_openai_base_url_honours_env(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1/")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    model, _ = _reasoning_model("openai:gpt-5.6-luna")
    assert model.base_url.rstrip("/") == "https://openrouter.ai/api/v1"


def test_anthropic_spec_gets_thinking_and_caching(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from pydantic_ai.models.anthropic import AnthropicModel

    model, settings = _reasoning_model("anthropic:claude-sonnet-5", effort="high")
    assert isinstance(model, AnthropicModel)
    assert model.model_name == "claude-sonnet-5"
    assert settings["thinking"] == "high"
    assert settings["max_tokens"] == 64_000
    assert settings["anthropic_cache"] is True
    assert settings["anthropic_cache_instructions"] is True
    assert settings["anthropic_cache_tool_definitions"] is True


def test_run_model_layers_effort_over_model_settings(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    model, settings = run_model("anthropic:claude-sonnet-5", "low")
    assert model.model_name == "claude-sonnet-5"
    assert settings["thinking"] == "low"
    assert settings["anthropic_cache"] is True
    model, settings = run_model("test", "xhigh")
    assert model == "test"
    assert settings == {"openai_reasoning_effort": "xhigh", "thinking": "xhigh"}


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
