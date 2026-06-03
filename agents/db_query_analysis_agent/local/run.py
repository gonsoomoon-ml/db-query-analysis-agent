"""단발 SQL 리뷰 진입점. uv run -m agents.db_query_analysis_agent.local.run --sql "..." """
import argparse
import asyncio
from pathlib import Path

from dotenv import load_dotenv

from agents.db_query_analysis_agent.shared.agent import build_db_query_agent
from shared.streaming import stream_response

CYAN = "\033[0;36m"
NC = "\033[0m"


async def _amain(sql: str) -> None:
    agent = build_db_query_agent()
    await stream_response(agent, f"다음 SQL을 리뷰해줘:\n```sql\n{sql}\n```")


def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[3] / ".env")  # repo root .env (CWD 무관)
    parser = argparse.ArgumentParser(description="db-query-analysis-agent 단발 리뷰")
    parser.add_argument("--sql", required=True, help="리뷰할 SQL")
    args = parser.parse_args()
    print(f"{CYAN}{'=' * 60}\n  db-query-analysis-agent (단발)\n{'=' * 60}{NC}")
    print(f"SQL: {args.sql}\n\n분석 중...\n")
    asyncio.run(_amain(args.sql))


if __name__ == "__main__":
    main()
