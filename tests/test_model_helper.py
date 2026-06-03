from agents.db_query_analysis_agent.shared.model import build_bedrock_model


def test_build_uses_env_overrides(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-2")
    monkeypatch.setenv("DBQUERY_MODEL_ID", "test.model.id")
    monkeypatch.setenv("DBQUERY_TEMPERATURE", "0.0")
    monkeypatch.setenv("DBQUERY_MAX_TOKENS", "1234")
    model = build_bedrock_model(
        "DBQUERY_MODEL_ID", "default.model", "DBQUERY_TEMPERATURE", 0.1,
        "DBQUERY_MAX_TOKENS", 4096,
    )
    assert model is not None
    cfg = model.get_config()
    assert cfg["model_id"] == "test.model.id"


def test_build_uses_defaults(monkeypatch):
    monkeypatch.delenv("ANALYZE_MODEL_ID", raising=False)
    model = build_bedrock_model(
        "ANALYZE_MODEL_ID", "default.analyze.model", "ANALYZE_TEMPERATURE", 0.1,
        "ANALYZE_MAX_TOKENS", 2048, cache_tools=False,
    )
    assert model.get_config()["model_id"] == "default.analyze.model"
