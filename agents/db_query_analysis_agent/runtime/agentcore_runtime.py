"""AgentCore Runtime 엔트리포인트 — db-query-analysis-agent.

TOOLS_SOURCE 환경변수로 모드를 선택한다.

- inprocess (기본): Phase 2 동작 유지 — 세션별 warm 에이전트 캐시(agent.messages 보존).
- gateway: invoke마다 stateless. @requires_access_token 데코레이터가 workload identity
  컨텍스트(runtimeUserId)로 Cognito M2M 토큰을 자동 획득해 Gateway MCP 도구를 사용한다.

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

    TOOLS_SOURCE=gateway: invoke마다 stateless. @requires_access_token 데코레이터가
                          workload identity 컨텍스트로 토큰을 자동 획득.
    TOOLS_SOURCE=inprocess(기본): 세션별 warm 에이전트 캐시(멀티턴 유지).
    """
    query = (payload or {}).get("query") or ""
    if not query:
        yield {"type": "agent_text_stream", "text": '[error] payload에 "query" 누락'}
        yield {"type": "workflow_complete", "text": ""}
        return

    tools_source = os.environ.get("TOOLS_SOURCE", "inprocess")

    if tools_source == "gateway":
        # per-invoke, stateless — Gateway MCP 도구 사용.
        # 토큰은 AgentCore Identity 데코레이터 경유(workload identity runtimeUserId 컨텍스트).
        tok = await _fetch_gateway_token()
        with agent_session(token=tok) as agent:
            async for ev in _stream_review(agent, query):
                yield ev
    else:
        # inprocess 분기: 세션별 warm 에이전트 캐시 유지(Phase 2 동작 그대로).
        # 세션 키: AgentCore runtimeSessionId(context, 헤더) 우선 → payload → "default".
        # 헤더 기반이라 표준 호출(payload에 session_id 미포함)에서도 멀티턴 세션이 격리된다.
        session_id = (
            getattr(context, "session_id", None)
            or (payload or {}).get("session_id")
            or "default"
        )
        agent = _get_or_create_agent(session_id)
        async for ev in _stream_review(agent, query):
            yield ev


if __name__ == "__main__":
    app.run()
