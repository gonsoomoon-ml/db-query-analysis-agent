# db-query-analysis-agent

MySQL/SQL 쿼리 1차 리뷰 에이전트 (Strands Agents + AWS Bedrock). 규칙 기반 체크 +
테이블 메타 조회 + LLM 심층 분석. 설계: `design/2026-06-03-db-query-analysis-agent-migration-spec.md`.

## 빠른 시작 (Stage 1: mock)

```bash
bash bootstrap.sh                # uv sync + .env 생성 + AWS_REGION/DEMO_USER + 테스트
# 단발
uv run -m agents.db_query_analysis_agent.local.run --sql "DELETE FROM orders"
# 대화형
uv run -m agents.db_query_analysis_agent.local.chat
```

## 타 에이전트/프로그램에서 호출

```python
from agents.db_query_analysis_agent.shared.review import review_sql
text = await review_sql("SELECT * FROM orders WHERE user_id = 1")
```

## Stage 2: Redis backend

```bash
docker run -d -p 6379:6379 redis
uv run --extra redis python -m data.seed.seed_redis
META_BACKEND=redis uv run -m agents.db_query_analysis_agent.local.run --sql "..."
```

## 테스트

```bash
uv run pytest -v
```

## 참고: 토큰 캐시 (Cache R/W 0/0)

단발/짧은 프롬프트에서는 토큰 usage의 `Cache R/W: 0/0`이 정상입니다. 캐시 가능한
prefix(도구 스키마 + 시스템 프롬프트)가 Bedrock 최소 캐시 크기(~1,024 토큰) 미만이면
캐시 엔트리가 생성되지 않습니다. prefix가 커지거나 멀티턴 대화가 누적되면 캐시가
활성화되어 Cache Read/Write가 0보다 커집니다. (`cache_tools`/`cachePoint`는 이미 구성됨.)
