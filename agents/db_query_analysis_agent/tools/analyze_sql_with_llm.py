"""AWS Bedrock(Strands BedrockModel)로 SQL 심층 분석 (plain @tool).

LLM 호출은 Strands BedrockModel을 직접 사용 — Agent를 만들지 않음 ("단일 에이전트"
결정 준수). raw boto3 converse 미사용. analyze는 plain tool.
"""
import json
import re
from pathlib import Path

from strands import tool

from agents.db_query_analysis_agent.shared.model import build_bedrock_model

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "shared" / "prompts" / "analyze_prompt.md"


def _analyze_model():
    """분석 전용 BedrockModel (ANALYZE_* 구성)."""
    return build_bedrock_model(
        model_id_env="ANALYZE_MODEL_ID",
        default_model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        temp_env="ANALYZE_TEMPERATURE", default_temp=0.1,
        max_tok_env="ANALYZE_MAX_TOKENS", default_max_tok=2048,
        cache_tools=False,
    )


_JSON_OBJ = re.compile(r"\{.*\}", re.DOTALL)


async def _invoke_model(user_msg: str) -> str:
    """BedrockModel 직접 1회 호출 — 텍스트 누적 반환 (Agent 미생성)."""
    model = _analyze_model()
    system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")
    messages = [{"role": "user", "content": [{"text": user_msg}]}]
    chunks: list[str] = []
    async for event in model.stream(messages, tool_specs=None, system_prompt=system_prompt):
        delta = (event.get("contentBlockDelta") or {}).get("delta") or {}
        if "text" in delta:
            chunks.append(delta["text"])
    return "".join(chunks)


def _parse(text: str) -> dict:
    m = _JSON_OBJ.search(text or "")
    candidate = m.group(0) if m else (text or "")
    try:
        data = json.loads(candidate)
        return {
            "index_efficiency": data.get("index_efficiency", ""),
            "service_impact": data.get("service_impact", ""),
            "optimizations": data.get("optimizations", []),
            "analysis": data.get("analysis", text),
        }
    except (json.JSONDecodeError, TypeError):
        return {"index_efficiency": "", "service_impact": "",
                "optimizations": [], "analysis": text}


async def run_analysis(sql: str, violations_json: str, meta_summary: str = "") -> dict:
    """심층 분석 1회 호출 + 파싱. 실패 시 {"error":..., "analysis":""}."""
    try:
        user_msg = (
            f"## SQL\n{sql}\n\n"
            f"## 이미 탐지된 규칙 위반 (재언급 금지)\n{violations_json}\n\n"
            f"## 테이블 메타\n{meta_summary or '(없음)'}"
        )
        return _parse(await _invoke_model(user_msg))
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "analysis": ""}


@tool
async def analyze_sql_with_llm(sql: str, violations_json: str, meta_summary: str = "") -> dict:
    """AWS Bedrock Claude로 SQL의 인덱스 효율/서비스 영향도/최적화를 분석.

    규칙 체크(violations_json)에 이미 있는 항목은 재언급하지 않음.
    파라미터: sql(str), violations_json(str), meta_summary(str, 선택). "sql" 키 필수.
    """
    return await run_analysis(sql, violations_json, meta_summary)
