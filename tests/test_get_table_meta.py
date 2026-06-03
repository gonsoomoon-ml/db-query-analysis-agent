import pytest
from agents.db_query_analysis_agent.tools.get_table_meta import (
    collect_table_meta,
    extract_table_names,
    table_meta_core,
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


def test_extract_ignores_from_inside_string_literal():
    # 작은따옴표 문자열 안의 FROM 은 테이블로 추출되지 않음
    assert extract_table_names("SELECT * FROM orders WHERE note = 'sync FROM cache'") == ["orders"]


def test_extract_keeps_double_quoted_identifier():
    # 큰따옴표 식별자(테이블명)는 보존되어 추출됨
    assert extract_table_names('SELECT * FROM "orders"') == ["orders"]


# ── table_meta_core 직접 호출 테스트 ─────────────────────────────────────────
# Lambda 핸들러가 임포트하는 table_meta_core 가 @tool 래퍼와 동일하게 동작함을 확인.

def test_table_meta_core_known_table_returned():
    """알려진 테이블은 table_meta_core 에서도 found=True 로 반환된다."""
    out = table_meta_core("SELECT * FROM users")
    t = out["tables"][0]
    assert t["found"] is True
    assert t["name"] == "users"


def test_table_meta_core_large_table_flagged():
    """orders 는 large_table=True 로 플래그된다 (mock row_count > threshold)."""
    t = table_meta_core("SELECT * FROM orders")["tables"][0]
    assert t["large_table"] is True


def test_table_meta_core_unknown_table():
    """존재하지 않는 테이블은 found=False 만 포함된 dict 를 반환한다."""
    t = table_meta_core("SELECT * FROM ghost")["tables"][0]
    assert t == {"name": "ghost", "found": False}


def test_table_meta_core_matches_collect_table_meta():
    """table_meta_core 와 collect_table_meta 는 동일한 결과를 반환한다."""
    sql = "SELECT * FROM orders JOIN users ON orders.user_id = users.id"
    assert table_meta_core(sql) == collect_table_meta(sql)
