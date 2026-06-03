"""TABLE_META → data/sample.db 생성 (Phase 1). 단일 진실 원천은 TABLE_META.

sample.db = 테이블 + 인덱스 + table_stats(행수). sqlite backend / EXPLAIN이 이 DB를 읽음.

사용: uv run python -m data.seed.build_sqlite
"""
import sqlite3
from pathlib import Path

from data.mock.table_meta import TABLE_META

_DATA_DIR = Path(__file__).resolve().parents[1]
DB_PATH = _DATA_DIR / "sample.db"


def _pk_columns(table: str, meta: dict) -> list[str]:
    """pk_<table> 인덱스의 컬럼(우리 컨벤션). 없으면 빈 리스트."""
    for idx in meta["indexes"]:
        if idx["name"] == f"pk_{table}":
            return list(idx["columns"])
    return []


def _ddl_from_table_meta() -> str:
    """TABLE_META → CREATE TABLE/INDEX DDL. 단일 컬럼 INTEGER PK 가정."""
    parts: list[str] = []
    for t, meta in TABLE_META.items():
        pk = set(_pk_columns(t, meta))
        col_defs = []
        for c in meta["columns"]:
            d = f'  {c["name"]} {c["type"]}'
            if c["name"] in pk:
                d += " PRIMARY KEY"
            col_defs.append(d)
        parts.append(f"CREATE TABLE {t} (\n" + ",\n".join(col_defs) + "\n);")
        for idx in meta["indexes"]:
            if idx["name"] == f"pk_{t}":
                continue
            uniq = "UNIQUE " if idx["unique"] else ""
            cols = ", ".join(idx["columns"])
            parts.append(f'CREATE {uniq}INDEX {idx["name"]} ON {t}({cols});')
        parts.append("")
    return "\n".join(parts)


def build_sample_db(db_path: Path | None = None) -> Path:
    """sample.db 재생성(멱등) — DDL 실행 + table_stats 적재."""
    db_path = db_path or DB_PATH
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    try:
        con.executescript(_ddl_from_table_meta())
        con.execute("CREATE TABLE table_stats (table_name TEXT PRIMARY KEY, row_count INTEGER)")
        con.executemany(
            "INSERT INTO table_stats (table_name, row_count) VALUES (?, ?)",
            [(t, meta["row_count"]) for t, meta in TABLE_META.items()],
        )
        con.commit()
    finally:
        con.close()
    return db_path


def ensure_sample_db(db_path: Path | None = None) -> Path:
    """없으면 build_sample_db() 후 경로 반환 (lazy)."""
    target = db_path or DB_PATH
    if not target.exists():
        build_sample_db(db_path)
    return target


if __name__ == "__main__":
    p = build_sample_db()
    con = sqlite3.connect(p)
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    con.close()
    print(f"✅ sample.db 생성: {p}  테이블: {tables}")
