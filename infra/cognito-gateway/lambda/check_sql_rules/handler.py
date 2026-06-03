"""check-sql-rules Lambda — AgentCore Gateway 타겟 (strands-free 경량).

패키징: check_sql_rules 코어는 strands 의존 없음 → Lambda 레이어 없이 배포 가능.
배포/패키징은 별도 태스크에서 처리.

AgentCore Gateway 호출 패턴:
- tool 식별자: context.client_context.custom["bedrockAgentCoreToolName"]
- input: event 자체가 inputSchema.properties 의 값 dict
"""
from agents.db_query_analysis_agent.tools.check_sql_rules import check_rules_core


def _tool_name(context) -> str:
    cc = getattr(context, "client_context", None)
    custom = getattr(cc, "custom", None) if cc else None
    return (custom or {}).get("bedrockAgentCoreToolName", "")


def handler(event, context):
    sql = (event or {}).get("sql", "")
    try:
        return check_rules_core(sql)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
