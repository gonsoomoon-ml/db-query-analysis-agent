import pytest
from agents.db_query_analysis_agent.tools.get_table_meta import (
    extract_table_names, collect_table_meta,
)


@pytest.fixture(autouse=True)
def _mock_backend(monkeypatch):
    monkeypatch.setenv("META_BACKEND", "mock")
    monkeypatch.setenv("LARGE_TABLE_THRESHOLD", "1000000")


def test_extract_simple():
    assert extract_table_names("SELECT * FROM orders WHERE id = 1") == ["orders"]


def test_extract_join_with_alias():
    sql = "SELECT * FROM orders o JOIN order_items oi ON o.id = oi.order_id"
    assert extract_table_names(sql) == ["orders", "order_items"]


def test_extract_update():
    assert extract_table_names("UPDATE users SET name = 'x' WHERE id = 1") == ["users"]


def test_extract_insert_into():
    assert extract_table_names("INSERT INTO products (sku) VALUES ('a')") == ["products"]


def test_extract_schema_qualified():
    assert extract_table_names("SELECT * FROM shop.orders") == ["orders"]


def test_large_table_flagged():
    t = collect_table_meta("SELECT * FROM orders")["tables"][0]
    assert t["found"] is True and t["large_table"] is True


def test_small_table_not_flagged():
    t = collect_table_meta("SELECT id FROM users WHERE id = 1")["tables"][0]
    assert t["found"] is True and t["large_table"] is False


def test_unknown_table():
    t = collect_table_meta("SELECT * FROM ghost")["tables"][0]
    assert t == {"name": "ghost", "found": False}


def test_backend_reported():
    out = collect_table_meta("SELECT * FROM users")
    assert out["backend"] == "mock"
    assert out["large_table_threshold"] == 1_000_000
