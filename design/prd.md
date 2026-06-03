## Background
- DB query 분석 agent (db-query-analysis-agent) 를 기존에 사용 중이었습니다. (참조: ## Prior Implementation)
- MySQL 쿼리 1차 리뷰. 규칙 기반 체크 + LLM 분석으로 DBA 반복 작업 자동화를 수행 하였습니다.
  - Slack /db-query-review 또는 타 에이전트에서 호출
  - 모델: Haiku 4.5 (planner/executor/summarizer), min_loops:1 / max_loops:10 를 사용함.
  - Tools 3종: check_sql_rules(정적 규칙 분석), get_table_meta(테이블 메타 Redis 조회), analyze_sql_with_llm(Bedrock Claude 분석)
  - 파라미터 키 컨벤션 엄격: 반드시 sql 키 사용

## Goal
- 기존에 사용 에이전트를 아래의 코드를 기반으로 이관하는 작업을 합니다. (기존 코드 ## Base Code 섹션 참조)
- 현재 MySQL 을 기반으로 하고 있는데, 샘플로 코드를 작성을 하기에, Mockup 코드와 실습이 가능한 가벼윤 DBMS 로 구현하면 좋을 것 같습니다. 
- 유저 클라이언트의 요구사항은 Slack /db-query-review 또는 타 에이전트에서 호출인데, 간단하게 하기 위해서 베이스 코드의 A2A 의 supervisor agent 사용을 고려해주세요.
- background 섹션의 구현내용을 (에: Tool 3종) 구현해주세요.


## Prior Implementation
models:
  planner:
    provider: bedrock
    model_id: us.anthropic.claude-haiku-4-5-20251001-v1:0
    max_tokens: 2048
    temperature: 0.1
    region: us-east-2
  executor:
    provider: bedrock
    model_id: us.anthropic.claude-haiku-4-5-20251001-v1:0
    max_tokens: 1024
    temperature: 0.1
    region: us-east-2

  summarizer:
    provider: bedrock
    model_id: us.anthropic.claude-haiku-4-5-20251001-v1:0
    max_tokens: 2048
    temperature: 0.1
    region: us-east-2

tools:
  - name: check_sql_rules
    windmill_path: f/agents/db-query-analysis-agent/tool/check_sql_rules
    description: >
      MySQL 쿼리의 규칙 기반 정적 분석.
      DELETE/UPDATE without WHERE, DROP, TRUNCATE, SELECT *, LIKE leading wildcard 탐지.
      필수 파라미터: sql (str) — 반드시 "sql" 이라는 키 이름을 사용할 것. "query" 사용 금지.

    max_calls: 1
  - name: get_table_meta
    windmill_path: f/agents/db-query-analysis-agent/tool/get_table_meta

    description: >
      SQL에서 테이블명을 추출하고 Redis에서 메타데이터(스키마, 인덱스, 행수)를 조회.
      필수 파라미터: sql (str) — 반드시 "sql" 이라는 키 이름을 사용할 것. "table_name" 사용 금지.

    max_calls: 1

  - name: analyze_sql_with_llm
    windmill_path: f/agents/db-query-analysis-agent/tool/analyze_sql_with_llm
    description: >
      AWS Bedrock Claude로 SQL의 인덱스 효율, 서비스 영향도, 최적화 제안을 분석.
      규칙 체크에서 이미 플래그된 항목은 재언급하지 않음.
      파라미터: sql(str), violations_json(str, check_sql_rules 결과 JSON),
      meta_summary(str, get_table_meta 결과, 선택).

    max_calls: 1

sub_agents: []

defaults:
  large_table_threshold: 1000000


  ## Base Code
  ### AIOps Multi-Agent Workshop — A2A across team-owned agents
  URL: https://github.com/gonsoomoon-ml/aiops-multi-agent-workshop