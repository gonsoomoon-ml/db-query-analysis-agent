"""DB Query Analysis Agent — AgentCore Gateway 생성 (boto3, idempotent).

deploy.sh 가 CFN outputs 를 환경변수로 주입한 뒤 호출:
    GATEWAY_IAM_ROLE_ARN
    COGNITO_USER_POOL_ID
    COGNITO_CLIENT_ID
    COGNITO_GATEWAY_SCOPE
    LAMBDA_CHECK_SQL_RULES_ARN
    LAMBDA_GET_TABLE_META_ARN
    LAMBDA_ANALYZE_SQL_WITH_LLM_ARN

3개의 검증 레이어 (Gateway CUSTOM_JWT authorizer):
    들어오는 JWT
      ↓ Gateway authorizer
      ① 서명 검증     ← discoveryUrl (UserPoolId 로부터)
      ② audience 검증 ← allowedClients=[Client ID]
      ③ scope 검증    ← allowedScopes=[<resource-server>/invoke]
      → 통과 시 Target 호출

출력: GATEWAY_ID + GATEWAY_URL (deploy.sh 가 .env 갱신 시 캡처).
"""
import os
import sys
import time

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
DEMO_USER = os.environ.get("DEMO_USER", "")  # main() 에서 검증

GATEWAY_NAME = f"dbq-{DEMO_USER}-gw"

TARGET_CHECK_SQL = "check-sql-rules"
TARGET_GET_META = "get-table-meta"
TARGET_ANALYZE = "analyze-sql-with-llm"

CHECK_SQL_TOOL_SCHEMA = [
    {
        "name": "check_sql_rules",
        "description": (
            "SQL 쿼리에 대해 규칙 기반 정적 분석을 수행합니다. "
            "DELETE/UPDATE without WHERE, DROP, TRUNCATE, SELECT *, LIKE 선행 와일드카드 탐지."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["sql"],
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "분석할 SQL 쿼리 문자열",
                },
            },
        },
    },
]

GET_TABLE_META_TOOL_SCHEMA = [
    {
        "name": "get_table_meta",
        "description": (
            "SQL 쿼리에서 테이블명을 추출하고 각 테이블의 메타데이터(컬럼, 인덱스, 행수, large_table 플래그)를 조회합니다."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["sql"],
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "테이블명을 추출할 SQL 쿼리 문자열",
                },
            },
        },
    },
]

ANALYZE_SQL_TOOL_SCHEMA = [
    {
        "name": "analyze_sql_with_llm",
        "description": (
            "AWS Bedrock Claude 로 SQL 쿼리의 인덱스 효율, 서비스 영향도, 최적화 제안을 분석합니다. "
            "규칙 기반 check(violations_json)와 테이블 메타(meta_summary)를 함께 받아 심층 분석."
        ),
        "inputSchema": {
            "type": "object",
            # meta_summary 는 파이썬 시그니처상 optional(default "") → required 에서 제외.
            "required": ["sql", "violations_json"],
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "분석할 SQL 쿼리 문자열",
                },
                "violations_json": {
                    "type": "string",
                    "description": "check_sql_rules 결과 JSON 문자열 (재언급 방지용)",
                },
                "meta_summary": {
                    "type": "string",
                    "description": "get_table_meta 결과 요약 문자열",
                },
            },
        },
    },
]


def _client():
    return boto3.client("bedrock-agentcore-control", region_name=REGION)


def wait_for_gateway_ready(gw, gateway_id: str, max_wait: int = 90, poll: int = 3) -> None:
    """Gateway 가 READY 상태 될 때까지 대기 (Target 추가 전 필수).

    create_gateway 직후엔 CREATING — Target 추가 시 ValidationException. 본 함수가
    get_gateway 폴링 → READY 확인 후 반환. 이미 READY 면 즉시 반환 (idempotent).
    """
    print(f"  Gateway READY 대기 (max {max_wait}s)")
    deadline = time.monotonic() + max_wait
    status = "UNKNOWN"
    while time.monotonic() < deadline:
        detail = gw.get_gateway(gatewayIdentifier=gateway_id)
        status = detail.get("status", "UNKNOWN")
        if status == "READY":
            print("  Gateway READY")
            return
        if status in ("FAILED", "DELETING", "DELETED"):
            raise RuntimeError(f"Gateway 비정상 상태: {status}")
        time.sleep(poll)
    raise RuntimeError(f"Gateway READY 타임아웃 ({max_wait}s, 현재={status})")


def create_gateway(gw, role_arn, pool_id, client_id, scope):
    print("\n=== Step 1: AgentCore Gateway 생성 ===")
    existing = next(
        (g for g in gw.list_gateways().get("items", []) if g.get("name") == GATEWAY_NAME),
        None,
    )
    if existing:
        print(f"  이미 존재: gatewayId={existing['gatewayId']} (재사용)")
        detail = gw.get_gateway(gatewayIdentifier=existing["gatewayId"])
        return detail

    discovery_url = (
        f"https://cognito-idp.{REGION}.amazonaws.com/{pool_id}/.well-known/openid-configuration"
    )
    resp = gw.create_gateway(
        name=GATEWAY_NAME,
        roleArn=role_arn,
        protocolType="MCP",
        authorizerType="CUSTOM_JWT",
        authorizerConfiguration={
            "customJWTAuthorizer": {
                "discoveryUrl": discovery_url,
                "allowedClients": [client_id],
                "allowedScopes": [scope],
            }
        },
    )
    print(f"  gatewayId={resp['gatewayId']}")
    print(f"  gatewayUrl={resp['gatewayUrl']}")
    return resp


def create_or_update_target(gw, gateway_id, name, lambda_arn, tool_schema):
    """Target 이 없으면 create, 있으면 update — lambdaArn + schema 강제 동기화."""
    print(f"\n=== Step 2: GatewayTarget '{name}' 추가/갱신 ===")

    target_config = {
        "mcp": {
            "lambda": {
                "lambdaArn": lambda_arn,
                "toolSchema": {"inlinePayload": tool_schema},
            }
        }
    }
    cred_configs = [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]

    existing = next(
        (
            t
            for t in gw.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])
            if t.get("name") == name
        ),
        None,
    )
    if existing:
        target_id = existing["targetId"]
        print(f"  이미 존재: targetId={target_id} — lambdaArn + schema 동기화")
        resp = gw.update_gateway_target(
            gatewayIdentifier=gateway_id,
            targetId=target_id,
            name=name,
            targetConfiguration=target_config,
            credentialProviderConfigurations=cred_configs,
        )
        print(f"  targetId={target_id} 갱신 완료")
        return resp

    resp = gw.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name=name,
        targetConfiguration=target_config,
        credentialProviderConfigurations=cred_configs,
    )
    print(f"  targetId={resp['targetId']}")
    return resp


def main():
    required = [
        "DEMO_USER",
        "GATEWAY_IAM_ROLE_ARN",
        "COGNITO_USER_POOL_ID",
        "COGNITO_CLIENT_ID",
        "COGNITO_GATEWAY_SCOPE",
        "LAMBDA_CHECK_SQL_RULES_ARN",
        "LAMBDA_GET_TABLE_META_ARN",
        "LAMBDA_ANALYZE_SQL_WITH_LLM_ARN",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"환경변수 누락: {missing}", file=sys.stderr)
        print("deploy.sh 가 CFN outputs 를 export 한 뒤 호출하세요.", file=sys.stderr)
        sys.exit(1)

    gw = _client()
    gateway = create_gateway(
        gw,
        role_arn=os.environ["GATEWAY_IAM_ROLE_ARN"],
        pool_id=os.environ["COGNITO_USER_POOL_ID"],
        client_id=os.environ["COGNITO_CLIENT_ID"],
        scope=os.environ["COGNITO_GATEWAY_SCOPE"],
    )
    gateway_id = gateway["gatewayId"]
    gateway_url = gateway["gatewayUrl"]

    # Target 추가 전 Gateway READY 대기 — CREATING 상태에서 Target 추가 시 ValidationException
    wait_for_gateway_ready(gw, gateway_id)

    create_or_update_target(
        gw,
        gateway_id,
        TARGET_CHECK_SQL,
        lambda_arn=os.environ["LAMBDA_CHECK_SQL_RULES_ARN"],
        tool_schema=CHECK_SQL_TOOL_SCHEMA,
    )
    create_or_update_target(
        gw,
        gateway_id,
        TARGET_GET_META,
        lambda_arn=os.environ["LAMBDA_GET_TABLE_META_ARN"],
        tool_schema=GET_TABLE_META_TOOL_SCHEMA,
    )
    create_or_update_target(
        gw,
        gateway_id,
        TARGET_ANALYZE,
        lambda_arn=os.environ["LAMBDA_ANALYZE_SQL_WITH_LLM_ARN"],
        tool_schema=ANALYZE_SQL_TOOL_SCHEMA,
    )

    # deploy.sh 가 stdout 에서 캡처해 .env 에 기록
    print(f"\nGATEWAY_ID={gateway_id}")
    print(f"GATEWAY_URL={gateway_url}")


if __name__ == "__main__":
    main()
