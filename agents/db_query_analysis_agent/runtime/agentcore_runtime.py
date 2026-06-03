"""AgentCore Runtime 엔트리포인트 — db-query-analysis-agent.

build_db_query_agent를 세션별로 캐시(멀티턴 warm) + SSE 스트리밍. 로컬 == 관리형
단일 truth. SigV4 인증. 컨테이너에선 deploy가 agents/,shared/,data/를 build context로 복사.
"""
import sys
from pathlib import Path
from typing import Any, AsyncGenerator

_SCRIPT_DIR = Path(__file__).resolve().parent
# 로컬: repo root (parents[2] = repo root from runtime/ dir), 컨테이너: build context root(/app).
# parents[0]=db_query_analysis_agent, parents[1]=agents, parents[2]=repo root
for _p in (_SCRIPT_DIR.parents[2], _SCRIPT_DIR):
    if _p.exists() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from bedrock_agentcore.runtime import BedrockAgentCoreApp  # noqa: E402

try:
    from agents.db_query_analysis_agent.shared.agent import build_db_query_agent  # noqa: E402
except ModuleNotFoundError:  # 컨테이너 flatten 폴백
    from shared.agent import build_db_query_agent  # type: ignore # noqa: E402

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
    session_id = (payload or {}).get("session_id") or "default"
    agent = _get_or_create_agent(session_id)
    async for ev in _stream_review(agent, query):
        yield ev


if __name__ == "__main__":
    app.run()
