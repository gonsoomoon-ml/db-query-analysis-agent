"""redis 메타 backend — tablemeta:{DEMO_USER}:{name} 키의 JSON 조회 (Stage 2). redis lazy import."""
import json
import os

from shared.config import demo_user

_client_cache = None


def _client():
    """Redis 클라이언트(모듈 캐시 — 커넥션 풀 재사용). redis lazy import."""
    global _client_cache
    if _client_cache is None:
        import redis  # lazy — mock 모드에선 redis 미설치여도 동작
        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        _client_cache = redis.Redis.from_url(url, decode_responses=True)
    return _client_cache


def lookup(table_name: str) -> dict | None:
    """tablemeta:{DEMO_USER}:{name} JSON 조회. 연결 실패 시 한국어 RuntimeError."""
    try:
        raw = _client().get(f"tablemeta:{demo_user()}:{table_name.lower()}")
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Redis 연결 실패 ({os.environ.get('REDIS_URL')}): {e}") from e
    return json.loads(raw) if raw else None
