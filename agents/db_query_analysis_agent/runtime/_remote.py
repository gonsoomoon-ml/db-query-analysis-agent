"""원격 AgentCore Runtime 호출 + SSE 파싱 (invoke_runtime.py + chat.py 공유)."""
import json
import os

import boto3
from botocore.config import Config

DIM = "\033[2m"
NC = "\033[0m"


def _runtime_arn() -> str:
    arn = os.environ.get("RUNTIME_ARN")
    if not arn:
        raise SystemExit("[error] RUNTIME_ARN 미설정 — deploy_runtime.py 먼저 실행")
    return arn


def _parse_sse(line: bytes) -> dict | None:
    """SSE 'data: {...}' 또는 평문 JSON 라인 → dict. 실패 시 None."""
    try:
        text = line.decode("utf-8").strip()
        if text.startswith("data: "):
            text = text[6:]
        return json.loads(text) if text else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def stream_invoke(query: str, session_id: str | None = None) -> str:
    """invoke_agent_runtime(SigV4) → SSE 실시간 출력 + 전체 텍스트 반환."""
    region = os.environ.get("AWS_REGION", "us-east-1")
    cfg = Config(connect_timeout=300, read_timeout=600, retries={"max_attempts": 0})
    client = boto3.client("bedrock-agentcore", region_name=region, config=cfg)
    payload = {"query": query}
    if session_id:
        payload["session_id"] = session_id
    kwargs = {
        "agentRuntimeArn": _runtime_arn(),
        "qualifier": "DEFAULT",
        "runtimeUserId": os.environ.get("DEMO_USER", "ubuntu"),
        "payload": json.dumps(payload),
    }
    if session_id:
        kwargs["runtimeSessionId"] = session_id
    resp = client.invoke_agent_runtime(**kwargs)
    chunks: list[str] = []
    if "text/event-stream" in resp.get("contentType", ""):
        for line in resp["response"].iter_lines(chunk_size=1):
            ev = _parse_sse(line)
            if not ev:
                continue
            if ev.get("type") == "agent_text_stream":
                t = ev.get("text", "")
                chunks.append(t)
                print(t, end="", flush=True)
            elif ev.get("type") == "token_usage":
                print(f"\n{DIM}📊 usage: {ev.get('usage', {})}{NC}")
        print()
    else:
        body = resp["response"].read().decode("utf-8")
        print(body)
        chunks.append(body)
    return "".join(chunks)
