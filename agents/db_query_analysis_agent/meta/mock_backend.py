"""mock 메타 backend — data/mock/table_meta.py 의 TABLE_META 조회."""
import copy

from data.mock.table_meta import TABLE_META


def lookup(table_name: str) -> dict | None:
    """테이블명(대소문자 무시)으로 메타 dict의 복사본 반환. 미존재 시 None.

    deepcopy 반환 — 호출자가 변형해도 TABLE_META(단일 진실 원천) 보호 + redis
    backend(json.loads로 새 dict 반환)와 동작 동형.
    """
    meta = TABLE_META.get(table_name.lower())
    return copy.deepcopy(meta) if meta is not None else None
