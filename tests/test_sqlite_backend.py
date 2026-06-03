import pytest
from agents.db_query_analysis_agent.meta import sqlite_backend, mock_backend
from data.mock.table_meta import TABLE_META
from data.seed.build_sqlite import build_sample_db


@pytest.fixture(autouse=True)
def _fresh_db():
    build_sample_db()  # canonical sample.db 재생성
    yield


def _norm_idx(idx_list):
    return {i["name"]: (tuple(i["columns"]), bool(i["unique"])) for i in idx_list}


@pytest.mark.parametrize("table", list(TABLE_META))
def test_sqlite_mock_parity(table):
    s = sqlite_backend.lookup(table)
    m = mock_backend.lookup(table)
    assert s is not None
    assert s["name"] == m["name"]
    assert s["columns"] == m["columns"]
    assert s["row_count"] == m["row_count"]
    assert _norm_idx(s["indexes"]) == _norm_idx(m["indexes"])


def test_sqlite_unknown_table_returns_none():
    assert sqlite_backend.lookup("ghost") is None
