import asyncio
import contextlib
from agents.db_query_analysis_agent.runtime import agentcore_runtime as rt


class _FakeAgent:
    async def stream_async(self, _prompt):
        for chunk in ["리", "뷰"]:
            yield {"data": chunk}
        yield {"event": {"metadata": {"usage": {"totalTokens": 7}}}}


def _collect(agen):
    async def run():
        return [e async for e in agen]
    return asyncio.run(run())


def test_stream_review_yields_sse():
    events = _collect(rt._stream_review(_FakeAgent(), "SELECT 1"))
    types = [e["type"] for e in events]
    assert "agent_text_stream" in types
    assert types[-1] == "workflow_complete"
    text = "".join(e.get("text", "") for e in events if e["type"] == "agent_text_stream")
    assert text == "리뷰"
    usage = [e for e in events if e["type"] == "token_usage"]
    assert usage and usage[0]["usage"]["totalTokens"] == 7


def test_get_or_create_agent_caches(monkeypatch):
    calls = {"n": 0}

    def fake_build():
        calls["n"] += 1
        return object()
    monkeypatch.setattr(rt, "build_db_query_agent", fake_build)
    rt._session_agents.clear()
    a1 = rt._get_or_create_agent("s1")
    a2 = rt._get_or_create_agent("s1")
    a3 = rt._get_or_create_agent("s2")
    assert a1 is a2 and a1 is not a3
    assert calls["n"] == 2


def test_entrypoint_missing_query():
    events = _collect(rt.review({}))
    assert events[0]["type"] == "agent_text_stream" and "query" in events[0]["text"]
    assert events[-1]["type"] == "workflow_complete"


def test_entrypoint_prefers_context_session_id(monkeypatch):
    """C1: 세션 키는 context.session_id(AgentCore 헤더) 우선 — payload보다 앞선다."""
    seen = {}

    class _Ctx:
        session_id = "ctx-session"

    def fake_get(session_id):
        seen["sid"] = session_id
        return _FakeAgent()
    monkeypatch.setattr(rt, "_get_or_create_agent", fake_get)
    _collect(rt.review({"query": "SELECT 1", "session_id": "payload-session"}, context=_Ctx()))
    assert seen["sid"] == "ctx-session"


def test_entrypoint_session_id_fallbacks(monkeypatch):
    """context 없으면 payload.session_id, 그것도 없으면 'default'."""
    seen = {}

    def fake_get(session_id):
        seen["sid"] = session_id
        return _FakeAgent()
    monkeypatch.setattr(rt, "_get_or_create_agent", fake_get)
    _collect(rt.review({"query": "SELECT 1", "session_id": "payload-session"}, context=None))
    assert seen["sid"] == "payload-session"
    _collect(rt.review({"query": "SELECT 1"}, context=None))
    assert seen["sid"] == "default"


# ---------------------------------------------------------------------------
# gateway 분기 테스트
# ---------------------------------------------------------------------------

def test_review_gateway_mode(monkeypatch):
    """TOOLS_SOURCE=gateway 시:
    - _fetch_gateway_token()으로 토큰 획득
    - agent_session(token=tok) contextmanager 경유 (stateless, per-invoke)
    - _get_or_create_agent는 호출되지 않음
    """
    monkeypatch.setenv("TOOLS_SOURCE", "gateway")

    # _fetch_gateway_token async stub — "tok" 반환
    async def _fake_fetch_gateway_token():
        return "tok"

    monkeypatch.setattr(rt, "_fetch_gateway_token", _fake_fetch_gateway_token)

    # _get_or_create_agent가 호출되면 fail
    def _must_not_call(session_id):
        raise AssertionError("_get_or_create_agent must NOT be called in gateway mode")

    monkeypatch.setattr(rt, "_get_or_create_agent", _must_not_call)

    # agent_session contextmanager stub — token kwarg 검증 후 _FakeAgent yield
    captured_token = {}

    @contextlib.contextmanager
    def _fake_agent_session(system_prompt_filename=None, token=None):
        captured_token["token"] = token
        yield _FakeAgent()

    monkeypatch.setattr(rt, "agent_session", _fake_agent_session)

    events = _collect(rt.review({"query": "SELECT 1"}))

    # SSE 이벤트 검증
    types = [e["type"] for e in events]
    assert "agent_text_stream" in types
    assert types[-1] == "workflow_complete"
    text = "".join(e.get("text", "") for e in events if e["type"] == "agent_text_stream")
    assert text == "리뷰"

    # 토큰이 agent_session에 전달됐는지 검증
    assert captured_token["token"] == "tok"
