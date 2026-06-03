"""Lambda 핸들러 단위 테스트 — 코어 함수를 monkeypatch 하여 실제 AWS/LLM 호출 없이 검증.

각 핸들러:
- 올바른 인자로 코어를 호출하는지
- 코어 반환값을 그대로 반환하는지
- 코어에서 예외 발생 시 {"error": ...} 를 반환하는지

context.client_context.custom["bedrockAgentCoreToolName"] 패턴도 헬퍼(_tool_name) 포함.

핸들러 파일은 infra/cognito-gateway/lambda/<name>/handler.py 에 있으나
디렉터리명에 하이픈/예약어가 포함되어 Python 패키지로 직접 임포트 불가.
importlib.util.spec_from_file_location 으로 파일 경로 직접 로드.
"""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

_LAMBDA_ROOT = Path(__file__).resolve().parents[1] / "infra" / "cognito-gateway" / "lambda"


def _load_handler(name: str):
    """infra/cognito-gateway/lambda/<name>/handler.py 를 모듈로 로드."""
    path = _LAMBDA_ROOT / name / "handler.py"
    mod_name = f"_lambda_handler_{name}"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fake context 헬퍼
# ---------------------------------------------------------------------------

def _make_context(tool_name: str):
    """bedrockAgentCoreToolName 이 설정된 가짜 Lambda context."""
    custom = {"bedrockAgentCoreToolName": tool_name}
    cc = types.SimpleNamespace(custom=custom)
    return types.SimpleNamespace(client_context=cc)


# ---------------------------------------------------------------------------
# check_sql_rules Lambda handler
# ---------------------------------------------------------------------------

class TestCheckSqlRulesHandler:
    @pytest.fixture(autouse=True)
    def mod(self):
        return _load_handler("check_sql_rules")

    def test_calls_core_with_sql(self, mod, monkeypatch):
        captured = {}

        def fake_core(sql):
            captured["sql"] = sql
            return {"violations": [], "checked_rules": []}

        monkeypatch.setattr(mod, "check_rules_core", fake_core)
        ctx = _make_context("check-sql-rules___check_sql_rules")
        result = mod.handler({"sql": "SELECT * FROM users"}, ctx)
        assert captured["sql"] == "SELECT * FROM users"
        assert result == {"violations": [], "checked_rules": []}

    def test_returns_core_dict(self, mod, monkeypatch):
        expected = {"violations": [{"rule": "DROP"}], "checked_rules": ["DROP"]}
        monkeypatch.setattr(mod, "check_rules_core", lambda sql: expected)
        ctx = _make_context("target___check_sql_rules")
        assert mod.handler({"sql": "DROP TABLE x"}, ctx) == expected

    def test_empty_event_uses_empty_sql(self, mod, monkeypatch):
        captured = {}
        monkeypatch.setattr(mod, "check_rules_core", lambda sql: captured.update({"sql": sql}) or {})
        mod.handler({}, _make_context(""))
        assert captured["sql"] == ""

    def test_none_event_uses_empty_sql(self, mod, monkeypatch):
        captured = {}
        monkeypatch.setattr(mod, "check_rules_core", lambda sql: captured.update({"sql": sql}) or {})
        mod.handler(None, _make_context(""))
        assert captured["sql"] == ""

    def test_exception_returns_error_dict(self, mod, monkeypatch):
        def boom(sql):
            raise RuntimeError("db connection failed")

        monkeypatch.setattr(mod, "check_rules_core", boom)
        ctx = _make_context("target___check_sql_rules")
        result = mod.handler({"sql": "SELECT 1"}, ctx)
        assert "error" in result
        assert "db connection failed" in result["error"]


# ---------------------------------------------------------------------------
# get_table_meta Lambda handler
# ---------------------------------------------------------------------------

class TestGetTableMetaHandler:
    @pytest.fixture(autouse=True)
    def mod(self):
        return _load_handler("get_table_meta")

    def test_calls_core_with_sql(self, mod, monkeypatch):
        captured = {}

        def fake_core(sql):
            captured["sql"] = sql
            return {"tables": [], "backend": "mock", "large_table_threshold": 1000000}

        monkeypatch.setattr(mod, "table_meta_core", fake_core)
        ctx = _make_context("get-table-meta___get_table_meta")
        result = mod.handler({"sql": "SELECT * FROM orders"}, ctx)
        assert captured["sql"] == "SELECT * FROM orders"
        assert result["backend"] == "mock"

    def test_returns_core_dict(self, mod, monkeypatch):
        expected = {"tables": [{"name": "orders", "found": True}], "backend": "mock", "large_table_threshold": 1000000}
        monkeypatch.setattr(mod, "table_meta_core", lambda sql: expected)
        ctx = _make_context("target___get_table_meta")
        assert mod.handler({"sql": "SELECT * FROM orders"}, ctx) == expected

    def test_empty_event_uses_empty_sql(self, mod, monkeypatch):
        captured = {}
        monkeypatch.setattr(mod, "table_meta_core", lambda sql: captured.update({"sql": sql}) or {})
        mod.handler({}, _make_context(""))
        assert captured["sql"] == ""

    def test_none_event_uses_empty_sql(self, mod, monkeypatch):
        captured = {}
        monkeypatch.setattr(mod, "table_meta_core", lambda sql: captured.update({"sql": sql}) or {})
        mod.handler(None, _make_context(""))
        assert captured["sql"] == ""

    def test_exception_returns_error_dict(self, mod, monkeypatch):
        def boom(sql):
            raise ValueError("backend unavailable")

        monkeypatch.setattr(mod, "table_meta_core", boom)
        ctx = _make_context("target___get_table_meta")
        result = mod.handler({"sql": "SELECT 1"}, ctx)
        assert "error" in result
        assert "backend unavailable" in result["error"]


# ---------------------------------------------------------------------------
# analyze_sql_with_llm Lambda handler
# ---------------------------------------------------------------------------

class TestAnalyzeSqlWithLlmHandler:
    @pytest.fixture(autouse=True)
    def mod(self):
        return _load_handler("analyze_sql_with_llm")

    def test_calls_run_analysis_with_all_params(self, mod, monkeypatch):
        captured = {}

        async def fake_run(sql, violations_json, meta_summary):
            captured.update({"sql": sql, "violations_json": violations_json, "meta_summary": meta_summary})
            return {"index_efficiency": "ok", "analysis": "fine"}

        monkeypatch.setattr(mod, "run_analysis", fake_run)
        ctx = _make_context("analyze___analyze_sql_with_llm")
        event = {"sql": "SELECT * FROM t", "violations_json": "[]", "meta_summary": "rows=100"}
        result = mod.handler(event, ctx)
        assert captured["sql"] == "SELECT * FROM t"
        assert captured["violations_json"] == "[]"
        assert captured["meta_summary"] == "rows=100"
        assert result == {"index_efficiency": "ok", "analysis": "fine"}

    def test_returns_core_dict(self, mod, monkeypatch):
        expected = {"index_efficiency": "low", "service_impact": "high", "optimizations": [], "analysis": "bad"}

        async def fake_run(sql, violations_json, meta_summary):
            return expected

        monkeypatch.setattr(mod, "run_analysis", fake_run)
        ctx = _make_context("target___analyze_sql_with_llm")
        result = mod.handler({"sql": "DROP TABLE x", "violations_json": "[DROP]", "meta_summary": ""}, ctx)
        assert result == expected

    def test_missing_optional_meta_summary(self, mod, monkeypatch):
        captured = {}

        async def fake_run(sql, violations_json, meta_summary):
            captured["meta_summary"] = meta_summary
            return {}

        monkeypatch.setattr(mod, "run_analysis", fake_run)
        mod.handler({"sql": "SELECT 1", "violations_json": "[]"}, _make_context(""))
        assert captured["meta_summary"] == ""

    def test_none_event_defaults(self, mod, monkeypatch):
        captured = {}

        async def fake_run(sql, violations_json, meta_summary):
            captured.update({"sql": sql, "violations_json": violations_json, "meta_summary": meta_summary})
            return {}

        monkeypatch.setattr(mod, "run_analysis", fake_run)
        mod.handler(None, _make_context(""))
        assert captured == {"sql": "", "violations_json": "", "meta_summary": ""}

    def test_exception_returns_error_dict(self, mod, monkeypatch):
        async def boom(sql, violations_json, meta_summary):
            raise ConnectionError("bedrock timeout")

        monkeypatch.setattr(mod, "run_analysis", boom)
        ctx = _make_context("target___analyze_sql_with_llm")
        result = mod.handler({"sql": "SELECT 1", "violations_json": "[]", "meta_summary": ""}, ctx)
        assert "error" in result
        assert "bedrock timeout" in result["error"]
