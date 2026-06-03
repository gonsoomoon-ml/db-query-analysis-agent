import sqlite3
from data.seed.build_sqlite import build_sample_db
from data.mock.table_meta import TABLE_META


def test_build_creates_tables_stats_and_indexes(tmp_path):
    db = tmp_path / "t.db"
    build_sample_db(db)
    con = sqlite3.connect(db)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert set(TABLE_META) <= tables
        assert "table_stats" in tables

        stats = dict(con.execute("SELECT table_name, row_count FROM table_stats"))
        for t, meta in TABLE_META.items():
            assert stats[t] == meta["row_count"]

        idx = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert "uq_users_email" in idx
        assert "idx_orders_user_id" in idx
    finally:
        con.close()


def test_build_is_idempotent(tmp_path):
    db = tmp_path / "t.db"
    build_sample_db(db)
    build_sample_db(db)
    con = sqlite3.connect(db)
    n = con.execute("SELECT count(*) FROM table_stats").fetchone()[0]
    con.close()
    assert n == len(TABLE_META)
