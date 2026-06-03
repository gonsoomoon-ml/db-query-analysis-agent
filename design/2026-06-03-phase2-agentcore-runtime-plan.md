# Phase 2 — AgentCore Runtime 승격 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`).

**Goal:** 로컬 단일 에이전트(`build_db_query_agent`)를 관리형 AgentCore Runtime으로 승격 — SigV4, SSE 스트리밍, 세션 멀티턴, 단발(`invoke_runtime.py`) + 원격 멀티턴 chat(`chat.py`).

**Architecture:** `runtime/agentcore_runtime.py`(BedrockAgentCoreApp + @entrypoint, 세션 캐시로 `build_db_query_agent` 재사용 → `stream_async` → SSE). 클라이언트는 boto3 `invoke_agent_runtime`(SigV4). `deploy_runtime.py`가 toolkit으로 Docker→ECR→Runtime 배포. 멀티라인 입력/원격 invoke는 공용 헬퍼로 DRY.

**Tech Stack:** Python 3.12, bedrock-agentcore + bedrock-agentcore-starter-toolkit, strands, boto3, sqlite3, pytest.

> 참조 spec: `design/2026-06-03-phase2-agentcore-runtime-spec.md`. 브랜치: `feat/phase2-runtime`. 컨벤션: docstring/comment 한국어, identifier 영어.
>
> **실행 노트:** 로컬 단위 테스트 가능 단위(repl, SSE 파싱, entrypoint 로직)는 TDD. deploy/컨테이너/SDK 세부는 dev-briefing(`managed-agentcore`)·AIOps Phase 3 패턴을 따르되 **실제 배포(Claude가 실행)로 검증**. AWS API/컨테이너 import 경로는 배포 중 실 SDK에 맞춰 보정.

---

## File Structure

| 파일 | 변경 | 책임 |
|---|---|---|
| `pyproject.toml` | 수정 | `bedrock-agentcore` + `bedrock-agentcore-starter-toolkit`(dev) 추가 |
| `shared/repl.py` | 신규 | `read_multiline_input` (로컬+원격 chat 공유) |
| `agents/db_query_analysis_agent/local/chat.py` | 수정 | `shared.repl` 사용 |
| `tests/test_repl.py` | 신규(test_chat_input 대체) | `read_multiline_input` 단위 |
| `agents/db_query_analysis_agent/runtime/__init__.py` | 신규 | 빈 패키지 |
| `…/runtime/agentcore_runtime.py` | 신규 | BedrockAgentCoreApp + @entrypoint + 세션 캐시 + SSE |
| `…/runtime/_remote.py` | 신규 | `stream_invoke` + SSE 파싱(invoke/chat 공유) |
| `…/runtime/invoke_runtime.py` | 신규 | 단발 CLI(SigV4) |
| `…/runtime/chat.py` | 신규 | 원격 멀티턴 REPL |
| `…/runtime/deploy_runtime.py` | 신규 | toolkit 배포 + IAM(bedrock) + ARN 저장 |
| `…/runtime/teardown.sh` | 신규 | Runtime/ECR/role 정리(실행 안 함) |
| `…/runtime/requirements.txt` | 신규 | 컨테이너 런타임 의존 |
| `tests/test_runtime_entrypoint.py` | 신규 | entrypoint SSE 로직(agent mock) |
| `tests/test_remote_sse.py` | 신규 | `_parse_sse` 단위 |

> Dockerfile은 toolkit `Runtime.configure(auto_create_ecr=True)`가 자동 생성 — 직접 작성 안 함.

---

## Task 1: 의존성 + runtime 패키지 스캐폴딩

**Files:** Modify `pyproject.toml`; Create `agents/db_query_analysis_agent/runtime/__init__.py`, `agents/db_query_analysis_agent/runtime/requirements.txt`

- [ ] **Step 1: 브랜치 생성** — `git checkout -b feat/phase2-runtime`

- [ ] **Step 2: `pyproject.toml` deps 추가** — `[project].dependencies`에 추가:
```toml
    "bedrock-agentcore>=1.4.0",
```
그리고 `[project.optional-dependencies].dev`에 추가:
```toml
    "bedrock-agentcore-starter-toolkit>=0.1.14",
```

- [ ] **Step 3: `agents/db_query_analysis_agent/runtime/__init__.py`** — 빈 파일 생성.

- [ ] **Step 4: `agents/db_query_analysis_agent/runtime/requirements.txt`** (컨테이너용):
```
strands-agents>=1.10.0
bedrock-agentcore>=1.4.0
boto3>=1.34.0
python-dotenv>=1.0.0
```

- [ ] **Step 5: 설치 + import 검증**
Run: `uv sync --extra dev && uv run python -c "import bedrock_agentcore; from bedrock_agentcore.runtime import BedrockAgentCoreApp; print('agentcore ok')"`
Expected: `agentcore ok`. (실패 시 BLOCKED — 정확한 패키지명/버전 보고. `bedrock-agentcore`가 `bedrock_agentcore`로 import되는지 확인.)

- [ ] **Step 6: Commit**
```bash
git add pyproject.toml uv.lock agents/db_query_analysis_agent/runtime/__init__.py agents/db_query_analysis_agent/runtime/requirements.txt
git commit -m "chore(phase2): add bedrock-agentcore deps + runtime package"
```

---

## Task 2: `shared/repl.py` 추출 (멀티라인 입력 DRY)

**Files:** Create `shared/repl.py`; Modify `agents/db_query_analysis_agent/local/chat.py`; Create `tests/test_repl.py`; Delete `tests/test_chat_input.py`

- [ ] **Step 1: 실패 테스트** `tests/test_repl.py`:
```python
from shared.repl import read_multiline_input


def _feed(lines):
    it = iter(lines)

    def fn(_prompt=""):
        try:
            return next(it)
        except StopIteration:
            raise EOFError
    return fn


def test_multiline_until_blank():
    assert read_multiline_input(_feed(["SELECT * FROM orders WHERE", "  user_id = 1", ""])) \
        == "SELECT * FROM orders WHERE\n  user_id = 1"


def test_strips_surrounding_quotes():
    assert read_multiline_input(_feed(['"SELECT 1"', ""])) == "SELECT 1"


def test_command_first_line():
    assert read_multiline_input(_feed(["/QUIT"])) == "/quit"
    assert read_multiline_input(_feed(["/reset"])) == "/reset"


def test_blank_returns_empty():
    assert read_multiline_input(_feed([""])) == ""


def test_eof_returns_none():
    def boom(_p=""):
        raise EOFError
    assert read_multiline_input(boom) is None
```

- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_repl.py -v` (ImportError).

- [ ] **Step 3: `shared/repl.py`**:
```python
"""대화형 입력 헬퍼 — 여러 줄 누적 → 빈 줄 전송. 로컬/원격 chat 공유."""
GREEN = "\033[0;32m"
NC = "\033[0m"
_COMMANDS = ("/quit", "quit", "exit", "/reset")


def read_multiline_input(input_fn=input) -> str | None:
    """여러 줄 누적 → 빈 줄(Enter)로 전송. 첫 줄 명령(소문자)이면 즉시. EOF→None. 양끝 큰따옴표 제거.

    input_fn 주입 가능(테스트용). 기본은 builtins.input.
    """
    lines: list[str] = []
    while True:
        try:
            line = input_fn(f"{GREEN}> {NC}" if not lines else f"{GREEN}… {NC}")
        except (EOFError, KeyboardInterrupt):
            return None
        if not lines and line.strip().lower() in _COMMANDS:
            return line.strip().lower()
        if line.strip() == "":
            break
        lines.append(line)
    return "\n".join(lines).strip().strip('"').strip()
```

- [ ] **Step 4: 통과 확인** — `uv run pytest tests/test_repl.py -v` → 5 passed.

- [ ] **Step 5: `local/chat.py`를 공용 헬퍼 사용으로 변경** — `_read_input`/`_COMMANDS` 정의 제거, import + 호출 교체:
  - 상단에 `from shared.repl import read_multiline_input` 추가.
  - `_COMMANDS = (...)` 및 `def _read_input(): ...` 블록 삭제.
  - `main()`의 `user = _read_input()` → `user = read_multiline_input()`.
  (나머지 로직/배너 동일.)

- [ ] **Step 6: 옛 테스트 제거** — `git rm tests/test_chat_input.py` (로직은 test_repl.py로 이전됨).

- [ ] **Step 7: 통과 확인** — `uv run pytest -q` → 전체 PASS(개수: 기존 62 - 6(chat_input) + 5(repl) = 61, 1 skipped). `uv run python -c "import agents.db_query_analysis_agent.local.chat; print('ok')"`.

- [ ] **Step 8: Commit**
```bash
git add shared/repl.py agents/db_query_analysis_agent/local/chat.py tests/test_repl.py
git commit -m "refactor(phase2): extract read_multiline_input to shared/repl (DRY for remote chat)"
```

---

## Task 3: `agentcore_runtime.py` — 엔트리포인트 + 세션 캐시 + SSE

**Files:** Create `agents/db_query_analysis_agent/runtime/agentcore_runtime.py`; Test `tests/test_runtime_entrypoint.py`

- [ ] **Step 1: 실패 테스트** `tests/test_runtime_entrypoint.py`:
```python
import asyncio
from agents.db_query_analysis_agent.runtime import agentcore_runtime as rt


class _FakeAgent:
    async def stream_async(self, _prompt):
        for chunk in ["리", "뷰"]:
            yield {"data": chunk}
        yield {"event": {"metadata": {"usage": {"totalTokens": 7}}}}


def _collect(agen):
    async def run():
        return [e async for e in agen]
    return asyncio.run(run())


def test_stream_review_yields_sse():
    events = _collect(rt._stream_review(_FakeAgent(), "SELECT 1"))
    types = [e["type"] for e in events]
    assert "agent_text_stream" in types
    assert types[-1] == "workflow_complete"
    text = "".join(e.get("text", "") for e in events if e["type"] == "agent_text_stream")
    assert text == "리뷰"
    usage = [e for e in events if e["type"] == "token_usage"]
    assert usage and usage[0]["usage"]["totalTokens"] == 7


def test_get_or_create_agent_caches(monkeypatch):
    calls = {"n": 0}

    def fake_build():
        calls["n"] += 1
        return object()
    monkeypatch.setattr(rt, "build_db_query_agent", fake_build)
    rt._session_agents.clear()
    a1 = rt._get_or_create_agent("s1")
    a2 = rt._get_or_create_agent("s1")
    a3 = rt._get_or_create_agent("s2")
    assert a1 is a2 and a1 is not a3
    assert calls["n"] == 2  # s1 1회 + s2 1회


def test_entrypoint_missing_query():
    events = _collect(rt.review({}))
    assert events[0]["type"] == "agent_text_stream" and "query" in events[0]["text"]
    assert events[-1]["type"] == "workflow_complete"
```

- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_runtime_entrypoint.py -v`.

- [ ] **Step 3: `agentcore_runtime.py`**:
```python
"""AgentCore Runtime 엔트리포인트 — db-query-analysis-agent.

build_db_query_agent를 세션별로 캐시(멀티턴 warm) + SSE 스트리밍. 로컬 == 관리형
단일 truth. SigV4 인증(Cognito 없음). 컨테이너에선 deploy가 agents/,shared/,data/를
build context로 복사.
"""
import sys
from pathlib import Path
from typing import Any, AsyncGenerator

_SCRIPT_DIR = Path(__file__).resolve().parent
# 로컬: PROJECT_ROOT, 컨테이너: build context root(/app)를 sys.path에.
for _p in (_SCRIPT_DIR.parents[3], _SCRIPT_DIR):  # parents[3] = repo root (agents/db_.../runtime → 4 up)
    if _p.exists() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from bedrock_agentcore.runtime import BedrockAgentCoreApp  # noqa: E402

try:
    from agents.db_query_analysis_agent.shared.agent import build_db_query_agent  # noqa: E402
except ModuleNotFoundError:  # 컨테이너 flatten 폴백
    from shared.agent import build_db_query_agent  # type: ignore # noqa: E402

app = BedrockAgentCoreApp()
_session_agents: dict[str, Any] = {}


def _get_or_create_agent(session_id: str):
    """세션별 Agent 캐시 — 같은 id면 재사용(agent.messages 보존 = 멀티턴)."""
    if session_id in _session_agents:
        return _session_agents[session_id]
    agent = build_db_query_agent()
    _session_agents[session_id] = agent
    return agent


async def _stream_review(agent, query: str) -> AsyncGenerator[dict, None]:
    """agent.stream_async 소비 → SSE 이벤트(text/usage/complete) yield."""
    usage = {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0,
             "cacheReadInputTokens": 0, "cacheWriteInputTokens": 0}
    async for event in agent.stream_async(query):
        data = event.get("data", "")
        if data:
            yield {"type": "agent_text_stream", "text": data}
        meta = event.get("event", {}).get("metadata", {})
        if "usage" in meta:
            for k in usage:
                usage[k] += meta["usage"].get(k, 0)
    yield {"type": "token_usage", "usage": usage}
    yield {"type": "workflow_complete", "text": ""}


@app.entrypoint
async def review(payload: dict, context: Any = None) -> AsyncGenerator[dict, None]:
    """Operator → 진입. payload {query, session_id?} → SSE."""
    query = (payload or {}).get("query") or ""
    if not query:
        yield {"type": "agent_text_stream", "text": '[error] payload에 "query" 누락'}
        yield {"type": "workflow_complete", "text": ""}
        return
    session_id = (payload or {}).get("session_id") or "default"
    agent = _get_or_create_agent(session_id)
    async for ev in _stream_review(agent, query):
        yield ev


if __name__ == "__main__":
    app.run()
```

- [ ] **Step 4: 통과 확인** — `uv run pytest tests/test_runtime_entrypoint.py -v` → 3 passed.
  - SDK 보정: `app = BedrockAgentCoreApp()`가 import 시 에러나면(환경 요구) — `@app.entrypoint` 데코레이터가 `review`를 호출 불가하게 wrap하면 — 로직(`_stream_review`/`_get_or_create_agent`)을 별도 모듈로 빼고 `review`가 그걸 호출하도록 조정(테스트는 로직 함수 대상). `parents[3]` 경로가 repo root 아니면 보정.

- [ ] **Step 5: Commit**
```bash
git add agents/db_query_analysis_agent/runtime/agentcore_runtime.py tests/test_runtime_entrypoint.py
git commit -m "feat(phase2): AgentCore Runtime entrypoint (session cache + SSE)"
```

---

## Task 4: `_remote.py` — invoke + SSE 파싱

**Files:** Create `agents/db_query_analysis_agent/runtime/_remote.py`; Test `tests/test_remote_sse.py`

- [ ] **Step 1: 실패 테스트** `tests/test_remote_sse.py`:
```python
from agents.db_query_analysis_agent.runtime._remote import _parse_sse


def test_parse_data_line():
    assert _parse_sse(b'data: {"type":"agent_text_stream","text":"hi"}') == \
        {"type": "agent_text_stream", "text": "hi"}


def test_parse_plain_json():
    assert _parse_sse(b'{"type":"workflow_complete"}') == {"type": "workflow_complete"}


def test_parse_empty_or_garbage():
    assert _parse_sse(b"") is None
    assert _parse_sse(b"data: not json") is None
```

- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_remote_sse.py -v`.

- [ ] **Step 3: `_remote.py`**:
```python
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
```

- [ ] **Step 4: 통과 확인** — `uv run pytest tests/test_remote_sse.py -v` → 3 passed. (`stream_invoke`의 boto3 호출은 e2e(Task 7)로 검증.)

- [ ] **Step 5: Commit**
```bash
git add agents/db_query_analysis_agent/runtime/_remote.py tests/test_remote_sse.py
git commit -m "feat(phase2): remote invoke + SSE parse helper"
```

---

## Task 5: `invoke_runtime.py` + `chat.py` (원격 CLI)

**Files:** Create `…/runtime/invoke_runtime.py`, `…/runtime/chat.py`

- [ ] **Step 1: `invoke_runtime.py`**:
```python
"""단발 원격 호출 (SigV4). uv run -m agents.db_query_analysis_agent.runtime.invoke_runtime --query "..." """
import argparse

from dotenv import load_dotenv

from agents.db_query_analysis_agent.runtime._remote import stream_invoke

CYAN = "\033[0;36m"
NC = "\033[0m"


def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser(description="db-query-analysis-agent 원격 단발 호출")
    p.add_argument("--query", required=True, help="리뷰 요청(자연어/SQL)")
    p.add_argument("--session-id", default=None, help="멀티턴 재사용(≥33자)")
    args = p.parse_args()
    if args.session_id and len(args.session_id) < 33:
        p.error("--session-id 는 ≥33자 (AgentCore 제약)")
    print(f"{CYAN}원격 호출 중...{NC}\n")
    stream_invoke(args.query, args.session_id)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: `chat.py` (원격 멀티턴 REPL)**:
```python
"""원격 멀티턴 chat — runtimeSessionId 재사용으로 warm 멀티턴.
uv run -m agents.db_query_analysis_agent.runtime.chat
"""
import uuid

from dotenv import load_dotenv

from agents.db_query_analysis_agent.runtime._remote import stream_invoke
from shared.repl import read_multiline_input

CYAN = "\033[0;36m"
YELLOW = "\033[1;33m"
DIM = "\033[2m"
NC = "\033[0m"


def _new_session_id() -> str:
    return "dbqchat-" + uuid.uuid4().hex  # 8+32 = 40자 (≥33 만족)


def main() -> None:
    load_dotenv()
    session_id = _new_session_id()
    print(f"\n{CYAN}{'=' * 50}\n  db-query-analysis-agent (원격 대화형)\n{'=' * 50}{NC}")
    print(f"{DIM}  여러 줄 입력 후 빈 줄(Enter)로 전송 · /reset 새 세션 · /quit 종료{NC}\n")
    while True:
        user = read_multiline_input()
        if user is None:
            print("\n")
            break
        if user in ("/quit", "quit", "exit"):
            break
        if user == "/reset":
            session_id = _new_session_id()
            print(f"{YELLOW}새 세션 시작{NC}\n")
            continue
        if not user:
            continue
        print()
        stream_invoke(user, session_id)
        print()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: import 스모크** (자격증명 무관 — 모듈 import + --help):
```
uv run python -c "import agents.db_query_analysis_agent.runtime.invoke_runtime, agents.db_query_analysis_agent.runtime.chat; print('cli import ok')"
uv run python -m agents.db_query_analysis_agent.runtime.invoke_runtime --help
```
Expected: `cli import ok` + argparse help. (실제 호출은 RUNTIME_ARN 필요 → Task 7 e2e.)

- [ ] **Step 4: Commit**
```bash
git add agents/db_query_analysis_agent/runtime/invoke_runtime.py agents/db_query_analysis_agent/runtime/chat.py
git commit -m "feat(phase2): remote invoke CLI + multi-turn chat"
```

---

## Task 6: `deploy_runtime.py` + `teardown.sh`

**Files:** Create `…/runtime/deploy_runtime.py`, `…/runtime/teardown.sh`

- [ ] **Step 1: `deploy_runtime.py`** (dev-briefing/AIOps Phase 3 패턴):
```python
#!/usr/bin/env python3
"""db-query-analysis-agent → AgentCore Runtime 배포 (toolkit). 첫 배포 ~5-10분.

수행: agents/,shared/,data/ 를 build context(runtime/)로 복사 → Runtime.configure
(Dockerfile/ECR/IAM 자동) → launch(Docker→ECR→Runtime) → 실행 role에 bedrock:InvokeModel
부착 → READY 대기 → RUNTIME_ARN 등을 repo root .env 에 저장.
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
PROJECT_ROOT = SCRIPT_DIR.parents[3]
os.chdir(SCRIPT_DIR)
load_dotenv(PROJECT_ROOT / ".env", override=True)

GREEN, YELLOW, RED, NC = "\033[0;32m", "\033[1;33m", "\033[0;31m", "\033[0m"
REGION = os.environ.get("AWS_REGION", "us-east-1")
DEMO_USER = os.environ.get("DEMO_USER", "ubuntu")
AGENT_NAME = f"db_query_analysis_agent_{DEMO_USER}"


def copy_into_build_context() -> None:
    for name in ("agents", "shared", "data"):
        src = PROJECT_ROOT / name
        dst = SCRIPT_DIR / name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "sample.db"))
    print(f"{GREEN}✅ build context 복사 완료{NC}")


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
    print(f"{GREEN}✅ IAM 부착: {role_name}/BedrockInvoke{NC}")

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
    lines = [ln for ln in (env_file.read_text().splitlines() if env_file.exists() else [])
             if not ln.startswith(("RUNTIME_ARN=", "RUNTIME_ID=", "RUNTIME_NAME="))]
    lines += [f"RUNTIME_NAME={AGENT_NAME}", f"RUNTIME_ID={result.agent_id}", f"RUNTIME_ARN={result.agent_arn}"]
    env_file.write_text("\n".join(lines) + "\n")
    print(f"{GREEN}✅ 배포 완료 — RUNTIME_ARN .env 저장 ({datetime.now():%H:%M}){NC}")
    print(f"   다음: uv run agents/db_query_analysis_agent/runtime/invoke_runtime.py --query \"SELECT * FROM orders WHERE user_id=1\"")


if __name__ == "__main__":
    main()
```
> **배포 중 보정**: toolkit `Runtime.configure/launch` 인자·`launch_result` 속성(`agent_arn`/`agent_id`), `bedrock-agentcore-control` 메서드명은 설치된 toolkit/boto3 실 API에 맞춰 조정. 컨테이너 import 실패 시 `agentcore_runtime.py`의 sys.path/폴백 보정.

- [ ] **Step 2: `teardown.sh`**:
```bash
#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; source "$PROJECT_ROOT/.env" 2>/dev/null || true
REGION="${AWS_REGION:-us-east-1}"; NAME="${RUNTIME_NAME:-db_query_analysis_agent_${DEMO_USER:-ubuntu}}"
echo "Runtime 삭제: ${RUNTIME_ID:-?}"
[ -n "${RUNTIME_ID:-}" ] && aws bedrock-agentcore-control delete-agent-runtime --agent-runtime-id "$RUNTIME_ID" --region "$REGION" || echo "RUNTIME_ID 없음"
echo "ECR repo 삭제: $NAME"
aws ecr delete-repository --repository-name "$NAME" --force --region "$REGION" 2>/dev/null || echo "ECR repo 없음/스킵"
echo "✅ teardown 완료 (IAM role은 수동 확인)"
```

- [ ] **Step 3: import 스모크** — `uv run python -c "import agents.db_query_analysis_agent.runtime.deploy_runtime; print('deploy import ok')"` (실행은 Task 7). `chmod +x agents/db_query_analysis_agent/runtime/teardown.sh`.

- [ ] **Step 4: 전체 단위 테스트 + ruff** — `uv run pytest -q && uv run ruff check .` → PASS, 0 errors.

- [ ] **Step 5: Commit**
```bash
git add agents/db_query_analysis_agent/runtime/deploy_runtime.py agents/db_query_analysis_agent/runtime/teardown.sh
git commit -m "feat(phase2): deploy_runtime (toolkit + bedrock IAM) + teardown.sh"
```

---

## Task 7: e2e 배포 + 검증 (Claude가 실행) — 배포 유지

**사전:** 모든 단위 테스트 green. AWS 자격증명 + Docker(확인됨).

- [ ] **Step 1: 실제 배포** — `uv run python agents/db_query_analysis_agent/runtime/deploy_runtime.py`
  Expected: READY + `RUNTIME_ARN` .env 저장. (배포 중 toolkit/SDK/컨테이너 import 이슈 발생 시 보정 후 재배포. CloudWatch 로그로 진단.)

- [ ] **Step 2: 단발 e2e** — `uv run agents/db_query_analysis_agent/runtime/invoke_runtime.py --query "SELECT * FROM orders WHERE user_id = 1"`
  Expected: 원격 SSE 스트리밍으로 한국어 리뷰(SELECT_STAR 경고 + orders 대형 + 인덱스). 짧은 발췌 기록.

- [ ] **Step 3: 멀티턴 검증** — 동일 session_id로 2턴(공용 `stream_invoke`를 같은 session_id로 2회 호출하는 스크립트 또는 chat에 파이프):
  `printf 'SELECT * FROM orders WHERE user_id=1\n\n어떤 컬럼만 선택할까?\n\n/quit\n' | uv run -m agents.db_query_analysis_agent.runtime.chat`
  Expected: 2턴 모두 응답, 2번째가 맥락 유지(warm 세션). 발췌 기록.

- [ ] **Step 4: 배포 유지** — teardown 실행 안 함. `RUNTIME_ARN`을 결과에 보고.

- [ ] **Step 5: Commit (있다면 보정분)** — 배포 중 보정한 코드가 있으면:
```bash
git add -A && git commit -m "fix(phase2): deploy/runtime adjustments from e2e"
```

---

## Self-Review (작성자 점검)

**1. Spec coverage:**
- Runtime 엔트리포인트(세션 캐시+SSE) → Task 3 ✅
- deploy_runtime(toolkit+bedrock IAM+ARN 저장) → Task 6 ✅
- invoke_runtime(단발 SigV4) → Task 5 ✅
- **chat.py(원격 멀티턴, session 재사용)** → Task 5 ✅
- DRY: `shared/repl`(멀티라인) + `_remote`(invoke/SSE) → Task 2/4 ✅
- 컨테이너 backend mock 기본 + data/ 포함 → Task 6 env_vars + copy ✅
- SigV4, Cognito 없음 → invoke/_remote(SigV4 자동), Gateway 없음 ✅
- pyproject deps(bedrock-agentcore[-starter-toolkit]) → Task 1 ✅
- teardown.sh 제공(실행 안 함) → Task 6 ✅
- Claude 배포+검증, 배포 유지 → Task 7 ✅
- 로컬==관리형 단일 truth(build_db_query_agent 재사용) → Task 3 ✅

**2. Placeholder scan:** 코드/명령 실제 포함. deploy/SDK 보정 지점은 "실 API에 맞춰 조정"으로 명시(추상적 placeholder 아님 — 검증 행위 지정).

**3. Type consistency:** `read_multiline_input`(repl) / `_stream_review`·`_get_or_create_agent`·`review`(entrypoint) / `_parse_sse`·`stream_invoke`(_remote) / `build_db_query_agent`(재사용) — 정의처·호출처(invoke/chat) 일치. SSE 이벤트 타입(`agent_text_stream`/`token_usage`/`workflow_complete`)이 entrypoint(생성)와 `_remote._parse_sse`(소비)에서 일관.

**알려진 위험(배포로 검증):** toolkit `configure/launch` 실 API, `launch_result` 속성, `bedrock-agentcore-control` 메서드, 컨테이너 import 경로(sys.path/full-path), `app=BedrockAgentCoreApp()` import-time 동작. Task 7에서 Claude가 실배포로 해소.
