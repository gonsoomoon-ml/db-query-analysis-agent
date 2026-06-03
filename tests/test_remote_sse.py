from agents.db_query_analysis_agent.runtime import _remote
from agents.db_query_analysis_agent.runtime._remote import _parse_sse


def test_parse_data_line():
    assert _parse_sse(b'data: {"type":"agent_text_stream","text":"hi"}') == \
        {"type": "agent_text_stream", "text": "hi"}


def test_parse_data_line_no_space():
    """'data:'(공백 없음) 접두사도 파싱 — SSE 구현체 차이에 견고."""
    assert _parse_sse(b'data:{"type":"token_usage","usage":{}}') == \
        {"type": "token_usage", "usage": {}}


def test_parse_plain_json():
    assert _parse_sse(b'{"type":"workflow_complete"}') == {"type": "workflow_complete"}


def test_parse_empty_or_garbage():
    assert _parse_sse(b"") is None
    assert _parse_sse(b"data: not json") is None


def test_stream_invoke_routes_text_stdout_errors_stderr(monkeypatch, capsys):
    """표현 분리: 정상 텍스트/usage는 stdout, 에러·미지 프레임은 stderr (stdout 본문과 미혼합)."""
    events = [
        {"type": "agent_text_stream", "text": "리뷰본문"},
        {"type": "token_usage", "usage": {"totalTokens": 5}},
        {"error": "boom"},                       # 타입 없는 에러 프레임
        {"type": "workflow_complete"},
    ]
    monkeypatch.setattr(_remote, "iter_review_events", lambda q, s=None: iter(events))
    _remote.stream_invoke("SELECT 1")
    cap = capsys.readouterr()
    assert "리뷰본문" in cap.out and "boom" not in cap.out
    assert "boom" in cap.err
