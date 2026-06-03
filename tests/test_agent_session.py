"""agent_session() context manager 단위 테스트 — TOOLS_SOURCE 분기 검증."""


# ---------------------------------------------------------------------------
# inprocess 분기 (기본값)
# ---------------------------------------------------------------------------

def test_agent_session_inprocess_default(monkeypatch):
    """TOOLS_SOURCE 미설정 시 build_db_query_agent sentinel 반환 검증."""
    monkeypatch.delenv("TOOLS_SOURCE", raising=False)

    sentinel = object()

    import agents.db_query_analysis_agent.shared.agent as agent_mod
    monkeypatch.setattr(agent_mod, "build_db_query_agent", lambda: sentinel)

    from agents.db_query_analysis_agent.shared.agent import agent_session

    with agent_session() as agent:
        assert agent is sentinel


def test_agent_session_inprocess_explicit(monkeypatch):
    """TOOLS_SOURCE=inprocess 명시 시에도 in-process 경로 사용."""
    monkeypatch.setenv("TOOLS_SOURCE", "inprocess")

    sentinel = object()

    import agents.db_query_analysis_agent.shared.agent as agent_mod
    monkeypatch.setattr(agent_mod, "build_db_query_agent", lambda: sentinel)

    from agents.db_query_analysis_agent.shared.agent import agent_session

    with agent_session() as agent:
        assert agent is sentinel


# ---------------------------------------------------------------------------
# gateway 분기
# ---------------------------------------------------------------------------

def test_agent_session_gateway(monkeypatch):
    """TOOLS_SOURCE=gateway 시 get_gateway_token + create_mcp_client + create_agent 호출 검증."""
    monkeypatch.setenv("TOOLS_SOURCE", "gateway")

    fake_token = "gw-token-001"
    fake_tools = ["t1", "t2"]

    # Fake MCP context manager whose list_tools_sync returns fake_tools
    class _FakeMCP:
        def __init__(self):
            self.entered = False

        def __enter__(self):
            self.entered = True
            return self

        def __exit__(self, *a):
            pass

        def list_tools_sync(self):
            return fake_tools

    fake_mcp = _FakeMCP()

    captured_create_agent = {}

    def _fake_get_gateway_token():
        return fake_token

    def _fake_create_mcp_client(token):
        assert token == fake_token
        return fake_mcp

    sentinel_agent = object()

    def _fake_create_agent(tools, system_prompt_filename):
        captured_create_agent["tools"] = tools
        captured_create_agent["system_prompt_filename"] = system_prompt_filename
        return sentinel_agent

    import agents.db_query_analysis_agent.shared.agent as agent_mod

    # Patch gateway functions at the module level via the gateway submodule
    import agents.db_query_analysis_agent.shared.gateway as gw_mod
    monkeypatch.setattr(gw_mod, "get_gateway_token", _fake_get_gateway_token)
    monkeypatch.setattr(gw_mod, "create_mcp_client", _fake_create_mcp_client)
    # Patch create_agent on the agent module
    monkeypatch.setattr(agent_mod, "create_agent", _fake_create_agent)

    from agents.db_query_analysis_agent.shared.agent import agent_session

    with agent_session() as agent:
        assert agent is sentinel_agent

    assert captured_create_agent["tools"] == fake_tools
    assert captured_create_agent["system_prompt_filename"] == "system_prompt.md"
    assert fake_mcp.entered


def test_agent_session_gateway_custom_prompt(monkeypatch):
    """system_prompt_filename 인자가 create_agent에 전달되는지 검증."""
    monkeypatch.setenv("TOOLS_SOURCE", "gateway")

    class _FakeMCP:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def list_tools_sync(self):
            return []

    captured = {}

    import agents.db_query_analysis_agent.shared.gateway as gw_mod
    import agents.db_query_analysis_agent.shared.agent as agent_mod

    monkeypatch.setattr(gw_mod, "get_gateway_token", lambda: "tok")
    monkeypatch.setattr(gw_mod, "create_mcp_client", lambda t: _FakeMCP())
    monkeypatch.setattr(
        agent_mod,
        "create_agent",
        lambda tools, system_prompt_filename: captured.update({"spf": system_prompt_filename}) or object(),
    )

    from agents.db_query_analysis_agent.shared.agent import agent_session

    with agent_session(system_prompt_filename="custom_prompt.md"):
        pass

    assert captured["spf"] == "custom_prompt.md"
