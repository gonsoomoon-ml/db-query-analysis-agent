"""db-query-analysis-agent 팩토리 — 단일 create_agent 진실 원천 (AgentCore 승격 대비).

tool은 caller 주입(phase-agnostic). build_db_query_agent()가 표준 tool 3종 조립.
planner/executor/summarizer는 Strands tool-use loop가 단일 모델로 흡수.
agent_session() context manager 는 TOOLS_SOURCE env 로 in-process / gateway 분기.
"""
import contextlib
import os
from pathlib import Path

from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.handlers.callback_handler import null_callback_handler
from strands.types.content import SystemContentBlock

from agents.db_query_analysis_agent.shared.model import build_bedrock_model
from agents.db_query_analysis_agent.tools.strands_tools import (
    analyze_sql_with_llm,
    check_sql_rules,
    get_table_meta,
)
from shared.config import demo_user

_PROMPTS_DIR = Path(__file__).parent / "prompts"
AGENT_BASE_NAME = "db-query-analysis-agent"
AGENT_DESC = "MySQL/SQL 쿼리 1차 리뷰 에이전트 — 규칙 체크 + 메타 조회 + LLM 분석"


def agent_name() -> str:
    """멀티시연자 구분용 — 기본명 + DEMO_USER suffix (예: db-query-analysis-agent-alice)."""
    return f"{AGENT_BASE_NAME}-{demo_user()}"


def _load_prompt(filename: str) -> str:
    return (_PROMPTS_DIR / filename).read_text(encoding="utf-8")


def create_agent(tools: list, system_prompt_filename: str) -> Agent:
    """Strands Agent 생성. model_id/temp/max_tokens는 DBQUERY_* env."""
    model = build_bedrock_model(
        model_id_env="DBQUERY_MODEL_ID",
        default_model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        temp_env="DBQUERY_TEMPERATURE", default_temp=0.1,
        max_tok_env="DBQUERY_MAX_TOKENS", default_max_tok=4096,
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
        name=agent_name(),
        description=AGENT_DESC,
    )
    return agent


def build_db_query_agent() -> Agent:
    """표준 db-query-analysis-agent 조립 — tool 3종 + system_prompt.md."""
    return create_agent(
        tools=[check_sql_rules, get_table_meta, analyze_sql_with_llm],
        system_prompt_filename="system_prompt.md",
    )


@contextlib.contextmanager
def agent_session(system_prompt_filename: str | None = None, token: str | None = None):
    """TOOLS_SOURCE 스위치: inprocess(기본)=in-process @tool 에이전트, gateway=Gateway MCP 도구 에이전트.

    gateway 분기: __enter__마다 MCP 세션 open + list_tools + 새 에이전트 생성. MCP는 with 블록
    수명 동안 유지되므로 warm/stateless는 호출자가 블록을 얼마나 오래 여느냐로 결정된다
    (Runtime은 세션당 1회 열어 재사용 = warm; local run은 단발). gateway 관련 import는 lazy —
    inprocess 모드에서는 gateway.py 의존성/env 불필요.

    Args:
        system_prompt_filename: 시스템 프롬프트 파일명 (기본: system_prompt.md).
        token: gateway 분기에서 사용할 Cognito Bearer 토큰. None이면 내부에서 get_gateway_token()으로
               자동 획득 (로컬 호출용). AgentCore Runtime은 @requires_access_token 데코레이터가
               획득한 토큰을 이 인자로 전달해 workload identity 컨텍스트를 보존.
    """
    src = os.environ.get("TOOLS_SOURCE", "inprocess")
    if src == "gateway":
        from agents.db_query_analysis_agent.shared.gateway import (
            create_mcp_client,
            get_gateway_token,
        )
        tok = token or get_gateway_token()
        with create_mcp_client(tok) as mcp:
            tools = mcp.list_tools_sync()
            yield create_agent(
                tools=tools,
                system_prompt_filename=system_prompt_filename or "system_prompt.md",
            )
    else:
        yield build_db_query_agent()
