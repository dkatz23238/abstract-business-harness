from bizharness.engine import _reasoning_model


def test_openai_chat_spec_uses_responses_api(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from pydantic_ai.models.openai import OpenAIResponsesModel

    model, settings = _reasoning_model("openai-chat:gpt-5.6-luna")
    assert isinstance(model, OpenAIResponsesModel)
    assert settings is not None


def test_test_model_passthrough():
    model, settings = _reasoning_model("test")
    assert model == "test"
    assert settings is None
