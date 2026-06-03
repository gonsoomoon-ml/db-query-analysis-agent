"""mock 메타 backend — data/mock/table_meta.py 의 TABLE_META 조회."""
from data.mock.table_meta import TABLE_META


def lookup(table_name: str) -> dict | None:
    """테이블명(대소문자 무시)으로 메타 dict 반환. 미존재 시 None."""
    return TABLE_META.get(table_name.lower())
