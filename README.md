# Workshop: db-query-analysis-agent

SQLite/SQL 쿼리 **1차 리뷰 에이전트** (Strands Agents + AWS Bedrock) — 규칙 기반 체크 +
테이블 메타 조회 + LLM 심층 분석을 한 번에. **로컬(in-process)** 또는 **관리형 AgentCore
Runtime**으로 동일 코드를 실행하며, 도구 공급원은 `TOOLS_SOURCE`로 전환합니다.
설계: `design/2026-06-03-db-query-analysis-agent-migration-spec.md`.

> **왜 이렇게 — 단일 에이전트 + TOOLS_SOURCE 스위치**
>
> - **단일 Strands 에이전트** + plain tool 3종(`check_sql_rules` · `get_table_meta` ·
> `analyze_sql_with_llm`). planner/executor/summarizer를 별도 sub-agent로 두지 않고
> **단일 tool-use 루프**가 흡수합니다(원본이 모두 같은 Haiku 루프 단계였음). supervisor는
> SRE·cost 에이전트가 합류하는 A2A 단계까지 의도적으로 보류.
> - `**create_agent()` 단일 truth** — 로컬과 관리형 Runtime이 같은 팩토리를 씁니다(코드 분기 0).
> - `**TOOLS_SOURCE` 스위치** — 같은 에이전트가 도구를 in-process(`@tool`)로 쓰거나
> Cognito-보안 Gateway(MCP)로 쓰도록 **환경변수 하나로 전환**. 오프라인 회귀와 관리형
> 배포가 같은 코드 경로를 공유합니다.

---

## 1. 시나리오 — "위험한 SQL 1차 리뷰"

운영자가 리뷰가 필요한 SQL을 던지면, 단일 에이전트가 도구 3종을 순서대로 호출해 **통합 리뷰**를 스트리밍합니다.

```
입력   uv run -m …local.run --sql "SELECT * FROM orders WHERE user_id = 1"
         │
         ▼  단일 Strands 에이전트 (tool-use 루프)
   ① check_sql_rules     규칙 정적 분석   → SELECT * 경고(불필요 컬럼 로드)
   ② get_table_meta      메타 조회        → orders = 500만 행 large_table, idx_orders_user_id 보유
   ③ analyze_sql_with_llm 실제 EXPLAIN     → idx_orders_user_id 활용 확인 + 최적화 제안(컬럼 명시·페이징)
         │
         ▼
   통합 리뷰 (한국어 스트리밍) — 규칙 위반 + 인덱스 효율 + 서비스 영향도 + 권장 SQL
```

`DELETE FROM orders`(WHERE 없음)처럼 위험한 문장은 `critical`로, `SELECT *`·`LIKE '%…'`는
`warning`으로 즉시 표면화됩니다. 멀티턴 chat에서는 "방금 쿼리에서 가장 큰 문제 하나만"처럼
직전 맥락을 이어 물을 수 있습니다(warm 세션).

---

## 2. 여정 요약 — phase 별 진화


| Phase       | 추가되는 layer                             | 핵심 학습 / 목표                               | 상태  |
| ----------- | -------------------------------------- | ---------------------------------------- | --- |
| **Stage 1** | 로컬 단일 Strands 에이전트 + plain tool 3종     | Bedrock 단일 의존, `create_agent()` 단일 truth | ✅   |
| **Phase 1** | SQLite 백엔드 + `EXPLAIN QUERY PLAN` 그라운딩 | `META_BACKEND` 스위치, 단일 출처 `TABLE_META`   | ✅   |
| **Phase 2** | AgentCore Runtime (SigV4 + SSE)        | 로컬 코드를 **그대로 관리형 승격**, warm 세션           | ✅   |
| **Phase 3** | Cognito + AgentCore Gateway (MCP)      | 도구 외부화, `TOOLS_SOURCE` 스위치, M2M JWT      | ✅   |
| (다음)        | Redis 캐시                               | 배포 Runtime 인스턴스 간 공유·갱신 메타 캐시            | 🚧  |
| (다음)        | A2A + Supervisor                       | SRE·cost 에이전트 합류 시 multi-agent           | 🚧  |


**Stage 1 · Phase 1 · Phase 2 · Phase 3 완료** (모두 `main`).

**phase별 주요 폴더·코드** (`agt/` = `agents/db_query_analysis_agent/`):

- **Stage 1** (로컬 단일 에이전트 + tool 3종) — `agt/shared/`(`agent.py` 단일 `create_agent` 팩토리 · `model.py` `build_bedrock_model` · `review.py` `review_sql` facade · `prompts/`), `agt/tools/`(strands-free 코어 `check_sql_rules`·`get_table_meta`·`analyze_sql_with_llm` + `strands_tools.py` @tool 래퍼), `agt/local/`(`run.py`·`chat.py`), `agt/meta/mock_backend.py`, `data/mock/table_meta.py`(`TABLE_META`), repo-root `shared/config.py`·`streaming.py`
- **Phase 1** (SQLite + EXPLAIN) — `agt/meta/`(`__init__.py` `META_BACKEND` 분기 · `sqlite_backend.py`), `data/seed/build_sqlite.py`(`TABLE_META`→`sample.db`), `agt/tools/analyze_sql_with_llm.py`의 `run_explain`(read-only `EXPLAIN QUERY PLAN` 그라운딩)
- **Phase 2** (AgentCore Runtime) — `agt/runtime/`(`agentcore_runtime.py` entrypoint·SSE·세션캐시 / `deploy_runtime.py` / `invoke_runtime.py` / `chat.py` / `_remote.py` SigV4 / `teardown.sh` / `Dockerfile` / `requirements.txt`), repo-root `shared/repl.py`(멀티라인 입력)
- **Phase 3** (Cognito + Gateway) — `infra/cognito-gateway/`(`cognito.yaml` CFN · `setup_gateway.py` · `deploy.sh` · `teardown.sh` · `cleanup_gateway.py` + `lambda/{check_sql_rules,get_table_meta,analyze_sql_with_llm}/handler.py`), `agt/shared/gateway.py`(토큰·MCP client), `agt/shared/agent.py`의 `agent_session` gateway 분기(`TOOLS_SOURCE`), `agt/tools/strands_tools.py`(strands-free 코어↔@tool 분리), `agt/runtime/deploy_runtime.py`의 `attach_oauth_provider`(OAuth2 provider)
- **(다음) Redis 캐시** — `agt/meta/redis_backend.py`, `data/seed/seed_redis.py`
- **(다음) A2A + Supervisor** — 예정 (supervisor + A2A Runtime)

---

## 3. 아키텍처

`TOOLS_SOURCE` 스위치를 중심으로 한 호출 경로. 같은 단일 에이전트가 in-process 도구 또는
Gateway 경유 도구를 소비합니다.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Operator (Human)                                                     │
│    ├─ 로컬:  local.run / local.chat                                   │
│    └─ 원격:  invoke_runtime / chat  ── SigV4 (IAM) ──┐                │
└──────────────────────────────────────────────────────┼───────────────┘
                                                        ▼
                          ┌───────────────────────────────────────────┐
                          │  AgentCore Runtime — 단일 리뷰 에이전트    │  (Phase 2)
                          │   Strands Agent + BedrockModel + SSE        │
                          │   session 캐시 → warm 멀티턴                │
                          └─────────────────────┬───────────────────────┘
                                                ▼
   ╔══════════════════════════  TOOLS_SOURCE  ══════════════════════════╗
   ║  inprocess (기본)                              gateway              ║
   ╚═════════╤════════════════════════════════════════╤═════════════════╝
             ▼                                         ▼  Cognito M2M JWT
   ┌──────────────────────┐            ┌──────────────────────────────────┐
   │ in-process @tool 3종 │            │ AgentCore Gateway (MCP · 3 target)│  (Phase 3)
   │  check_sql_rules     │            │  ├ check-sql-rules  → Lambda      │
   │  get_table_meta      │            │  ├ get-table-meta   → Lambda      │
   │  analyze_sql_with_llm│            │  └ analyze-sql…     → Lambda      │
   └──────────┬───────────┘            └─────────────────┬─────────────────┘
              │                                           │
              └─────────────────────┬─────────────────────┘
                                    ▼
                         Amazon Bedrock (Sonnet 4.6)
                         analyze 도구의 LLM 심층 분석 + EXPLAIN 그라운딩

  ─── 공통 인프라 ──────────────────────────────────────────────────────
   Cognito UserPool + M2M Client (Phase 3) → JWT → Gateway authorizer 3단 검증
   AgentCore Identity OAuth2 provider (Phase 3) → Runtime workload identity → M2M 토큰
   META_BACKEND (mock | sqlite | redis) → 테이블 메타 (단일 출처 TABLE_META)
```


| `TOOLS_SOURCE`   | 도구 경로                                     | 의존                                              |
| ---------------- | ----------------------------------------- | ----------------------------------------------- |
| `inprocess` (기본) | in-process `@tool` 3종                     | Cognito/Gateway 불필요 — 오프라인 동작                   |
| `gateway`        | Cognito-보안 Gateway(MCP) → per-tool Lambda | Phase 3 인프라 (`infra/cognito-gateway/deploy.sh`) |


---

## 4. 핵심 기술

**Strands SDK**

- **단일 에이전트 tool-use 루프** *(Stage 1)* — planner/executor/summarizer를 단일 모델 루프로 흡수
- `**BedrockModel` 공유 헬퍼** *(Stage 1)* — 모든 LLM 호출이 동일 경로(raw boto3 converse 0)
- `**MCPClient` + streamable HTTP** *(Phase 3)* — Gateway 도구를 Bearer JWT로 소비
- `**stream_async` → SSE** *(Phase 2)* — `agent_text_stream` / `token_usage` / `workflow_complete`

**AWS Bedrock AgentCore**

- **Runtime** *(Phase 2)* — CodeBuild ARM64 원격 빌드(로컬 Docker 불필요), SigV4 인바운드, microVM/세션
- **Gateway** *(Phase 3)* — MCP 도구 외부화 + `CUSTOM_JWT` 3단 검증(서명·audience·scope), `<target>___<tool>` 네임스페이스
- **Identity** *(Phase 3)* — OAuth2 provider + `@requires_access_token`(workload identity → Cognito M2M)

**스위치 / 추상화**

- `**TOOLS_SOURCE`** *(Phase 3)* — `inprocess` | `gateway` 도구 공급원 전환
- `**META_BACKEND`** *(Phase 1)* — `mock` | `sqlite` | `redis`, 응답 shape 동형(단일 출처 `TABLE_META`)

**패키징 (Phase 3)**

- **strands-free 코어 ↔ `@tool` 래퍼 분리** — check/meta 핵심 로직은 strands 의존 0 → Lambda 경량
- **per-tool Lambda (1 tool/Lambda)** — 이질적 프로필 분리(check/meta 경량 vs analyze=strands+Bedrock) + 최소권한 IAM
- **cp312 휠 강제** — `--platform manylinux2014_x86_64 --python-version 3.12`로 Lambda ABI 일치(빌드호스트 python 무관)

**성능 + 멀티턴**

- **Prompt caching** *(Phase 2+)* — `cache_tools="default"` + `SystemContentBlock` cachePoint
- **Warm 세션** *(Phase 2+)* — `runtimeSessionId` 재사용으로 같은 microVM/에이전트 재사용(inprocess·gateway 양쪽)

---

## 5. 사전 요구사항

- **AWS 계정** — `us-east-1` (Bedrock + AgentCore 가용성)
- **Bedrock 모델 액세스** — Anthropic Claude Sonnet 4.6 (`global.anthropic.claude-sonnet-4-6`)
- **IAM 권한** — CFN / IAM / Lambda / Cognito / Bedrock / AgentCore / ECR 자원 생성. **데모 단순화용** — 운영은 least-privilege 권장.
- **Python 3.12+** + `[uv](https://github.com/astral-sh/uv)`
- **Docker 불필요** — Runtime은 CodeBuild ARM64로 원격 빌드(로컬 빌드 없음)
- **단위 테스트는 AWS 자격증명 불필요** — LLM/네트워크 전부 mock (오프라인 통과가 정상)

---

## 6. 폴더 구조

```
db-query-analysis-agent/
├── agents/db_query_analysis_agent/
│   ├── local/              # 로컬 진입점 (run 단발 / chat 멀티턴)
│   ├── runtime/            # AgentCore Runtime (entrypoint·deploy·invoke·chat·teardown)
│   ├── shared/             # 단일 truth: agent·model·gateway·review·repl + prompts/
│   ├── tools/              # 도구 3종: strands-free 코어 + @tool 래퍼(strands_tools)
│   └── meta/               # META_BACKEND 분기 (mock | sqlite | redis)
├── infra/cognito-gateway/  # Phase 3: CFN(cognito.yaml) + setup_gateway + deploy/teardown
│   └── lambda/             #   per-tool Lambda 핸들러 3종 (handler.py만 추적, 나머지는 빌드 산출물)
├── data/
│   ├── mock/               # 단일 출처 TABLE_META
│   └── seed/               # sample.db 빌드 / redis seed
├── design/                 # phase별 spec·plan (의사결정 로그)
├── tests/                  # 95 단위 테스트 (오프라인)
├── shared/                 # repo-root 공통 (config 등)
├── bootstrap.sh            # uv sync + .env + 테스트
├── pyproject.toml / uv.lock
└── README.md
```

---

## 7. 실행 & 검증 — 쉬운 것부터 단계별

하나의 사다리로 **0 준비 → 2 로컬 → 3 원격 → 4 게이트웨이** 순서. 위로 갈수록 AWS
의존이 커지고, 각 단계는 **배포 → 검증 → 정리**까지 독립적으로 실행할 수 있습니다.


| 단계              | 무엇                                | AWS            | 핵심 명령                               |
| --------------- | --------------------------------- | -------------- | ----------------------------------- |
| **0 준비**        | uv sync + `.env`                  | 단위는 불필요        | `bash bootstrap.sh`                 |
| **2 로컬 실행/e2e** | 실제 Bedrock, `META_BACKEND`        | ✅ 자격증명 + 모델    | `local.run` / `local.chat`          |
| **3 원격 e2e**    | Runtime 배포 (SigV4·SSE·warm)       | ✅ + 배포         | `deploy_runtime` → `invoke_runtime` |
| **4 게이트웨이 e2e** | Cognito + Gateway (MCP) · Phase 3 | ✅ + CFN/Lambda | `deploy.sh` → gateway invoke        |


> **모델**: 로컬/원격 e2e는 Bedrock **Sonnet 4.6**(`global.anthropic.claude-sonnet-4-6`) 액세스가 필요합니다.
>
> **env 명시**: 아래 명령은 `TOOLS_SOURCE`(`inprocess`|`gateway`)·`META_BACKEND`(`mock`|`sqlite`)를 **항상 명시**합니다 — 기본값(`deploy_runtime`은 `gateway`, 그 외 `inprocess`)에 의존하면 헷갈리기 쉽습니다. 배포된 Runtime을 호출하는 invoke/chat 클라이언트는 모드가 배포 시 고정돼 env가 불필요합니다.

### 0. 준비 (모든 단계 공통)

```bash
bash bootstrap.sh            # uv sync --extra dev + .env 생성 + AWS_REGION/DEMO_USER + (가능하면) 단위 테스트
# 또는 수동:
uv sync --extra dev          # pytest·ruff·agentcore 툴킷 포함
```

- **1 단위 테스트**: AWS 자격증명 **불필요** — LLM/네트워크가 전부 mock입니다.
- **2~4 e2e**: AWS 자격증명(`aws configure` 또는 환경변수) + Bedrock Sonnet 4.6 모델 액세스 필요.

### 2. 로컬 실행 & e2e (실제 Bedrock)

AWS 자격증명 + 모델 액세스가 필요합니다. `META_BACKEND`로 메타데이터 출처를 바꿔가며 검증합니다.

```bash
# 단발 — 규칙 위반 탐지 확인 (in-process 도구 + mock 메타)
TOOLS_SOURCE=inprocess META_BACKEND=mock \
  uv run -m agents.db_query_analysis_agent.local.run --sql "DELETE FROM orders"

# SQLite 백엔드 — TABLE_META로 빌드한 sample.db에 실제 EXPLAIN QUERY PLAN(read-only) 그라운딩
TOOLS_SOURCE=inprocess META_BACKEND=sqlite \
  uv run -m agents.db_query_analysis_agent.local.run --sql "SELECT * FROM orders WHERE user_id = 1"

# 대화형 (멀티턴) — mock 메타
TOOLS_SOURCE=inprocess META_BACKEND=mock \
  uv run -m agents.db_query_analysis_agent.local.chat

# 대화형 (멀티턴) — sqlite 메타 (실제 EXPLAIN QUERY PLAN 그라운딩)
TOOLS_SOURCE=inprocess META_BACKEND=sqlite \
  uv run -m agents.db_query_analysis_agent.local.chat
```

**멀티턴 chat 예시** — `local.chat` 실행 후 한 세션에서 순서대로 입력. 각 입력은 **빈 줄(Enter)** 로 전송, `/reset` 새 세션, `/quit` 종료. 턴 2~4는 일부러 직전 내용을 다시 적지 않아 warm 세션(로컬은 한 `agent_session` 안에서 agent 재사용)을 검증합니다:

```text
> DELETE FROM orders
  → (턴1) WHERE 없는 DELETE = critical 경고 + 전체 행 삭제 위험 설명

> 그럼 안전하게 고치려면?
  → (턴2) "그" = 턴1의 DELETE — WHERE 조건·트랜잭션·백업 등 제안 (SQL 재입력 없이 맥락 참조)

> SELECT * FROM orders WHERE user_id = 1 는 어때?
  → (턴3) 새 쿼리 — SELECT * 경고 + idx_orders_user_id 인덱스 효율 분석

> 방금 두 쿼리 중 더 위험한 건?
  → (턴4) 앞 대화 전체(턴1 DELETE vs 턴3 SELECT)를 비교 — 누적 맥락

> /quit
```

`**META_BACKEND**` — 테이블 메타데이터의 단일 출처는 `data/mock/table_meta.py`의 `TABLE_META`이며, 출처만 바뀌고 응답 형태는 동일합니다:

- `mock` (기본) — `TABLE_META`를 메모리에서 직접 제공. 파일·서버 의존 0.
- `sqlite` — `TABLE_META`로 빌드한 `sample.db`를 PRAGMA 조회 + 실제 `EXPLAIN QUERY PLAN`(read-only)으로 분석 그라운딩.
- `redis` (배포 단계 · optional) — 배포된 Runtime 인스턴스 간 공유·갱신 캐시.
  ```bash
  추후 개발 예정
  ```

**추후 확장시 (현재 스킵) : 프로그램에서 직접 호출** (facade):

```python
from agents.db_query_analysis_agent.shared.review import review_sql
text = await review_sql("SELECT * FROM orders WHERE user_id = 1")
```

확인 포인트: `WHERE` 없는 `DELETE`/`UPDATE` 경고, 인덱스 효율 분석, 한국어 리뷰 스트리밍.

### 3. 원격 e2e (AgentCore Runtime · Phase 2)

> 엔트리포인트는 **반드시 `-m`(모듈)** 로 실행하세요. 파일 경로(`uv run …/invoke_runtime.py`)로 실행하면 repo root가 `sys.path`에 없어 import가 실패합니다.

```bash
# 1) 배포 (Phase 2 = in-process 도구, Cognito/Gateway 불필요) — CodeBuild ARM64. → RUNTIME_*를 .env에 저장
TOOLS_SOURCE=inprocess META_BACKEND=mock \
  uv run python -m agents.db_query_analysis_agent.runtime.deploy_runtime

# 2) 단발 invoke (SigV4) — 배포된 Runtime 호출이라 클라이언트엔 env 불필요(모드는 배포 시 고정)
uv run python -m agents.db_query_analysis_agent.runtime.invoke_runtime \
  --query "SELECT * FROM orders WHERE user_id = 1"

# 3) 멀티턴 chat — 한 runtimeSessionId 를 재사용해 warm 세션
uv run python -m agents.db_query_analysis_agent.runtime.chat

```

**티턴 chat 예시** — 한 세션에서 순서대로 입력. 각 입력은 **빈 줄(Enter)** 로 전송, `/reset` 새 세션, `/quit` 종료. 턴 2~4는 일부러 직전 내용을 다시 적지 않아 warm 세션을 검증합니다:

```text
> SELECT * FROM orders WHERE user_id = 1
  → (턴1) 규칙 위반·인덱스 효율·최적화 전체 리뷰

> 방금 쿼리에서 가장 큰 문제 하나만 한 줄로 알려줘
  → (턴2) "방금 쿼리" = 턴1 — SQL 재입력 없이 맥락 참조

> 그 문제를 고친 SQL이랑 추가하면 좋은 인덱스를 같이 제안해줘
  → (턴3) "그 문제" = 턴2 답변 — 대화 누적 맥락

> orders 대신 users 테이블이었어도 같은 지적이야?
  → (턴4) 앞 대화 전체를 전제로 한 가정 질문

> /quit
```

확인 포인트:

- 단발 리뷰가 SSE로 실시간 스트리밍되는지.
- **턴 2~4가 직전 쿼리/답변을 추가 설명 없이 이해하면 warm 세션 정상** (같은 `runtimeSessionId` 재사용 → 컨테이너가 `agent.messages` 보존). 맥락을 잃거나 "어떤 쿼리?"를 되물으면 세션이 안 붙은 것.
- `/reset` 직후엔 새 세션 id라 맥락이 초기화(cold)됨.
- 세션 키는 `runtimeSessionId`(헤더) 우선 — payload 없이 헤더만으로도 세션이 격리됩니다.

### 4. 게이트웨이 e2e (Cognito + AgentCore Gateway · Phase 3)

SQL 도구를 Cognito-보안 Gateway(MCP)로 노출하고 Runtime이 `TOOLS_SOURCE=gateway`로 소비하는 경로. 인바운드는 그대로 SigV4, 아웃바운드(Runtime→Gateway)만 Cognito M2M JWT.

```bash
# 1) Cognito + Gateway + Lambda ×3 배포 (CFN + boto3). .env 에 COGNITO_*/GATEWAY_* 자동 기록.
bash infra/cognito-gateway/deploy.sh

# 2) 표준 MCP 클라이언트 검증 (SigV4 Runtime 무관 — Cognito JWT 직접):
uv run python -c "
from dotenv import load_dotenv; load_dotenv()
from agents.db_query_analysis_agent.shared.gateway import get_gateway_token, create_mcp_client
with create_mcp_client(get_gateway_token()) as m:
    print([t.tool_name for t in m.list_tools_sync()])
    print(m.call_tool_sync(tool_use_id='v', name='check-sql-rules___check_sql_rules', arguments={'sql':'SELECT * FROM orders'}))
"

# 3) Runtime 을 gateway 모드로 재배포 (OAuth2 provider 생성):
TOOLS_SOURCE=gateway META_BACKEND=mock \
  uv run python -m agents.db_query_analysis_agent.runtime.deploy_runtime

# 4) gateway 경유 invoke (Runtime → Cognito JWT → Gateway → Lambda) — 클라이언트엔 env 불필요:
uv run python -m agents.db_query_analysis_agent.runtime.invoke_runtime --query "SELECT * FROM orders WHERE user_id = 1"

# 5) 로컬에서도 gateway 모드(직접 토큰)로 호출 가능:
TOOLS_SOURCE=gateway META_BACKEND=mock \
  uv run -m agents.db_query_analysis_agent.local.run --sql "SELECT * FROM orders WHERE user_id = 1"
```

> 정리(teardown)는 **아래 chat 테스트까지 마친 뒤** 실행하세요 — 이 단계 끝에 있습니다.

확인 포인트:

- (2) `list_tools` 에 `check-sql-rules___check_sql_rules` · `get-table-meta___get_table_meta` · `analyze-sql-with-llm___analyze_sql_with_llm` 3개 노출 + 호출 시 `{"violations":[...]}` 반환.
- (4) gateway invoke 가 SigV4 invoke 와 동일한 리뷰를 스트리밍(차이는 도구가 Gateway·Lambda 경유라는 점뿐). `**runtimeUserId` 필수** — 없으면 workload identity 토큰 획득 실패.
- `TOOLS_SOURCE=inprocess`(기본)로 두면 Cognito/Gateway 없이 오프라인 동작(단계 2) — 회귀 확인용.
- analyze Lambda 는 `DB_PATH=/tmp` 로 EXPLAIN용 `sample.db` 를 빌드(읽기전용 `/var/task` 우회), 모델은 `ANALYZE_MODEL_ID`(CFN 파라미터, env-driven). 실패해도 graceful(분석은 정상).

**게이트웨이 chat (멀티턴)** — 로컬·원격 두 경로 모두 warm(세션 캐시). 입력 방식은 단계 3과 동일:

```bash
# (A) 로컬 gateway chat — 한 세션에서 agent 재사용 → warm 멀티턴 + Gateway 경유 도구
TOOLS_SOURCE=gateway META_BACKEND=mock uv run python -m agents.db_query_analysis_agent.local.chat
# (B) 배포된 Runtime 의 gateway 모드 chat (SigV4 호출, 도구는 Gateway 경유)
uv run python -m agents.db_query_analysis_agent.runtime.chat
```

✅ **둘 다 warm**: (A) 로컬은 한 `agent_session` 안에서 agent를 재사용하고, (B) 배포된 Runtime의 gateway 모드는 `**session_id`별 (agent, 열린 MCP)을 캐시·재사용**(`_gateway_sessions`)해 같은 `runtimeSessionId`의 턴들이 맥락을 잇습니다 — "방금 쿼리"가 로컬·원격 모두 통합니다. 첫 호출만 토큰+`list_tools`+agent 생성, 이후 재사용(Phase 2 inprocess 캐시와 동일 패턴). caveat: 원격은 첫 호출 JWT를 계속 써서 토큰 TTL(~1h)을 넘는 긴 세션은 만료 가능(일반 chat 길이엔 무방).

> **참고: 토큰 캐시 (Cache R/W 0/0)** — 단발/짧은 프롬프트에서는 토큰 usage의 `Cache R/W: 0/0`이
> 정상입니다. 캐시 가능한 prefix(도구 스키마 + 시스템 프롬프트)가 Bedrock 최소 캐시 크기(~1,024
> 토큰) 미만이면 캐시 엔트리가 생성되지 않습니다. prefix가 커지거나 멀티턴이 누적되면 캐시가
> 활성화되어 Cache Read/Write가 0보다 커집니다. (`cache_tools`/`cachePoint`는 이미 구성됨.)

**6) 검증을 마쳤으면 정리** — invoke·chat까지 끝낸 뒤 실행 (Phase 3 → Phase 2 순, 상세 §8):

```bash
bash infra/cognito-gateway/teardown.sh                   # Gateway/Cognito/Lambda + OAuth provider 삭제
bash agents/db_query_analysis_agent/runtime/teardown.sh  # Runtime + ECR 삭제
```

---

## 8. Teardown / Reset

배포 단계별 정리(각 스크립트는 멱등 — 이미 삭제된 자원도 안전):

```bash
# Phase 3 — Gateway/Target → Cognito CFN 스택 → Lambda 로그그룹 → 버킷 → OAuth2 provider → .env 초기화
bash infra/cognito-gateway/teardown.sh

# Phase 2 — Runtime + bedrock-agentcore-* ECR repo (자동 생성 IAM role 은 수동 확인)
bash agents/db_query_analysis_agent/runtime/teardown.sh
```

> Phase 3 → Phase 2 순으로 정리하세요. `cognito-gateway/teardown.sh` 는 Cognito 재생성 시 stale가
> 되는 OAuth2 provider(`dbq-<DEMO_USER>-oauth`)도 함께 삭제합니다 — 안 지우면 다음 배포에서
> deploy_runtime 이 stale provider 를 idempotent skip 해 토큰 교환이 실패합니다.

---

## 9. References


| Repo                                                                                             | 차용 패턴                                                                                                                  |
| ------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------- |
| [aiops-multi-agent-workshop](https://github.com/gonsoomoon-ml/aiops-multi-agent-workshop) (base) | `infra/cognito-gateway` 구조, Gateway + Target boto3 step-by-step, per-tool Lambda 벤더링, CFN+boto3 하이브리드, Cognito M2M CFN |
| [developer-briefing-agent](https://github.com/gonsoomoon-ml/developer-briefing-agent)            | local-agent ↔ managed-agentcore split, 단일 `create_agent()` truth source, `prompts/system_prompt.md` 외부화, 스트리밍 chat     |


설계·의사결정 로그: `design/` (phase별 spec·plan).