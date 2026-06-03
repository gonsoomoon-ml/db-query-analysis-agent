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
    """Cognito Bearer JWT — standalone(로컬/표준 클라이언트 검증)용 direct client_credentials.

    Runtime(컨테이너)은 이 함수가 아니라 @requires_access_token 데코레이터로 토큰을 얻는다.
    provider 경로(get_resource_oauth2_token)는 workloadIdentityToken(컨테이너 컨텍스트)이
    필요해 standalone에선 쓸 수 없으므로, OAUTH_PROVIDER_NAME 설정 여부와 무관하게 항상 direct.
    """
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
