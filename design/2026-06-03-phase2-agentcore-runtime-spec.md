# Phase 2 — AgentCore Runtime 승격 (Spec)

> 작성일: 2026-06-03
> 선행: Stage 1 + Phase 1(SQLite) 완료 (`main`)
> 로드맵: SQLite ✅ → **AgentCore Runtime (본 phase, Redis 제외)** → (Redis) → Cognito/Gateway → A2A/Supervisor
> 참조: developer-briefing `managed-agentcore/` (단일 에이전트 Runtime), AIOps Phase 3

---

## 1. 배경 & 목표

로컬 단일 에이전트(`build_db_query_agent`)를 **관리형 AgentCore Runtime**으로 승격한다 — 동일 `create_agent` 로직을 컨테이너로 서비스화(로컬 == 관리형 단일 truth, developer-briefing C1). SigV4 인증, SSE 스트리밍, 세션 기반 멀티턴. **원격 chat mode** 포함.

**비목표(범위 외)**: Redis(다음 sub-phase), Cognito/AgentCore Gateway/MCP/A2A(후속), 사용자 데이터 실제 쿼리 실행.

**실행 모델**: 본 phase는 배포 코드가 핵심. **Claude가 직접 `deploy_runtime.py` 실행 → `invoke_runtime.py`/`chat.py`로 e2e 검증 → 배포 유지(teardown 안 함)**. `teardown.sh`는 제공만. (이 환경: Docker + Admin + AgentCore/ECR 권한 확인됨, acct ACCOUNT_ID, us-east-1.)

---

## 2. 결정 요약

| 항목 | 결정 |
|---|---|
| 범위 | **Runtime만** (Redis 다음 sub-phase) |
| 패턴 | developer-briefing `managed-agentcore` (단일 에이전트, SigV4, SSE, 세션 캐시). Gateway/MCP/Cognito 미사용(tool in-process) |
| 재사용 | `build_db_query_agent`/`create_agent` 그대로 — 엔트리포인트가 호출 |
| 인증 | **SigV4** (Cognito 없음) |
| 멀티턴 | `runtimeSessionId` 재사용 → 엔트리포인트 세션 캐시(`agent.messages` warm) |
| 진입점 | `invoke_runtime.py`(단발) + **`chat.py`(원격 멀티턴 REPL)** |
| 컨테이너 backend | 기본 `META_BACKEND=mock`(파일시스템 의존 0). `data/` 포함 → sqlite 전환 가능 |
| DRY | 멀티라인 입력 `shared/repl.py`로 추출(로컬+원격 chat 공유); 원격 invoke+SSE 공용 헬퍼 |
| 배포/검증 | Claude가 deploy + invoke + chat e2e, **배포 유지**. teardown.sh 제공 |

---

## 3. 아키텍처

```
Operator
 ├─ invoke_runtime.py --query "…"        (단발)
 └─ chat.py                               (멀티턴 REPL — runtimeSessionId 재사용)
        │ boto3 invoke_agent_runtime (SigV4)  +  payload {query, …}
        ▼
┌─ AgentCore Runtime (컨테이너) ───────────────────────────────┐
│  agentcore_runtime.py: BedrockAgentCoreApp + @app.entrypoint │
│    session_id(AgentCore) → _get_or_create_agent (세션 캐시)    │
│      = build_db_query_agent()  (없으면 생성, 있으면 재사용)     │
│    agent.stream_async(query) → SSE yield:                     │
│      agent_text_stream / token_usage / workflow_complete      │
│        └ tools(check_sql_rules/get_table_meta/analyze) → Bedrock │
└──────────────────────────────────────────────────────────────┘
```
로컬(`local/run.py`,`local/chat.py`)과 **동일 `build_db_query_agent`** — 서빙만 다름.

---

## 4. 컴포넌트

신규 `agents/db_query_analysis_agent/runtime/`:

### 4.1 `agentcore_runtime.py`
- `app = BedrockAgentCoreApp()`, `@app.entrypoint async def review(payload, context)`.
- payload: `{"query": "<자연어/SQL 리뷰 요청>", "session_id"?}`. `query` 누락 → 에러 yield + complete.
- **세션 캐시**: 모듈 전역 `_session_agents: dict[str, Agent]`. key = AgentCore session id(`context`/`BedrockAgentCoreContext.get_session_id()`, 없으면 payload.session_id, 없으면 "default"). 같은 key → 기존 Agent 반환(멀티턴), 없으면 `build_db_query_agent()`.
- `agent.stream_async(query)` 소비 → SSE: `{"type":"agent_text_stream","text":…}`, 종료 시 `{"type":"token_usage","usage":…}`(가능하면) + `{"type":"workflow_complete"}`.
- 컨테이너/로컬 import 폴백: `shared.agent` 직접 import(로컬) — deploy가 `agents/`,`shared/`,`data/`를 build context로 복사.
- `if __name__ == "__main__": app.run()`.

### 4.2 `deploy_runtime.py`
- `os.chdir(SCRIPT_DIR)`; `agents/`,`shared/`,`data/`를 build context(runtime/)로 복사.
- toolkit `Runtime.configure(agent_name="db_query_analysis_agent_{DEMO_USER}", entrypoint="agentcore_runtime.py", auto_create_execution_role=True, auto_create_ecr=True, requirements_file="requirements.txt", region, non_interactive=True)`.
- `Runtime.launch(env_vars={AWS_REGION, DBQUERY_*, ANALYZE_*, META_BACKEND(기본 mock), LARGE_TABLE_THRESHOLD, DEMO_USER}, auto_update_on_conflict=True)`.
- **실행 role에 `bedrock:InvokeModel`(+ `bedrock:InvokeModelWithResponseStream`) inline policy 부착** — 에이전트가 Bedrock 호출(자동 role에 없을 수 있음).
- READY 대기(폴링) → `RUNTIME_ARN`/`_ID`/`_NAME`을 repo root `.env`에 저장.

### 4.3 `invoke_runtime.py` (단발)
- `--query`(기본 예시), `--session-id`(옵션, ≥33자 검증). 공용 헬퍼로 invoke + SSE 출력.

### 4.4 `chat.py` (원격 멀티턴 REPL) ← chat mode
- 세션 시작 시 **runtimeSessionId 1개 생성(≥33자)**, 매 턴 재사용 → 엔트리포인트 세션 캐시 hit(warm, 멀티턴).
- `shared/repl.read_multiline_input()`로 입력(여러 줄→빈 줄 전송, 따옴표 strip), `/reset`(새 session id), `/quit`.
- 공용 invoke+SSE 헬퍼로 스트리밍 출력.

### 4.5 공용 헬퍼 / DRY
- `runtime/_remote.py`: `stream_invoke(query, session_id=None) -> str` — boto3 `invoke_agent_runtime`(SigV4, `RUNTIME_ARN` from `.env`) + SSE 파싱 + 실시간 출력 + 전체 텍스트 반환. `invoke_runtime.py`·`chat.py` 공용.
- **`shared/repl.py`**: `read_multiline_input(prompt=...) -> str | None` — 기존 `local/chat.py._read_input` 추출. `local/chat.py`+`runtime/chat.py` 공유. (local/chat.py는 이걸 import하도록 변경, `tests/test_chat_input.py`도 import 경로 갱신.)

### 4.6 `teardown.sh` + `Dockerfile` + `requirements.txt`
- teardown.sh: Runtime 삭제 + ECR repo 삭제 + 실행 role 정리(만들되 실행 안 함).
- requirements.txt: 컨테이너 런타임 의존(strands-agents, bedrock-agentcore, boto3 …).
- pyproject: `bedrock-agentcore`, `bedrock-agentcore-starter-toolkit`(dev) 추가.

---

## 5. 데이터 흐름

```
chat.py: session_id=한 번 생성
  > 이 쿼리 리뷰: SELECT * FROM orders WHERE user_id=1   (여러 줄 가능, 빈 줄 전송)
    → invoke_agent_runtime(arn, payload={query}, runtimeSessionId=session_id)
    → Runtime: 세션 캐시(같은 id) → 기존/새 agent → stream_async → SSE
    → 클라이언트 실시간 출력
  > 그 쿼리에 인덱스 추가하면?                           (같은 session_id → 맥락 유지)
```

---

## 6. 에러 처리

| 지점 | 처리 |
|---|---|
| payload `query` 누락 | 에러 SSE + workflow_complete |
| Bedrock 실패 | tool/agent 기존 graceful (analyze→{error}) |
| deploy 실패(quota/Docker/IAM) | 상태/CloudWatch 로그 안내, exit 1 |
| invoke 권한/ARN 없음 | 한국어 에러(`RUNTIME_ARN` 미설정 → deploy 먼저) |
| 세션 id < 33자 | invoke/chat에서 검증 후 에러 |

---

## 7. 테스트

- **로컬(자격증명 무관)**:
  - `shared/repl.read_multiline_input` 단위 테스트(기존 test_chat_input 이전/갱신).
  - `agentcore_runtime` import 스모크 + `@entrypoint` payload 로직: agent를 mock(stream_async fake)하여 SSE yield 3종 + query 누락 에러 검증.
  - `_remote.stream_invoke`: boto3 client mock으로 SSE 파싱 검증.
- **e2e (Claude가 실행)**:
  1. `uv run agents/db_query_analysis_agent/runtime/deploy_runtime.py` → READY + `RUNTIME_ARN` 저장.
  2. `uv run agents/db_query_analysis_agent/runtime/invoke_runtime.py --query "SELECT * FROM orders WHERE user_id=1"` → 스트리밍 리뷰 확인.
  3. `chat.py` 멀티턴: 동일 session_id로 2턴 호출(공용 헬퍼 직접 호출로 비대화 검증 가능) → 2번째 턴이 맥락 유지/정상.
  4. **배포 유지**(teardown 안 함). `teardown.sh`만 제공.

---

## 8. 범위 / 범위 외
- 범위: runtime/(agentcore_runtime, deploy, invoke, chat, teardown, Dockerfile, requirements) + shared/repl 추출 + pyproject deps + 테스트 + Claude 배포·검증.
- 범위 외: Redis(다음), Cognito/Gateway/A2A(후속), 데이터 쿼리 실행.

## 9. 미해결
- 없음(핵심 결정 완료). 세부(toolkit configure 인자, IAM policy ARN 형식, SSE event 키)는 구현/배포 중 실 API에 맞춰 확정.
