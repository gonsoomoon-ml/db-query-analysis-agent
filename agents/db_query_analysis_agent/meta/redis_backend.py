"""redis 메타 backend — tablemeta:{name} 키의 JSON 조회 (Stage 2). redis lazy import."""
import json
import os

from shared.config import demo_user


def _client():
    import redis  # lazy — mock 모드에선 redis 미설치여도 동작
    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(url, decode_responses=True)


def lookup(table_name: str) -> dict | None:
    """tablemeta:{name} JSON 조회. 연결 실패 시 한국어 RuntimeError."""
    try:
        raw = _client().get(f"tablemeta:{demo_user()}:{table_name.lower()}")
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Redis 연결 실패 ({os.environ.get('REDIS_URL')}): {e}") from e
    return json.loads(raw) if raw else None
