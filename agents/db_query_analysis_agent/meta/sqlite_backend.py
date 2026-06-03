"""sqlite 메타 backend — sample.db introspect (PRAGMA) + table_stats 행수.

mock과 동일 shape 반환(parity). INTEGER PK는 PRAGMA table_info의 pk 플래그로
pk_<table> 인덱스를 합성해 mock 표현과 일치. sample.db는 TABLE_META에서 파생.
"""
import sqlite3

from data.seed.build_sqlite import ensure_sample_db


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def lookup(table_name: str) -> dict | None:
    """sample.db introspect → {name, columns, indexes, row_count}. 미존재 → None."""
    db_path = ensure_sample_db()
    name = table_name.lower()
    con = sqlite3.connect(db_path)
    try:
        if not _table_exists(con, name):
            return None
        info = con.execute(f'PRAGMA table_info("{name}")').fetchall()
        # info row: (cid, name, type, notnull, dflt_value, pk)
        columns = [{"name": r[1], "type": r[2]} for r in info]
        pk_cols = [r[1] for r in info if r[5]]

        indexes: list[dict] = []
        if pk_cols:
            indexes.append({"name": f"pk_{name}", "columns": pk_cols, "unique": True})
        for ix in con.execute(f'PRAGMA index_list("{name}")').fetchall():
            # ix row: (seq, name, unique, origin, partial)
            ix_name = ix[1]
            if ix_name.startswith("sqlite_autoindex"):
                continue
            cols = [r[2] for r in con.execute(f'PRAGMA index_info("{ix_name}")').fetchall()]
            indexes.append({"name": ix_name, "columns": cols, "unique": bool(ix[2])})

        row = con.execute(
            "SELECT row_count FROM table_stats WHERE table_name=?", (name,)
        ).fetchone()
        row_count = int(row[0]) if row else 0
        return {"name": name, "columns": columns, "indexes": indexes, "row_count": row_count}
    finally:
        con.close()
