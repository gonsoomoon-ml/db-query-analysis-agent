from data.mock.table_meta import TABLE_META


def test_expected_tables_present():
    assert set(TABLE_META) == {"users", "orders", "products", "order_items", "audit_log"}


def test_each_table_has_required_keys():
    for name, meta in TABLE_META.items():
        assert meta["name"] == name
        assert isinstance(meta["columns"], list) and meta["columns"]
        assert all("name" in c and "type" in c for c in meta["columns"])
        assert isinstance(meta["indexes"], list)
        assert isinstance(meta["row_count"], int)


def test_large_tables_exceed_threshold():
    assert TABLE_META["orders"]["row_count"] > 1_000_000
    assert TABLE_META["order_items"]["row_count"] > 1_000_000
    assert TABLE_META["audit_log"]["row_count"] > 1_000_000
    assert TABLE_META["users"]["row_count"] < 1_000_000
