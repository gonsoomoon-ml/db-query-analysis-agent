import asyncio
from agents.db_query_analysis_agent.shared import review as mod


class _FakeAgent:
    async def invoke_async(self, prompt):
        assert "SELECT 1" in prompt
        return "리뷰 결과"


def test_review_sql(monkeypatch):
    monkeypatch.setattr(mod, "build_db_query_agent", lambda: _FakeAgent())
    out = asyncio.run(mod.review_sql("SELECT 1"))
    assert out == "리뷰 결과"
