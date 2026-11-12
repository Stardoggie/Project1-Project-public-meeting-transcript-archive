"""Terminal input that treats a multi-line paste as one message, and keeps pasted text out of [y/N] prompts.

A terminal hands a pasted block to the program one line at a time, so without this every pasted line would be sent
to the agent as its own request, and leftover lines would answer the next confirmation prompt.
"""
import sys
import time
PASTE_SETTLE_SECONDS = 0.05  # long enough for the rest of a paste to arrive, too short to notice when typing
try:
    if sys.platform == "win32":
        import msvcrt

        def _waiting() -> bool:
            return msvcrt.kbhit()

        def _discard():
            while msvcrt.kbhit():
                msvcrt.getwch()
    else:
        import select
        import termios

        def _waiting() -> bool:
            return bool(select.select([sys.stdin], [], [], 0)[0])

        def _discard():
            termios.tcflush(sys.stdin, termios.TCIFLUSH)
except ImportError:  # pragma: no cover - unusual platforms just read line by line
    def _waiting() -> bool:
        return False

    def _discard():
        pass


def _interactive() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def read_message(prompt: str) -> str:
    """One message from the user: a typed line, or every line of a pasted block joined together."""
    lines = [input(prompt)]
    if not _interactive():
        return lines[0]
    time.sleep(PASTE_SETTLE_SECONDS)
    while _waiting():
        try:
            lines.append(input())
        except EOFError:  # the paste ended the input; send what arrived
            break
        time.sleep(PASTE_SETTLE_SECONDS)
    return "\n".join(lines)


def ask(prompt: str) -> str:
    """A question that must be answered by the user now, not by text typed or pasted before it appeared.
    The end of input (Ctrl+D, or a closed or redirected stdin) answers "", which every prompt takes as no or cancel."""
    if _interactive():
        try:
            _discard()
        except (OSError, ValueError):
            pass
    try:
        return input(prompt)
    except EOFError:
        print()
        return ""
