# Phase 1 — SQLite backend + EXPLAIN (Spec)

> 작성일: 2026-06-03
> 선행: Stage 1 구현 완료(`main`), `design/2026-06-03-db-query-analysis-agent-migration-spec.md`
> 로드맵: **SQLite(본 phase) → AgentCore Runtime(+Redis) → Cognito/Gateway → A2A/Supervisor**

---

## 1. 배경 & 목표

Stage 1은 메타데이터를 mock(`TABLE_META` dict)으로만 제공한다. Phase 1은 **실제 SQLite DB(`sample.db`)를 도입**해:
1. `get_table_meta`에 **`sqlite` backend** 추가 — PRAGMA introspection으로 실제 스키마/인덱스 + `table_stats` 행수.
2. `analyze_sql_with_llm`에 **EXPLAIN QUERY PLAN** 통합 — 인덱스 효율/풀스캔을 *추측*이 아닌 *실측*으로 grounding.

**불변식**: `TABLE_META`는 **단일 진실 원천**으로 유지. `sample.db`는 `TABLE_META`에서 **생성(파생)**되며, `sqlite` backend는 mock과 **동일 메타(parity)**를 반환한다.

**범위 외**: Redis(배포 단계로 연기), Runtime/A2A(후속 phase), 사용자 데이터에 대한 실제 쿼리 실행(EXPLAIN까지만).

---

## 2. 결정 요약

| 항목 | 결정 |
|---|---|
| 단일 진실 원천 | `data/mock/table_meta.py` `TABLE_META` (스키마+인덱스+행수) — 유지 |
| 메타 backend | `META_BACKEND=mock`(기본) \| `sqlite`(opt-in). redis는 배포 단계 |
| sample.db | `build_sqlite.py`가 **`TABLE_META`에서 생성**(테이블+인덱스+`table_stats`). `schema.sql`은 TABLE_META에서 재생성(파생 문서) |
| sqlite backend | PRAGMA introspect + `table_stats` 행수 → **mock과 semantic 동일(parity)** |
| EXPLAIN | `analyze_sql_with_llm` **내부**에 통합(tool 3종 유지). 실패 시 LLM-only graceful fallback |
| PK 표현 | sqlite backend가 PRAGMA `table_info`의 pk 플래그로 `pk_<table>` 인덱스를 **합성**해 mock 표현과 일치 |

---

## 3. 아키텍처

```
            data/mock/table_meta.py  TABLE_META   ← 단일 진실 원천
                  │
   build_sqlite() │ (TABLE_META → DDL + table_stats)
                  ▼
            data/sample.db  (tables + indexes + table_stats(table_name,row_count))
                  │
   ┌──────────────┼───────────────────────────────┐
   ▼              ▼                                 (redis: 배포 단계)
 mock           sqlite backend
 (dict)         (PRAGMA introspect + table_stats → mock과 동일 shape)
                  │
 get_table_meta ◀─┘   (META_BACKEND 분기, parity 보장)

 analyze_sql_with_llm:
   run_explain(sql) → sample.db EXPLAIN QUERY PLAN → 실제 플랜(or None)
   → 있으면 LLM 프롬프트에 주입 → grounding된 분석 / 없으면 LLM-only
```

---

## 4. 단일 진실 원천 불변식

- `TABLE_META`만 손으로 작성. `sample.db`·(향후)redis는 전부 여기서 파생.
- **parity 불변식**: 모든 테이블 t에 대해 `sqlite_lookup(t)` ≡ `mock_lookup(t)` (columns·indexes(집합)·row_count·name 동일). 테스트로 강제.
- `schema.sql`은 `build_sqlite`가 `TABLE_META`에서 **재생성**(사람이 읽는 DDL 참조; 손으로 안 고침 → 2번째 source 제거).

---

## 5. 컴포넌트 상세

### 5.1 `data/seed/build_sqlite.py` (재작성)
`TABLE_META` 기반으로 sample.db 생성. `schema.sql`도 재생성.

```python
def _ddl_from_table_meta() -> str:
    """TABLE_META → CREATE TABLE/INDEX DDL 문자열 생성."""
    # 각 테이블: CREATE TABLE t (col type, ...);  (pk 컬럼은 INTEGER PRIMARY KEY)
    # 각 인덱스(pk_* 제외): CREATE [UNIQUE] INDEX name ON t(cols);

def build_sample_db(db_path: Path | None = None) -> Path:
    """sample.db 재생성 — 기존 삭제 후 DDL 실행 + table_stats 적재."""
    # 1) 기존 sample.db 삭제(멱등)
    # 2) executescript(_ddl_from_table_meta())
    # 3) CREATE TABLE table_stats(table_name TEXT PRIMARY KEY, row_count INTEGER)
    #    INSERT (name, row_count) for each table in TABLE_META
    # 4) schema.sql 재생성 — _ddl_from_table_meta() 결과를 write (파생 문서, 손으로 안 고침)

def ensure_sample_db(db_path: Path | None = None) -> Path:
    """없으면 build_sample_db() 호출 후 경로 반환 (lazy)."""

# __main__: build_sample_db() 명시 실행 + 테이블 목록 출력
```
- `DB_PATH = data/sample.db` (gitignore 유지).
- pk 컬럼: `TABLE_META` 인덱스 중 `pk_<table>`로 식별 → 해당 컬럼을 `INTEGER PRIMARY KEY`로. 나머지 인덱스는 `CREATE [UNIQUE] INDEX`.

### 5.2 `agents/db_query_analysis_agent/meta/sqlite_backend.py` (신규)
```python
def lookup(table_name: str) -> dict | None:
    """sample.db introspect → mock과 동일 shape. 미존재 테이블 → None."""
    # ensure_sample_db()
    # 테이블 존재 확인(sqlite_master) — 없으면 None
    # columns: PRAGMA table_info(t) → [{"name","type"}]  (+ pk 컬럼 식별)
    # indexes:
    #   - pk 합성: pk>0 컬럼으로 {"name": f"pk_{t}", "columns":[pk_col], "unique": True}
    #   - 명시 인덱스: PRAGMA index_list(t) (name이 'sqlite_autoindex'로 시작하면 제외)
    #       각 index: PRAGMA index_info(idx) → columns; "unique"= bool(index_list.unique)
    # row_count: SELECT row_count FROM table_stats WHERE table_name=t
    # return {"name": t, "columns":[...], "indexes":[pk_합성, ...명시], "row_count": n}
```
- 반환 shape는 mock_backend과 동일 → `get_table_meta.collect_table_meta`가 그대로 소비.
- PK 합성으로 mock의 `pk_<table>` 표현과 일치 → parity.

### 5.3 `agents/db_query_analysis_agent/meta/__init__.py` (수정)
`lookup_table_meta` 분기에 `sqlite` 추가:
```python
backend = current_backend()
if backend == "redis":
    from .redis_backend import lookup
elif backend == "sqlite":
    from .sqlite_backend import lookup
else:
    from .mock_backend import lookup
return lookup(table_name)
```

### 5.4 `agents/db_query_analysis_agent/tools/analyze_sql_with_llm.py` (수정)
EXPLAIN 통합 (deterministic, LLM 호출 전):
```python
def run_explain(sql: str) -> str | None:
    """sample.db에 EXPLAIN QUERY PLAN 실행 → 사람이 읽는 플랜 요약. 실패 시 None.

    실패 케이스(→None, graceful): sample.db 빌드 불가 / 유효치 않은 SQL /
    미존재 테이블(sqlite3.OperationalError).
    """
    # ensure_sample_db(); con.execute("EXPLAIN QUERY PLAN " + sql)
    # rows의 detail 컬럼 join → "SEARCH orders USING INDEX idx_orders_user_id ..." 등
    # except (sqlite3.Error): return None

# run_analysis: plan = run_explain(sql); plan 있으면 user_msg에
#   "## 실제 쿼리 플랜 (EXPLAIN QUERY PLAN)\n{plan}" 추가. 없으면 생략(기존 동작).
```
- `run_explain`은 별도 함수 → 단독 테스트. `run_analysis`는 plan을 프롬프트에 주입만(기존 `_invoke_model`/`_parse` 흐름 유지).

### 5.5 `shared/prompts/analyze_prompt.md` (수정)
"실제 쿼리 플랜(EXPLAIN)이 제공되면 그것을 근거로 인덱스 효율을 판단(추측 대신)" 지시 추가.

### 5.6 `.env.example` (수정)
`META_BACKEND=mock` 줄에 `# mock | sqlite (redis: 배포 단계)` 주석.

---

## 6. 데이터 흐름

```
META_BACKEND=sqlite + "SELECT * FROM orders WHERE user_id=1":
  get_table_meta → lookup_table_meta → sqlite_backend
     → ensure_sample_db → PRAGMA(orders) + table_stats → {orders: cols/idx/5,000,000/large}
  analyze_sql_with_llm(sql, violations, meta):
     run_explain("SELECT * FROM orders WHERE user_id=1")
       → "SEARCH orders USING INDEX idx_orders_user_id (user_id=?)"
     → 프롬프트에 실플랜 주입 → "user_id 인덱스 실제 사용 확인" (실측 근거)
```

---

## 7. 에러 처리

| 지점 | 처리 |
|---|---|
| `sample.db` 없음 | `ensure_sample_db()`가 `TABLE_META`에서 lazy 빌드 |
| 빌드 실패 | sqlite_backend → 한국어 RuntimeError / `run_explain` → None |
| EXPLAIN 대상 유효치 않음(미존재 테이블/MySQL 전용 문법) | `sqlite3.OperationalError` → `run_explain` None → analyze LLM-only |
| 미존재 테이블 메타 조회 | sqlite_backend → None (→ get_table_meta `found:false`) |
| parity 위반 | 테스트 실패로 차단 |

---

## 8. 테스트 (TDD, 결정적)

- **`tests/test_build_sqlite.py`**: `build_sample_db()` → sample.db에 5개 테이블 + 기대 인덱스 + `table_stats`(행수) 존재.
- **`tests/test_sqlite_backend.py`**:
  - **parity**: 모든 테이블에 대해 `sqlite_backend.lookup(t)` ≡ `mock_backend.lookup(t)` (columns 동일, indexes 집합 동일[name/columns/unique], row_count 동일).
  - 미존재 테이블 → None.
  - large_table 판정용 row_count가 `table_stats`에서 정확.
- **`tests/test_explain.py`**: `run_explain` 유효 쿼리(orders) → 플랜 문자열(테이블/인덱스 포함), 미존재 테이블/빈 SQL → None.
- (analyze 통합) `run_explain`을 monkeypatch해 plan이 user_msg에 주입되는지 — 또는 `run_explain` 단독 테스트로 갈음(기존 `test_analyze_sql`의 `_invoke_model` mock 유지).
- 전체 회귀: 기존 스위트 + 신규 통과(ruff 0).

---

## 9. 범위 / 범위 외

- **범위(Phase 1)**: sqlite backend(introspect + table_stats) + build_sqlite(TABLE_META 기반) + EXPLAIN 통합 + 프롬프트/테스트. mock 기본 유지.
- **범위 외**: Redis(배포 단계), AgentCore Runtime/Cognito/Gateway/A2A(후속), 실제 데이터 쿼리 실행.

## 10. 미해결
- 없음. 세부(EXPLAIN detail 파싱 문안, DDL 생성 순서)는 TDD로 확정.
