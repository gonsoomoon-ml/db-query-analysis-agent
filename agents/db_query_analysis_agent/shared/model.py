"""Bedrock 모델 구성 공통 헬퍼.

create_agent(메인 에이전트)와 analyze_sql_with_llm(분석 호출)이 동일 경로로
BedrockModel을 생성 → Bedrock 호출 방식 단일화(raw boto3 혼용 0). 별도 모듈로 둬
agent.py ↔ analyze tool 순환 import 회피.
"""
import os

from strands.models import BedrockModel


def build_bedrock_model(
    model_id_env: str, default_model: str,
    temp_env: str, default_temp: float,
    max_tok_env: str, default_max_tok: int,
    *, cache_tools: bool = True,
) -> BedrockModel:
    """env 우선 + 기본값으로 BedrockModel 생성. region은 AWS_REGION(기본 us-east-1)."""
    kwargs = {
        "model_id": os.environ.get(model_id_env) or default_model,
        "region_name": os.environ.get("AWS_REGION") or "us-east-1",
        "temperature": float(os.environ.get(temp_env) or default_temp),
        "max_tokens": int(os.environ.get(max_tok_env) or default_max_tok),
    }
    if cache_tools:
        kwargs["cache_tools"] = "default"
    return BedrockModel(**kwargs)
