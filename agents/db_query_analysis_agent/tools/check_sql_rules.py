"""MySQL/SQL 쿼리 규칙 기반 정적 분석 (순수 함수, strands-free).

탐지: DELETE/UPDATE without WHERE, DROP, TRUNCATE, SELECT *, LIKE 선행 와일드카드.
주석(-- , /* */)과 문자열 리터럴을 키워드 탐지 전에 제거해 오탐 최소화하고,
세미콜론 기준 문장 단위로 평가해 다중 문장에서의 WHERE 누락 오탐을 방지한다.
1차 리뷰용 경량 정규식.

@tool 래퍼는 agents/db_query_analysis_agent/tools/strands_tools.py 에 있음.
"""
import re

_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_COMMENT_LINE = re.compile(r"--[^\n]*")
# 문자열 리터럴: 'foo'('' escape), "bar"("" escape)
_STRING = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"")

CHECKED_RULES = [
    "DELETE_WITHOUT_WHERE", "UPDATE_WITHOUT_WHERE", "DROP",
    "TRUNCATE", "SELECT_STAR", "LIKE_LEADING_WILDCARD",
]


def _strip_comments(sql: str) -> str:
    return _COMMENT_LINE.sub(" ", _COMMENT_BLOCK.sub(" ", sql))


def _statement_violations(stmt: str) -> list[dict]:
    """단일 문장(주석 제거됨, 문자열 리터럴 유지)의 위반 목록.

    키워드/WHERE 탐지는 문자열 제거본(nostr)에서 — 문자열 속 키워드 오탐 방지.
    LIKE 선행 와일드카드는 문자열 리터럴이 살아있어야 탐지 가능 → 원본 stmt에서 검사.
    """
    nostr = _STRING.sub(" ", stmt)
    has_where = re.search(r"\bWHERE\b", nostr, re.IGNORECASE) is not None
    v: list[dict] = []
    if re.search(r"\bDELETE\s+FROM\b", nostr, re.IGNORECASE) and not has_where:
        v.append({"rule": "DELETE_WITHOUT_WHERE", "severity": "critical",
                  "message": "WHERE 없는 DELETE — 전체 행 삭제 위험"})
    if (re.search(r"\bUPDATE\b", nostr, re.IGNORECASE)
            and re.search(r"\bSET\b", nostr, re.IGNORECASE) and not has_where):
        v.append({"rule": "UPDATE_WITHOUT_WHERE", "severity": "critical",
                  "message": "WHERE 없는 UPDATE — 전체 행 변경 위험"})
    if re.search(r"\bDROP\s+(TABLE|DATABASE|INDEX|VIEW)\b", nostr, re.IGNORECASE):
        v.append({"rule": "DROP", "severity": "critical",
                  "message": "DROP 문 — 객체 영구 삭제"})
    if re.search(r"\bTRUNCATE\b", nostr, re.IGNORECASE):
        v.append({"rule": "TRUNCATE", "severity": "critical",
                  "message": "TRUNCATE — 전체 행 삭제(롤백 제약)"})
    if re.search(r"\bSELECT\s+\*", nostr, re.IGNORECASE):
        v.append({"rule": "SELECT_STAR", "severity": "warning",
                  "message": "SELECT * — 불필요한 컬럼 로드"})
    if re.search(r"\bLIKE\s+N?['\"]%", stmt, re.IGNORECASE):
        v.append({"rule": "LIKE_LEADING_WILDCARD", "severity": "warning",
                  "message": "LIKE 선행 와일드카드 — 인덱스 미사용"})
    return v


def check_rules_core(sql: str) -> dict:
    """규칙 위반 목록 반환(순수 함수 — @tool 래퍼와 Lambda 핸들러 공유 진입점).

    세미콜론 기준 문장 단위 평가 + 규칙명 dedup. {"violations":[...], "checked_rules":[...]}.
    """
    s = _strip_comments(sql or "")
    violations: list[dict] = []
    seen: set[str] = set()
    for stmt in s.split(";"):
        for viol in _statement_violations(stmt):
            if viol["rule"] not in seen:
                seen.add(viol["rule"])
                violations.append(viol)
    return {"violations": violations, "checked_rules": CHECKED_RULES}


