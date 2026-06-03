# db-query-analysis-agent

MySQL/SQL 쿼리 1차 리뷰 에이전트 (Strands Agents + AWS Bedrock). 규칙 기반 체크 +
테이블 메타 조회 + LLM 심층 분석. 설계: `design/2026-06-03-db-query-analysis-agent-migration-spec.md`.

## 빠른 시작 (Stage 1: mock)

```bash
uv sync --extra dev
cp .env.example .env          # AWS_REGION / 모델 ID 확인 (Bedrock 액세스 필요)
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
