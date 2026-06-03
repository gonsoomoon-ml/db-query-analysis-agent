import os
import pytest
from agents.db_query_analysis_agent.meta import lookup_table_meta, current_backend


def test_mock_known_table(monkeypatch):
    monkeypatch.setenv("META_BACKEND", "mock")
    meta = lookup_table_meta("orders")
    assert meta is not None
    assert meta["name"] == "orders"
    assert meta["row_count"] == 5_000_000
    assert any(c["name"] == "user_id" for c in meta["columns"])


def test_mock_case_insensitive(monkeypatch):
    monkeypatch.setenv("META_BACKEND", "mock")
    assert lookup_table_meta("ORDERS")["name"] == "orders"


def test_mock_unknown_returns_none(monkeypatch):
    monkeypatch.setenv("META_BACKEND", "mock")
    assert lookup_table_meta("ghost") is None


def test_current_backend_default(monkeypatch):
    monkeypatch.delenv("META_BACKEND", raising=False)
    assert current_backend() == "mock"


def test_redis_parity_if_available(monkeypatch):
    """redis 가동 시에만 — mock 과 동일 shape 검증. 아니면 skip."""
    try:
        import redis  # noqa: F401
    except ModuleNotFoundError:
        pytest.skip("redis 패키지 미설치")
    from data.seed import seed_redis
    try:
        seed_redis.main()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"redis 미가동: {e}")
    monkeypatch.setenv("META_BACKEND", "redis")
    assert lookup_table_meta("orders") == lookup_table_meta_mock("orders")


def lookup_table_meta_mock(name):
    from agents.db_query_analysis_agent.meta.mock_backend import lookup
    return lookup(name)
