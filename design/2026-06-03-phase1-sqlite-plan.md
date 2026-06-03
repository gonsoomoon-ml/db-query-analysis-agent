# Phase 1 — SQLite backend + EXPLAIN Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `get_table_meta`에 실제 SQLite introspection backend(`META_BACKEND=sqlite`)를 추가하고 `analyze_sql_with_llm`에 EXPLAIN QUERY PLAN을 통합한다 — `TABLE_META`는 단일 진실 원천 유지, `sample.db`는 거기서 파생, sqlite는 mock과 parity.

**Architecture:** `build_sqlite.py`가 `TABLE_META`에서 `sample.db`(테이블+인덱스+`table_stats`)를 생성. `sqlite_backend`가 PRAGMA introspect(+`table_stats` 행수)로 mock과 동일 shape 반환. `analyze`는 `run_explain()`으로 `sample.db`에 EXPLAIN을 돌려 실제 플랜을 LLM에 주입(실패 시 graceful).

**Tech Stack:** Python 3.12, sqlite3(stdlib), Strands, pytest.

> 참조 spec: `design/2026-06-03-phase1-sqlite-spec.md`. 브랜치 `main`. 컨벤션: docstring/comment 한국어, identifier 영어. `uv run`으로 실행.

---

## File Structure

| 파일 | 변경 | 책임 |
|---|---|---|
| `data/seed/build_sqlite.py` | 재작성 | `TABLE_META` → `sample.db`(+`table_stats`) 생성, `schema.sql` 재생성, `ensure_sample_db` |
| `agents/db_query_analysis_agent/meta/sqlite_backend.py` | 신규 | `sample.db` introspect → mock과 동일 shape(`lookup`) |
| `agents/db_query_analysis_agent/meta/__init__.py` | 수정 | `lookup_table_meta` 분기에 `sqlite` 추가 |
| `agents/db_query_analysis_agent/tools/analyze_sql_with_llm.py` | 수정 | `run_explain()` + `run_analysis`에 플랜 주입 |
| `agents/db_query_analysis_agent/shared/prompts/analyze_prompt.md` | 수정 | 실제 플랜 우선 사용 지시 |
| `.env.example` | 수정 | `META_BACKEND` 주석(mock\|sqlite) |
| `tests/test_build_sqlite.py` | 재작성/신규 | build 결과 검증 |
| `tests/test_sqlite_backend.py` | 신규 | sqlite↔mock parity |
| `tests/test_explain.py` | 신규 | `run_explain` 동작 |
| `tests/test_analyze_sql.py` | 수정 | run_explain 격리 + 플랜 주입 테스트 |

**구현 노트(단일 source 유지):** `build_sample_db`는 `TABLE_META`만 읽어 DDL을 생성. `schema.sql`은 **기본 DB 빌드 시에만** `TABLE_META`에서 재생성(파생 문서). 단일 컬럼 INTEGER PK 가정(우리 데이터에 부합). `TABLE_META`엔 NOT NULL 정보가 없으므로 생성 DDL/스키마엔 NOT NULL 미포함(메타 shape엔 무관).

---

## Task 1: `build_sqlite.py` — TABLE_META에서 sample.db 생성

**Files:**
- Modify(재작성): `data/seed/build_sqlite.py`
- Test: `tests/test_build_sqlite.py`

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_build_sqlite.py` (기존 파일 있으면 교체)

```python
import sqlite3
from data.seed.build_sqlite import build_sample_db
from data.mock.table_meta import TABLE_META


def test_build_creates_tables_stats_and_indexes(tmp_path):
    db = tmp_path / "t.db"
    build_sample_db(db)
    con = sqlite3.connect(db)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert set(TABLE_META) <= tables          # 모든 TABLE_META 테이블 생성
        assert "table_stats" in tables             # 행수 stats 테이블

        stats = dict(con.execute("SELECT table_name, row_count FROM table_stats"))
        for t, meta in TABLE_META.items():
            assert stats[t] == meta["row_count"]   # 행수가 TABLE_META와 일치

        idx = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert "uq_users_email" in idx             # 명시 UNIQUE 인덱스
        assert "idx_orders_user_id" in idx         # 명시 인덱스
    finally:
        con.close()


def test_build_is_idempotent(tmp_path):
    db = tmp_path / "t.db"
    build_sample_db(db)
    build_sample_db(db)  # 재실행해도 에러 없음(재생성)
    con = sqlite3.connect(db)
    n = con.execute("SELECT count(*) FROM table_stats").fetchone()[0]
    con.close()
    assert n == len(TABLE_META)
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_build_sqlite.py -v`
Expected: FAIL (`build_sample_db` 시그니처/동작 불일치 또는 ImportError)

- [ ] **Step 3: `data/seed/build_sqlite.py` 재작성**

```python
"""TABLE_META → data/sample.db 생성 (Phase 1). 단일 진실 원천은 TABLE_META.

sample.db = 테이블 + 인덱스 + table_stats(행수). schema.sql은 기본 빌드 시
TABLE_META에서 재생성(파생 문서). sqlite backend / EXPLAIN이 이 DB를 읽음.

사용: uv run python -m data.seed.build_sqlite
"""
import sqlite3
from pathlib import Path

from data.mock.table_meta import TABLE_META

_DATA_DIR = Path(__file__).resolve().parents[1]
DB_PATH = _DATA_DIR / "sample.db"
SCHEMA_PATH = _DATA_DIR / "schema.sql"


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
                continue  # PK는 컬럼 정의에서 처리
            uniq = "UNIQUE " if idx["unique"] else ""
            cols = ", ".join(idx["columns"])
            parts.append(f'CREATE {uniq}INDEX {idx["name"]} ON {t}({cols});')
        parts.append("")
    return "\n".join(parts)


def build_sample_db(db_path: Path | None = None) -> Path:
    """sample.db 재생성(멱등) — DDL 실행 + table_stats 적재. 기본 경로면 schema.sql도 재생성."""
    is_default = db_path is None
    db_path = db_path or DB_PATH
    if db_path.exists():
        db_path.unlink()  # 멱등 재생성
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
    if is_default:  # 기본 빌드일 때만 canonical schema.sql 재생성(파생 문서)
        SCHEMA_PATH.write_text(
            "-- 자동 생성: build_sqlite.py가 TABLE_META에서 생성. 직접 수정 금지.\n\n"
            + _ddl_from_table_meta(),
            encoding="utf-8",
        )
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
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_build_sqlite.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add data/seed/build_sqlite.py tests/test_build_sqlite.py data/schema.sql
git commit -m "feat(phase1): build sample.db from TABLE_META (+ table_stats, schema.sql regen)"
```

---

## Task 2: `sqlite_backend` + meta 분기 (parity)

**Files:**
- Create: `agents/db_query_analysis_agent/meta/sqlite_backend.py`
- Modify: `agents/db_query_analysis_agent/meta/__init__.py`
- Test: `tests/test_sqlite_backend.py`

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_sqlite_backend.py`

```python
import pytest
from agents.db_query_analysis_agent.meta import sqlite_backend, mock_backend
from data.mock.table_meta import TABLE_META
from data.seed.build_sqlite import build_sample_db


@pytest.fixture(autouse=True)
def _fresh_db():
    build_sample_db()  # canonical sample.db 재생성
    yield


def _norm_idx(idx_list):
    # 이름→(컬럼 튜플, unique). 순서 무관 비교.
    return {i["name"]: (tuple(i["columns"]), bool(i["unique"])) for i in idx_list}


@pytest.mark.parametrize("table", list(TABLE_META))
def test_sqlite_mock_parity(table):
    s = sqlite_backend.lookup(table)
    m = mock_backend.lookup(table)
    assert s is not None
    assert s["name"] == m["name"]
    assert s["columns"] == m["columns"]          # 컬럼(순서 포함) 동일
    assert s["row_count"] == m["row_count"]      # 행수 동일
    assert _norm_idx(s["indexes"]) == _norm_idx(m["indexes"])  # 인덱스 집합 동일(pk 합성 포함)


def test_sqlite_unknown_table_returns_none():
    assert sqlite_backend.lookup("ghost") is None
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_sqlite_backend.py -v`
Expected: FAIL (ImportError: sqlite_backend)

- [ ] **Step 3: `agents/db_query_analysis_agent/meta/sqlite_backend.py` 작성**

```python
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
        if pk_cols:  # mock의 pk_<table> 표현과 일치하도록 합성
            indexes.append({"name": f"pk_{name}", "columns": pk_cols, "unique": True})
        for ix in con.execute(f'PRAGMA index_list("{name}")').fetchall():
            # ix row: (seq, name, unique, origin, partial)
            ix_name = ix[1]
            if ix_name.startswith("sqlite_autoindex"):
                continue  # UNIQUE 제약 자동 인덱스 제외(명시 인덱스만)
            cols = [r[2] for r in con.execute(f'PRAGMA index_info("{ix_name}")').fetchall()]
            indexes.append({"name": ix_name, "columns": cols, "unique": bool(ix[2])})

        row = con.execute(
            "SELECT row_count FROM table_stats WHERE table_name=?", (name,)
        ).fetchone()
        row_count = int(row[0]) if row else 0
        return {"name": name, "columns": columns, "indexes": indexes, "row_count": row_count}
    finally:
        con.close()
```

- [ ] **Step 4: `meta/__init__.py` 분기 추가**

기존:
```python
def lookup_table_meta(table_name: str) -> dict | None:
    """현재 backend에서 테이블 메타 조회. 미존재 시 None."""
    if current_backend() == "redis":
        from .redis_backend import lookup
    else:
        from .mock_backend import lookup
    return lookup(table_name)
```
교체:
```python
def lookup_table_meta(table_name: str) -> dict | None:
    """현재 backend에서 테이블 메타 조회. 미존재 시 None."""
    backend = current_backend()
    if backend == "redis":
        from .redis_backend import lookup
    elif backend == "sqlite":
        from .sqlite_backend import lookup
    else:
        from .mock_backend import lookup
    return lookup(table_name)
```

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_sqlite_backend.py -v`
Expected: PASS (parity 5 + unknown 1 = 6 passed). parity 실패 시 — PK 합성/인덱스 이름이 mock과 다른지 점검(테스트는 약화 금지).

- [ ] **Step 6: Commit**

```bash
git add agents/db_query_analysis_agent/meta/sqlite_backend.py agents/db_query_analysis_agent/meta/__init__.py tests/test_sqlite_backend.py
git commit -m "feat(phase1): sqlite meta backend (PRAGMA introspect, mock parity)"
```

---

## Task 3: `run_explain` (EXPLAIN QUERY PLAN)

**Files:**
- Modify: `agents/db_query_analysis_agent/tools/analyze_sql_with_llm.py` (`run_explain` 추가)
- Test: `tests/test_explain.py`

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_explain.py`

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_explain.py -v`
Expected: FAIL (ImportError: run_explain)

- [ ] **Step 3: `analyze_sql_with_llm.py`에 `run_explain` 추가**

파일 상단 import에 추가:
```python
import sqlite3

from data.seed.build_sqlite import ensure_sample_db
```
(`import json`/`import re` 등 기존 import 옆에. `from pathlib import Path`는 이미 있음.)

`_parse` 위(또는 `run_analysis` 위)에 함수 추가:
```python
def run_explain(sql: str) -> str | None:
    """sample.db에 EXPLAIN QUERY PLAN 실행 → 사람이 읽는 플랜 요약. 실패 시 None.

    read-only로 열어 어떤 쿼리(DELETE/DROP 포함)도 실행되지 않음 — EXPLAIN은
    플랜만 기술. 실패(유효치 않은 SQL/미존재 테이블/빌드 불가) → None (graceful).
    """
    try:
        db_path = ensure_sample_db()
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = con.execute("EXPLAIN QUERY PLAN " + sql).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    # rows: (id, parent, notused, detail)
    details = [r[3] for r in rows if len(r) >= 4 and r[3]]
    return "\n".join(details) if details else None
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_explain.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add agents/db_query_analysis_agent/tools/analyze_sql_with_llm.py tests/test_explain.py
git commit -m "feat(phase1): run_explain (EXPLAIN QUERY PLAN, read-only, graceful)"
```

---

## Task 4: `run_analysis`에 플랜 주입 + 프롬프트 + 테스트 격리

**Files:**
- Modify: `agents/db_query_analysis_agent/tools/analyze_sql_with_llm.py` (`run_analysis`)
- Modify: `agents/db_query_analysis_agent/shared/prompts/analyze_prompt.md`
- Modify: `tests/test_analyze_sql.py`

- [ ] **Step 1: 플랜 주입 테스트 작성** — `tests/test_analyze_sql.py` 상단에 autouse 격리 fixture + 신규 테스트 추가

기존 파일 맨 위(import 다음)에 추가:
```python
import pytest


@pytest.fixture(autouse=True)
def _no_explain(monkeypatch):
    # 기존 analyze 테스트는 sqlite와 격리 — run_explain 기본 비활성
    monkeypatch.setattr(mod, "run_explain", lambda _sql: None)
```
파일 맨 아래에 추가:
```python
def test_plan_injected_into_prompt(monkeypatch):
    captured = {}

    async def _capture(user_msg):
        captured["msg"] = user_msg
        return '{"index_efficiency":"i","service_impact":"s","optimizations":[],"analysis":"a"}'

    monkeypatch.setattr(mod, "run_explain", lambda _sql: "SEARCH orders USING INDEX idx_orders_user_id")
    monkeypatch.setattr(mod, "_invoke_model", _capture)
    out = asyncio.run(mod.run_analysis("SELECT * FROM orders WHERE user_id=1", "[]", ""))
    assert out["index_efficiency"] == "i"
    assert "EXPLAIN QUERY PLAN" in captured["msg"]
    assert "idx_orders_user_id" in captured["msg"]
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_analyze_sql.py -v`
Expected: FAIL — `test_plan_injected_into_prompt` 실패(아직 플랜 주입 안 함). 기존 4개는 통과(격리 fixture로).

- [ ] **Step 3: `run_analysis`에 플랜 주입**

기존 `run_analysis`:
```python
async def run_analysis(sql: str, violations_json: str, meta_summary: str = "") -> dict:
    """심층 분석 1회 호출 + 파싱. 실패 시 {"error":..., "analysis":""}."""
    try:
        user_msg = (
            f"## SQL\n{sql}\n\n"
            f"## 이미 탐지된 규칙 위반 (재언급 금지)\n{violations_json}\n\n"
            f"## 테이블 메타\n{meta_summary or '(없음)'}"
        )
        return _parse(await _invoke_model(user_msg))
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "analysis": ""}
```
교체:
```python
async def run_analysis(sql: str, violations_json: str, meta_summary: str = "") -> dict:
    """심층 분석 1회 호출 + 파싱. 실제 플랜(EXPLAIN) 있으면 주입. 실패 시 {"error":..., "analysis":""}."""
    try:
        plan = run_explain(sql)
        user_msg = (
            f"## SQL\n{sql}\n\n"
            f"## 이미 탐지된 규칙 위반 (재언급 금지)\n{violations_json}\n\n"
            f"## 테이블 메타\n{meta_summary or '(없음)'}"
        )
        if plan:
            user_msg += f"\n\n## 실제 쿼리 플랜 (EXPLAIN QUERY PLAN)\n{plan}"
        return _parse(await _invoke_model(user_msg))
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "analysis": ""}
```

- [ ] **Step 4: `analyze_prompt.md`에 플랜 우선 지시 추가**

`## 규칙` 섹션의 첫 bullet 위에 추가:
```markdown
- **실제 쿼리 플랜(EXPLAIN QUERY PLAN)이 제공되면** 그것을 근거로 인덱스 효율을 판단하세요(추측 대신 실측 우선).
```

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_analyze_sql.py -v`
Expected: PASS (기존 4 + 신규 1 = 5 passed)

- [ ] **Step 6: Commit**

```bash
git add agents/db_query_analysis_agent/tools/analyze_sql_with_llm.py agents/db_query_analysis_agent/shared/prompts/analyze_prompt.md tests/test_analyze_sql.py
git commit -m "feat(phase1): inject real EXPLAIN plan into analyze; prompt prefers measured plan"
```

---

## Task 5: `.env.example` + 전체 검증 + e2e

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: `.env.example`에 META_BACKEND 주석**

기존 `META_BACKEND=mock` 줄을 두 줄로 교체:
```bash
# META_BACKEND: mock | sqlite  (redis는 배포 단계)
META_BACKEND=mock
```

- [ ] **Step 2: 전체 단위 테스트**

Run: `uv run pytest -q && uv run ruff check .`
Expected: 전부 PASS (기존 43 + 신규: build 2 + sqlite 6 + explain 4 + analyze +1 ≈ **56 passed, 1 skipped**), ruff 0 에러. (정확한 수는 환경에 따라; 신규 테스트가 모두 통과하면 됨.)

- [ ] **Step 3: sqlite backend 수동 확인 (Bedrock 불필요)**

Run:
```bash
uv run python -m data.seed.build_sqlite
META_BACKEND=sqlite uv run python -c "from agents.db_query_analysis_agent.tools.get_table_meta import collect_table_meta; import json; print(json.dumps(collect_table_meta('SELECT * FROM orders WHERE user_id=1'), ensure_ascii=False, indent=2))"
```
Expected: `orders` 메타가 sqlite backend로 조회됨(`backend: "sqlite"`, `large_table: true`, 인덱스에 `pk_orders`/`idx_orders_user_id` 포함).

- [ ] **Step 4: e2e (Bedrock 자격증명 필요)**

Run: `META_BACKEND=sqlite uv run -m agents.db_query_analysis_agent.local.run --sql "SELECT * FROM orders WHERE user_id = 1"`
Expected: 스트리밍 리뷰에 **실제 EXPLAIN 플랜 근거의 인덱스 분석**(예: "idx_orders_user_id 실제 사용 확인") 포함. 자격증명 없으면 보류(단위 테스트가 게이트).

- [ ] **Step 5: Commit**

```bash
git add .env.example
git commit -m "docs(phase1): META_BACKEND mock|sqlite note"
```

---

## Self-Review (작성자 점검)

**1. Spec coverage:**
- sqlite backend(introspect + table_stats) → Task 2 ✅
- build_sqlite가 TABLE_META 기반(+table_stats, schema.sql 재생성) → Task 1 ✅
- EXPLAIN 통합(analyze 내부, 3 tool 유지) → Task 3/4 ✅
- parity(sqlite≡mock, PK 합성) → Task 2 테스트 ✅
- 단일 source(TABLE_META) 유지, sample.db 파생 → Task 1 ✅
- 프롬프트 실측 우선 → Task 4 ✅
- mock 기본 유지, sqlite opt-in → meta 분기 Task 2 + .env Task 5 ✅
- 에러 graceful(EXPLAIN 실패→None, 미존재→found:false) → Task 3 + 기존 get_table_meta ✅
- Redis/Runtime/A2A 범위 외 → 미포함 ✅

**2. Placeholder scan:** 모든 step에 실제 코드/명령. placeholder 없음.

**3. Type consistency:** `build_sample_db`/`ensure_sample_db`/`_ddl_from_table_meta`/`_pk_columns`(build_sqlite), `lookup`(sqlite_backend, mock과 동일 shape), `run_explain`/`run_analysis`(analyze), `lookup_table_meta` 분기 — 정의처/호출처 일치. sqlite `lookup` 반환 키(name/columns/indexes/row_count)가 `get_table_meta.collect_table_meta`가 기대하는 키와 일치 ✅.

**알려진 가정:** 단일 컬럼 INTEGER PK(우리 데이터 부합); `TABLE_META`에 NOT NULL 미포함(메타 shape 무관). 실행 중 보정: 인덱스 parity가 어긋나면 `sqlite_autoindex` 필터/PK 합성 점검.
