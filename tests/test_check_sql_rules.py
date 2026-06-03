from agents.db_query_analysis_agent.tools.check_sql_rules import check_rules_core


def rules(sql: str) -> set[str]:
    return {v["rule"] for v in check_rules_core(sql)["violations"]}


def test_delete_without_where():
    assert "DELETE_WITHOUT_WHERE" in rules("DELETE FROM orders")


def test_delete_with_where_ok():
    assert "DELETE_WITHOUT_WHERE" not in rules("DELETE FROM orders WHERE id = 1")


def test_update_without_where():
    assert "UPDATE_WITHOUT_WHERE" in rules("UPDATE users SET active = 0")


def test_update_with_where_ok():
    assert "UPDATE_WITHOUT_WHERE" not in rules("UPDATE users SET active = 0 WHERE id = 1")


def test_drop():
    assert "DROP" in rules("DROP TABLE users")


def test_truncate():
    assert "TRUNCATE" in rules("TRUNCATE TABLE audit_log")


def test_select_star():
    assert "SELECT_STAR" in rules("SELECT * FROM users")


def test_select_columns_ok():
    assert "SELECT_STAR" not in rules("SELECT id, name FROM users")


def test_like_leading_wildcard():
    assert "LIKE_LEADING_WILDCARD" in rules("SELECT id FROM users WHERE name LIKE '%kim'")


def test_like_trailing_ok():
    assert "LIKE_LEADING_WILDCARD" not in rules("SELECT id FROM users WHERE name LIKE 'kim%'")


def test_where_in_comment_does_not_suppress():
    assert "DELETE_WITHOUT_WHERE" in rules("DELETE FROM orders -- WHERE id = 1")


def test_clean_select_no_violations():
    assert rules("SELECT id, name FROM users WHERE id = 1") == set()


def test_return_shape():
    # check_rules_core: @tool 래퍼와 Lambda 핸들러가 공유하는 순수 코어
    out = check_rules_core("DROP TABLE x")
    assert "violations" in out and "checked_rules" in out
    assert out["violations"][0]["severity"] == "critical"


def test_multistatement_delete_not_suppressed_by_later_where():
    assert "DELETE_WITHOUT_WHERE" in rules("DELETE FROM a; SELECT * FROM b WHERE id = 1")


def test_where_inside_string_literal_not_counted():
    assert "UPDATE_WITHOUT_WHERE" in rules("UPDATE t SET note = 'set WHERE later'")
