"""멀티턴 대화 REPL. uv run -m agents.db_query_analysis_agent.local.chat

같은 Agent 객체 재사용 → agent.messages에 대화 누적. 여러 줄 입력은 빈 줄(Enter)로
전송 — 멀티라인 SQL 붙여넣기 지원. /reset 초기화, /quit 종료.
"""
import asyncio

from dotenv import load_dotenv

from agents.db_query_analysis_agent.shared.agent import build_db_query_agent
from shared.streaming import stream_response

CYAN = "\033[0;36m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
DIM = "\033[2m"
NC = "\033[0m"

_COMMANDS = ("/quit", "quit", "exit", "/reset")


def _read_input() -> str | None:
    """여러 줄 입력 누적 → 빈 줄(Enter)로 전송. 멀티라인 SQL 붙여넣기 지원.

    첫 줄이 명령(/quit·/reset 등)이면 즉시 그 명령(소문자) 반환. 빈 줄만 입력 시 "".
    EOF/Ctrl-C → None. 양끝 큰따옴표는 제거(쿼리를 통째로 "..."로 감싼 경우 대비).
    """
    lines: list[str] = []
    while True:
        try:
            line = input(f"{GREEN}> {NC}" if not lines else f"{GREEN}… {NC}")
        except (EOFError, KeyboardInterrupt):
            return None
        if not lines and line.strip().lower() in _COMMANDS:
            return line.strip().lower()
        if line.strip() == "":  # 빈 줄 → 누적분 전송
            break
        lines.append(line)
    return "\n".join(lines).strip().strip('"').strip()


def main() -> None:
    load_dotenv()
    agent = build_db_query_agent()

    print(f"\n{CYAN}{'=' * 50}\n  db-query-analysis-agent (대화형)\n{'=' * 50}{NC}")
    print(f"{DIM}  SQL을 붙여넣고(여러 줄 가능) 빈 줄(Enter)로 전송 · /reset 초기화 · /quit 종료{NC}\n")

    while True:
        user = _read_input()
        if user is None:           # EOF / Ctrl-C
            print("\n")
            break
        if user in ("/quit", "quit", "exit"):
            break
        if user == "/reset":
            agent = build_db_query_agent()
            print(f"{YELLOW}대화를 초기화했습니다{NC}\n")
            continue
        if not user:               # 빈 입력
            continue
        print()
        asyncio.run(stream_response(agent, user))
        print()


if __name__ == "__main__":
    main()
