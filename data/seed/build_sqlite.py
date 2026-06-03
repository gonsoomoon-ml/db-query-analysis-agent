"""schema.sql → data/sample.db 생성 (옵션, 향후 EXPLAIN 용).

사용: uv run python -m data.seed.build_sqlite
"""
import sqlite3
from pathlib import Path


def main() -> None:
    data_dir = Path(__file__).resolve().parents[1]
    ddl = (data_dir / "schema.sql").read_text(encoding="utf-8")
    db_path = data_dir / "sample.db"
    con = sqlite3.connect(db_path)
    con.executescript(ddl)
    con.commit()
    con.close()
    print(f"✅ sample.db 생성: {db_path}")


if __name__ == "__main__":
    main()
