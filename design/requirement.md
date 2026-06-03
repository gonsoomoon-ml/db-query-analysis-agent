기존 진행되어있는 부분에서 windmill, hermes는 별개로 수행되고있으며,

필요한 부분에 대해 hermes가 windmill을 http curl기반으로 호출하여 답을 받고, 처리해주는 형태를 취하고있습니다.

그리고, Windmill에서는 별도의 스킬을 활용하고 있지 않고 직접 개발된 tool을 수행하도록 되어있으며, hermes로 신규 셋업되는 항목들만 skill을 활용하고 있습니다.

(초기에 생성한 tool이기도 하고  Windmill dependency 존재로 인해 tool부분도 새로 생성시켜 변환하는게 더 나을듯합니다.)



목표중 하나인 DB query 분석 agent는 아래와 같이 설정하여 사용중입니다.

name: db-query-analysis-agent



description: "MySQL 쿼리 1차 리뷰 에이전트. 규칙 기반 체크 + LLM 분석으로 DBA의 반복 리뷰 작업을 자동화합니다. Slack slash command(/db-query-review) 또는 다른 에이전트에서 호출 가능합니다."





min_loops: 1

max_loops: 10



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









이번 목표중 하나인 SRE-agent (모니터링 / alert분석)은 완전히 신규로 만들어야 해서 기존을 참조하긴 어렵지만, 비용분석 쪽은 아래와 같이 사용중입니다.





name: cost_analyzer



description: >

    CAST AI 클러스터의 비용 분석 에이전트. 클러스터별 core 수를 집계하고

    Karpenter/ClusterAutoScaler 대비 절감액을 EDP/SP 할인 적용하여 계산합니다.

    프로바이더별(AWS/GCP/Azure) 분리 계산, h200 GPU 노드 제외,

    Karpenter 전용 클러스터 포함/제외 두 버전을 출력합니다.

    중요: 사용자가 요청한 날짜/기간은 그대로 tool에 전달할 것. 날짜 유효성 판단 금지.



min_loops: 1

max_loops: 1



defaults:

  env: "production"

  aws_region: "ap-northeast-2"

  current_year: "2026"



models:

  planner:

    provider: bedrock

    model_id: global.anthropic.claude-sonnet-4-5-20250929-v1:0

    max_tokens: 2048

    temperature: 0.1

    region: us-east-2

  executor:

    provider: bedrock

    model_id: global.anthropic.claude-sonnet-4-5-20250929-v1:0

    max_tokens: 512

    temperature: 0.1

    region: us-east-2

  summarizer:

    provider: bedrock

    model_id: global.anthropic.claude-haiku-4-5-20251001-v1:0

    max_tokens: 4096

    temperature: 0.1

    region: us-east-2

  compress:

    provider: bedrock

    model_id: global.anthropic.claude-haiku-4-5-20251001-v1:0

    max_tokens: 512

    temperature: 0.0

    region: us-east-2



tools:

  - name: castai_savings_calculator

    windmill_path: f/agents/cost_analyzer/tools/castai_savings_calculator

    description: >

      CAST AI 클러스터 core 수 집계 및 Karpenter/CAS 대비 절감액 계산.

      두 가지 모드: snapshot(현재 시점), period(기간별 일별 비용).

      파라미터: mode(str, 'snapshot'|'period'),

      start_date/end_date(str, ISO8601, period 모드용),

      cluster_ids(list[str], 빈 리스트면 전체),

      exclude_gpu_instance_types(list[str], 기본 p5en.48xlarge/p5e.48xlarge),

      karpenter_cluster_ids(list[str], Karpenter 전용 클러스터),

      edp_od_discount_pct(float, EDP On-Demand 할인율, 기본 54.28%),

      edp_spot_discount_pct(float, EDP Spot 할인율, 기본 40%),

      savings_plan_discount_pct(float, SP 할인율, on-demand에만),

      karpenter_overhead_pct(float, 기본 25%),

      cas_overhead_pct(float, 기본 40%),

      aws/gcp/azure_ref_price_per_hour(float, 프로바이더별 기준 가격),

      ref_vcpu(int, 기준 인스턴스 vCPU 수, 기본 8).

      인증: Windmill Variable CAST_AI_API_KEY 자동 로딩.

    max_calls: 1



sub_agents: []

