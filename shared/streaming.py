"""에이전트 응답 스트리밍 + 토큰 usage 출력 (developer-briefing 패턴)."""
DIM = "\033[2m"
NC = "\033[0m"


def _usage_line(u: dict) -> str:
    return (
        f"{DIM}📊 Tokens — Total: {u.get('totalTokens', 0):,} | "
        f"Input: {u.get('inputTokens', 0):,} | Output: {u.get('outputTokens', 0):,} | "
        f"Cache R/W: {u.get('cacheReadInputTokens', 0):,}/"
        f"{u.get('cacheWriteInputTokens', 0):,}{NC}"
    )


async def stream_response(agent, prompt: str) -> str:
    """agent.stream_async를 소비 — 텍스트 실시간 출력 + usage 누적 표시. 전체 텍스트 반환."""
    usage = {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0,
             "cacheReadInputTokens": 0, "cacheWriteInputTokens": 0}
    chunks: list[str] = []
    async for event in agent.stream_async(prompt):
        data = event.get("data", "")
        if data:
            chunks.append(data)
            print(data, end="", flush=True)
        meta = event.get("event", {}).get("metadata", {})
        if "usage" in meta:
            for k in usage:
                usage[k] += meta["usage"].get(k, 0)
    print()
    print(_usage_line(usage))
    return "".join(chunks)
