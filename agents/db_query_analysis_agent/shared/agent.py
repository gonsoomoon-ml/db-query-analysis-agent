"""db-query-analysis-agent 팩토리 — 단일 create_agent 진실 원천 (AgentCore 승격 대비).

tool은 caller 주입(phase-agnostic). build_db_query_agent()가 표준 tool 3종 조립.
planner/executor/summarizer는 Strands tool-use loop가 단일 모델로 흡수.
"""
from pathlib import Path

from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.handlers.callback_handler import null_callback_handler
from strands.types.content import SystemContentBlock

from agents.db_query_analysis_agent.shared.model import build_bedrock_model
from agents.db_query_analysis_agent.tools.analyze_sql_with_llm import analyze_sql_with_llm
from agents.db_query_analysis_agent.tools.check_sql_rules import check_sql_rules
from agents.db_query_analysis_agent.tools.get_table_meta import get_table_meta

_PROMPTS_DIR = Path(__file__).parent / "prompts"
AGENT_NAME = "db-query-analysis-agent"
AGENT_DESC = "MySQL/SQL 쿼리 1차 리뷰 에이전트 — 규칙 체크 + 메타 조회 + LLM 분석"


def _load_prompt(filename: str) -> str:
    return (_PROMPTS_DIR / filename).read_text(encoding="utf-8")


def create_agent(tools: list, system_prompt_filename: str) -> Agent:
    """Strands Agent 생성. model_id/temp/max_tokens는 DBQUERY_* env."""
    model = build_bedrock_model(
        "DBQUERY_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "DBQUERY_TEMPERATURE", 0.1, "DBQUERY_MAX_TOKENS", 4096,
    )
    prompt = _load_prompt(system_prompt_filename)
    agent = Agent(
        model=model,
        tools=tools,
        system_prompt=[
            SystemContentBlock(text=prompt),
            SystemContentBlock(cachePoint={"type": "default"}),
        ],
        conversation_manager=SlidingWindowConversationManager(window_size=20),
        callback_handler=null_callback_handler,
        name=AGENT_NAME,
        description=AGENT_DESC,
    )
    return agent


def build_db_query_agent() -> Agent:
    """표준 db-query-analysis-agent 조립 — tool 3종 + system_prompt.md."""
    return create_agent(
        tools=[check_sql_rules, get_table_meta, analyze_sql_with_llm],
        system_prompt_filename="system_prompt.md",
    )
