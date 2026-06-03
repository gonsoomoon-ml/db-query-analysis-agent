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
