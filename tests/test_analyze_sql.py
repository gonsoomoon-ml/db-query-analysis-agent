import asyncio
from agents.db_query_analysis_agent.tools import analyze_sql_with_llm as mod


def _returning(text):
    async def _f(_user_msg):
        return text
    return _f


def test_parses_json(monkeypatch):
    payload = ('{"index_efficiency":"idx ok","service_impact":"low",'
               '"optimizations":["add index"],"analysis":"fine"}')
    monkeypatch.setattr(mod, "_invoke_model", _returning(payload))
    out = asyncio.run(mod.run_analysis("SELECT 1", "[]", ""))
    assert out["index_efficiency"] == "idx ok"
    assert out["optimizations"] == ["add index"]


def test_non_json_falls_back_to_analysis(monkeypatch):
    monkeypatch.setattr(mod, "_invoke_model", _returning("그냥 텍스트 분석"))
    out = asyncio.run(mod.run_analysis("SELECT 1", "[]", ""))
    assert out["analysis"] == "그냥 텍스트 분석"
    assert out["optimizations"] == []


def test_exception_returns_error(monkeypatch):
    async def _boom(_user_msg):
        raise RuntimeError("bedrock down")
    monkeypatch.setattr(mod, "_invoke_model", _boom)
    out = asyncio.run(mod.run_analysis("SELECT 1", "[]", ""))
    assert "error" in out and out["analysis"] == ""


def test_parses_fenced_json(monkeypatch):
    fenced = '```json\n{"index_efficiency":"ok","service_impact":"low","optimizations":[],"analysis":"a"}\n```'
    monkeypatch.setattr(mod, "_invoke_model", _returning(fenced))
    out = asyncio.run(mod.run_analysis("SELECT 1", "[]", ""))
    assert out["index_efficiency"] == "ok"
