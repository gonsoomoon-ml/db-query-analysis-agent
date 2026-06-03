"""AgentCore Runtime 엔트리포인트 — db-query-analysis-agent.

TOOLS_SOURCE 환경변수로 모드를 선택한다.

- inprocess (기본): Phase 2 동작 유지 — 세션별 warm 에이전트 캐시(agent.messages 보존).
- gateway: 세션별 (agent, 열린 MCP) 캐시 재사용 → warm 멀티턴. 첫 호출만 @requires_access_token
  (workload identity runtimeUserId → Cognito M2M)으로 토큰 획득 + list_tools + agent 생성.

build_db_query_agent를 세션별로 캐시(멀티턴 warm) + SSE 스트리밍. 로컬 == 관리형
단일 truth. SigV4 인증. 컨테이너에선 deploy가 agents/,shared/,data/를 build context로 복사.
"""
import os
import sys
from pathlib import Path
from typing import Any, AsyncGenerator

_SCRIPT_DIR = Path(__file__).resolve().parent
# 최상위 패키지(agents/·shared/)를 포함한 루트를 sys.path에 추가. 컨테이너(/app, 평탄 복사)와
# 로컬(repo root) 양쪽을 IndexError 없이 처리 — self→parents 순으로 첫 매칭 루트를 사용.
for _root in (_SCRIPT_DIR, *_SCRIPT_DIR.parents):
    if (_root / "agents").is_dir() and (_root / "shared").is_dir():
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))
        break
else:  # 루트 미발견 → build context 누락. 모호한 ImportError 대신 원인을 명시.
    raise RuntimeError(f"agents/·shared/ 를 가진 루트를 찾지 못함 (build context 누락?): {_SCRIPT_DIR}")

from bedrock_agentcore.runtime import BedrockAgentCoreApp  # noqa: E402
from bedrock_agentcore.identity.auth import requires_access_token  # noqa: E402

# 위 부트스트랩이 agents/·shared/ 루트를 sys.path에 넣으므로 컨테이너(/app)·로컬 모두 이 import로 충분.
from agents.db_query_analysis_agent.shared.agent import (  # noqa: E402
    agent_session,
    build_db_query_agent,
)

app = BedrockAgentCoreApp()

# runtimeSessionId당 전용 microVM이라 한 프로세스는 사실상 한 세션 → dict는 ~1개 항목, LRU 불필요.
_session_agents: dict[str, Any] = {}


def _get_or_create_agent(session_id: str):
    """세션별 Agent 캐시 — 같은 id면 재사용(agent.messages 보존 = 멀티턴)."""
    if session_id in _session_agents:
        return _session_agents[session_id]
    agent = build_db_query_agent()
    _session_agents[session_id] = agent
    return agent


# @requires_access_token 데코레이터는 import 시점에 적용된다.
# OAUTH_PROVIDER_NAME / COGNITO_GATEWAY_SCOPE 를 os.environ.get()으로 읽어 기본값 ""을 사용하면,
# inprocess 모드 혹은 테스트 환경에서 env 미설정 시에도 import 오류 없이 로드된다.
# 실제 gateway 호출 시에는 env가 채워져 있어야 한다.
@requires_access_token(
    provider_name=os.environ.get("OAUTH_PROVIDER_NAME", ""),
    scopes=[os.environ.get("COGNITO_GATEWAY_SCOPE", "")],
    auth_flow="M2M",
    into="access_token",
)
async def _fetch_gateway_token(*, access_token: str = "") -> str:
    """OAuth2CredentialProvider가 자동 inject한 access_token을 반환.

    데코레이터가 workload identity → Cognito M2M 교환(GetResourceOauth2Token) 후
    access_token kwarg로 주입. 함수는 그대로 반환만 한다.
    """
    return access_token


# gateway 세션 캐시 — session_id → (agent, agent_session CM). 첫 호출만 토큰+MCP open+list_tools+
# create_agent(agent_session.__enter__); 이후 agent 재사용(agent.messages 보존 = warm). MCP는
# 열어둔 채(CM __exit__ 안 함) — microVM(=세션) 종료 시 OS가 정리(inprocess와 동일 수명 모델).
_gateway_sessions: dict[str, Any] = {}


async def _get_or_create_gateway_agent(session_id: str):
    """gateway: 첫 호출만 (토큰→MCP open→list_tools→agent) 생성·캐시, 이후 재사용(warm)."""
    cached = _gateway_sessions.get(session_id)
    if cached is not None:
        return cached[0]
    token = await _fetch_gateway_token()
    cm = agent_session(token=token)   # gateway 분기: MCP open + list_tools + create_agent
    agent = cm.__enter__()            # yield까지 실행 → agent (MCP는 열린 채 유지)
    # cm을 함께 보관하는 이유 = GC 방지. agent_session은 `with create_mcp_client(...)` 블록 안에서
    # yield하므로, cm 참조를 놓으면 generator가 GC될 때 GeneratorExit로 그 with가 __exit__ →
    # MCP 세션이 끊겨 warm 재사용이 깨진다. 따라서 microVM 수명 동안 cm 참조를 살려둔다.
    _gateway_sessions[session_id] = (agent, cm)
    return agent


async def _stream_review(agent, query: str) -> AsyncGenerator[dict, None]:
    """agent.stream_async 소비 → SSE 이벤트(text/usage/complete) yield."""
    usage = {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0,
             "cacheReadInputTokens": 0, "cacheWriteInputTokens": 0}
    async for event in agent.stream_async(query):
        data = event.get("data", "")
        if data:
            yield {"type": "agent_text_stream", "text": data}
        meta = event.get("event", {}).get("metadata", {})
        if "usage" in meta:
            for k in usage:
                usage[k] += meta["usage"].get(k, 0)
    yield {"type": "token_usage", "usage": usage}
    yield {"type": "workflow_complete", "text": ""}


@app.entrypoint
async def review(payload: dict, context: Any = None) -> AsyncGenerator[dict, None]:
    """Operator → 진입. payload {query, session_id?} → SSE.

    TOOLS_SOURCE=gateway: 세션별 (agent, 열린 MCP) 캐시 재사용 → warm. 첫 호출만 토큰 획득 +
                          list_tools + agent 생성, 이후 재사용(agent.messages 보존).
    TOOLS_SOURCE=inprocess(기본): 세션별 warm 에이전트 캐시(멀티턴 유지).
    """
    query = (payload or {}).get("query") or ""
    if not query:
        yield {"type": "agent_text_stream", "text": '[error] payload에 "query" 누락'}
        yield {"type": "workflow_complete", "text": ""}
        return

    # 세션 키: AgentCore runtimeSessionId(context, 헤더) 우선 → payload → "default".
    # 헤더 기반이라 표준 호출(payload에 session_id 미포함)에서도 멀티턴 세션이 격리된다.
    session_id = (
        getattr(context, "session_id", None)
        or (payload or {}).get("session_id")
        or "default"
    )
    tools_source = os.environ.get("TOOLS_SOURCE", "inprocess")

    if tools_source == "gateway":
        # gateway: 세션별 (agent, 열린 MCP) 캐시 재사용 → warm 멀티턴.
        agent = await _get_or_create_gateway_agent(session_id)
    else:
        # inprocess(기본): Phase 2 warm 에이전트 캐시.
        agent = _get_or_create_agent(session_id)

    async for ev in _stream_review(agent, query):
        yield ev


if __name__ == "__main__":
    app.run()
