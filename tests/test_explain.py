from agents.db_query_analysis_agent.tools.analyze_sql_with_llm import run_explain
from data.seed.build_sqlite import build_sample_db


def setup_module(_):
    build_sample_db()  # canonical sample.db


def test_explain_valid_query_returns_plan():
    plan = run_explain("SELECT * FROM orders WHERE user_id = 1")
    assert plan is not None
    assert "orders" in plan.lower()


def test_explain_indexed_lookup_mentions_search_or_index():
    plan = run_explain("SELECT id FROM orders WHERE user_id = 1")
    assert plan is not None
    assert ("index" in plan.lower()) or ("search" in plan.lower())


def test_explain_unknown_table_returns_none():
    assert run_explain("SELECT * FROM ghost_table_xyz") is None


def test_explain_garbage_returns_none():
    assert run_explain("this is not sql ;;;") is None
