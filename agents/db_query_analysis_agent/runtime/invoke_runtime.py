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
    if args.session_id and not (33 <= len(args.session_id) <= 128):
        p.error("--session-id 는 33–128자 (AgentCore 제약)")
    print(f"{CYAN}원격 호출 중...{NC}\n")
    stream_invoke(args.query, args.session_id)


if __name__ == "__main__":
    main()
