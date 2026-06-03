from agents.db_query_analysis_agent.shared.agent import build_db_query_agent, AGENT_NAME


def test_build_agent_offline():
    """BedrockModel 구성은 AWS 호출 없음 → 오프라인 생성 가능 + 프롬프트 파일 로드 검증."""
    agent = build_db_query_agent()
    assert agent is not None
    assert agent.name == AGENT_NAME
