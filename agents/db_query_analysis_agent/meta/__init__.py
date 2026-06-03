"""메타 backend 분기 — META_BACKEND(mock|sqlite|redis). 응답 shape 동형."""
import os


def current_backend() -> str:
    return os.environ.get("META_BACKEND", "mock").lower()


def lookup_table_meta(table_name: str) -> dict | None:
    """현재 backend에서 테이블 메타 조회. 미존재 시 None."""
    backend = current_backend()
    if backend == "redis":
        from .redis_backend import lookup
    elif backend == "sqlite":
        from .sqlite_backend import lookup
    else:
        from .mock_backend import lookup
    return lookup(table_name)
