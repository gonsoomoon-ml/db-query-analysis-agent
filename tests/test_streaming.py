import asyncio
from shared.streaming import stream_response


class _FakeAgent:
    async def stream_async(self, _prompt):
        for chunk in ["안녕", "하세요"]:
            yield {"data": chunk}
        yield {"event": {"metadata": {"usage": {"totalTokens": 5}}}}


def test_stream_collects_text(capsys):
    out = asyncio.run(stream_response(_FakeAgent(), "hi"))
    assert out == "안녕하세요"
    captured = capsys.readouterr().out
    assert "안녕하세요" in captured
    assert "Tokens" in captured
