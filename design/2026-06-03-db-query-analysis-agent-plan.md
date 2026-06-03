# db-query-analysis-agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 `db-query-analysis-agent`(Windmill/MySQL/Redis)를 로컬 우선 단일 Strands 에이전트 + plain tool 3종으로 이관(Stage 1: `META_BACKEND=mock`).

**Architecture:** 단일 Strands `Agent`(Bedrock Haiku 4.5, temp 0.1) + 3 plain tool(`check_sql_rules` 순수 함수 / `get_table_meta` 메타 조회 / `analyze_sql_with_llm` Strands `BedrockModel` 단발 호출). 메타는 `META_BACKEND`(mock|redis) swap, 응답 shape 동형. 대화형 chat + 단발 run + callable facade. supervisor 없음(A2A 단계로 연기).

**Tech Stack:** Python 3.12, Strands Agents SDK, AWS Bedrock(boto3 경유는 Strands 내부만), python-dotenv, pytest, (옵션) redis.

> **참조 spec:** `design/2026-06-03-db-query-analysis-agent-migration-spec.md`
> **컨벤션:** 식별자 영어 / docstring·comment·로그·에러 한국어 / tool 파라미터 키는 반드시 `sql`.

---

## File Structure

| 파일 | 책임 |
|---|---|
| `pyproject.toml` | 의존성 + pytest pythonpath |
| `.env.example` | 환경변수 템플릿 |
| `data/mock/table_meta.py` | `TABLE_META` dict — 메타 단일 진실 원천 |
| `data/schema.sql` | 동일 구조 SQLite DDL (사람이 읽는 참조) |
| `agents/db_query_analysis_agent/meta/__init__.py` | `lookup_table_meta()` + `current_backend()` — backend 분기 |
| `agents/db_query_analysis_agent/meta/mock_backend.py` | mock lookup (`TABLE_META`) |
| `agents/db_query_analysis_agent/meta/redis_backend.py` | redis lookup (lazy import) |
| `agents/db_query_analysis_agent/tools/check_sql_rules.py` | 규칙 정적 분석 (`evaluate_sql_rules` + `@tool`) |
| `agents/db_query_analysis_agent/tools/get_table_meta.py` | 테이블 추출 + 메타 (`collect_table_meta` + `@tool`) |
| `agents/db_query_analysis_agent/tools/analyze_sql_with_llm.py` | LLM 심층 분석 (`run_analysis` + `@tool`) |
| `agents/db_query_analysis_agent/shared/model.py` | `build_bedrock_model()` 공통 헬퍼 |
| `agents/db_query_analysis_agent/shared/agent.py` | `create_agent()` + `build_db_query_agent()` |
| `agents/db_query_analysis_agent/shared/review.py` | `review_sql()` callable facade |
| `agents/db_query_analysis_agent/shared/prompts/system_prompt.md` | 에이전트 시스템 프롬프트 |
| `agents/db_query_analysis_agent/shared/prompts/analyze_prompt.md` | analyze tool 시스템 프롬프트 |
| `agents/db_query_analysis_agent/local/run.py` | 단발 진입점 (`--sql`) |
| `agents/db_query_analysis_agent/local/chat.py` | 멀티턴 REPL 진입점 |
| `shared/streaming.py` | `stream_response()` 공통 스트리밍 헬퍼 |
| `data/seed/seed_redis.py` | `TABLE_META` → Redis 적재 (Stage 2) |
| `data/seed/build_sqlite.py` | `schema.sql` → `sample.db` (옵션) |
| `tests/test_check_sql_rules.py` · `test_get_table_meta.py` · `test_meta_backend_parity.py` · `test_agent_build.py` | 단위 테스트 |

> **구현 노트:** `build_bedrock_model()`은 `shared/agent.py`가 아니라 `shared/model.py`에 둔다 — `analyze_sql_with_llm`(model 사용)과 `agent.py`(tool import) 사이 순환 import 회피. (spec §5.1의 의도 동일, 위치만 분리.)

---

## Task 1: 프로젝트 스캐폴딩

**Files:**
- Create: `pyproject.toml`, `.env.example`
- Create (빈 패키지): `agents/__init__.py`, `agents/db_query_analysis_agent/__init__.py`, `agents/db_query_analysis_agent/shared/__init__.py`, `agents/db_query_analysis_agent/shared/tools/` 대신 `tools/__init__.py`, `agents/db_query_analysis_agent/meta/__init__.py`(임시 빈), `agents/db_query_analysis_agent/local/__init__.py`, `shared/__init__.py`, `data/__init__.py`, `data/mock/__init__.py`, `tests/__init__.py`

- [ ] **Step 1: git 저장소 초기화**

Run:
```bash
cd /home/ubuntu/db-query-analysis-agent && git init && git branch -M main
```
Expected: `Initialized empty Git repository ...`

- [ ] **Step 2: `pyproject.toml` 작성**

```toml
[project]
name = "db-query-analysis-agent"
version = "0.1.0"
description = "MySQL/SQL 쿼리 1차 리뷰 에이전트 (Strands + Bedrock)"
requires-python = ">=3.12"
dependencies = [
    "strands-agents>=1.10.0",
    "boto3>=1.34.0",
    "python-dotenv>=1.0.0",
]

[project.optional-dependencies]
redis = ["redis>=5.0.0"]
dev = ["pytest>=8.0.0", "ruff>=0.7.0"]

[tool.pytest.ini_options]
pythonpath = ["."]
addopts = "-q"

[tool.uv]
package = false
```

- [ ] **Step 3: `.env.example` 작성**

```bash
AWS_REGION=us-east-1
DBQUERY_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0
DBQUERY_TEMPERATURE=0.1
DBQUERY_MAX_TOKENS=4096
ANALYZE_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0
ANALYZE_TEMPERATURE=0.1
ANALYZE_MAX_TOKENS=2048
META_BACKEND=mock
REDIS_URL=redis://localhost:6379/0
LARGE_TABLE_THRESHOLD=1000000
```

- [ ] **Step 4: 패키지 디렉토리 + `__init__.py` 생성**

Run:
```bash
cd /home/ubuntu/db-query-analysis-agent && mkdir -p agents/db_query_analysis_agent/shared/prompts agents/db_query_analysis_agent/tools agents/db_query_analysis_agent/meta agents/db_query_analysis_agent/local shared data/mock data/seed tests && touch agents/__init__.py agents/db_query_analysis_agent/__init__.py agents/db_query_analysis_agent/shared/__init__.py agents/db_query_analysis_agent/tools/__init__.py agents/db_query_analysis_agent/local/__init__.py shared/__init__.py data/__init__.py data/mock/__init__.py tests/__init__.py
```
Expected: 에러 없음.

- [ ] **Step 5: `.gitignore` 작성**

```bash
__pycache__/
*.pyc
.venv/
.env
data/sample.db
.pytest_cache/
```

- [ ] **Step 6: 의존성 설치 + 검증**

Run: `cd /home/ubuntu/db-query-analysis-agent && uv sync --extra dev 2>&1 | tail -3 && uv run python -c "import strands; print('strands ok')"`
Expected: `strands ok`

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "chore: scaffold db-query-analysis-agent project"
```

---

## Task 2: 샘플 메타데이터 (`TABLE_META`) + DDL

**Files:**
- Create: `data/mock/table_meta.py`
- Create: `data/schema.sql`
- Test: `tests/test_table_meta_shape.py`

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_table_meta_shape.py`

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/test_table_meta_shape.py -v`
Expected: FAIL — `ModuleNotFoundError: data.mock.table_meta`

- [ ] **Step 3: `data/mock/table_meta.py` 작성**

```python
"""샘플 테이블 메타데이터 — 메타 조회의 단일 진실 원천.

손으로 작성(베이스 코드 mock 패턴). 행수(row_count)는 stats — 실제 대량 행을 넣지
않고도 large-table(>LARGE_TABLE_THRESHOLD) 경로를 데모. redis backend는 seed_redis.py
가 이 dict를 그대로 적재하므로 두 backend가 동일 데이터.
"""
from typing import Dict

TABLE_META: Dict[str, dict] = {
    "users": {
        "name": "users",
        "columns": [
            {"name": "id", "type": "INTEGER"},
            {"name": "email", "type": "TEXT"},
            {"name": "name", "type": "TEXT"},
            {"name": "created_at", "type": "TEXT"},
        ],
        "indexes": [
            {"name": "pk_users", "columns": ["id"], "unique": True},
            {"name": "uq_users_email", "columns": ["email"], "unique": True},
        ],
        "row_count": 1_000,
    },
    "orders": {
        "name": "orders",
        "columns": [
            {"name": "id", "type": "INTEGER"},
            {"name": "user_id", "type": "INTEGER"},
            {"name": "status", "type": "TEXT"},
            {"name": "total", "type": "REAL"},
            {"name": "created_at", "type": "TEXT"},
        ],
        "indexes": [
            {"name": "pk_orders", "columns": ["id"], "unique": True},
            {"name": "idx_orders_user_id", "columns": ["user_id"], "unique": False},
            {"name": "idx_orders_created_at", "columns": ["created_at"], "unique": False},
        ],
        "row_count": 5_000_000,
    },
    "products": {
        "name": "products",
        "columns": [
            {"name": "id", "type": "INTEGER"},
            {"name": "sku", "type": "TEXT"},
            {"name": "name", "type": "TEXT"},
            {"name": "price", "type": "REAL"},
            {"name": "category", "type": "TEXT"},
        ],
        "indexes": [
            {"name": "pk_products", "columns": ["id"], "unique": True},
            {"name": "uq_products_sku", "columns": ["sku"], "unique": True},
            {"name": "idx_products_category", "columns": ["category"], "unique": False},
        ],
        "row_count": 50_000,
    },
    "order_items": {
        "name": "order_items",
        "columns": [
            {"name": "id", "type": "INTEGER"},
            {"name": "order_id", "type": "INTEGER"},
            {"name": "product_id", "type": "INTEGER"},
            {"name": "qty", "type": "INTEGER"},
        ],
        "indexes": [
            {"name": "pk_order_items", "columns": ["id"], "unique": True},
            {"name": "idx_order_items_order_id", "columns": ["order_id"], "unique": False},
            {"name": "idx_order_items_product_id", "columns": ["product_id"], "unique": False},
        ],
        "row_count": 12_000_000,
    },
    "audit_log": {
        "name": "audit_log",
        "columns": [
            {"name": "id", "type": "INTEGER"},
            {"name": "actor", "type": "TEXT"},
            {"name": "action", "type": "TEXT"},
            {"name": "payload", "type": "TEXT"},
            {"name": "created_at", "type": "TEXT"},
        ],
        "indexes": [
            {"name": "pk_audit_log", "columns": ["id"], "unique": True},
        ],  # 인덱스 사실상 없음(PK만) — large + no-index 경고 데모용
        "row_count": 8_000_000,
    },
}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/test_table_meta_shape.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: `data/schema.sql` 작성 (사람이 읽는 참조)**

```sql
-- 샘플 e-commerce 스키마 (TABLE_META 와 동일 구조). 향후 sample.db / EXPLAIN 용.
CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL,
  name TEXT,
  created_at TEXT
);
CREATE UNIQUE INDEX uq_users_email ON users(email);

CREATE TABLE orders (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  status TEXT,
  total REAL,
  created_at TEXT
);
CREATE INDEX idx_orders_user_id ON orders(user_id);
CREATE INDEX idx_orders_created_at ON orders(created_at);

CREATE TABLE products (
  id INTEGER PRIMARY KEY,
  sku TEXT NOT NULL,
  name TEXT,
  price REAL,
  category TEXT
);
CREATE UNIQUE INDEX uq_products_sku ON products(sku);
CREATE INDEX idx_products_category ON products(category);

CREATE TABLE order_items (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL,
  qty INTEGER
);
CREATE INDEX idx_order_items_order_id ON order_items(order_id);
CREATE INDEX idx_order_items_product_id ON order_items(product_id);

CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY,
  actor TEXT,
  action TEXT,
  payload TEXT,
  created_at TEXT
);
```

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: add sample table metadata (mock source of truth) + schema.sql"
```

---

## Task 3: 메타 backend 추상화 (mock | redis)

**Files:**
- Create: `agents/db_query_analysis_agent/meta/__init__.py` (Task 1의 빈 파일 덮어쓰기), `meta/mock_backend.py`, `meta/redis_backend.py`
- Test: `tests/test_meta_backend_parity.py`

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_meta_backend_parity.py`

```python
import os
import pytest
from agents.db_query_analysis_agent.meta import lookup_table_meta, current_backend


def test_mock_known_table(monkeypatch):
    monkeypatch.setenv("META_BACKEND", "mock")
    meta = lookup_table_meta("orders")
    assert meta is not None
    assert meta["name"] == "orders"
    assert meta["row_count"] == 5_000_000
    assert any(c["name"] == "user_id" for c in meta["columns"])


def test_mock_case_insensitive(monkeypatch):
    monkeypatch.setenv("META_BACKEND", "mock")
    assert lookup_table_meta("ORDERS")["name"] == "orders"


def test_mock_unknown_returns_none(monkeypatch):
    monkeypatch.setenv("META_BACKEND", "mock")
    assert lookup_table_meta("ghost") is None


def test_current_backend_default(monkeypatch):
    monkeypatch.delenv("META_BACKEND", raising=False)
    assert current_backend() == "mock"


def test_redis_parity_if_available(monkeypatch):
    """redis 가동 시에만 — mock 과 동일 shape 검증. 아니면 skip."""
    try:
        import redis  # noqa: F401
    except ModuleNotFoundError:
        pytest.skip("redis 패키지 미설치")
    from data.seed import seed_redis
    try:
        seed_redis.main()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"redis 미가동: {e}")
    monkeypatch.setenv("META_BACKEND", "redis")
    assert lookup_table_meta("orders") == lookup_table_meta_mock("orders")


def lookup_table_meta_mock(name):
    from agents.db_query_analysis_agent.meta.mock_backend import lookup
    return lookup(name)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/test_meta_backend_parity.py -v`
Expected: FAIL — `ImportError: cannot import name 'lookup_table_meta'`

- [ ] **Step 3: `meta/mock_backend.py` 작성**

```python
"""mock 메타 backend — data/mock/table_meta.py 의 TABLE_META 조회."""
from data.mock.table_meta import TABLE_META


def lookup(table_name: str) -> dict | None:
    """테이블명(대소문자 무시)으로 메타 dict 반환. 미존재 시 None."""
    return TABLE_META.get(table_name.lower())
```

- [ ] **Step 4: `meta/redis_backend.py` 작성**

```python
"""redis 메타 backend — tablemeta:{name} 키의 JSON 조회 (Stage 2). redis lazy import."""
import json
import os


def _client():
    import redis  # lazy — mock 모드에선 redis 미설치여도 동작
    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(url, decode_responses=True)


def lookup(table_name: str) -> dict | None:
    """tablemeta:{name} JSON 조회. 연결 실패 시 한국어 RuntimeError."""
    try:
        raw = _client().get(f"tablemeta:{table_name.lower()}")
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Redis 연결 실패 ({os.environ.get('REDIS_URL')}): {e}") from e
    return json.loads(raw) if raw else None
```

- [ ] **Step 5: `meta/__init__.py` 작성 (분기)**

```python
"""메타 backend 분기 — META_BACKEND(mock|redis). 두 backend 응답 shape 동형."""
import os


def current_backend() -> str:
    return os.environ.get("META_BACKEND", "mock").lower()


def lookup_table_meta(table_name: str) -> dict | None:
    """현재 backend에서 테이블 메타 조회. 미존재 시 None."""
    if current_backend() == "redis":
        from .redis_backend import lookup
    else:
        from .mock_backend import lookup
    return lookup(table_name)
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `uv run pytest tests/test_meta_backend_parity.py -v`
Expected: PASS — 4 passed, 1 skipped (redis 미가동 시)

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat: meta backend abstraction (mock default, redis swap)"
```

---

## Task 4: `check_sql_rules` tool (TDD)

**Files:**
- Create: `agents/db_query_analysis_agent/tools/check_sql_rules.py`
- Test: `tests/test_check_sql_rules.py`

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_check_sql_rules.py`

```python
from agents.db_query_analysis_agent.tools.check_sql_rules import evaluate_sql_rules


def rules(sql: str) -> set[str]:
    return {v["rule"] for v in evaluate_sql_rules(sql)["violations"]}


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
    # WHERE가 주석에만 있으면 DELETE는 여전히 플래그
    assert "DELETE_WITHOUT_WHERE" in rules("DELETE FROM orders -- WHERE id = 1")


def test_clean_select_no_violations():
    assert rules("SELECT id, name FROM users WHERE id = 1") == set()


def test_return_shape():
    out = evaluate_sql_rules("DROP TABLE x")
    assert "violations" in out and "checked_rules" in out
    assert out["violations"][0]["severity"] == "critical"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/test_check_sql_rules.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: `tools/check_sql_rules.py` 작성**

```python
"""MySQL/SQL 쿼리 규칙 기반 정적 분석 (순수 함수 + @tool).

탐지: DELETE/UPDATE without WHERE, DROP, TRUNCATE, SELECT *, LIKE 선행 와일드카드.
주석(-- , /* */)은 WHERE 탐지 전에 제거해 오탐 최소화. 1차 리뷰용 경량 정규식.
"""
import re

from strands import tool

_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_COMMENT_LINE = re.compile(r"--[^\n]*")

CHECKED_RULES = [
    "DELETE_WITHOUT_WHERE", "UPDATE_WITHOUT_WHERE", "DROP",
    "TRUNCATE", "SELECT_STAR", "LIKE_LEADING_WILDCARD",
]


def _strip_comments(sql: str) -> str:
    return _COMMENT_LINE.sub(" ", _COMMENT_BLOCK.sub(" ", sql))


def evaluate_sql_rules(sql: str) -> dict:
    """규칙 위반 목록 반환. {"violations": [...], "checked_rules": [...]}."""
    s = _strip_comments(sql or "")
    has_where = re.search(r"\bWHERE\b", s, re.IGNORECASE) is not None
    v: list[dict] = []

    if re.search(r"\bDELETE\s+FROM\b", s, re.IGNORECASE) and not has_where:
        v.append({"rule": "DELETE_WITHOUT_WHERE", "severity": "critical",
                  "message": "WHERE 없는 DELETE — 전체 행 삭제 위험"})
    if (re.search(r"\bUPDATE\b", s, re.IGNORECASE)
            and re.search(r"\bSET\b", s, re.IGNORECASE) and not has_where):
        v.append({"rule": "UPDATE_WITHOUT_WHERE", "severity": "critical",
                  "message": "WHERE 없는 UPDATE — 전체 행 변경 위험"})
    if re.search(r"\bDROP\s+(TABLE|DATABASE|INDEX|VIEW)\b", s, re.IGNORECASE):
        v.append({"rule": "DROP", "severity": "critical",
                  "message": "DROP 문 — 객체 영구 삭제"})
    if re.search(r"\bTRUNCATE\b", s, re.IGNORECASE):
        v.append({"rule": "TRUNCATE", "severity": "critical",
                  "message": "TRUNCATE — 전체 행 삭제(롤백 제약)"})
    if re.search(r"\bSELECT\s+\*", s, re.IGNORECASE):
        v.append({"rule": "SELECT_STAR", "severity": "warning",
                  "message": "SELECT * — 불필요한 컬럼 로드"})
    if re.search(r"\bLIKE\s+N?['\"]%", s, re.IGNORECASE):
        v.append({"rule": "LIKE_LEADING_WILDCARD", "severity": "warning",
                  "message": "LIKE 선행 와일드카드 — 인덱스 미사용"})

    return {"violations": v, "checked_rules": CHECKED_RULES}


@tool
def check_sql_rules(sql: str) -> dict:
    """MySQL/SQL 쿼리의 규칙 기반 정적 분석.

    DELETE/UPDATE without WHERE, DROP, TRUNCATE, SELECT *, LIKE 선행 와일드카드 탐지.
    필수 파라미터: sql (str) — 반드시 "sql" 키 사용. "query" 금지.
    """
    return evaluate_sql_rules(sql)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/test_check_sql_rules.py -v`
Expected: PASS (13 passed)

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: check_sql_rules tool with rule-based static analysis"
```

---

## Task 5: `get_table_meta` tool (TDD)

**Files:**
- Create: `agents/db_query_analysis_agent/tools/get_table_meta.py`
- Test: `tests/test_get_table_meta.py`

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_get_table_meta.py`

```python
import pytest
from agents.db_query_analysis_agent.tools.get_table_meta import (
    extract_table_names, collect_table_meta,
)


@pytest.fixture(autouse=True)
def _mock_backend(monkeypatch):
    monkeypatch.setenv("META_BACKEND", "mock")
    monkeypatch.setenv("LARGE_TABLE_THRESHOLD", "1000000")


def test_extract_simple():
    assert extract_table_names("SELECT * FROM orders WHERE id = 1") == ["orders"]


def test_extract_join_with_alias():
    sql = "SELECT * FROM orders o JOIN order_items oi ON o.id = oi.order_id"
    assert extract_table_names(sql) == ["orders", "order_items"]


def test_extract_update():
    assert extract_table_names("UPDATE users SET name = 'x' WHERE id = 1") == ["users"]


def test_extract_insert_into():
    assert extract_table_names("INSERT INTO products (sku) VALUES ('a')") == ["products"]


def test_extract_schema_qualified():
    assert extract_table_names("SELECT * FROM shop.orders") == ["orders"]


def test_large_table_flagged():
    t = collect_table_meta("SELECT * FROM orders")["tables"][0]
    assert t["found"] is True and t["large_table"] is True


def test_small_table_not_flagged():
    t = collect_table_meta("SELECT id FROM users WHERE id = 1")["tables"][0]
    assert t["found"] is True and t["large_table"] is False


def test_unknown_table():
    t = collect_table_meta("SELECT * FROM ghost")["tables"][0]
    assert t == {"name": "ghost", "found": False}


def test_backend_reported():
    out = collect_table_meta("SELECT * FROM users")
    assert out["backend"] == "mock"
    assert out["large_table_threshold"] == 1_000_000
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/test_get_table_meta.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: `tools/get_table_meta.py` 작성**

```python
"""SQL에서 테이블명 추출 + 메타데이터 조회 (순수 함수 + @tool).

FROM/JOIN/INTO/UPDATE/TABLE 다음 식별자를 추출(별칭/백틱/스키마 접두 처리),
META_BACKEND를 통해 메타 조회, large_table 플래그 부여.
"""
import os
import re

from strands import tool

from agents.db_query_analysis_agent.meta import current_backend, lookup_table_meta

_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+"
    r"([`\"\[]?[A-Za-z_]\w*[`\"\]]?(?:\.[`\"\[]?[A-Za-z_]\w*[`\"\]]?)?)",
    re.IGNORECASE,
)


def _clean_ident(tok: str) -> str:
    tok = tok.strip()
    if "." in tok:  # schema.table → table
        tok = tok.split(".")[-1]
    return tok.strip('`"[]').lower()


def extract_table_names(sql: str) -> list[str]:
    """SQL에서 테이블명 목록(중복 제거, 등장 순서) 추출."""
    names: list[str] = []
    for m in _TABLE_RE.finditer(sql or ""):
        n = _clean_ident(m.group(1))
        if n and n not in names:
            names.append(n)
    return names


def collect_table_meta(sql: str) -> dict:
    """추출 테이블별 메타 + large_table 플래그. backend/threshold 동봉."""
    threshold = int(os.environ.get("LARGE_TABLE_THRESHOLD", "1000000"))
    tables: list[dict] = []
    for name in extract_table_names(sql):
        meta = lookup_table_meta(name)
        if meta is None:
            tables.append({"name": name, "found": False})
            continue
        row_count = int(meta.get("row_count", 0))
        tables.append({
            "name": meta["name"],
            "found": True,
            "columns": meta["columns"],
            "indexes": meta["indexes"],
            "row_count": row_count,
            "large_table": row_count > threshold,
        })
    return {"tables": tables, "backend": current_backend(),
            "large_table_threshold": threshold}


@tool
def get_table_meta(sql: str) -> dict:
    """SQL에서 테이블명을 추출하고 메타데이터(스키마/인덱스/행수)를 조회.

    행수 > LARGE_TABLE_THRESHOLD 면 large_table=true. 미존재 테이블은 found=false.
    필수 파라미터: sql (str) — 반드시 "sql" 키 사용. "table_name" 금지.
    """
    return collect_table_meta(sql)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/test_get_table_meta.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: get_table_meta tool (table extraction + meta lookup + large flag)"
```

---

## Task 6: `build_bedrock_model()` 공통 헬퍼

**Files:**
- Create: `agents/db_query_analysis_agent/shared/model.py`
- Test: `tests/test_model_helper.py`

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_model_helper.py`

```python
from agents.db_query_analysis_agent.shared.model import build_bedrock_model


def test_build_uses_env_overrides(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-2")
    monkeypatch.setenv("DBQUERY_MODEL_ID", "test.model.id")
    monkeypatch.setenv("DBQUERY_TEMPERATURE", "0.0")
    monkeypatch.setenv("DBQUERY_MAX_TOKENS", "1234")
    model = build_bedrock_model(
        "DBQUERY_MODEL_ID", "default.model", "DBQUERY_TEMPERATURE", 0.1,
        "DBQUERY_MAX_TOKENS", 4096,
    )
    # BedrockModel 구성은 AWS 호출 없이 즉시 생성됨
    assert model is not None
    cfg = model.get_config()
    assert cfg["model_id"] == "test.model.id"


def test_build_uses_defaults(monkeypatch):
    monkeypatch.delenv("ANALYZE_MODEL_ID", raising=False)
    model = build_bedrock_model(
        "ANALYZE_MODEL_ID", "default.analyze.model", "ANALYZE_TEMPERATURE", 0.1,
        "ANALYZE_MAX_TOKENS", 2048, cache_tools=False,
    )
    assert model.get_config()["model_id"] == "default.analyze.model"
```

> 참고: Strands `BedrockModel.get_config()`는 구성 dict를 반환. 키 이름이 다르면(`model_id` 등) 실제 API에 맞춰 assert를 조정.

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/test_model_helper.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: `shared/model.py` 작성**

```python
"""Bedrock 모델 구성 공통 헬퍼.

create_agent(메인 에이전트)와 analyze_sql_with_llm(분석 호출)이 동일 경로로
BedrockModel을 생성 → Bedrock 호출 방식 단일화(raw boto3 혼용 0). 별도 모듈로 둬
agent.py ↔ analyze tool 순환 import 회피.
"""
import os

from strands.models import BedrockModel


def build_bedrock_model(
    model_id_env: str, default_model: str,
    temp_env: str, default_temp: float,
    max_tok_env: str, default_max_tok: int,
    *, cache_tools: bool = True,
) -> BedrockModel:
    """env 우선 + 기본값으로 BedrockModel 생성. region은 AWS_REGION(기본 us-east-1)."""
    kwargs = {
        "model_id": os.environ.get(model_id_env) or default_model,
        "region_name": os.environ.get("AWS_REGION") or "us-east-1",
        "temperature": float(os.environ.get(temp_env, default_temp)),
        "max_tokens": int(os.environ.get(max_tok_env, default_max_tok)),
    }
    if cache_tools:
        kwargs["cache_tools"] = "default"
    return BedrockModel(**kwargs)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/test_model_helper.py -v`
Expected: PASS (2 passed). 실패 시 `get_config()` 키 이름을 실제 Strands API에 맞춰 테스트만 조정.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: build_bedrock_model shared helper"
```

---

## Task 7: 시스템 프롬프트 2종

**Files:**
- Create: `agents/db_query_analysis_agent/shared/prompts/system_prompt.md`
- Create: `agents/db_query_analysis_agent/shared/prompts/analyze_prompt.md`

- [ ] **Step 1: `system_prompt.md` 작성**

```markdown
# db-query-analysis-agent

당신은 MySQL/SQL 쿼리 **1차 리뷰** 에이전트입니다. DBA의 반복 리뷰를 자동화합니다.

## 작업 순서 (각 도구는 1회만 호출)

1. `check_sql_rules(sql=<원본 SQL>)` — 규칙 기반 위반 탐지
2. `get_table_meta(sql=<원본 SQL>)` — 관련 테이블 스키마/인덱스/행수 (large_table 여부)
3. `analyze_sql_with_llm(sql=<원본 SQL>, violations_json=<1의 결과 JSON 문자열>, meta_summary=<2의 요약>)` — 인덱스 효율/서비스 영향도/최적화 심층 분석

## 도구 호출 규칙

- 파라미터 키는 반드시 `sql`. ("query"/"table_name" 금지)
- 각 도구 1회만 호출. 같은 도구 재호출 금지.

## 최종 리뷰 작성 (한국어)

도구 결과를 종합해 다음 형식으로:

- **위험도 요약**: critical/warning 위반 한 줄 요약
- **규칙 위반**: check_sql_rules 결과 (없으면 "없음")
- **테이블 영향**: 관련 테이블, 대형 테이블(large_table) 주의, 인덱스 유무
- **심층 분석**: analyze 결과 (인덱스 효율, 서비스 영향, 최적화 제안)
- **권장 조치**: 구체적 다음 단계

이미 규칙 체크에서 플래그된 항목을 심층 분석이 중복 언급하면 한 번만 제시하세요.
```

- [ ] **Step 2: `analyze_prompt.md` 작성**

```markdown
# SQL 심층 분석기

당신은 SQL 성능 분석 전문가입니다. 주어진 SQL, 이미 탐지된 규칙 위반(JSON), 테이블 메타를 보고 분석합니다.

## 분석 항목
- **인덱스 효율**: WHERE/JOIN/ORDER BY가 기존 인덱스를 활용하는지, 누락 인덱스
- **서비스 영향도**: 대형 테이블 풀스캔/락/장기 실행 위험
- **최적화 제안**: 구체적 개선(인덱스 추가, 컬럼 선택, 페이징 등)

## 규칙
- 입력 violations_json 에 이미 있는 항목은 **재언급 금지** (중복 회피).
- 반드시 아래 JSON만 출력 (다른 텍스트 금지):

```json
{
  "index_efficiency": "<한국어 1-3문장>",
  "service_impact": "<한국어 1-3문장>",
  "optimizations": ["<제안1>", "<제안2>"],
  "analysis": "<한국어 종합 1-2문장>"
}
```
```

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -m "feat: agent + analyze system prompts"
```

---

## Task 8: `analyze_sql_with_llm` tool

**Files:**
- Create: `agents/db_query_analysis_agent/tools/analyze_sql_with_llm.py`
- Test: `tests/test_analyze_sql.py`

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_analyze_sql.py`

```python
from agents.db_query_analysis_agent.tools import analyze_sql_with_llm as mod


class _FakeAnalyzer:
    def __init__(self, text):
        self._text = text

    def __call__(self, _prompt):
        return self._text


def test_parses_json(monkeypatch):
    payload = ('{"index_efficiency":"idx ok","service_impact":"low",'
               '"optimizations":["add index"],"analysis":"fine"}')
    monkeypatch.setattr(mod, "_build_analyzer", lambda: _FakeAnalyzer(payload))
    out = mod.run_analysis("SELECT 1", "[]", "")
    assert out["index_efficiency"] == "idx ok"
    assert out["optimizations"] == ["add index"]


def test_non_json_falls_back_to_analysis(monkeypatch):
    monkeypatch.setattr(mod, "_build_analyzer", lambda: _FakeAnalyzer("그냥 텍스트 분석"))
    out = mod.run_analysis("SELECT 1", "[]", "")
    assert out["analysis"] == "그냥 텍스트 분석"
    assert out["optimizations"] == []


def test_exception_returns_error(monkeypatch):
    def _boom():
        raise RuntimeError("bedrock down")
    monkeypatch.setattr(mod, "_build_analyzer", _boom)
    out = mod.run_analysis("SELECT 1", "[]", "")
    assert "error" in out and out["analysis"] == ""
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/test_analyze_sql.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: `tools/analyze_sql_with_llm.py` 작성**

```python
"""AWS Bedrock(Strands BedrockModel)로 SQL 심층 분석 (plain @tool).

LLM 호출은 base code 패턴 — build_bedrock_model() + minimal tool-less Agent 1회 호출.
raw boto3 converse 미사용. analyze는 plain tool (orchestration 없음).
"""
import json
from pathlib import Path

from strands import Agent, tool
from strands.handlers.callback_handler import null_callback_handler

from agents.db_query_analysis_agent.shared.model import build_bedrock_model

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "shared" / "prompts" / "analyze_prompt.md"


def _build_analyzer() -> Agent:
    """분석 전용 tool-less Agent (ANALYZE_* 모델 구성)."""
    model = build_bedrock_model(
        "ANALYZE_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "ANALYZE_TEMPERATURE", 0.1, "ANALYZE_MAX_TOKENS", 2048,
        cache_tools=False,
    )
    return Agent(
        model=model,
        system_prompt=_PROMPT_PATH.read_text(encoding="utf-8"),
        callback_handler=null_callback_handler,
    )


def _parse(text: str) -> dict:
    try:
        data = json.loads(text)
        return {
            "index_efficiency": data.get("index_efficiency", ""),
            "service_impact": data.get("service_impact", ""),
            "optimizations": data.get("optimizations", []),
            "analysis": data.get("analysis", text),
        }
    except (json.JSONDecodeError, TypeError):
        return {"index_efficiency": "", "service_impact": "",
                "optimizations": [], "analysis": text}


def run_analysis(sql: str, violations_json: str, meta_summary: str = "") -> dict:
    """심층 분석 1회 호출 + 파싱. 실패 시 {"error":..., "analysis":""}."""
    try:
        analyzer = _build_analyzer()
        user_msg = (
            f"## SQL\n{sql}\n\n"
            f"## 이미 탐지된 규칙 위반 (재언급 금지)\n{violations_json}\n\n"
            f"## 테이블 메타\n{meta_summary or '(없음)'}"
        )
        return _parse(str(analyzer(user_msg)))
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "analysis": ""}


@tool
def analyze_sql_with_llm(sql: str, violations_json: str, meta_summary: str = "") -> dict:
    """AWS Bedrock Claude로 SQL의 인덱스 효율/서비스 영향도/최적화를 분석.

    규칙 체크(violations_json)에 이미 있는 항목은 재언급하지 않음.
    파라미터: sql(str), violations_json(str), meta_summary(str, 선택). "sql" 키 필수.
    """
    return run_analysis(sql, violations_json, meta_summary)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/test_analyze_sql.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: analyze_sql_with_llm tool (Strands BedrockModel one-shot)"
```

---

## Task 9: 에이전트 팩토리 (`create_agent` / `build_db_query_agent`)

**Files:**
- Create: `agents/db_query_analysis_agent/shared/agent.py`
- Test: `tests/test_agent_build.py`

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_agent_build.py`

```python
from agents.db_query_analysis_agent.shared.agent import build_db_query_agent, AGENT_NAME


def test_build_agent_offline():
    """BedrockModel 구성은 AWS 호출 없음 → 오프라인 생성 가능 + 프롬프트 파일 로드 검증."""
    agent = build_db_query_agent()
    assert agent is not None
    assert agent.name == AGENT_NAME
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/test_agent_build.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: `shared/agent.py` 작성**

```python
"""db-query-analysis-agent 팩토리 — 단일 create_agent 진실 원천 (AgentCore 승격 대비).

tool은 caller 주입(phase-agnostic). build_db_query_agent()가 표준 tool 3종 조립.
planner/executor/summarizer는 Strands tool-use loop가 단일 모델로 흡수.
"""
from pathlib import Path

from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.handlers.callback_handler import null_callback_handler
from strands.types.content import SystemContentBlock

from agents.db_query_analysis_agent.shared.model import build_bedrock_model
from agents.db_query_analysis_agent.tools.analyze_sql_with_llm import analyze_sql_with_llm
from agents.db_query_analysis_agent.tools.check_sql_rules import check_sql_rules
from agents.db_query_analysis_agent.tools.get_table_meta import get_table_meta

_PROMPTS_DIR = Path(__file__).parent / "prompts"
AGENT_NAME = "db-query-analysis-agent"
AGENT_DESC = "MySQL/SQL 쿼리 1차 리뷰 에이전트 — 규칙 체크 + 메타 조회 + LLM 분석"


def _load_prompt(filename: str) -> str:
    return (_PROMPTS_DIR / filename).read_text(encoding="utf-8")


def create_agent(tools: list, system_prompt_filename: str) -> Agent:
    """Strands Agent 생성. model_id/temp/max_tokens는 DBQUERY_* env."""
    model = build_bedrock_model(
        "DBQUERY_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "DBQUERY_TEMPERATURE", 0.1, "DBQUERY_MAX_TOKENS", 4096,
    )
    prompt = _load_prompt(system_prompt_filename)
    agent = Agent(
        model=model,
        tools=tools,
        system_prompt=[
            SystemContentBlock(text=prompt),
            SystemContentBlock(cachePoint={"type": "default"}),
        ],
        conversation_manager=SlidingWindowConversationManager(window_size=20),
        callback_handler=null_callback_handler,
    )
    agent.name = AGENT_NAME
    agent.description = AGENT_DESC
    return agent


def build_db_query_agent() -> Agent:
    """표준 db-query-analysis-agent 조립 — tool 3종 + system_prompt.md."""
    return create_agent(
        tools=[check_sql_rules, get_table_meta, analyze_sql_with_llm],
        system_prompt_filename="system_prompt.md",
    )
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/test_agent_build.py -v`
Expected: PASS (1 passed). 실패가 `agent.name` 설정 불가 등 Strands API 차이면 `Agent(name=AGENT_NAME, ...)` 생성자 인자로 이동.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: db-query-analysis-agent factory (create_agent + build_db_query_agent)"
```

---

## Task 10: 스트리밍 헬퍼 + callable facade

**Files:**
- Create: `shared/streaming.py`
- Create: `agents/db_query_analysis_agent/shared/review.py`
- Test: `tests/test_streaming.py`, `tests/test_review_facade.py`

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_streaming.py`

```python
import asyncio
from shared.streaming import stream_response


class _FakeAgent:
    async def stream_async(self, _prompt):
        for chunk in ["안녕", "하세요"]:
            yield {"data": chunk}
        yield {"event": {"metadata": {"usage": {"totalTokens": 5}}}}


def test_stream_collects_text(capsys):
    out = asyncio.run(stream_response(_FakeAgent(), "hi"))
    assert out == "안녕하세요"
    captured = capsys.readouterr().out
    assert "안녕하세요" in captured
    assert "Tokens" in captured
```

- [ ] **Step 2: 실패 확인 → `shared/streaming.py` 작성**

Run: `uv run pytest tests/test_streaming.py -v` → FAIL (ModuleNotFound)

```python
"""에이전트 응답 스트리밍 + 토큰 usage 출력 (developer-briefing 패턴)."""
DIM = "\033[2m"
NC = "\033[0m"


def _usage_line(u: dict) -> str:
    return (
        f"{DIM}📊 Tokens — Total: {u.get('totalTokens', 0):,} | "
        f"Input: {u.get('inputTokens', 0):,} | Output: {u.get('outputTokens', 0):,} | "
        f"Cache R/W: {u.get('cacheReadInputTokens', 0):,}/"
        f"{u.get('cacheWriteInputTokens', 0):,}{NC}"
    )


async def stream_response(agent, prompt: str) -> str:
    """agent.stream_async를 소비 — 텍스트 실시간 출력 + usage 누적 표시. 전체 텍스트 반환."""
    usage = {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0,
             "cacheReadInputTokens": 0, "cacheWriteInputTokens": 0}
    chunks: list[str] = []
    async for event in agent.stream_async(prompt):
        data = event.get("data", "")
        if data:
            chunks.append(data)
            print(data, end="", flush=True)
        meta = event.get("event", {}).get("metadata", {})
        if "usage" in meta:
            for k in usage:
                usage[k] += meta["usage"].get(k, 0)
    print()
    print(_usage_line(usage))
    return "".join(chunks)
```

- [ ] **Step 3: 테스트 통과 확인**

Run: `uv run pytest tests/test_streaming.py -v`
Expected: PASS

- [ ] **Step 4: 실패 테스트 작성** — `tests/test_review_facade.py`

```python
import asyncio
from agents.db_query_analysis_agent.shared import review as mod


class _FakeAgent:
    async def invoke_async(self, prompt):
        assert "SELECT 1" in prompt
        return "리뷰 결과"


def test_review_sql(monkeypatch):
    monkeypatch.setattr(mod, "build_db_query_agent", lambda: _FakeAgent())
    out = asyncio.run(mod.review_sql("SELECT 1"))
    assert out == "리뷰 결과"
```

- [ ] **Step 5: 실패 확인 → `shared/review.py` 작성**

Run: `uv run pytest tests/test_review_facade.py -v` → FAIL

```python
"""callable facade — 타 에이전트 / Slack adapter / 향후 A2A wrapper 공통 호출 표면.

단발·stateless. supervisor 없이 누구나 부르는 깨끗한 callee.
"""
from agents.db_query_analysis_agent.shared.agent import build_db_query_agent


async def review_sql(sql: str) -> str:
    """단발 SQL 리뷰 — db-query-analysis-agent 1회 실행 후 리뷰 텍스트 반환."""
    agent = build_db_query_agent()
    result = await agent.invoke_async(f"다음 SQL을 리뷰해줘:\n```sql\n{sql}\n```")
    return str(result)
```

- [ ] **Step 6: 테스트 통과 확인 + Commit**

Run: `uv run pytest tests/test_streaming.py tests/test_review_facade.py -v` → PASS
```bash
git add -A && git commit -m "feat: streaming helper + review_sql callable facade"
```

---

## Task 11: 로컬 진입점 (단발 `run.py` + 멀티턴 `chat.py`)

**Files:**
- Create: `agents/db_query_analysis_agent/local/run.py`
- Create: `agents/db_query_analysis_agent/local/chat.py`

- [ ] **Step 1: `local/run.py` 작성 (단발)**

```python
"""단발 SQL 리뷰 진입점. uv run -m agents.db_query_analysis_agent.local.run --sql "..." """
import argparse
import asyncio

from dotenv import load_dotenv

from agents.db_query_analysis_agent.shared.agent import build_db_query_agent
from shared.streaming import stream_response

CYAN = "\033[0;36m"
NC = "\033[0m"


async def _amain(sql: str) -> None:
    agent = build_db_query_agent()
    await stream_response(agent, f"다음 SQL을 리뷰해줘:\n```sql\n{sql}\n```")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="db-query-analysis-agent 단발 리뷰")
    parser.add_argument("--sql", required=True, help="리뷰할 SQL")
    args = parser.parse_args()
    print(f"{CYAN}{'=' * 60}\n  db-query-analysis-agent (단발)\n{'=' * 60}{NC}")
    print(f"SQL: {args.sql}\n\n분석 중...\n")
    asyncio.run(_amain(args.sql))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: `local/chat.py` 작성 (멀티턴 REPL)**

```python
"""멀티턴 대화 REPL. uv run -m agents.db_query_analysis_agent.local.chat

같은 Agent 객체 재사용 → agent.messages에 대화 누적. /reset 초기화, /quit 종료.
"""
import asyncio

from dotenv import load_dotenv

from agents.db_query_analysis_agent.shared.agent import build_db_query_agent
from shared.streaming import stream_response

CYAN = "\033[0;36m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
DIM = "\033[2m"
NC = "\033[0m"


def main() -> None:
    load_dotenv()
    agent = build_db_query_agent()

    print(f"\n{CYAN}{'=' * 50}\n  db-query-analysis-agent (대화형)\n{'=' * 50}{NC}")
    print(f"{DIM}  SQL을 붙여넣고 리뷰를 요청하세요. /reset 초기화 · /quit 종료{NC}\n")

    while True:
        try:
            user = input(f"{GREEN}> {NC}").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n")
            break
        if not user:
            continue
        if user.lower() in ("/quit", "quit", "exit"):
            break
        if user.lower() == "/reset":
            agent = build_db_query_agent()
            print(f"{YELLOW}대화를 초기화했습니다{NC}\n")
            continue
        print()
        asyncio.run(stream_response(agent, user))
        print()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: import 스모크 체크**

Run: `uv run python -c "import agents.db_query_analysis_agent.local.run, agents.db_query_analysis_agent.local.chat; print('entrypoints import ok')"`
Expected: `entrypoints import ok`

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "feat: local entrypoints (single-shot run + multi-turn chat)"
```

---

## Task 12: 시드 스크립트 (Stage 2 / 옵션)

**Files:**
- Create: `data/seed/seed_redis.py`
- Create: `data/seed/build_sqlite.py`

- [ ] **Step 1: `data/seed/seed_redis.py` 작성**

```python
"""TABLE_META를 Redis에 적재 (Stage 2). tablemeta:{name} = JSON.

사용: uv run python -m data.seed.seed_redis   (redis 가동 + redis 패키지 필요)
"""
import json
import os

from data.mock.table_meta import TABLE_META


def main() -> None:
    import redis  # lazy
    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    client = redis.Redis.from_url(url, decode_responses=True)
    for name, meta in TABLE_META.items():
        client.set(f"tablemeta:{name}", json.dumps(meta, ensure_ascii=False))
    print(f"✅ {len(TABLE_META)}개 테이블 메타를 Redis에 적재 ({url})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: `data/seed/build_sqlite.py` 작성**

```python
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
```

- [ ] **Step 3: build_sqlite 동작 확인 (DDL 유효성)**

Run: `uv run python -m data.seed.build_sqlite && uv run python -c "import sqlite3; c=sqlite3.connect('data/sample.db'); print([r[0] for r in c.execute('SELECT name FROM sqlite_master WHERE type=\"table\"')])"`
Expected: `['users', 'orders', 'products', 'order_items', 'audit_log']`

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "feat: seed scripts (redis seed + sqlite build)"
```

---

## Task 13: 전체 테스트 + 수동 e2e 검증 + README

**Files:**
- Create: `README.md`

- [ ] **Step 1: 전체 단위 테스트**

Run: `uv run pytest -v`
Expected: 모든 테스트 PASS (redis 테스트는 미가동 시 skip)

- [ ] **Step 2: `README.md` 작성**

```markdown
# db-query-analysis-agent

MySQL/SQL 쿼리 1차 리뷰 에이전트 (Strands Agents + AWS Bedrock). 규칙 기반 체크 +
테이블 메타 조회 + LLM 심층 분석. 설계: `design/2026-06-03-db-query-analysis-agent-migration-spec.md`.

## 빠른 시작 (Stage 1: mock)

```bash
uv sync --extra dev
cp .env.example .env          # AWS_REGION / 모델 ID 확인 (Bedrock 액세스 필요)
# 단발
uv run -m agents.db_query_analysis_agent.local.run --sql "DELETE FROM orders"
# 대화형
uv run -m agents.db_query_analysis_agent.local.chat
```

## 타 에이전트/프로그램에서 호출

```python
from agents.db_query_analysis_agent.shared.review import review_sql
text = await review_sql("SELECT * FROM orders WHERE user_id = 1")
```

## Stage 2: Redis backend

```bash
docker run -d -p 6379:6379 redis
uv run --extra redis python -m data.seed.seed_redis
META_BACKEND=redis uv run -m agents.db_query_analysis_agent.local.run --sql "..."
```

## 테스트

```bash
uv run pytest -v
```
```

- [ ] **Step 3: 수동 e2e 검증 (Bedrock 자격증명 필요)**

Run: `uv run -m agents.db_query_analysis_agent.local.run --sql "DELETE FROM orders"`
Expected: 스트리밍으로 한국어 리뷰 출력 — DELETE_WITHOUT_WHERE(critical) + orders large_table 경고 + 심층 분석 + 권장 조치. 마지막에 토큰 usage 라인.

> 자격증명 없으면 이 단계는 보류. 단위 테스트(Step 1)는 자격증명 없이 통과해야 함.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "docs: README + Stage 1 verification"
```

---

## Self-Review (작성자 점검 결과)

**1. Spec coverage:**
- tool 3종 (check_sql_rules/get_table_meta/analyze_sql_with_llm) → Task 4/5/8 ✅
- 파라미터 키 `sql` 엄수 → 각 tool docstring + 테스트 ✅
- 메타 mock→redis swap (동형 shape) → Task 3 + Task 12 + parity 테스트 ✅
- 단일 에이전트 + Strands 단일 루프(planner/executor/summarizer 흡수) → Task 9 ✅
- LLM 호출 Strands BedrockModel 일원화 (build_bedrock_model) → Task 6/8/9 ✅
- 대화형 chat + 단발 run + facade → Task 10/11 ✅
- 스트리밍 + SlidingWindow + 캐싱 → Task 9/10 ✅
- temperature 0.1 / max_tokens / LARGE_TABLE_THRESHOLD → Task 1/6 ✅
- 샘플 스키마(large/no-index 데모) → Task 2 ✅
- 에러 처리(크래시 금지) → 각 tool 반환값 + 테스트 ✅
- TDD(결정적 단위 우선) → Task 2~10 테스트 우선 ✅

**2. Placeholder scan:** 모든 step에 실제 코드/명령 포함. 미해결 placeholder 없음.

**3. Type consistency:**
- 메타 dict 키(`name/columns/indexes/row_count`)가 table_meta ↔ mock_backend ↔ get_table_meta 일치 ✅
- `lookup_table_meta` / `current_backend` / `evaluate_sql_rules` / `collect_table_meta` / `extract_table_names` / `build_bedrock_model` / `create_agent` / `build_db_query_agent` / `run_analysis` / `_build_analyzer` / `review_sql` / `stream_response` 시그니처가 정의처와 호출처에서 일치 ✅
- `build_bedrock_model` 위치는 spec(§5.1)의 `shared/agent.py` 언급과 달리 `shared/model.py`로 분리(순환 import 회피) — File Structure 노트에 명시 ✅

**알려진 보정 포인트(실행 중 조정):** Strands API 세부(`BedrockModel.get_config()` 키, `agent.name` 설정 방식)는 설치 버전에 맞춰 해당 테스트/생성자 인자만 미세 조정.
