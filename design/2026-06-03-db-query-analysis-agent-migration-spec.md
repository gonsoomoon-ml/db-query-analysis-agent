# db-query-analysis-agent 이관 설계 (Spec)

> 작성일: 2026-06-03 (rev. supervisor 제거)
> 기반: `design/requirement.md`, `design/prd.md`
> Base code: https://github.com/gonsoomoon-ml/aiops-multi-agent-workshop (Strands 팩토리/A2A 구조)
> Reference: https://github.com/gonsoomoon-ml/developer-briefing-agent (대화형 챗 + 스트리밍)

---

## 1. 배경 & 목표

기존 `db-query-analysis-agent`(Windmill tool 기반, MySQL + Redis)를 베이스 코드의 **Strands Agent** 패턴으로 이관한다. MySQL 의존을 제거하고 **가벼운 mockup → Redis 실습 경로**로 재구성하되, 원본의 **tool 3종과 파라미터 컨벤션은 충실히 보존**한다.

**비목표(범위 외)**: supervisor / AgentCore Runtime / A2A 실제 배포(구조만 승격 가능하게), Slack 연동, SRE/cost 에이전트(별도 spec).

---

## 2. 핵심 결정 요약

| 항목 | 결정 | 근거 |
|---|---|---|
| 이름 | 표시명 `db-query-analysis-agent` / 패키지 `db_query_analysis_agent` | 원본 config 일치, Python 식별자 제약 |
| 실행 고도 | 로컬 우선, AWS 의존 = Bedrock(LLM)뿐 | "가벼운 + mockup + 실습" |
| 에이전트 구성 | **단일 에이전트 (Stage 1 supervisor 없음)** + tool 3종 충실 | 단일 sub-agent supervisor는 순수 오버헤드; 베이스 코드도 "단일=supervisor 없음" |
| 타 에이전트/Slack 호출 | **clean callable facade** (`review_sql(sql)`) — 호출자가 wrapping | supervisor는 호출자 측 관심사, A2A 단계로 연기 |
| 메타데이터 | mock 먼저 → Redis (`META_BACKEND` swap, 응답 shape 동형) | 베이스 코드 storage backend 추상화 패턴 |
| 대화 | db-query 에이전트 자체가 멀티턴 챗 + 단발, 스트리밍 | Strands Agent가 `agent.messages`/`stream_async` 네이티브 제공 |
| 모델 | Bedrock Haiku 4.5, `temperature 0.1` + `max_tokens` 명시 (env 조정) | 원본 충실 (1차 리뷰 = 경량·결정적) |
| planner/executor/summarizer | **Strands 단일 tool-use loop로 통합** (sub-agent 아님) | 원본 셋 다 동일 Haiku 4.5 = hermes phase loop; Strands가 plan→execute→summarize native 수행 |
| LLM 호출 방식 | **Strands `BedrockModel`로 일원화** (`analyze_sql_with_llm`도 raw boto3 converse 미사용) | base code 패턴 일관, boto3/Strands 혼용 제거 |
| 승격 경로 | `shared/agent.py` 단일 `create_agent()` 팩토리 유지 | 추후 AgentCore Runtime / A2A 승격 |

> **설계 핵심**: db-query-analysis-agent를 **깨끗한 callee(호출 가능한 단위)**로 설계한다. supervisor/오케스트레이션은 **호출하는 쪽**의 책임이며, 멀티에이전트(SRE/cost 합류) 또는 A2A 노출 시점에 도입한다. ("For A2A, it is fine.")

---

## 3. 아키텍처

로컬 우선, 단일 에이전트. 베이스 코드 `shared/agent.py` 단일 팩토리 패턴 유지(향후 AgentCore 승격 대비).

```
┌─ 클라이언트 (Stage 1 = CLI) ───────────────────────────────────────┐
│  대화형:  uv run -m agents.db_query_analysis_agent.local.chat        │
│  단발:    uv run -m agents.db_query_analysis_agent.local.run --sql ".."│
│                                                                      │
│  (향후) Slack /db-query-review · 타 에이전트 · A2A wrapper            │
│         → 모두 동일 callable facade review_sql(sql) 호출             │
└────────────────────────────┬─────────────────────────────────────────┘
                             ▼  (in-process)
┌─ db-query-analysis-agent (Strands, LLM) ───────────────────────────┐
│   멀티턴 대화 유지 (agent.messages + SlidingWindowConversationManager) │
│   streaming (stream_async) + 3-layer prompt cache                    │
│   system_prompt: tool 3종 각 1회 순차 호출 → 한국어 리뷰 작성           │
│     ① check_sql_rules(sql)      → 규칙 기반 정적 분석 (순수 함수)        │
│     ② get_table_meta(sql)       → 테이블 메타 조회 (META_BACKEND)       │
│     ③ analyze_sql_with_llm(...) → 별도 Bedrock 호출 (심층 분석)         │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
        ┌─ meta backend (swap, 응답 shape 동형) ──────────┐
        │  META_BACKEND=mock  → data/mock/table_meta.py   │ ← Stage 1 (기본)
        │  META_BACKEND=redis → Redis (seed_redis.py 적재) │ ← Stage 2
        └─────────────────────────────────────────────────┘
```

---

## 4. 디렉토리 구조

```
db-query-analysis-agent/
├── CLAUDE.md                        # (기존) base code + reference 기록
├── pyproject.toml                   # strands-agents, boto3, python-dotenv; [redis], [dev] extras
├── .env.example
├── design/                          # (기존) requirement.md, prd.md, 본 spec
├── agents/
│   ├── __init__.py
│   └── db_query_analysis_agent/
│       ├── __init__.py
│       ├── shared/
│       │   ├── __init__.py
│       │   ├── agent.py             # create_agent(tools, prompt) + build_db_query_agent()
│       │   ├── review.py            # review_sql(sql) -> str  (clean callable facade)
│       │   ├── prompts/system_prompt.md
│       │   └── tools/
│       │       ├── __init__.py
│       │       ├── check_sql_rules.py
│       │       ├── get_table_meta.py
│       │       └── analyze_sql_with_llm.py
│       ├── meta/                    # 메타 backend 추상화
│       │   ├── __init__.py          # lookup_table_meta(table) — META_BACKEND 분기
│       │   ├── mock_backend.py
│       │   └── redis_backend.py     # redis lazy-import
│       └── local/
│           ├── __init__.py
│           ├── chat.py              # 멀티턴 대화 REPL (메인 진입점)
│           └── run.py               # 단발 (--sql)
├── shared/
│   ├── __init__.py
│   └── streaming.py                 # stream_response() + 토큰 usage 출력 (공통)
├── data/
│   ├── __init__.py
│   ├── schema.sql                   # SQLite DDL — 사람이 읽는 구조 참조 (+ 향후 sample.db)
│   ├── mock/
│   │   ├── __init__.py
│   │   └── table_meta.py            # 런타임 메타 source of truth (TABLE_META dict)
│   └── seed/
│       ├── build_sqlite.py          # schema.sql → sample.db (옵션, 향후 EXPLAIN)
│       └── seed_redis.py            # TABLE_META → Redis 적재 (Stage 2)
└── tests/
    ├── __init__.py
    ├── test_check_sql_rules.py
    ├── test_get_table_meta.py
    └── test_meta_backend_parity.py
```

---

## 5. 컴포넌트 상세

### 5.1 db-query-analysis-agent 팩토리 (`shared/agent.py`)

베이스 코드와 동일 시그니처. tool은 caller가 주입(phase-agnostic) + 표준 조립 헬퍼 제공.

```python
def create_agent(tools: list, system_prompt_filename: str) -> Agent:
    model_id = os.environ.get("DBQUERY_MODEL_ID") or "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    region = os.environ.get("AWS_REGION") or "us-east-1"
    # BedrockModel(model_id, region_name, cache_tools="default",
    #              temperature=0.1, max_tokens=4096)   ← 원본 temp 0.1 보존 + 리뷰 길이 여유
    # system_prompt=[text, cachePoint]
    # callback_handler=null_callback_handler
    # agent.name = "db-query-analysis-agent"; agent.description = 원본 description
    # conversation_manager=SlidingWindowConversationManager(window_size=20)

def build_db_query_agent() -> Agent:
    """표준 db-query 에이전트 조립 — tool 3종 + system_prompt.md."""
    return create_agent(
        tools=[check_sql_rules, get_table_meta, analyze_sql_with_llm],
        system_prompt_filename="system_prompt.md",
    )
```

- 프롬프트 캐싱 Layer 1(`cache_tools`) + Layer 2(`cachePoint`) + (멀티턴) Layer 3(턴 경계).
- `create_agent`는 향후 AgentCore 승격 시 그대로 재사용(베이스 코드 C1 단일 truth).
- **planner/executor/summarizer 매핑**: 원본 셋은 모두 동일 Haiku 4.5(temp 0.1)인 hermes phase loop. Strands tool-use loop가 plan→execute→summarize를 단일 모델로 수행 → 단일 `create_agent`로 통합. `temperature=0.1`·`max_tokens`는 BedrockModel에 명시 보존.
- **`build_bedrock_model()` 공통 헬퍼**: `BedrockModel` 구성(model_id/region/temperature/max_tokens)을 한 곳에서 — `create_agent`(메인 에이전트)와 `analyze_sql_with_llm`(분석 호출)이 동일 헬퍼 사용 → Bedrock 호출 방식 단일화(raw boto3 혼용 0).

### 5.2 Callable facade (`shared/review.py`)

"타 에이전트 호출 / Slack adapter / 향후 A2A wrapper"의 **공통 호출 표면**. 단발·stateless.

```python
async def review_sql(sql: str) -> str:
    """단발 SQL 리뷰. supervisor 없이 누구나 부르는 깨끗한 callee 표면."""
    agent = build_db_query_agent()
    result = await agent.invoke_async(f"다음 SQL을 리뷰해줘:\n```sql\n{sql}\n```")
    return str(result)
```

### 5.3 Tool 3종 — 파라미터 키 컨벤션 엄수

원본 PRD의 strict 규칙: **모든 tool은 `sql` 키 사용** ("query"/"table_name" 금지).

**① `check_sql_rules(sql: str) -> dict`** — 순수 함수, I/O 없음.
- 탐지: `DELETE_WITHOUT_WHERE`, `UPDATE_WITHOUT_WHERE`, `DROP`, `TRUNCATE`, `SELECT_STAR`, `LIKE_LEADING_WILDCARD`.
- severity: `critical`(DROP/TRUNCATE/DELETE·UPDATE without WHERE), `warning`(SELECT *, LIKE leading wildcard).
- 반환: `{"violations": [{"rule": str, "severity": str, "message": str}], "checked_rules": [str, ...]}`
- 구현: 정규식 + 경량 토큰화. WHERE 탐지는 주석/문자열 리터럴 오탐 최소화(대소문자 무시, 단어 경계).

**② `get_table_meta(sql: str) -> dict`** — SQL에서 테이블명 추출 → `lookup_table_meta()` 호출.
- 반환:
  ```json
  {"tables": [{"name": "orders", "found": true,
               "columns": [{"name":"id","type":"INTEGER"}, ...],
               "indexes": [{"name":"idx_orders_user_id","columns":["user_id"],"unique":false}, ...],
               "row_count": 5000000, "large_table": true}],
   "backend": "mock", "large_table_threshold": 1000000}
  ```
- `large_table` = `row_count > LARGE_TABLE_THRESHOLD`(기본 1,000,000).
- 미존재 테이블: `{"name": "x", "found": false}` (크래시 금지).
- 테이블 추출: FROM/JOIN/UPDATE/INTO/TABLE 다음 식별자 파싱(별칭/백틱/스키마 접두 처리).

**③ `analyze_sql_with_llm(sql: str, violations_json: str, meta_summary: str = "") -> dict`** — 별도 LLM 호출 (plain @tool).
- **LLM 호출은 base code 패턴(Strands `BedrockModel`)으로** — raw boto3 converse 대신 `build_bedrock_model()` 헬퍼로 `BedrockModel(ANALYZE_MODEL_ID, temperature=0.1, max_tokens=2048)` 생성 후 **`BedrockModel`을 직접 호출**(`model.stream`, Agent 미생성) 1회 호출. region/모델 구성이 메인 에이전트와 일관.
- (주: analyze는 plain tool이며 **시스템 내 Agent 객체는 메인 db-query 하나뿐** — analyze는 BedrockModel을 직접 호출. "단일 에이전트 + plain tool 3종" 결정과 완전 일치.)
- 분석: 인덱스 효율, 서비스 영향도, 최적화 제안. **이미 ①에서 플래그된 항목 재언급 금지**(프롬프트 강제).
- 반환: `{"index_efficiency": str, "service_impact": str, "optimizations": [str], "analysis": str}` (analyzer가 JSON 섹션 생성 → 파싱; 파싱 실패 시 raw 텍스트를 `analysis`에 보존).
- 실패 시: `{"error": "...", "analysis": ""}` (에이전트 크래시 금지).

### 5.4 메타 backend 추상화 (`meta/`)

```python
# meta/__init__.py
def lookup_table_meta(table_name: str) -> dict | None:
    """META_BACKEND(mock|redis) 분기. 반환 shape 두 backend 동형. 미존재 시 None."""
```
- `META_BACKEND=mock`(기본): `data/mock/table_meta.py`의 `TABLE_META[table_name]` 반환.
- `META_BACKEND=redis`: `redis_backend.py`가 `tablemeta:{name}` 키의 JSON 읽기(redis lazy-import). 연결 실패 시 한국어 에러.
- **단일 진실 원천 = `TABLE_META` dict.** `seed_redis.py`가 이를 Redis에 적재 → 두 backend 동일 데이터.

---

## 6. 진입점

| 진입점 | 명령 / 호출 | 동작 |
|---|---|---|
| 대화형 (메인) | `uv run -m agents.db_query_analysis_agent.local.chat` | 멀티턴 REPL. `agent.messages` 누적, `/reset`·`/quit`, 스트리밍 |
| 단발 (CLI) | `uv run -m agents.db_query_analysis_agent.local.run --sql "<SQL>"` | 1회 실행 후 종료, 스트리밍 |
| 프로그램 호출 | `from agents.db_query_analysis_agent.shared.review import review_sql` | 타 에이전트/Slack adapter/A2A wrapper 공통 표면 |

`chat.py`는 developer-briefing 패턴: 같은 Agent 재사용, `SlidingWindowConversationManager(window_size=20)`, `shared/streaming.py`의 `stream_response()` 공통 헬퍼.

---

## 7. 데이터 흐름

**단발 예시**
```
"이 쿼리 리뷰해줘: DELETE FROM orders"   (또는 review_sql("DELETE FROM orders"))
 → db-query-analysis-agent:
     ① check_sql_rules → [{"rule":"DELETE_WITHOUT_WHERE","severity":"critical"}]
     ② get_table_meta  → orders: 5,000,000행(large_table=true), idx(user_id,created_at)
     ③ analyze_sql_with_llm(sql, violations, meta) → "전체 행 삭제 위험 + 대형 테이블 락 영향..."
     → 한국어 통합 리뷰 (스트리밍 출력)
```

**멀티턴 예시**
```
> 이 쿼리 리뷰해줘: SELECT * FROM orders WHERE user_id = 1
  (tool 3종 호출 → SELECT * 경고 + user_id 인덱스 분석)
> 그럼 어떤 컬럼만 선택하는 게 좋아?
  (이전 리뷰가 대화 컨텍스트에 있음 → 에이전트가 직접 답, tool 재호출 불필요)
> created_at 으로 정렬도 추가하면?
  (필요 시 tool 재호출하여 재분석)
```

---

## 8. 모델 & 설정 (`.env.example`)

| 변수 | 기본값 | 비고 |
|---|---|---|
| `AWS_REGION` | `us-east-1` | 원본은 us-east-2 — env로 조정 |
| `DBQUERY_MODEL_ID` | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | 원본 충실 |
| `DBQUERY_TEMPERATURE` | `0.1` | 원본 충실 (결정적 리뷰) |
| `DBQUERY_MAX_TOKENS` | `4096` | 최종 리뷰 길이 (원본 summarizer 2048 대비 여유) |
| `ANALYZE_MODEL_ID` | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | analyze tool 내부 |
| `ANALYZE_TEMPERATURE` | `0.1` | 결정적 분석 |
| `ANALYZE_MAX_TOKENS` | `2048` | 심층 분석 출력 |
| `META_BACKEND` | `mock` | `mock` \| `redis` |
| `REDIS_URL` | `redis://localhost:6379/0` | Stage 2 |
| `LARGE_TABLE_THRESHOLD` | `1000000` | 원본 defaults |

**원본 planner/executor/summarizer → Strands 단일 루프**: 원본 셋은 모두 동일 Haiku 4.5(temp 0.1)였고 hermes의 phase loop(plan→execute→summarize)였음. Strands `Agent`의 tool-use loop가 이를 단일 모델로 대체하므로 phase별 모델 분리는 두지 않음. 원본의 phase별 `max_tokens`(2048/1024/2048) 차이는 단일 `DBQUERY_MAX_TOKENS`로 통합.

원본의 `max_calls:1`(tool당 1회)·`max_loops:10`은 시스템 프롬프트 지침으로 재현(각 tool 1회 순차 호출). 필요 시 안전장치로 에이전트 최대 반복 제한.

---

## 9. 샘플 데이터

`data/mock/table_meta.py`의 `TABLE_META` dict가 런타임 진실 원천(손으로 작성, 베이스 코드 mock 패턴). 행수 stats 포함 → **실제 대량 행 없이 large-table 데모**. `schema.sql`은 동일 구조 DDL(사람이 읽는 참조 + 향후 `sample.db`/EXPLAIN).

샘플 스키마(e-commerce):

| 테이블 | 인덱스 | 행수(mock) | 데모 포인트 |
|---|---|---|---|
| `users` | PK id, UNIQUE email | 1,000 | 소형 |
| `orders` | PK id, idx user_id/created_at | 5,000,000 | **large** |
| `products` | PK id, UNIQUE sku, idx category | 50,000 | 중형 |
| `order_items` | PK id, idx order_id/product_id | 12,000,000 | **large** |
| `audit_log` | (인덱스 없음) | 8,000,000 | **large + no-index 경고** |

(옵션) drift 방지용 일관성 테스트: `schema.sql` 파싱 결과와 `TABLE_META` 구조 비교.

---

## 10. 스트리밍 & 대화 (developer-briefing 패턴)

- **스트리밍**: `shared/streaming.py`의 `stream_response(agent, prompt)` — `async for event in agent.stream_async(prompt)` → `event["data"]` `flush=True` 출력 + `metadata.usage` 누적 표시.
- **멀티턴**: `chat.py`가 같은 Agent 재사용 → `agent.messages` 자동 누적.
- **`SlidingWindowConversationManager(window_size=20)`**: 장기 대화 토큰 안정 + 캐시 prefix 유지.
- **3-layer caching**: ①`cache_tools` ②system_prompt `cachePoint` ③(멀티턴) 턴 경계 cachePoint.

---

## 11. 에러 처리

| 지점 | 처리 |
|---|---|
| `check_sql_rules` | 파싱 실패 → 빈 violations + note (크래시 금지) |
| `get_table_meta` | 테이블 추출 실패 → 빈 결과 + note / 미존재 → `found:false` / Redis 연결 실패 → 한국어 에러 |
| `analyze_sql_with_llm` | Bedrock 실패 → `{"error":...}` 반환(크래시 금지) |
| `review_sql` facade | 내부 예외 → 호출자에게 구조화된 에러 문자열(타 에이전트가 파싱 가능) |
| 파라미터 키 | tool 시그니처는 `sql` 정확히 — 위반 시 즉시 실패(컨벤션 보존) |

---

## 12. 테스트 전략 (TDD)

- **순수/결정적 → TDD 우선 (구현 전 테스트 작성)**:
  - `test_check_sql_rules.py`: 규칙별 positive/negative (DELETE/UPDATE without WHERE, DROP, TRUNCATE, SELECT *, LIKE leading wildcard; WHERE 있는 안전 케이스).
  - `test_get_table_meta.py`: 테이블 추출(FROM/JOIN/UPDATE/별칭), large_table 플래그, 미존재 테이블.
  - `test_meta_backend_parity.py`: mock↔redis 동일 shape(redis 없으면 skip).
- **LLM 의존**: `analyze_sql_with_llm`·에이전트 e2e·`review_sql`은 Strands `BedrockModel`/Bedrock 응답 mock 단위 테스트 + 자격증명 게이트 통합 테스트.

---

## 13. 빌드 단계 & 향후 승격

- **Stage 1 (이번 핵심)**: `META_BACKEND=mock`으로 db-query-analysis-agent + tool 3종 + 대화형/단발/스트리밍 + callable facade 전체 동작. 외부 인프라 0.
- **Stage 2 (후속)**: `docker run redis` + `seed_redis.py` + `META_BACKEND=redis` → 동일 동작(코드 변경 0).
- **A2A / 멀티에이전트 단계 (범위 외)**:
  1. db-query를 **A2A server**로 노출(callee) — `create_agent` 팩토리 + `review_sql` facade 그대로 재사용.
  2. SRE/cost 합류 시 **supervisor(A2A)** 도입 — supervisor는 **호출자 측**에서 db-query를 `@tool`로 wrap(베이스 코드 Phase 5 패턴).

---

## 14. 미해결/확인 필요

- 없음(핵심 결정 완료). 구현 중 세부(정규식 규칙 경계, converse 프롬프트 문안)는 TDD로 확정.
```
