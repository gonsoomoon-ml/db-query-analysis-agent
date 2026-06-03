"""analyze-sql-with-llm Lambda — AgentCore Gateway 타겟 (strands + Bedrock 필요).

패키징: run_analysis 는 strands BedrockModel 을 사용하므로 strands + boto3 필요.
check/meta Lambda 와 달리 무거운 레이어가 필요. 배포/패키징은 별도 태스크에서 처리.

AgentCore Gateway 호출 패턴:
- tool 식별자: context.client_context.custom["bedrockAgentCoreToolName"]
- input: event 자체가 inputSchema.properties 의 값 dict
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
