"""Gateway helper — Cognito Bearer 토큰 획득 + MCP client 팩토리.

auth_local.py (base reference) 패턴 적용:
  ``get_gateway_token`` → env 기반 자동 dispatch (provider vs direct)
  ``create_mcp_client``  → token 헤더 주입 → Strands MCPClient 반환.

## 2-mode dispatch

| 조건 | 경로 |
|---|---|
| ``OAUTH_PROVIDER_NAME`` 설정 | AgentCore Identity 경유 — boto3 ``get_resource_oauth2_token`` |
| ``OAUTH_PROVIDER_NAME`` 미설정 | Cognito token endpoint 직접 호출 — urllib Basic auth |
"""
import base64
import json
import os
import urllib.parse
import urllib.request
from datetime import timedelta


def _require_env(name: str) -> str:
    """env 값을 반환하거나, 미설정이면 RuntimeError (한국어 메시지)."""
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"필수 환경변수 '{name}'가 설정되지 않았습니다. .env 파일 또는 환경 변수를 확인하세요.")
    return val


def _fetch_token_via_provider() -> str:
    """Phase 3+ 정상 경로 — AgentCore Identity의 OAuth provider 경유.

    boto3 ``get_resource_oauth2_token`` 호출 → Cognito JWT 반환.
    clientSecret은 provider 등록 시 한 번만 입력되므로 직접 다루지 않음.
    """
    import boto3

    region = os.environ.get("AWS_REGION") or "us-east-1"
    provider_name = _require_env("OAUTH_PROVIDER_NAME")
    scope = _require_env("COGNITO_GATEWAY_SCOPE")

    agentcore = boto3.client("bedrock-agentcore", region_name=region)
    response = agentcore.get_resource_oauth2_token(
        resourceCredentialProviderName=provider_name,
        scopes=[scope],
        oauth2Flow="M2M",
    )
    return response["accessToken"]


def _fetch_token_direct() -> str:
    """Standalone fallback — Cognito token endpoint 직접 호출 (urllib + HTTP Basic auth).

    OAUTH_PROVIDER_NAME 미설정 시 사용. clientId/clientSecret을 HTTP Basic auth로 전송.
    Phase 3 provider 경로와 동일한 JWT 반환.
    """
    region = os.environ.get("AWS_REGION") or "us-east-1"
    domain = _require_env("COGNITO_DOMAIN")
    client_id = _require_env("COGNITO_CLIENT_ID")
    client_secret = _require_env("COGNITO_CLIENT_SECRET")
    scope = _require_env("COGNITO_GATEWAY_SCOPE")

    url = f"https://{domain}.auth.{region}.amazoncognito.com/oauth2/token"
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "scope": scope,
    }).encode("utf-8")
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {creds}",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        token = json.load(resp)["access_token"]
    return token


def get_gateway_token() -> str:
    """Cognito Bearer JWT 획득 — env 기반 자동 dispatch.

    ``OAUTH_PROVIDER_NAME`` 설정 시 → AgentCore Identity 경유 (Phase 3+ 정상 경로).
    미설정 시 → Cognito 직접 호출 (standalone 검증용).
    두 경로 모두 동일 JWT 문자열 반환.
    """
    if os.environ.get("OAUTH_PROVIDER_NAME"):
        return _fetch_token_via_provider()
    return _fetch_token_direct()


def create_mcp_client(token: str):
    """MCP client 생성 — token을 Authorization Bearer 헤더로 주입.

    Args:
        token: ``get_gateway_token()`` 으로 획득한 Cognito JWT.

    Returns:
        ``strands.tools.mcp.mcp_client.MCPClient`` 인스턴스.
        ``GATEWAY_URL`` env 필수.
    """
    from mcp.client.streamable_http import streamablehttp_client
    from strands.tools.mcp.mcp_client import MCPClient

    gateway_url = _require_env("GATEWAY_URL")

    return MCPClient(
        lambda: streamablehttp_client(
            gateway_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timedelta(seconds=120),
        )
    )
