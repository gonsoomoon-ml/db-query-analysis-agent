"""SQL에서 테이블명 추출 + 메타데이터 조회 (순수 함수 + @tool).

FROM/JOIN/INTO/UPDATE/TABLE 다음 식별자를 추출(별칭/백틱/스키마 접두 처리),
META_BACKEND를 통해 메타 조회, large_table 플래그 부여.
"""
import os
import re

from strands import tool

from agents.db_query_analysis_agent.meta import current_backend, lookup_table_meta

_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+"
    r"([`\"\[]?[A-Za-z_]\w*[`\"\]]?(?:\.[`\"\[]?[A-Za-z_]\w*[`\"\]]?)?)",
    re.IGNORECASE,
)

_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_COMMENT_LINE = re.compile(r"--[^\n]*")
_SQUOTE_STRING = re.compile(r"'(?:[^']|'')*'")  # 작은따옴표 문자열 리터럴만 (식별자 보호)


def _strip_noise(sql: str) -> str:
    """주석 + 작은따옴표 문자열 제거. 큰따옴표/백틱/대괄호(식별자)는 보존."""
    s = _COMMENT_LINE.sub(" ", _COMMENT_BLOCK.sub(" ", sql))
    return _SQUOTE_STRING.sub(" ", s)


def _clean_ident(tok: str) -> str:
    tok = tok.strip()
    if "." in tok:  # schema.table → table
        tok = tok.split(".")[-1]
    return tok.strip('`"[]').lower()


def extract_table_names(sql: str) -> list[str]:
    """SQL에서 테이블명 목록(중복 제거, 등장 순서) 추출. 주석/문자열 리터럴 내 키워드 무시."""
    names: list[str] = []
    for m in _TABLE_RE.finditer(_strip_noise(sql or "")):
        n = _clean_ident(m.group(1))
        if n and n not in names:
            names.append(n)
    return names


def collect_table_meta(sql: str) -> dict:
    """추출 테이블별 메타 + large_table 플래그. backend/threshold 동봉."""
    threshold = int(os.environ.get("LARGE_TABLE_THRESHOLD", "1000000"))
    tables: list[dict] = []
    for name in extract_table_names(sql):
        meta = lookup_table_meta(name)
        if meta is None:
            tables.append({"name": name, "found": False})
            continue
        row_count = int(meta.get("row_count", 0))
        tables.append({
            "name": meta["name"],
            "found": True,
            "columns": meta["columns"],
            "indexes": meta["indexes"],
            "row_count": row_count,
            "large_table": row_count > threshold,
        })
    return {"tables": tables, "backend": current_backend(),
            "large_table_threshold": threshold}


# Lambda 핸들러 등 외부에서 임포트할 수 있도록 공개 별칭 제공.
# @tool 래퍼와 Lambda 핸들러 양쪽이 동일한 순수 함수를 호출한다.
def table_meta_core(sql: str) -> dict:
    """테이블 메타 조회 순수 함수 (Lambda/직접 호출 공유 진입점).

    collect_table_meta 와 동일한 결과를 반환한다.
    반환 형식: {"tables": [...], "backend": str, "large_table_threshold": int}
    """
    return collect_table_meta(sql)


@tool
def get_table_meta(sql: str) -> dict:
    """SQL에서 테이블명을 추출하고 메타데이터(스키마/인덱스/행수)를 조회.

    행수 > LARGE_TABLE_THRESHOLD 면 large_table=true. 미존재 테이블은 found=false.
    필수 파라미터: sql (str) — 반드시 "sql" 키 사용. "table_name" 금지.
    """
    return table_meta_core(sql)
