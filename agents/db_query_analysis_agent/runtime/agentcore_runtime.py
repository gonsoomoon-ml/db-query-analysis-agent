"""AgentCore Runtime 엔트리포인트 — db-query-analysis-agent.

build_db_query_agent를 세션별로 캐시(멀티턴 warm) + SSE 스트리밍. 로컬 == 관리형
단일 truth. SigV4 인증. 컨테이너에선 deploy가 agents/,shared/,data/를 build context로 복사.
"""
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

# 위 부트스트랩이 agents/·shared/ 루트를 sys.path에 넣으므로 컨테이너(/app)·로컬 모두 이 import로 충분.
from agents.db_query_analysis_agent.shared.agent import build_db_query_agent  # noqa: E402

app = BedrockAgentCoreApp()
_session_agents: dict[str, Any] = {}


def _get_or_create_agent(session_id: str):
    """세션별 Agent 캐시 — 같은 id면 재사용(agent.messages 보존 = 멀티턴)."""
    if session_id in _session_agents:
        return _session_agents[session_id]
    agent = build_db_query_agent()
    _session_agents[session_id] = agent
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
    """Operator → 진입. payload {query, session_id?} → SSE."""
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
    agent = _get_or_create_agent(session_id)
    async for ev in _stream_review(agent, query):
        yield ev


if __name__ == "__main__":
    app.run()
