"""TABLE_META를 Redis에 적재 (Stage 2). tablemeta:{DEMO_USER}:{name} = JSON.

사용: uv run python -m data.seed.seed_redis   (redis 가동 + redis 패키지 필요)
"""
import json
import os

from data.mock.table_meta import TABLE_META
from shared.config import demo_user


def main() -> None:
    import redis  # lazy
    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    client = redis.Redis.from_url(url, decode_responses=True)
    for name, meta in TABLE_META.items():
        client.set(f"tablemeta:{demo_user()}:{name}", json.dumps(meta, ensure_ascii=False))
    print(f"✅ {len(TABLE_META)}개 테이블 메타를 Redis에 적재 (prefix={demo_user()}, {url})")


if __name__ == "__main__":
    main()
