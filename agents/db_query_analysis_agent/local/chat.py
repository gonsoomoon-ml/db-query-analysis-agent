"""멀티턴 대화 REPL. uv run -m agents.db_query_analysis_agent.local.chat

같은 Agent 객체 재사용 → agent.messages에 대화 누적. 여러 줄 입력은 빈 줄(Enter)로
전송 — 멀티라인 SQL 붙여넣기 지원. /reset 초기화(새 agent_session), /quit 종료.
TOOLS_SOURCE=inprocess(기본) → in-process @tool 에이전트.
TOOLS_SOURCE=gateway → Cognito-보안 Gateway MCP 도구 에이전트.
"""
import asyncio
from pathlib import Path

from dotenv import load_dotenv

from agents.db_query_analysis_agent.shared.agent import agent_session
from shared.repl import read_multiline_input
from shared.streaming import stream_response

CYAN = "\033[0;36m"
YELLOW = "\033[1;33m"
DIM = "\033[2m"
NC = "\033[0m"


def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[3] / ".env")  # repo root .env (CWD 무관)

    print(f"\n{CYAN}{'=' * 50}\n  db-query-analysis-agent (대화형)\n{'=' * 50}{NC}")
    print(f"{DIM}  SQL을 붙여넣고(여러 줄 가능) 빈 줄(Enter)로 전송 · /reset 초기화 · /quit 종료{NC}\n")

    while True:                          # 외부 루프: /reset → 새 session
        with agent_session() as agent:
            reset = False
            while True:
                user = read_multiline_input()
                if user is None:         # EOF / Ctrl-C
                    print("\n")
                    return
                if user in ("/quit", "quit", "exit"):
                    return
                if user == "/reset":
                    print(f"{YELLOW}대화를 초기화했습니다{NC}\n")
                    reset = True
                    break
                if not user:             # 빈 입력
                    continue
                print()
                asyncio.run(stream_response(agent, user))
                print()
        if not reset:
            return


if __name__ == "__main__":
    main()
