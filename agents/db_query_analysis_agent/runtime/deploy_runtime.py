#!/usr/bin/env python3
"""db-query-analysis-agent → AgentCore Runtime 배포 (toolkit). 첫 배포 ~5-10분.

agents/,shared/,data/ 를 build context로 복사 → Runtime.configure(Dockerfile/ECR/IAM
자동) → launch(Docker→ECR→Runtime) → 실행 role에 bedrock:InvokeModel 부착 → READY
대기 → RUNTIME_ARN 등을 repo root .env 에 저장.
"""
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import boto3
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[2]
os.chdir(SCRIPT_DIR)
load_dotenv(PROJECT_ROOT / ".env", override=True)

GREEN, YELLOW, RED, NC = "\033[0;32m", "\033[1;33m", "\033[0;31m", "\033[0m"
REGION = os.environ.get("AWS_REGION", "us-east-1")
DEMO_USER = os.environ.get("DEMO_USER", "ubuntu")
AGENT_NAME = f"db_query_analysis_agent_{DEMO_USER}"


def copy_into_build_context() -> None:
    """agents/,shared/,data/ 를 runtime/ 아래로 복사(컨테이너 import용)."""
    for name in ("agents", "shared", "data"):
        src = PROJECT_ROOT / name
        dst = SCRIPT_DIR / name
        if dst.exists():
            shutil.rmtree(dst)
        # "runtime" 제외 — 목적지(runtime/agents)가 src(agents) 안에 있어 재귀 폭주 방지.
        # entrypoint는 agents.db_query_analysis_agent.runtime을 import하지 않으므로 안전.
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "sample.db", "runtime"))
    print(f"{GREEN}✅ build context 복사{NC}")


def main() -> None:
    print(f"{YELLOW}[1/5] build context 복사{NC}")
    copy_into_build_context()

    print(f"{YELLOW}[2/5] Runtime configure{NC}")
    from bedrock_agentcore_starter_toolkit import Runtime
    rt = Runtime()
    rt.configure(
        agent_name=AGENT_NAME,
        entrypoint="agentcore_runtime.py",
        auto_create_execution_role=True,
        auto_create_ecr=True,
        requirements_file="requirements.txt",
        region=REGION,
        non_interactive=True,
    )

    print(f"{YELLOW}[3/5] launch (Docker→ECR→Runtime, ~5-10분){NC}")
    env_vars = {
        "AWS_REGION": REGION,
        "DEMO_USER": DEMO_USER,
        "META_BACKEND": os.environ.get("META_BACKEND", "mock"),
        "DBQUERY_MODEL_ID": os.environ.get("DBQUERY_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
        "DBQUERY_TEMPERATURE": os.environ.get("DBQUERY_TEMPERATURE", "0.1"),
        "DBQUERY_MAX_TOKENS": os.environ.get("DBQUERY_MAX_TOKENS", "4096"),
        "ANALYZE_MODEL_ID": os.environ.get("ANALYZE_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
        "ANALYZE_TEMPERATURE": os.environ.get("ANALYZE_TEMPERATURE", "0.1"),
        "ANALYZE_MAX_TOKENS": os.environ.get("ANALYZE_MAX_TOKENS", "2048"),
        "LARGE_TABLE_THRESHOLD": os.environ.get("LARGE_TABLE_THRESHOLD", "1000000"),
    }
    result = rt.launch(env_vars=env_vars, auto_update_on_conflict=True)
    print(f"{GREEN}✅ launch: {result.agent_arn}{NC}")

    print(f"{YELLOW}[4/5] 실행 role에 bedrock:InvokeModel 부착{NC}")
    ctrl = boto3.client("bedrock-agentcore-control", region_name=REGION)
    info = ctrl.get_agent_runtime(agentRuntimeId=result.agent_id)
    role_name = info["roleArn"].split("/")[-1]
    boto3.client("iam").put_role_policy(
        RoleName=role_name,
        PolicyName="BedrockInvoke",
        PolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                "Resource": "*",
            }],
        }),
    )
    print(f"{GREEN}✅ IAM: {role_name}/BedrockInvoke{NC}")

    print(f"{YELLOW}[5/5] READY 대기{NC}")
    status = "CREATING"
    for i in range(60):
        time.sleep(10)
        status = ctrl.get_agent_runtime(agentRuntimeId=result.agent_id)["status"]
        print(f"   [{i+1}/60] {status}")
        if status in ("READY", "CREATE_FAILED", "UPDATE_FAILED"):
            break
    if status != "READY":
        print(f"{RED}❌ 실패: {status} — aws logs tail /aws/bedrock-agentcore/runtimes/{AGENT_NAME} --region {REGION}{NC}")
        sys.exit(1)

    env_file = PROJECT_ROOT / ".env"
    keep = [ln for ln in (env_file.read_text().splitlines() if env_file.exists() else [])
            if not ln.startswith(("RUNTIME_ARN=", "RUNTIME_ID=", "RUNTIME_NAME="))]
    keep += [f"RUNTIME_NAME={AGENT_NAME}", f"RUNTIME_ID={result.agent_id}", f"RUNTIME_ARN={result.agent_arn}"]
    env_file.write_text("\n".join(keep) + "\n")
    print(f"{GREEN}✅ 배포 완료 — RUNTIME_ARN .env 저장 ({datetime.now():%H:%M}){NC}")
    print("   다음: uv run python -m agents.db_query_analysis_agent.runtime.invoke_runtime --query \"SELECT * FROM orders WHERE user_id=1\"")


if __name__ == "__main__":
    main()
