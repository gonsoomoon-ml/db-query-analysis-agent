"""get-table-meta Lambda — AgentCore Gateway 타겟 (strands-free 경량).

패키징: get_table_meta 코어는 strands 의존 없음 → deploy.sh 가 `tools meta` 서브패키지 벤더링.

AgentCore Gateway 호출 패턴:
- 1 tool / Lambda (target 1:1 매핑) — 단일 tool 이라 bedrockAgentCoreToolName 디스패치 불필요.
- input: event 자체가 inputSchema.properties 의 값 dict (wrapper 없음).
"""
from agents.db_query_analysis_agent.tools.get_table_meta import table_meta_core


def handler(event, context):
    sql = (event or {}).get("sql", "")
    try:
        return table_meta_core(sql)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
