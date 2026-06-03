"""원격 멀티턴 chat — runtimeSessionId 재사용으로 warm 멀티턴.
uv run -m agents.db_query_analysis_agent.runtime.chat
"""
import uuid
from pathlib import Path

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
    load_dotenv(Path(__file__).resolve().parents[3] / ".env")  # repo root .env (CWD 무관)
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
