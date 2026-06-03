# Phase 3 — Cognito + AgentCore Gateway 설계 spec

> 날짜: 2026-06-03. 베이스 레퍼런스: `aiops-multi-agent-workshop` `infra/cognito-gateway/` + `agents/monitor`(SigV4 Runtime + outbound OAuth).
> 선행: Stage 1 / Phase 1(SQLite) / Phase 2(AgentCore Runtime, SigV4) 완료(main). 후속: Phase 4 = Redis, 이후 A2A/Supervisor.

## 1. 목표

db-query-analysis-agent의 SQL 도구 3종(`check_sql_rules`·`get_table_meta`·`analyze_sql_with_llm`)을 **AgentCore Gateway(MCP) + Cognito JWT**로 보안 노출하고, **Runtime 에이전트가 그 Gateway를 통해 도구를 소비**하도록 한다(워크샵 풀 패턴 = "(i)"). 인바운드 호출(호출자→Runtime)은 **SigV4 그대로**, Cognito JWT는 **아웃바운드(Runtime→Gateway)에만** 쓴다.

확정 결정:
- **D1 = `TOOLS_SOURCE` 환경 스위치** (`inprocess`|`gateway`). 기존 `META_BACKEND`(mock|sqlite|redis)와 동일 철학. 로컬=`inprocess`(오프라인), Runtime=`gateway`. 단일 `create_agent`가 둘 다 지원 → 패리티 유지.
- **D2 = Cognito/Lambda/IAM은 CloudFormation**(`cognito.yaml`), **Gateway는 boto3 스크립트**(`setup_gateway.py`). CFN은 선언적이고 `delete-stack`으로 정리가 깔끔.
- **D3 = 도구별 Lambda 3개**(per-tool). 최소권한(analyze만 `bedrock:InvokeModel`)과 도구별 타임아웃을 위해.

## 2. 비목표(범위 외)

- Redis(Phase 4), A2A/Supervisor(후속), 사람 사용자 로그인(M2M `client_credentials`만), Runtime **인바운드** 인증 변경(SigV4 유지), 도구 로직 기능 변경(외부화만, 동작 보존).

## 3. 아키텍처 / 인증 흐름

두 개의 분리된 표면, 두 개의 인증:

```
호출자 ──SigV4──▶ Runtime(db-query-analysis-agent)         [인바운드: 변경 없음]
                    │  TOOLS_SOURCE=gateway → 도구를 Gateway에서 가져옴
                    │  requires_access_token(OAuth provider) → Cognito M2M JWT   [아웃바운드]
                    ▼
                AgentCore Gateway (protocolType=MCP, authorizerType=CUSTOM_JWT/Cognito)
                    │  MCP 도구: <target>___check_sql_rules / ___get_table_meta / ___analyze_sql_with_llm
                    │  (Gateway IAM role: lambda:InvokeFunction)
                    ▼
                Lambda ×3 (per-tool)  ── analyze→bedrock:InvokeModel · meta→vendored mock TABLE_META
```

- **인바운드(호출자→Runtime)**: SigV4 `invoke_agent_runtime`(`runtimeUserId` 필수 — 없으면 in-Runtime `requires_access_token`이 "Workload access token not set"으로 실패).
- **아웃바운드(Runtime→Gateway)**: Cognito M2M JWT. OAuth2CredentialProvider가 발급·캐시, `requires_access_token`이 주입.
- **Gateway→Lambda**: Gateway IAM role의 `lambda:InvokeFunction`(대상 3개 ARN으로 한정).
- **JWT 신뢰**: Gateway의 customJWTAuthorizer가 Cognito discoveryUrl(서명) + allowedClients(audience=app client id) + allowedScopes(`.../invoke`) 3중 검증.

## 4. `TOOLS_SOURCE` 스위치 (D1)

- `inprocess`(로컬 기본): 기존 in-process `@tool` 3종을 직접 사용. Cognito/Gateway 불필요 → **오프라인 개발·테스트 가능**.
- `gateway`(Runtime 기본): MCP 클라이언트로 Gateway에서 도구 목록을 받아 사용.
- 단일 진입 API: **`agent_session()` 컨텍스트매니저**(신규, `shared/agent.py`)가 `TOOLS_SOURCE`로 분기하고 MCP 세션 라이프사이클을 캡슐화. 로컬·Runtime 진입점 모두 동일하게 사용:
  ```
  with agent_session() as agent:
      ... agent.stream_async(query) ...
  ```
  - `inprocess`: 기존 `build_db_query_agent()`(3 @tool) 그대로 yield. MCP 없음.
  - `gateway`(invoke마다 1회, 베이스 패턴, stateless): 토큰 취득 → `with MCPClient(streamablehttp(GATEWAY_URL, Bearer)) as mcp: tools=mcp.list_tools_sync(); yield create_agent(tools=tools)`. 에이전트 실행이 MCP `with` 안에서 일어남(LLM 도구 호출 동안 세션 유지). 캐시·재사용 없음.

## 5. 도구 DRY 리팩터 (코어 1 + 래퍼 2)

각 도구의 핵심 로직을 **순수 함수**로 추출하고, in-process `@tool`과 Lambda 핸들러가 **같은 코어를 공유**(워크샵 `history_mock` 패턴 — 중복 0).
- `tools/check_sql_rules.py`: `def check_rules_core(sql:str)->dict` + 얇은 `@tool` 래퍼.
- `tools/get_table_meta.py`: `def table_meta_core(sql:str)->dict` + `@tool`. (META_BACKEND 경유 그대로 → 단일 출처 `TABLE_META` 유지.)
- `tools/analyze_sql_with_llm.py`: `def analyze_core(sql, violations_json, meta_summary)->dict`(기존 `run_analysis`가 이미 코어 역할) + `@tool`.
- 기존 단위 테스트는 코어 함수 대상으로 유지/이전(동작 보존 검증).

## 6. Lambda ×3 (D3)

위치: `infra/cognito-gateway/lambda/{check_sql_rules,get_table_meta,analyze_sql_with_llm}/handler.py`.
- 각 핸들러: `tool = context.client_context.custom["bedrockAgentCoreToolName"]`(형식 `<target>___<tool>`) 확인 → `event`(=inputSchema properties dict)에서 인자 추출 → 코어 함수 호출 → JSON 직렬화 가능 dict 반환(에러는 `{"error":...}`).
- 코어 공유: 배포 시 `agents/db_query_analysis_agent/tools` 코어 + `data/mock` 을 각 Lambda 디렉터리로 vendoring(`deploy.sh`가 복사; 워크샵 `deploy.sh:46`처럼).
- IAM/타임아웃(per-tool):
  - `analyze_sql_with_llm`: exec role에 `bedrock:InvokeModel`/`...WithResponseStream`, 타임아웃 120s, 메모리 ↑.
  - `check_sql_rules`·`get_table_meta`: 외부 호출 없음(순수 계산 + vendored mock), 기본 타임아웃·메모리.
- 각 도구 `inputSchema`(JSON Schema): `check_sql_rules`·`get_table_meta`는 `sql`(string) 필수. `analyze_sql_with_llm`은 **기존 in-process 계약 그대로** `sql`+`violations_json`+`meta_summary`(LLM이 앞 도구 출력을 인자로 넘김 — 현재 in-process 동작과 동일하므로 패리티 유지). 단순화(단일 `sql`) 안 함.

## 7. Cognito (CloudFormation, D2) — `infra/cognito-gateway/cognito.yaml`

`DemoUser` 파라미터, 리소스 prefix `dbq-${DemoUser}-`:
- **UserPool**(MFA off, admin-create-only), **UserPoolDomain**(`dbq-${DemoUser}-${AWS::AccountId}` — 전역 유일), **UserPoolResourceServer**(scope `invoke`), **UserPoolClient**(`GenerateSecret:true`, `AllowedOAuthFlows:[client_credentials]`, scope `.../invoke`).
- **Lambda exec role ×3**(per-tool 최소권한), **Gateway IAM role**(trust `bedrock-agentcore.amazonaws.com`, `lambda:InvokeFunction`을 3개 Lambda ARN으로 한정).
- **Outputs**: `UserPoolId`, `Domain`, `ClientId`, `ResourceServerScope`, Lambda ARN×3, `GatewayIamRoleArn`. **client secret은 CFN output 아님** → `deploy.sh`가 `describe-user-pool-client`로 별도 취득. discovery/token URL은 규약으로 파생.

## 8. Gateway (boto3) — `infra/cognito-gateway/setup_gateway.py`

`boto3.client("bedrock-agentcore-control")`:
- `create_gateway(name, roleArn=GatewayIamRoleArn, protocolType="MCP", authorizerType="CUSTOM_JWT", authorizerConfiguration={"customJWTAuthorizer":{"discoveryUrl":<cognito openid-config>, "allowedClients":[clientId], "allowedScopes":[scope]}})` → 이름으로 reuse, **`get_gateway` status==READY 폴링** 후 target 추가.
- target ×3: `create_gateway_target`(이름 존재 시 `update_gateway_target`), `targetConfiguration={"mcp":{"lambda":{"lambdaArn":…, "toolSchema":{"inlinePayload":[<tool schema>]}}}}`, `credentialProviderConfigurations=[{"credentialProviderType":"GATEWAY_IAM_ROLE"}]`.
- 출력 `gatewayUrl`/`gatewayId` → `.env`(`GATEWAY_URL`/`GATEWAY_ID`).

## 9. OAuth provider + Runtime 변경

- **`deploy_runtime.py`**(Phase 2 함수 구조에 추가): launch 후 `agentcore_control.create_oauth2_credential_provider(name, credentialProviderVendor="CustomOauth2", oauth2ProviderConfigInput={"customOauth2ProviderConfig":{clientId, clientSecret, oauthDiscovery:{authorizationServerMetadata:{issuer, authorizationEndpoint, tokenEndpoint, responseTypes:["token"]}}}})`(ConflictException 멱등) + 실행 role에 `bedrock-agentcore:GetResourceOauth2Token`(*) · `secretsmanager:GetSecretValue`(`bedrock-agentcore-identity!*`) 부착. `.env`에 `OAUTH_PROVIDER_NAME` 저장. Runtime env_vars에 `TOOLS_SOURCE=gateway`, `GATEWAY_URL`, `OAUTH_PROVIDER_NAME`, `COGNITO_GATEWAY_SCOPE` 추가.
- **`agentcore_runtime.py`**: `agent_session()`이 `gateway` 분기에서 `@requires_access_token(provider_name=OAUTH_PROVIDER_NAME, scopes=[scope], auth_flow="M2M", into="access_token")`로 JWT 취득 → MCP 클라이언트 → 도구.
- **MCP 라이프사이클 = 베이스 코드 그대로(단순)**: gateway 모드는 invoke마다 `token → create_mcp_client → with mcp_client: list_tools_sync() → create_agent(tools) → stream_async` 를 **1회 선형 호출**(워크샵 `monitor_agent` 엔트리포인트 lines 100-119 동일). 세션 캐시·이력 복원 같은 장치 없음 → **gateway 모드는 invoke 단위 stateless**(베이스도 Memory disabled로 동일). `inprocess` 모드는 Phase 2 warm 캐시(`_session_agents`) 유지. **트레이드오프**: gateway 모드엔 cross-turn 대화 메모리 없음 — 필요해지면 후속에 AgentCore Memory 도입(범위 외).
  > **[2026-06-03 변경]** 사용자 요청으로 gateway도 **warm**으로 변경 — Phase 2 inprocess처럼 `session_id`별 `(agent, 열린 MCP)`을 `_gateway_sessions`에 캐시·재사용(첫 호출만 토큰+list_tools+create_agent; MCP는 microVM 수명 동안 열어둠). runtime.chat 멀티턴 맥락 유지 검증. → 위 §4/§14의 "stateless" 서술은 이 변경으로 대체됨.

## 10. 로컬 토큰 경로 — `shared/gateway.py`(신규)

- `get_gateway_token()`: `OAUTH_PROVIDER_NAME` 설정 시 `get_resource_oauth2_token`(provider), 없으면 **direct client_credentials POST**(token endpoint, HTTP Basic `clientId:secret`, scope) — 로컬 standalone용.
- `create_mcp_client(token)`: `MCPClient(lambda: streamablehttp_client(GATEWAY_URL, headers={"Authorization": f"Bearer {token}"}, timeout=…))`.

## 11. 배포 / 정리 순서

- **스탠드업**: ① `infra/cognito-gateway/deploy.sh` → CFN deploy(Cognito+Lambda+IAM) + client secret 취득 + `setup_gateway.py`(Gateway+target) → `.env`. ② `runtime/deploy_runtime.py` 재실행 → OAuth provider + IAM 추가 + `TOOLS_SOURCE=gateway`로 Runtime 갱신.
- **정리**(역순): ① `runtime/teardown.sh`(Runtime + OAuth provider 삭제; Gateway/Cognito는 유지). ② `infra/cognito-gateway/teardown.sh` → `cleanup_gateway.py`(target→gateway, CFN의 lambda-invoke 권한 살아있을 때) → `cloudformation delete-stack`.

## 12. 설정 / env (신규)

`COGNITO_USER_POOL_ID` · `COGNITO_DOMAIN` · `COGNITO_CLIENT_ID` · `COGNITO_CLIENT_SECRET` · `COGNITO_GATEWAY_SCOPE` · `GATEWAY_URL` · `GATEWAY_ID` · `OAUTH_PROVIDER_NAME` · `TOOLS_SOURCE`(기본 `inprocess`). `.env.example`에 추가(secret은 값 비움).

## 13. 에러 처리

- `TOOLS_SOURCE=gateway`인데 `GATEWAY_URL`/토큰 없음 → 명확한 에러로 즉시 중단(SigV4 Runtime/inprocess와 구분).
- MCP 연결/토큰 실패 → 사용자에게 표면화(컨테이너 로그 + invoke 에러 프레임).
- `runtimeUserId` 누락 → 토큰 발급 실패 메시지 안내(`invoke_runtime`/`chat`은 이미 전달).

## 14. 테스트 / 검증

- **단위**: 도구 코어 함수(기존 테스트 이전), Lambda 핸들러 dispatch(`bedrockAgentCoreToolName`→코어, 인자 매핑, dict 반환), `agent_session()`의 `TOOLS_SOURCE` 분기(MCP는 mock), `get_gateway_token` 분기.
- **표준 MCP 클라이언트 검증**(SigV4 Runtime 무관): client_credentials 토큰 → `streamablehttp_client` → `MCPClient.list_tools_sync()`가 3개 도구 노출 확인 + 1개 호출. → Cognito-보안 Gateway 표면 end-to-end 입증.
- **e2e**: `TOOLS_SOURCE=gateway`로 Runtime 재배포 후 invoke → Gateway 경유 도구로 리뷰 스트리밍(단발; gateway는 stateless). 멀티턴 warm은 `inprocess` 모드에서 확인. 로컬 `inprocess` invoke로 오프라인 동작 회귀.

## 15. 불변식(유지)

- **단일 출처** `TABLE_META`(Lambda는 vendoring, 별도 스키마 없음). **패리티**(동일 `create_agent`/도구 정의, 출처만 스위치). **read-only**(analyze EXPLAIN 그대로). **DRY**(도구 코어 1 + 래퍼 2; 시크릿 미커밋 — client secret은 `.env`/CFN 취득만).

## 16. 파일 구조(신규/변경)

```
infra/cognito-gateway/
  cognito.yaml            (신규, CFN)
  setup_gateway.py        (신규, boto3)
  deploy.sh / teardown.sh / cleanup_gateway.py   (신규)
  lambda/{check_sql_rules,get_table_meta,analyze_sql_with_llm}/handler.py  (신규 ×3)
agents/db_query_analysis_agent/
  tools/*.py              (변경: 코어 함수 추출 + @tool 래퍼)
  shared/agent.py         (변경: agent_session() + TOOLS_SOURCE 분기)
  shared/gateway.py       (신규: 토큰 + MCP 클라이언트)
  runtime/agentcore_runtime.py  (변경: gateway 소비, 멀티턴 이력 캐시)
  runtime/deploy_runtime.py     (변경: OAuth provider + IAM + env_vars)
  local/run.py, local/chat.py   (변경: agent_session() 사용)
.env.example              (변경: 신규 env)
tests/                    (변경/신규: 코어·핸들러·스위치·토큰)
```

## 17. 리스크 / 오픈 이슈(plan에서 확정)

- **Lambda cold start**(특히 analyze + Bedrock) → 첫 호출 지연. 필요 시 provisioned/예열은 후속.
- **`get_table_meta` Lambda의 메타 backend** = vendored mock 고정(sqlite/redis 백엔드를 Lambda에 넣는 건 범위 외).
- **규모**: 한 spec이지만 구현은 자연히 3단계로 시퀀싱(① 도구 코어 추출+Lambda, ② Cognito CFN+Gateway+표준클라 검증, ③ OAuth provider+Runtime 소비+e2e) — plan에서 분해.
