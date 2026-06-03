"""멀티턴 대화 REPL. uv run -m agents.db_query_analysis_agent.local.chat

같은 Agent 객체 재사용 → agent.messages에 대화 누적. /reset 초기화, /quit 종료.
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


def main() -> None:
    load_dotenv()
    agent = build_db_query_agent()

    print(f"\n{CYAN}{'=' * 50}\n  db-query-analysis-agent (대화형)\n{'=' * 50}{NC}")
    print(f"{DIM}  SQL을 붙여넣고 리뷰를 요청하세요. /reset 초기화 · /quit 종료{NC}\n")

    while True:
        try:
            user = input(f"{GREEN}> {NC}").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n")
            break
        if not user:
            continue
        if user.lower() in ("/quit", "quit", "exit"):
            break
        if user.lower() == "/reset":
            agent = build_db_query_agent()
            print(f"{YELLOW}대화를 초기화했습니다{NC}\n")
            continue
        print()
        asyncio.run(stream_response(agent, user))
        print()


if __name__ == "__main__":
    main()
