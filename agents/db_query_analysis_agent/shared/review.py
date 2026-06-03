"""callable facade — 타 에이전트 / Slack adapter / 향후 A2A wrapper 공통 호출 표면.

단발·stateless. supervisor 없이 누구나 부르는 깨끗한 callee.
"""
from agents.db_query_analysis_agent.shared.agent import build_db_query_agent


async def review_sql(sql: str) -> str:
    """단발 SQL 리뷰 — db-query-analysis-agent 1회 실행 후 리뷰 텍스트 반환."""
    agent = build_db_query_agent()
    result = await agent.invoke_async(f"다음 SQL을 리뷰해줘:\n```sql\n{sql}\n```")
    return str(result)
