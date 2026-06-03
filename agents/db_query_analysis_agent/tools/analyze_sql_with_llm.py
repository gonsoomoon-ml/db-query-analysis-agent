"""AWS Bedrock(Strands BedrockModel)로 SQL 심층 분석 (plain @tool).

LLM 호출은 base code 패턴 — build_bedrock_model() + minimal tool-less Agent 1회 호출.
raw boto3 converse 미사용. analyze는 plain tool (orchestration 없음).
"""
import json
from pathlib import Path

from strands import Agent, tool
from strands.handlers.callback_handler import null_callback_handler

from agents.db_query_analysis_agent.shared.model import build_bedrock_model

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "shared" / "prompts" / "analyze_prompt.md"


def _build_analyzer() -> Agent:
    """분석 전용 tool-less Agent (ANALYZE_* 모델 구성)."""
    model = build_bedrock_model(
        "ANALYZE_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "ANALYZE_TEMPERATURE", 0.1, "ANALYZE_MAX_TOKENS", 2048,
        cache_tools=False,
    )
    return Agent(
        model=model,
        system_prompt=_PROMPT_PATH.read_text(encoding="utf-8"),
        callback_handler=null_callback_handler,
    )


def _parse(text: str) -> dict:
    try:
        data = json.loads(text)
        return {
            "index_efficiency": data.get("index_efficiency", ""),
            "service_impact": data.get("service_impact", ""),
            "optimizations": data.get("optimizations", []),
            "analysis": data.get("analysis", text),
        }
    except (json.JSONDecodeError, TypeError):
        return {"index_efficiency": "", "service_impact": "",
                "optimizations": [], "analysis": text}


def run_analysis(sql: str, violations_json: str, meta_summary: str = "") -> dict:
    """심층 분석 1회 호출 + 파싱. 실패 시 {"error":..., "analysis":""}."""
    try:
        analyzer = _build_analyzer()
        user_msg = (
            f"## SQL\n{sql}\n\n"
            f"## 이미 탐지된 규칙 위반 (재언급 금지)\n{violations_json}\n\n"
            f"## 테이블 메타\n{meta_summary or '(없음)'}"
        )
        return _parse(str(analyzer(user_msg)))
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "analysis": ""}


@tool
def analyze_sql_with_llm(sql: str, violations_json: str, meta_summary: str = "") -> dict:
    """AWS Bedrock Claude로 SQL의 인덱스 효율/서비스 영향도/최적화를 분석.

    규칙 체크(violations_json)에 이미 있는 항목은 재언급하지 않음.
    파라미터: sql(str), violations_json(str), meta_summary(str, 선택). "sql" 키 필수.
    """
    return run_analysis(sql, violations_json, meta_summary)
