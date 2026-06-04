#!/usr/bin/env python3
"""db-query-analysis-agent → AgentCore Runtime 배포 (toolkit). 첫 배포 ~5-10분.

agents/,shared/,data/ 를 build context로 복사 → Runtime.configure(Dockerfile/ECR/IAM
자동) → launch(Docker→ECR→Runtime) → 실행 role에 bedrock:InvokeModel 부착 →
OAuth2CredentialProvider 생성 및 추가 IAM 권한 부착 → READY 대기 →
RUNTIME_ARN 등을 repo root .env 에 저장.
"""
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[2]
os.chdir(SCRIPT_DIR)
load_dotenv(PROJECT_ROOT / ".env", override=True)

GREEN, YELLOW, RED, NC = "\033[0;32m", "\033[1;33m", "\033[0;31m", "\033[0m"
REGION = os.environ.get("AWS_REGION", "us-east-1")
DEMO_USER = os.environ.get("DEMO_USER", "ubuntu")
AGENT_NAME = f"db_query_analysis_agent_{DEMO_USER}"
OAUTH_PROVIDER_NAME = f"dbq-{DEMO_USER}-oauth"


def copy_into_build_context() -> None:
    """agents/,shared/,data/ 를 runtime/ 아래로 복사(컨테이너 import용)."""
    for name in ("agents", "shared", "data"):
        src = PROJECT_ROOT / name
        dst = SCRIPT_DIR / name
        if dst.exists():
            shutil.rmtree(dst)
        # "runtime" 제외 — 목적지(runtime/agents)가 src(agents) 안에 있어 재귀 폭주 방지.
        # entrypoint는 agents.db_query_analysis_agent.runtime을 import하지 않으므로 안전.
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "sample.db", "runtime"))
    print(f"{GREEN}✅ build context 복사{NC}")


def configure_runtime():
    """toolkit Runtime configure — Dockerfile/ECR/실행 role 자동 생성. 구성된 Runtime 반환."""
    from bedrock_agentcore_starter_toolkit import Runtime
    rt = Runtime()
    rt.configure(
        agent_name=AGENT_NAME,
        entrypoint="agentcore_runtime.py",
        auto_create_execution_role=True,
        auto_create_ecr=True,
        requirements_file="requirements.txt",
        region=REGION,
        non_interactive=True,
    )
    return rt


def _runtime_env_vars() -> dict:
    """컨테이너에 주입할 환경변수 (모두 기본값 폴백)."""
    return {
        "AWS_REGION": REGION,
        "DEMO_USER": DEMO_USER,
        "META_BACKEND": os.environ.get("META_BACKEND", "mock"),
        "DBQUERY_MODEL_ID": os.environ.get("DBQUERY_MODEL_ID", "global.anthropic.claude-sonnet-4-6"),
        "DBQUERY_TEMPERATURE": os.environ.get("DBQUERY_TEMPERATURE", "0.1"),
        "DBQUERY_MAX_TOKENS": os.environ.get("DBQUERY_MAX_TOKENS", "4096"),
        "ANALYZE_MODEL_ID": os.environ.get("ANALYZE_MODEL_ID", "global.anthropic.claude-sonnet-4-6"),
        "ANALYZE_TEMPERATURE": os.environ.get("ANALYZE_TEMPERATURE", "0.1"),
        "ANALYZE_MAX_TOKENS": os.environ.get("ANALYZE_MAX_TOKENS", "2048"),
        "LARGE_TABLE_THRESHOLD": os.environ.get("LARGE_TABLE_THRESHOLD", "1000000"),
        # C1: Gateway + OAuth provider 관련 변수
        "TOOLS_SOURCE": os.environ.get("TOOLS_SOURCE", "gateway"),
        "GATEWAY_URL": os.environ.get("GATEWAY_URL", ""),
        # 항상 DEMO_USER 파생값 — attach_oauth_provider가 만드는 provider명과 동일(컨테이너↔provider 일치).
        # .env 의 stale OAUTH_PROVIDER_NAME(다른 DEMO_USER로 배포된 잔재)에 의존하면 불일치가 박힌다.
        "OAUTH_PROVIDER_NAME": OAUTH_PROVIDER_NAME,
        "COGNITO_GATEWAY_SCOPE": os.environ.get("COGNITO_GATEWAY_SCOPE", ""),
    }


def launch_runtime(rt):
    """Docker→ECR→Runtime launch (충돌 시 갱신). launch 결과 반환."""
    result = rt.launch(env_vars=_runtime_env_vars(), auto_update_on_conflict=True)
    print(f"{GREEN}✅ launch: {result.agent_arn}{NC}")
    return result


def attach_bedrock_policy(agent_id: str) -> None:
    """실행 role에 bedrock:InvokeModel 인라인 정책 부착."""
    ctrl = boto3.client("bedrock-agentcore-control", region_name=REGION)
    role_name = ctrl.get_agent_runtime(agentRuntimeId=agent_id)["roleArn"].split("/")[-1]
    boto3.client("iam").put_role_policy(
        RoleName=role_name,
        PolicyName="BedrockInvoke",
        PolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                "Resource": "*",
            }],
        }),
    )
    print(f"{GREEN}✅ IAM: {role_name}/BedrockInvoke{NC}")


def attach_oauth_provider(agent_id: str) -> None:
    """[C1] 실행 role에 OAuth2 관련 IAM 권한 부착 + OAuth2CredentialProvider 생성.

    IAM inline policy:
      - bedrock-agentcore:GetResourceOauth2Token (Resource *)
      - secretsmanager:GetSecretValue (Resource bedrock-agentcore-identity!* 시크릿)

    OAuth2CredentialProvider:
      - Cognito client_credentials 흐름 (M2M)을 AgentCore Identity에 등록.
      - 재배포 시 ConflictException / "already exists" ValidationException → idempotent skip.

    OAUTH_PROVIDER_NAME을 repo root .env에 저장 (컨테이너 env_vars와 일치시킴).
    """
    # 선행 검증 — gateway 모드(TOOLS_SOURCE 기본값)에 필요한 값이 모두 채워졌는지 먼저 확인.
    # AWS 변경(IAM/provider) 이전에 fail-fast. GATEWAY_URL·COGNITO_GATEWAY_SCOPE도 포함 —
    # 비면 런타임 첫 gateway invoke에서 실패. deploy.sh(Cognito+Gateway) 선행이 전제.
    missing = [k for k in ("COGNITO_USER_POOL_ID", "COGNITO_DOMAIN", "COGNITO_CLIENT_ID",
                           "COGNITO_CLIENT_SECRET", "GATEWAY_URL", "COGNITO_GATEWAY_SCOPE")
               if not os.environ.get(k)]
    if missing:
        print(f"{RED}❌ {missing} 비어있음 — infra/cognito-gateway/deploy.sh 를 먼저 실행하세요{NC}")
        sys.exit(1)

    ctrl = boto3.client("bedrock-agentcore-control", region_name=REGION)
    runtime_info = ctrl.get_agent_runtime(agentRuntimeId=agent_id)
    role_arn = runtime_info["roleArn"]
    role_name = role_arn.split("/")[-1]
    account_id = role_arn.split(":")[4]

    iam = boto3.client("iam")
    oauth_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "GetResourceOauth2Token",
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:GetResourceOauth2Token"],
                "Resource": "*",
            },
            {
                "Sid": "ReadCognitoClientSecret",
                "Effect": "Allow",
                "Action": ["secretsmanager:GetSecretValue"],
                "Resource": [
                    f"arn:aws:secretsmanager:*:{account_id}:secret:bedrock-agentcore-identity!*",
                ],
            },
        ],
    }
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="DbqOAuthExtras",
        PolicyDocument=json.dumps(oauth_policy),
    )
    print(f"{GREEN}✅ IAM: {role_name}/DbqOAuthExtras (OAuth2 권한){NC}")

    user_pool_id = os.environ["COGNITO_USER_POOL_ID"]
    domain = os.environ["COGNITO_DOMAIN"]
    client_id = os.environ["COGNITO_CLIENT_ID"]
    client_secret = os.environ["COGNITO_CLIENT_SECRET"]

    try:
        ctrl.create_oauth2_credential_provider(
            name=OAUTH_PROVIDER_NAME,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput={
                "customOauth2ProviderConfig": {
                    "clientId": client_id,
                    "clientSecret": client_secret,
                    "oauthDiscovery": {
                        "authorizationServerMetadata": {
                            "issuer": f"https://cognito-idp.{REGION}.amazonaws.com/{user_pool_id}",
                            "authorizationEndpoint": (
                                f"https://{domain}.auth.{REGION}.amazoncognito.com/oauth2/authorize"
                            ),
                            "tokenEndpoint": (
                                f"https://{domain}.auth.{REGION}.amazoncognito.com/oauth2/token"
                            ),
                            "responseTypes": ["token"],
                        },
                    },
                },
            },
        )
        print(f"{GREEN}✅ OAuth Provider 생성: {OAUTH_PROVIDER_NAME}{NC}")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        message = e.response["Error"].get("Message", "")
        # 재배포 시나리오 — ConflictException 또는 "already exists" ValidationException → idempotent.
        if code == "ConflictException" or (
            code == "ValidationException" and "already exists" in message
        ):
            print(f"   (OAuth Provider {OAUTH_PROVIDER_NAME} 이미 존재 — skip)")
        else:
            raise

    # OAUTH_PROVIDER_NAME을 repo root .env에 저장 (컨테이너 env_vars와 일치).
    _save_oauth_provider_name()


def _save_oauth_provider_name() -> None:
    """OAUTH_PROVIDER_NAME을 repo root .env에 저장/갱신."""
    env_file = PROJECT_ROOT / ".env"
    lines = []
    if env_file.exists():
        lines = [
            ln for ln in env_file.read_text().splitlines()
            if not ln.startswith("OAUTH_PROVIDER_NAME=")
        ]
    lines.append(f"OAUTH_PROVIDER_NAME={OAUTH_PROVIDER_NAME}")
    env_file.write_text("\n".join(lines) + "\n")
    print(f"{GREEN}✅ .env: OAUTH_PROVIDER_NAME={OAUTH_PROVIDER_NAME} 저장{NC}")


def wait_until_ready(agent_id: str) -> None:
    """status가 READY가 될 때까지 폴링(최대 ~10분). READY 아니면 로그 힌트 출력 후 종료."""
    ctrl = boto3.client("bedrock-agentcore-control", region_name=REGION)
    status = "CREATING"
    for i in range(60):
        time.sleep(10)
        status = ctrl.get_agent_runtime(agentRuntimeId=agent_id)["status"]
        print(f"   [{i+1}/60] {status}")
        if status in ("READY", "CREATE_FAILED", "UPDATE_FAILED"):
            break
    if status != "READY":
        log_grp = f"/aws/bedrock-agentcore/runtimes/{agent_id}-DEFAULT"
        print(f"{RED}❌ 실패: {status} — aws logs tail {log_grp} --region {REGION}{NC}")
        sys.exit(1)


def save_runtime_env(result) -> None:
    """RUNTIME_NAME/ID/ARN 을 repo root .env 에 저장(기존 값 교체)."""
    env_file = PROJECT_ROOT / ".env"
    keep = [ln for ln in (env_file.read_text().splitlines() if env_file.exists() else [])
            if not ln.startswith(("RUNTIME_ARN=", "RUNTIME_ID=", "RUNTIME_NAME="))]
    keep += [f"RUNTIME_NAME={AGENT_NAME}", f"RUNTIME_ID={result.agent_id}", f"RUNTIME_ARN={result.agent_arn}"]
    env_file.write_text("\n".join(keep) + "\n")


def main() -> None:
    print(f"{YELLOW}[1/6] build context 복사{NC}")
    copy_into_build_context()

    print(f"{YELLOW}[2/6] Runtime configure{NC}")
    rt = configure_runtime()

    print(f"{YELLOW}[3/6] launch (Docker→ECR→Runtime, 수 분){NC}")
    result = launch_runtime(rt)

    print(f"{YELLOW}[4/6] 실행 role에 bedrock:InvokeModel 부착{NC}")
    attach_bedrock_policy(result.agent_id)

    # OAuth2 provider 는 gateway 모드일 때만 필요 — inprocess 단독 배포(Phase 2)는
    # Cognito/Gateway 불필요. TOOLS_SOURCE 기본값(gateway)을 그대로 두면 Phase 3 흐름.
    tools_source = os.environ.get("TOOLS_SOURCE", "gateway")
    if tools_source == "gateway":
        print(f"{YELLOW}[5/6] OAuth2CredentialProvider + 추가 IAM 권한 부착 (gateway 모드){NC}")
        attach_oauth_provider(result.agent_id)
    else:
        print(f"{YELLOW}[5/6] OAuth2 설정 skip — TOOLS_SOURCE={tools_source} (Cognito/Gateway 불필요){NC}")

    print(f"{YELLOW}[6/6] READY 대기{NC}")
    wait_until_ready(result.agent_id)

    save_runtime_env(result)
    print(f"{GREEN}✅ 배포 완료 — RUNTIME_ARN .env 저장 ({datetime.now():%H:%M}){NC}")
    print("   다음: uv run python -m agents.db_query_analysis_agent.runtime.invoke_runtime --query \"SELECT * FROM orders WHERE user_id=1\"")


if __name__ == "__main__":
    main()
