# Phase 3 — Cognito + AgentCore Gateway 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development 으로 task 단위 실행. 인프라/배포·검증 task는 controller가 직접("test it yourself"). 체크박스(`- [ ]`)로 추적.

**Goal:** SQL 도구 3종을 Cognito-보안 AgentCore Gateway(MCP)로 노출하고 Runtime이 `TOOLS_SOURCE=gateway`로 소비. 인바운드 SigV4 유지.

**Architecture:** spec `design/2026-06-03-phase3-cognito-gateway-spec.md` (아키텍처 i). 베이스: `/tmp/aiops-base`(=`gonsoomoon-ml/aiops-multi-agent-workshop`) `infra/cognito-gateway/` + `agents/monitor`.

**Tech:** CloudFormation(Cognito/Lambda/IAM) + boto3 `bedrock-agentcore-control`(Gateway) + Strands `MCPClient`/`mcp` + `bedrock-agentcore` OAuth provider/`requires_access_token`.

**실행 원칙:** 베이스 파일을 "어댑트"하는 task는 해당 base file을 읽고 명시된 델타만 적용. 우리 고유 코드는 전체 작성 + TDD. 각 task 후 commit. 베이스에 자격증명/시크릿 하드코딩 금지(.env/CFN 취득).

---

## File structure

```
infra/cognito-gateway/                     (신규)
  cognito.yaml · setup_gateway.py · deploy.sh · teardown.sh · cleanup_gateway.py
  lambda/{check_sql_rules,get_table_meta,analyze_sql_with_llm}/handler.py
agents/db_query_analysis_agent/
  tools/{check_sql_rules,get_table_meta,analyze_sql_with_llm}.py   (변경: 코어 추출)
  shared/agent.py        (변경: agent_session() + TOOLS_SOURCE)
  shared/gateway.py      (신규: 토큰 + MCP 클라이언트)
  runtime/agentcore_runtime.py   (변경: gateway 소비)
  runtime/deploy_runtime.py      (변경: OAuth provider + IAM + env_vars)
  local/{run,chat}.py    (변경: agent_session() 사용)
.env.example             (변경)
tests/                   (신규/변경)
```

---

# Stage A — 도구 코어 추출 + Lambda 핸들러 (AWS 불필요, 로컬 테스트)

## Task A1: 도구 코어 함수 추출 + @tool 얇은 래퍼

**Files:** `tools/check_sql_rules.py`, `tools/get_table_meta.py`, `tools/analyze_sql_with_llm.py`; `tests/test_check_sql_rules.py`, `tests/test_get_table_meta.py`, `tests/test_analyze_sql.py`

- [ ] **A1.1** 각 도구 파일에서 핵심 로직을 순수 함수로 분리하되 **동작 보존**:
  - `check_sql_rules.py`: `def check_rules_core(sql: str) -> dict:` (기존 규칙 검사 본문) + `@tool def check_sql_rules(sql: str) -> dict: return check_rules_core(sql)`.
  - `get_table_meta.py`: `def table_meta_core(sql: str) -> dict:` (META_BACKEND 경유 본문 그대로) + `@tool` 래퍼.
  - `analyze_sql_with_llm.py`: 이미 `run_analysis(sql, violations_json, meta_summary)`(async)가 코어 — 그대로 두고 `@tool`이 이를 호출(현재 구조 유지). 추가 추출 불필요.
- [ ] **A1.2** 기존 단위 테스트가 **코어 함수**(또는 @tool)를 호출하도록 유지/이전. 새 직접-코어 테스트 1개씩 추가(예: `test_check_rules_core_flags_select_star`).
- [ ] **A1.3** Run: `uv run pytest tests/test_check_sql_rules.py tests/test_get_table_meta.py tests/test_analyze_sql.py -q` → PASS. `uv run ruff check .` → clean.
- [ ] **A1.4** Commit: `refactor(tools): extract pure core fns from @tool wrappers (shared by Lambda)`

## Task A2: Lambda 핸들러 ×3 (per-tool)

**Files:** `infra/cognito-gateway/lambda/{check_sql_rules,get_table_meta,analyze_sql_with_llm}/handler.py`; `tests/test_lambda_handlers.py`

베이스 참조: `/tmp/aiops-base/infra/cognito-gateway/lambda/history_mock/handler.py`(코어 import 패턴), `cloudwatch_wrapper/handler.py`(`_tool_name(context)` 헬퍼). 델타: target=tool 1:1, 코어 호출.

- [ ] **A2.1** 각 `handler.py`: 
  ```python
  def _tool_name(context):
      try: return context.client_context.custom["bedrockAgentCoreToolName"]
      except Exception: return ""
  def handler(event, context):
      # event == inputSchema properties dict
      sql = event.get("sql", "")
      ...  # 코어 함수 호출, dict 반환; 예외→{"error": str(e)}
  ```
  - check_sql_rules: `from agents.db_query_analysis_agent.tools.check_sql_rules import check_rules_core` → `return check_rules_core(sql)`.
  - get_table_meta: `table_meta_core(sql)`.
  - analyze_sql_with_llm: `event`에서 `sql`/`violations_json`/`meta_summary` → `asyncio.run(run_analysis(...))`.
- [ ] **A2.2** 패키징 메모(주석): analyze Lambda는 `strands-agents`+`bedrock` 의존 → zip에 deps 포함 또는 Lambda layer(B 단계 deploy.sh에서 처리). check/meta는 순수 파이썬+vendored mock.
- [ ] **A2.3** `tests/test_lambda_handlers.py`: `bedrockAgentCoreToolName`을 흉내낸 fake context + event로 각 핸들러가 코어를 호출하고 dict 반환하는지(코어는 monkeypatch). analyze는 `run_analysis` monkeypatch.
- [ ] **A2.4** Run: `uv run pytest tests/test_lambda_handlers.py -q` → PASS; ruff clean.
- [ ] **A2.5** Commit: `feat(gateway): per-tool Lambda handlers wrapping the shared tool cores`

## Task A3: `TOOLS_SOURCE` 스위치 + `agent_session()`

**Files:** `shared/agent.py`, `shared/gateway.py`(신규, gateway 분기용 — B4에서 채움; 여기선 import만), `tests/test_agent_session.py`

- [ ] **A3.1** `shared/agent.py`에 컨텍스트매니저 추가:
  ```python
  import contextlib, os
  @contextlib.contextmanager
  def agent_session(system_prompt_filename: str | None = None):
      src = os.environ.get("TOOLS_SOURCE", "inprocess")
      if src == "gateway":
          from agents.db_query_analysis_agent.shared.gateway import get_gateway_token, create_mcp_client
          with create_mcp_client(get_gateway_token()) as mcp:
              tools = mcp.list_tools_sync()
              yield create_agent(tools=tools, system_prompt_filename=system_prompt_filename or DEFAULT_PROMPT)
      else:  # inprocess
          yield build_db_query_agent()
  ```
  (gateway 분기는 invoke마다 1회·stateless — 베이스 패턴. `build_db_query_agent`/`create_agent`는 기존.)
- [ ] **A3.2** `tests/test_agent_session.py`: `TOOLS_SOURCE` 미설정/`inprocess` → `build_db_query_agent` 경로(monkeypatch로 호출 확인). `gateway` → `get_gateway_token`/`create_mcp_client` monkeypatch(가짜 mcp의 `list_tools_sync` 반환) → `create_agent`가 그 tools로 호출되는지.
- [ ] **A3.3** Run: `uv run pytest tests/test_agent_session.py -q` → PASS; ruff clean.
- [ ] **A3.4** Commit: `feat(agent): TOOLS_SOURCE switch via agent_session() context manager`

---

# Stage B — Cognito CFN + Gateway + 표준 클라이언트 검증 (AWS)

## Task B1: `cognito.yaml` (CFN)

**Files:** `infra/cognito-gateway/cognito.yaml`

베이스 어댑트: `/tmp/aiops-base/infra/cognito-gateway/cognito.yaml`. 델타:
- [ ] **B1.1** prefix `aiops-demo-${DemoUser}` → `dbq-${DemoUser}`. UserPool/Domain(account-suffix)/ResourceServer(scope `invoke`)/M2M Client(`client_credentials`,`GenerateSecret`) 유지.
- [ ] **B1.2** Lambda를 **3개**로(check_sql_rules/get_table_meta/analyze_sql_with_llm), 각 exec role 분리. **analyze role에만** `bedrock:InvokeModel`/`...WithResponseStream`(Resource `*`). check/meta role은 기본 logs만.
- [ ] **B1.3** GatewayIamRole: trust `bedrock-agentcore.amazonaws.com`, policy `lambda:InvokeFunction`을 **3개 Lambda ARN**으로 한정.
- [ ] **B1.4** Outputs: UserPoolId, Domain, ClientId, ResourceServerScope, Lambda ARN×3, GatewayIamRoleArn.
- [ ] **B1.5** `aws cloudformation validate-template --template-body file://infra/cognito-gateway/cognito.yaml` → 유효. Commit: `feat(infra): cognito.yaml (Cognito M2M + 3 lambda + gateway IAM)`

## Task B2: deploy.sh / teardown.sh / cleanup_gateway.py

**Files:** `infra/cognito-gateway/{deploy.sh,teardown.sh,cleanup_gateway.py}`

베이스 어댑트: 동명 파일. 델타:
- [ ] **B2.1** `deploy.sh`: DEMO_USER/리전 로드 → (analyze Lambda용) deps+코어+`data/mock` vendoring → `aws cloudformation package`(필요 시 S3) → `deploy`(`CAPABILITY_NAMED_IAM`) → CFN outputs + `describe-user-pool-client`로 client secret → `setup_gateway.py` → repo-root `.env`에 `COGNITO_*`/`GATEWAY_*` 기록. (베이스의 Lambda 3종 vendoring 경로를 우리 3개로.)
- [ ] **B2.2** `cleanup_gateway.py`: target 삭제 → 대기 → gateway 삭제(이름으로 조회). `teardown.sh`: `cleanup_gateway.py` → `cloudformation delete-stack` + wait → `.env` 정리.
- [ ] **B2.3** `bash -n` 문법 검사 통과. Commit: `feat(infra): cognito-gateway deploy/teardown/cleanup scripts`

## Task B3: `setup_gateway.py`

**Files:** `infra/cognito-gateway/setup_gateway.py`

베이스 어댑트: 동명 파일. 델타:
- [ ] **B3.1** `create_gateway(name="dbq-${DemoUser}-gw", roleArn=GatewayIamRoleArn, protocolType="MCP", authorizerType="CUSTOM_JWT", customJWTAuthorizer={discoveryUrl(cognito openid-config), allowedClients:[ClientId], allowedScopes:[scope]})` + `get_gateway` READY 폴링(베이스 `wait_for_gateway_ready`).
- [ ] **B3.2** target **3개**(per-tool): 각 `create/update_gateway_target(targetConfiguration={"mcp":{"lambda":{"lambdaArn":<tool ARN>,"toolSchema":{"inlinePayload":[<해당 tool 1개 스키마>]}}}}, credentialProviderConfigurations=[{"credentialProviderType":"GATEWAY_IAM_ROLE"}])`.
  - tool 스키마(inputSchema): check_sql_rules`{sql}`·get_table_meta`{sql}`·analyze_sql_with_llm`{sql,violations_json,meta_summary}` (spec §6, 패리티).
- [ ] **B3.3** `GATEWAY_URL`/`GATEWAY_ID` stdout + `deploy.sh`가 `.env` 기록. Commit: `feat(infra): setup_gateway.py (MCP gateway + 3 per-tool targets, Cognito JWT)`

## Task B4: `shared/gateway.py` (토큰 + MCP 클라이언트)

**Files:** `agents/db_query_analysis_agent/shared/gateway.py`; `tests/test_gateway_helper.py`

베이스 어댑트: `agents/monitor/shared/{auth_local,mcp_client}.py` 병합.
- [ ] **B4.1** `get_gateway_token() -> str`: `OAUTH_PROVIDER_NAME` 있으면 `boto3 bedrock-agentcore.get_resource_oauth2_token(...,oauth2Flow="M2M")`, 없으면 **direct** `client_credentials` POST(token endpoint, HTTP Basic `CLIENT_ID:CLIENT_SECRET`, scope) via urllib/requests. URL 미설정 시 명확 에러.
- [ ] **B4.2** `create_mcp_client(token) -> MCPClient`: `MCPClient(lambda: streamablehttp_client(GATEWAY_URL, headers={"Authorization": f"Bearer {token}"}, timeout=timedelta(seconds=120)))`.
- [ ] **B4.3** `tests/test_gateway_helper.py`: `get_gateway_token` 분기(provider vs direct)를 monkeypatch(boto3/urllib)로; `create_mcp_client`가 헤더에 Bearer 포함하는지(streamablehttp monkeypatch).
- [ ] **B4.4** Run pytest + ruff. Commit: `feat(gateway): shared/gateway.py token + MCP client helper`

## Task B5: 배포 + 표준 MCP 클라이언트 검증 [controller-driven]

- [ ] **B5.1** `bash infra/cognito-gateway/deploy.sh` → CFN + Gateway 생성, `.env` 기록 확인.
- [ ] **B5.2** 표준 클라이언트 검증(SigV4 Runtime 무관): direct `client_credentials` 토큰 → `create_mcp_client` → `MCPClient.list_tools_sync()`가 `<target>___check_sql_rules`/`___get_table_meta`/`___analyze_sql_with_llm` 노출 확인 + `check_sql_rules` 1회 호출 결과 확인.
- [ ] **B5.3** Lambda 로그/권한 점검(analyze가 bedrock 호출 성공). 결과 보고. (배포 유지.)

---

# Stage C — OAuth provider + Runtime 소비 + e2e (AWS)

## Task C1: `deploy_runtime.py` — OAuth provider + IAM + env_vars

**Files:** `agents/db_query_analysis_agent/runtime/deploy_runtime.py`

베이스 어댑트: `agents/monitor/runtime/deploy_runtime.py`의 `attach_extras_and_oauth_provider`.
- [ ] **C1.1** launch 후 신규 함수 `attach_oauth_provider(agent_id)`: 실행 role에 `bedrock-agentcore:GetResourceOauth2Token`(*) + `secretsmanager:GetSecretValue`(`bedrock-agentcore-identity!*`) put_role_policy → `agentcore_control.create_oauth2_credential_provider(name="dbq-${DEMO_USER}-oauth", credentialProviderVendor="CustomOauth2", oauth2ProviderConfigInput={customOauth2ProviderConfig:{clientId,clientSecret,oauthDiscovery:{authorizationServerMetadata:{issuer,authorizationEndpoint,tokenEndpoint,responseTypes:["token"]}}}})`(ConflictException 멱등) → `.env`에 `OAUTH_PROVIDER_NAME`.
- [ ] **C1.2** `_runtime_env_vars()`에 `TOOLS_SOURCE=gateway`, `GATEWAY_URL`, `OAUTH_PROVIDER_NAME`, `COGNITO_GATEWAY_SCOPE` 추가(`.env`에서 읽어 전달).
- [ ] **C1.3** `main()`에 단계 추가(`[5/6] OAuth provider`, `[6/6] READY`). ruff clean. Commit: `feat(runtime): deploy adds OAuth2 credential provider + identity IAM + gateway env`

## Task C2: `agentcore_runtime.py` — gateway 소비

**Files:** `agents/db_query_analysis_agent/runtime/agentcore_runtime.py`

- [ ] **C2.1** `requires_access_token` 기반 토큰 함수 추가(`from bedrock_agentcore.identity.auth import requires_access_token`; `@requires_access_token(provider_name=OAUTH_PROVIDER_NAME, scopes=[scope], auth_flow="M2M", into="access_token")`). gateway 분기에서 이 토큰으로 `create_mcp_client`.
- [ ] **C2.2** `review()` 엔트리포인트를 `agent_session()` 기반으로: `TOOLS_SOURCE=gateway`면 베이스처럼 `with create_mcp_client(token) as mcp: tools=list_tools_sync(); agent=create_agent(tools); _stream_review`. `inprocess`면 기존 `_get_or_create_agent(session_id)`(warm 캐시) 유지. (gateway=invoke당 1회 stateless.)
- [ ] **C2.3** 기존 entrypoint 단위 테스트 유지 + gateway 분기 테스트(MCP/토큰 monkeypatch). ruff clean. Commit: `feat(runtime): consume gateway tools via MCP when TOOLS_SOURCE=gateway`

## Task C3: local 진입점 — `agent_session()` 사용

**Files:** `agents/db_query_analysis_agent/local/run.py`, `local/chat.py`

- [ ] **C3.1** `run.py`/`chat.py`가 `build_db_query_agent()` 직접 대신 `with agent_session() as agent:`로 감싸 스트리밍(루프는 `with` 안). 기본 `inprocess`라 로컬 동작 불변; `TOOLS_SOURCE=gateway`로도 가능.
- [ ] **C3.2** 스모크(`inprocess`): `run --help`, `chat` 빈입력 EOF. ruff clean. Commit: `refactor(local): run/chat use agent_session() (TOOLS_SOURCE-aware)`

## Task C4: `.env.example` + 문서

**Files:** `.env.example`, `README.md`

- [ ] **C4.1** `.env.example`에 `TOOLS_SOURCE=inprocess`, `COGNITO_*`(secret 빈값), `GATEWAY_URL`, `OAUTH_PROVIDER_NAME`, `COGNITO_GATEWAY_SCOPE` 추가. README 로드맵/테스트가이드에 Phase 3 한 줄. Commit: `docs: env + readme for Phase 3 gateway`

## Task C5: 재배포 + e2e [controller-driven]

- [ ] **C5.1** `uv run python -m agents.db_query_analysis_agent.runtime.deploy_runtime` → OAuth provider 생성 + `TOOLS_SOURCE=gateway`로 Runtime 갱신, READY.
- [ ] **C5.2** e2e: `invoke_runtime --query "SELECT * FROM orders WHERE user_id = 1"`(runtimeUserId 전달) → Gateway 경유 도구로 리뷰 스트리밍(단발). CloudWatch에서 Runtime→Gateway→Lambda 흐름 확인.
- [ ] **C5.3** 회귀: 로컬 `TOOLS_SOURCE=inprocess`(기본) 단발/멀티턴 — 오프라인 정상. 결과 보고. (배포 유지.)

---

## 최종

- [ ] 전체 `uv run pytest -q` + `ruff check .` green.
- [ ] 최종 코드 리뷰(opus) → superpowers:finishing-a-development-branch (feat/phase3-cognito-gateway → main).

## Self-review 체크(작성자)
- spec 커버리지: Cognito/Gateway/Lambda×3/TOOLS_SOURCE/OAuth/Runtime 소비/배포·정리/테스트 모두 task 존재 ✓.
- 미해결(spec §17): Lambda cold start·analyze 패키징(A2.2/B2.1에서 처리)·get_table_meta=vendored mock — 명시됨.
- 타입/이름 일관: `agent_session`/`TOOLS_SOURCE`/`get_gateway_token`/`create_mcp_client`/`OAUTH_PROVIDER_NAME`/`GATEWAY_URL` 전 task 일치 ✓.
