import builtins

from agents.db_query_analysis_agent.local import chat


def _feed(monkeypatch, lines):
    """input()이 lines를 순서대로 반환, 소진되면 EOFError."""
    it = iter(lines)

    def fake_input(_prompt=""):
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr(builtins, "input", fake_input)


def test_multiline_until_blank(monkeypatch):
    _feed(monkeypatch, ["SELECT * FROM orders WHERE", "  user_id = 1", ""])
    assert chat._read_input() == "SELECT * FROM orders WHERE\n  user_id = 1"


def test_strips_surrounding_quotes(monkeypatch):
    _feed(monkeypatch, ['"SELECT 1"', ""])
    assert chat._read_input() == "SELECT 1"


def test_command_first_line_quit(monkeypatch):
    _feed(monkeypatch, ["/quit"])
    assert chat._read_input() == "/quit"


def test_command_first_line_reset(monkeypatch):
    _feed(monkeypatch, ["/RESET"])  # 대소문자 무시
    assert chat._read_input() == "/reset"


def test_blank_returns_empty(monkeypatch):
    _feed(monkeypatch, [""])
    assert chat._read_input() == ""


def test_eof_returns_none(monkeypatch):
    def fake_input(_prompt=""):
        raise EOFError

    monkeypatch.setattr(builtins, "input", fake_input)
    assert chat._read_input() is None
