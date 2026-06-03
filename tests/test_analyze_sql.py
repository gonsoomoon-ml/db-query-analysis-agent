from agents.db_query_analysis_agent.tools import analyze_sql_with_llm as mod


class _FakeAnalyzer:
    def __init__(self, text):
        self._text = text

    def __call__(self, _prompt):
        return self._text


def test_parses_json(monkeypatch):
    payload = ('{"index_efficiency":"idx ok","service_impact":"low",'
               '"optimizations":["add index"],"analysis":"fine"}')
    monkeypatch.setattr(mod, "_build_analyzer", lambda: _FakeAnalyzer(payload))
    out = mod.run_analysis("SELECT 1", "[]", "")
    assert out["index_efficiency"] == "idx ok"
    assert out["optimizations"] == ["add index"]


def test_non_json_falls_back_to_analysis(monkeypatch):
    monkeypatch.setattr(mod, "_build_analyzer", lambda: _FakeAnalyzer("그냥 텍스트 분석"))
    out = mod.run_analysis("SELECT 1", "[]", "")
    assert out["analysis"] == "그냥 텍스트 분석"
    assert out["optimizations"] == []


def test_exception_returns_error(monkeypatch):
    def _boom():
        raise RuntimeError("bedrock down")
    monkeypatch.setattr(mod, "_build_analyzer", _boom)
    out = mod.run_analysis("SELECT 1", "[]", "")
    assert "error" in out and out["analysis"] == ""
