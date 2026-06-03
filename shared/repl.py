"""대화형 입력 헬퍼 — 여러 줄 누적 → 빈 줄 전송. 로컬/원격 chat 공유."""
GREEN = "\033[0;32m"
NC = "\033[0m"
_COMMANDS = ("/quit", "quit", "exit", "/reset")


def read_multiline_input(input_fn=input) -> str | None:
    """여러 줄 누적 → 빈 줄(Enter)로 전송. 첫 줄 명령(소문자)이면 즉시. EOF→None. 양끝 큰따옴표 제거.

    input_fn 주입 가능(테스트용). 기본은 builtins.input.
    """
    lines: list[str] = []
    while True:
        try:
            line = input_fn(f"{GREEN}> {NC}" if not lines else f"{GREEN}… {NC}")
        except (EOFError, KeyboardInterrupt):
            return None
        if not lines and line.strip().lower() in _COMMANDS:
            return line.strip().lower()
        if line.strip() == "":
            break
        lines.append(line)
    return "\n".join(lines).strip().strip('"').strip()
