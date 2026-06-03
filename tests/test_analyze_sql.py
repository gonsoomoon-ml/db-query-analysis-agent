import asyncio
import pytest
from agents.db_query_analysis_agent.tools import analyze_sql_with_llm as mod


@pytest.fixture(autouse=True)
def _no_explain(monkeypatch):
    # 기존 analyze 테스트는 sqlite와 격리 — run_explain 기본 비활성
    monkeypatch.setattr(mod, "run_explain", lambda _sql: None)


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


def test_plan_injected_into_prompt(monkeypatch):
    captured = {}

    async def _capture(user_msg):
        captured["msg"] = user_msg
        return '{"index_efficiency":"i","service_impact":"s","optimizations":[],"analysis":"a"}'

    monkeypatch.setattr(mod, "run_explain", lambda _sql: "SEARCH orders USING INDEX idx_orders_user_id")
    monkeypatch.setattr(mod, "_invoke_model", _capture)
    out = asyncio.run(mod.run_analysis("SELECT * FROM orders WHERE user_id=1", "[]", ""))
    assert out["index_efficiency"] == "i"
    assert "EXPLAIN QUERY PLAN" in captured["msg"]
    assert "idx_orders_user_id" in captured["msg"]
