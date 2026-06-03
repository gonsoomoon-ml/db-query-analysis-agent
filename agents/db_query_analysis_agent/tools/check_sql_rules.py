"""MySQL/SQL 쿼리 규칙 기반 정적 분석 (순수 함수 + @tool).

탐지: DELETE/UPDATE without WHERE, DROP, TRUNCATE, SELECT *, LIKE 선행 와일드카드.
주석(-- , /* */)은 WHERE 탐지 전에 제거해 오탐 최소화. 1차 리뷰용 경량 정규식.
"""
import re

from strands import tool

_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_COMMENT_LINE = re.compile(r"--[^\n]*")

CHECKED_RULES = [
    "DELETE_WITHOUT_WHERE", "UPDATE_WITHOUT_WHERE", "DROP",
    "TRUNCATE", "SELECT_STAR", "LIKE_LEADING_WILDCARD",
]


def _strip_comments(sql: str) -> str:
    return _COMMENT_LINE.sub(" ", _COMMENT_BLOCK.sub(" ", sql))


def evaluate_sql_rules(sql: str) -> dict:
    """규칙 위반 목록 반환. {"violations": [...], "checked_rules": [...]}."""
    s = _strip_comments(sql or "")
    has_where = re.search(r"\bWHERE\b", s, re.IGNORECASE) is not None
    v: list[dict] = []

    if re.search(r"\bDELETE\s+FROM\b", s, re.IGNORECASE) and not has_where:
        v.append({"rule": "DELETE_WITHOUT_WHERE", "severity": "critical",
                  "message": "WHERE 없는 DELETE — 전체 행 삭제 위험"})
    if (re.search(r"\bUPDATE\b", s, re.IGNORECASE)
            and re.search(r"\bSET\b", s, re.IGNORECASE) and not has_where):
        v.append({"rule": "UPDATE_WITHOUT_WHERE", "severity": "critical",
                  "message": "WHERE 없는 UPDATE — 전체 행 변경 위험"})
    if re.search(r"\bDROP\s+(TABLE|DATABASE|INDEX|VIEW)\b", s, re.IGNORECASE):
        v.append({"rule": "DROP", "severity": "critical",
                  "message": "DROP 문 — 객체 영구 삭제"})
    if re.search(r"\bTRUNCATE\b", s, re.IGNORECASE):
        v.append({"rule": "TRUNCATE", "severity": "critical",
                  "message": "TRUNCATE — 전체 행 삭제(롤백 제약)"})
    if re.search(r"\bSELECT\s+\*", s, re.IGNORECASE):
        v.append({"rule": "SELECT_STAR", "severity": "warning",
                  "message": "SELECT * — 불필요한 컬럼 로드"})
    if re.search(r"\bLIKE\s+N?['\"]%", s, re.IGNORECASE):
        v.append({"rule": "LIKE_LEADING_WILDCARD", "severity": "warning",
                  "message": "LIKE 선행 와일드카드 — 인덱스 미사용"})

    return {"violations": v, "checked_rules": CHECKED_RULES}


@tool
def check_sql_rules(sql: str) -> dict:
    """MySQL/SQL 쿼리의 규칙 기반 정적 분석.

    DELETE/UPDATE without WHERE, DROP, TRUNCATE, SELECT *, LIKE 선행 와일드카드 탐지.
    필수 파라미터: sql (str) — 반드시 "sql" 키 사용. "query" 금지.
    """
    return evaluate_sql_rules(sql)
