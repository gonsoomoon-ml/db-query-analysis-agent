"""analyze-sql-with-llm Lambda — AgentCore Gateway 타겟 (strands + Bedrock 필요).

패키징: run_analysis 는 strands BedrockModel 을 사용하므로 strands + boto3 필요 → deploy.sh 가
`tools meta shared` 벤더링 + cp312 휠 pip install(Lambda python3.12 ABI 일치).

AgentCore Gateway 호출 패턴:
- 1 tool / Lambda (target 1:1 매핑) — 단일 tool 이라 bedrockAgentCoreToolName 디스패치 불필요.
- input: event 자체가 inputSchema.properties 의 값 dict (wrapper 없음).
"""
import asyncio

from agents.db_query_analysis_agent.tools.analyze_sql_with_llm import run_analysis


def handler(event, context):
    params = event or {}
    sql = params.get("sql", "")
    violations_json = params.get("violations_json", "")
    meta_summary = params.get("meta_summary", "")
    try:
        return asyncio.run(run_analysis(sql, violations_json, meta_summary))
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
