from shared.repl import read_multiline_input


def _feed(lines):
    it = iter(lines)

    def fn(_prompt=""):
        try:
            return next(it)
        except StopIteration:
            raise EOFError
    return fn


def test_multiline_until_blank():
    assert read_multiline_input(_feed(["SELECT * FROM orders WHERE", "  user_id = 1", ""])) \
        == "SELECT * FROM orders WHERE\n  user_id = 1"


def test_strips_surrounding_quotes():
    assert read_multiline_input(_feed(['"SELECT 1"', ""])) == "SELECT 1"


def test_command_first_line():
    assert read_multiline_input(_feed(["/QUIT"])) == "/quit"
    assert read_multiline_input(_feed(["/reset"])) == "/reset"


def test_blank_returns_empty():
    assert read_multiline_input(_feed([""])) == ""


def test_eof_returns_none():
    def boom(_p=""):
        raise EOFError
    assert read_multiline_input(boom) is None
