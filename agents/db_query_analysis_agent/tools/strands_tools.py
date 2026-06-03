"""Strands @tool 래퍼 모음 — 에이전트 런타임 전용.

코어 함수(check_rules_core, table_meta_core)는 strands-free 모듈에 있어
Lambda(check/meta)가 strands 없이 임포트 가능.
이 파일은 Strands Agent가 사용하는 @tool 객체만 정의하며,
코어 로직은 각 도구 모듈에서 직접 임포트한다.
"""
from strands import tool

from agents.db_query_analysis_agent.tools.analyze_sql_with_llm import analyze_sql_with_llm  # noqa: F401
from agents.db_query_analysis_agent.tools.check_sql_rules import check_rules_core
from agents.db_query_analysis_agent.tools.get_table_meta import table_meta_core


@tool
def check_sql_rules(sql: str) -> dict:
    """MySQL/SQL 쿼리의 규칙 기반 정적 분석.

    DELETE/UPDATE without WHERE, DROP, TRUNCATE, SELECT *, LIKE 선행 와일드카드 탐지.
    필수 파라미터: sql (str) — 반드시 "sql" 키 사용. "query" 금지.
    """
    return check_rules_core(sql)


@tool
def get_table_meta(sql: str) -> dict:
    """SQL에서 테이블명을 추출하고 메타데이터(스키마/인덱스/행수)를 조회.

    행수 > LARGE_TABLE_THRESHOLD 면 large_table=true. 미존재 테이블은 found=false.
    필수 파라미터: sql (str) — 반드시 "sql" 키 사용. "table_name" 금지.
    """
    return table_meta_core(sql)
