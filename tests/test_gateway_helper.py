"""gateway.py 단위 테스트 — 네트워크 없이 monkeypatch만 사용."""
import json

import pytest

# ---------------------------------------------------------------------------
# get_gateway_token — 항상 direct client_credentials (standalone 전용).
# Runtime은 @requires_access_token 데코레이터로 토큰을 얻으므로 이 함수를 쓰지 않는다.
# provider 경로(get_resource_oauth2_token)는 workloadIdentityToken이 필요해 standalone 불가.
# ---------------------------------------------------------------------------

def test_get_gateway_token_direct_even_with_provider_env(monkeypatch):
    """OAUTH_PROVIDER_NAME이 설정돼 있어도 standalone은 direct(client_credentials)를 쓴다 (회귀)."""
    monkeypatch.setenv("OAUTH_PROVIDER_NAME", "ignored-in-standalone")
    monkeypatch.setenv("COGNITO_DOMAIN", "mypool")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "clientid123")
    monkeypatch.setenv("COGNITO_CLIENT_SECRET", "secret456")
    monkeypatch.setenv("COGNITO_GATEWAY_SCOPE", "https://example.com/invoke")
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    fake_token = "direct-jwt-xyz789"
    captured_requests = []

    class _FakeResponse:
        def __init__(self):
            self._data = json.dumps({"access_token": fake_token}).encode()

        def read(self):
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        # json.load 호환 — urllib opener가 반환하는 객체는 file-like
        def readable(self):
            return True

    import urllib.request

    def _fake_urlopen(req, timeout=None):
        captured_requests.append(req)
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    # json.load requires a file-like with .read(); patch at json level to avoid io compat issues
    import json as json_mod

    def _patched_load(fp):
        data = fp.read()
        return json_mod.loads(data)

    monkeypatch.setattr(json_mod, "load", _patched_load)

    from agents.db_query_analysis_agent.shared.gateway import get_gateway_token
    token = get_gateway_token()

    assert token == fake_token
    assert len(captured_requests) == 1
    req = captured_requests[0]
    # URL contains expected Cognito domain
    assert "mypool.auth.us-east-1.amazoncognito.com" in req.full_url
    # Authorization header is Basic
    assert req.get_header("Authorization").startswith("Basic ")


# ---------------------------------------------------------------------------
# get_gateway_token — 필수 env 누락 시 RuntimeError
# ---------------------------------------------------------------------------

def test_get_gateway_token_missing_env_raises(monkeypatch):
    """OAUTH_PROVIDER_NAME 미설정 + COGNITO_DOMAIN 없음 → RuntimeError."""
    monkeypatch.delenv("OAUTH_PROVIDER_NAME", raising=False)
    monkeypatch.delenv("COGNITO_DOMAIN", raising=False)
    monkeypatch.delenv("COGNITO_CLIENT_ID", raising=False)
    monkeypatch.delenv("COGNITO_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("COGNITO_GATEWAY_SCOPE", raising=False)

    from agents.db_query_analysis_agent.shared import gateway as gw_mod
    import importlib
    importlib.reload(gw_mod)

    with pytest.raises(RuntimeError, match="COGNITO_DOMAIN"):
        gw_mod.get_gateway_token()


# ---------------------------------------------------------------------------
# create_mcp_client — MCPClient 생성 + Bearer 헤더 전달 검증
# ---------------------------------------------------------------------------

def test_create_mcp_client_builds_mcp_client(monkeypatch):
    """create_mcp_client가 MCPClient를 올바른 URL·Bearer 헤더로 생성하는지 검증.

    create_mcp_client 내부의 lazy import (from strands/mcp)를 monkeypatch로 대체.
    streamablehttp_client는 mcp.client.streamable_http 모듈에서 패치,
    MCPClient는 strands.tools.mcp.mcp_client 모듈에서 패치.
    """
    monkeypatch.setenv("GATEWAY_URL", "https://gateway.example.com/mcp")

    captured_transport_calls = []

    class _FakeMCPClient:
        def __init__(self, transport_factory):
            self._factory = transport_factory

        def call_transport(self):
            """테스트에서 transport factory 직접 호출."""
            return self._factory()

    def _fake_streamable(url, headers=None, timeout=None):
        captured_transport_calls.append({"url": url, "headers": headers, "timeout": timeout})
        return object()  # sentinel transport

    # Patch in the canonical source modules that gateway.py imports from lazily
    import mcp.client.streamable_http as shttp
    import strands.tools.mcp.mcp_client as mcp_client_mod

    monkeypatch.setattr(shttp, "streamablehttp_client", _fake_streamable)
    monkeypatch.setattr(mcp_client_mod, "MCPClient", _FakeMCPClient)

    from agents.db_query_analysis_agent.shared.gateway import create_mcp_client

    token = "test-bearer-token"
    client = create_mcp_client(token)

    assert isinstance(client, _FakeMCPClient)
    # Invoke transport factory to verify parameters passed to streamablehttp_client
    client.call_transport()

    assert len(captured_transport_calls) == 1
    call = captured_transport_calls[0]
    assert call["url"] == "https://gateway.example.com/mcp"
    assert call["headers"] == {"Authorization": "Bearer test-bearer-token"}
