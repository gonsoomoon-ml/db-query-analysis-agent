"""원격 AgentCore Runtime 호출 + SSE 파싱 (invoke_runtime.py + chat.py 공유)."""
import json
import os
import sys

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
        if text.startswith("data:"):                 # 공백 유무 모두 허용
            text = text[len("data:"):].lstrip()
        return json.loads(text) if text else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def iter_review_events(query: str, session_id: str | None = None):
    """invoke_agent_runtime(SigV4) → 파싱된 SSE 이벤트 dict를 순차 yield. 출력/표현 없음(순수 데이터).

    세션은 헤더(runtimeSessionId)로만 전달 — 엔트리포인트가 context에서 읽으므로 payload 중복 불필요.
    """
    region = os.environ.get("AWS_REGION", "us-east-1")
    cfg = Config(connect_timeout=300, read_timeout=600, retries={"max_attempts": 0})
    client = boto3.client("bedrock-agentcore", region_name=region, config=cfg)
    kwargs = {
        "agentRuntimeArn": _runtime_arn(),
        "qualifier": "DEFAULT",
        "runtimeUserId": os.environ.get("DEMO_USER", "ubuntu"),
        "payload": json.dumps({"query": query}),
    }
    if session_id:
        kwargs["runtimeSessionId"] = session_id
    resp = client.invoke_agent_runtime(**kwargs)
    if "text/event-stream" in resp.get("contentType", ""):
        for line in resp["response"].iter_lines():
            ev = _parse_sse(line)
            if ev:
                yield ev
    else:  # 비스트림 응답 — 본문 전체를 단일 텍스트 이벤트로
        body = resp["response"].read().decode("utf-8")
        yield {"type": "agent_text_stream", "text": body}


def stream_invoke(query: str, session_id: str | None = None) -> None:
    """이벤트를 콘솔에 실시간 출력(표현 전담). 정상 텍스트/usage는 stdout,
    에러·미지 프레임은 stdout 본문과 섞이지 않게 stderr로 구분 출력."""
    for ev in iter_review_events(query, session_id):
        etype = ev.get("type")
        if etype == "agent_text_stream":
            print(ev.get("text", ""), end="", flush=True)
        elif etype == "token_usage":
            print(f"\n{DIM}📊 usage: {ev.get('usage', {})}{NC}")
        elif etype == "workflow_complete":
            pass
        else:  # 에러 프레임({"error":...})/미지 타입
            print(f"\n{DIM}[runtime error] {ev}{NC}", file=sys.stderr, flush=True)
    print()
