"""Terminal chat for the meeting transcript agent; give it a recording's file path and it uploads it.

    python chat.py                                   interactive chat
    python chat.py "What meetings are there?"        ask one question and exit
    python chat.py --upload PATH --meeting "Budget Hearing, Springfield City Council"

Start the agent first with `agentcore dev` in TranscriptAgent/.
"""
import argparse
import os
import re
import sys
import urllib.error
import uuid

from client import find_agent_url, invoke, put_file, summarize
from console import ask, read_message

ALLOWED_EXTENSIONS = {".mp3", ".wav"}
MAX_BYTES = 500 * 1024 * 1024
HELP = """Type a question or request, e.g. "What meetings does Springfield City Council have?"

To add a recording, give it the file's path, by typing it or dragging the file into this window. It uploads the
file and starts transcription. Say which meeting it's for in the same message or earlier in the chat; otherwise
the agent asks:
  C:\\Recordings\\hearing.mp3 Budget Hearing, Springfield City Council
  "C:\\My Recordings\\hearing.wav" is for the zoning meeting

  /new    start a new conversation
  /help   show this help
  /quit   exit
"""
# Where a recording's path ends, and where an unquoted path can start
AUDIO_END = re.compile(r"\.(?:mp3|wav)(?=$|[\s,;:!?)\]])", re.IGNORECASE)
PATH_START = re.compile(r"(?:^|(?<=\s))(?:[A-Za-z]:[\\/]|\\\\|\.{1,2}[\\/]|~[\\/])")
QUOTED = re.compile(r"([\"'])(.+?)\1")
URL = re.compile(r"https?://", re.IGNORECASE)
UPLOAD_WORD = re.compile(r"^\s*/?upload\b[\s:,-]*", re.IGNORECASE)


def new_session() -> str:
    return f"cli-{uuid.uuid4().hex}"  # AgentCore wants session ids of 33+ characters


def plain(text: str) -> str:
    """Drop markdown bold/code markers so replies read cleanly in a terminal."""
    return re.sub(r"\*\*(.+?)\*\*|`([^`]+)`", lambda m: m.group(1) or m.group(2), text)


def safe_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "transcript"


def _looks_like_path(text: str) -> bool:
    return bool(AUDIO_END.search(text)) or (("\\" in text or "/" in text) and bool(os.path.splitext(text)[1]))


def _rest(text: str, start: int, end: int) -> str:
    """The message with the path cut out: what's left says which meeting it's for."""
    rest = f"{text[:start]} {text[end:]}"
    rest = UPLOAD_WORD.sub("", rest)
    return re.sub(r"\s+", " ", rest).strip(" ,;:-")


def find_recording(line: str) -> tuple[str | None, str]:
    """Find a file path in what the user typed; returns (path, rest of the message), or (None, line) if there's none.

    Handles quoted paths, paths dragged into the window (PowerShell adds "& "), unquoted paths (even with spaces)
    and bare file names in the current folder. A path-like one that doesn't exist is still returned, so the user is
    told it wasn't found instead of the agent guessing.
    """
    text = line.strip()
    if text.startswith("& "):  # PowerShell prefixes dragged paths with the call operator
        text = text[2:].lstrip()
    for match in QUOTED.finditer(text):
        candidate = match.group(2).strip()
        if os.path.isfile(candidate) or _looks_like_path(candidate):
            return candidate, _rest(text, match.start(), match.end())
    for end_match in AUDIO_END.finditer(text):
        end = end_match.end()
        starts = [m.start() for m in PATH_START.finditer(text, 0, end)]
        word_start = max(text.rfind(" ", 0, end) + 1, 0)
        # Earliest start first, so an unquoted path with spaces is matched whole
        for start in sorted(set(starts + [word_start])):
            if os.path.isfile(text[start:end]):
                return text[start:end], _rest(text, start, end)
        if starts:
            start = starts[-1]
            return text[start:end], _rest(text, start, end)
    return None, line


def check_recording(path: str) -> str | None:
    """Why the file can't be uploaded, or None if it's fine."""
    if not os.path.isfile(path):
        return f"File not found: {path}"
    if os.path.splitext(path)[1].lower() not in ALLOWED_EXTENSIONS:
        return "Only MP3 and WAV recordings are accepted."
    size = os.path.getsize(path)
    if size == 0:
        return "That file is empty."
    if size > MAX_BYTES:
        return f"That file is {size / 1024 / 1024:,.0f} MB; the limit is 500 MB."
    return None


class Chat:
    def __init__(self, agent_url: str, transcript_dir: str = "."):
        self.agent_url = agent_url
        self.transcript_dir = transcript_dir
        self.session_id = new_session()
        self.turn = 0  # counts the user's messages
        self.pending_path = None  # the recording to send when the agent hands out an upload link
        self.pending_turn = 0  # the message that gave pending_path

    def send(self, payload: dict) -> dict | None:
        try:
            result = summarize(invoke(self.agent_url, self.session_id, payload))
        except (urllib.error.URLError, OSError) as e:
            print(f"  Can't reach the agent at {self.agent_url} ({e}). Is `agentcore dev` running?")
            return None
        if result["upload_url"]:
            # The agent is ready for a recording: upload it here instead of showing the link
            for error in result["errors"]:
                print(f"  Error: {error}")
            return self.finish_upload(result["upload_url"], result["audio_key"])
        self.show(result)
        return self.confirm(result)

    def show(self, result: dict):
        for kind, value in result["items"]:
            if kind == "transcript":
                self.show_transcript(value)
            else:
                print("\n" + plain(value) + "\n")
        for error in result["errors"]:
            print(f"  Error: {error}")

    def show_transcript(self, shown: dict):
        name = f"{shown['title']} {shown['meeting_date']}"
        path = os.path.join(self.transcript_dir, safe_filename(name) + ".txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(shown["transcript"])
        print(f"\n----- Full transcript: {shown['title']} ({shown['meeting_date']}) -----\n")
        print(shown["transcript"])
        print(f"\n----- end of transcript ({len(shown['transcript']):,} characters), saved to {path} -----")

    def confirm(self, result: dict) -> dict:
        """Answer a pending confirmation with a [y/N] prompt; Enter or anything but yes cancels."""
        while result and result["confirmation"]:
            question = result["confirmation"].replace(" Reply yes to continue or no to cancel.", "")
            answer = ask(f"  {question} [y/N] ").strip().lower()
            result = self.send({"confirm": answer in ("y", "yes")})
        return result

    def finish_upload(self, url: str, audio_key: str | None = None) -> dict | None:
        """Upload the recording to the link the agent handed out, then have it attach and transcribe."""
        path, self.pending_path = self.pending_path, None
        # A path from this message or the one before (e.g. answering "which meeting?") is used straight away; an
        # older one may belong to a request the user dropped, so check before sending it somewhere new
        if path and self.turn - self.pending_turn > 1:
            answer = ask(f"  Upload {os.path.basename(path)} (the file you gave earlier) for this? [y/N] ")
            if answer.strip().lower() not in ("y", "yes"):
                path = None
        while not path:
            answer = ask("  Path to the recording (type it or drag the file here; Enter to cancel): ").strip()
            if not answer:
                print("  Not uploaded.")
                return None
            found, _ = find_recording(answer)
            path = found or answer.strip("\"'")
            problem = check_recording(path)
            if problem:
                print(f"  {problem}")
                path = None
        # The link's name fixes the format Transcribe expects; if the agent guessed .mp3 for a .wav, get a matching one
        extension = os.path.splitext(path)[1].lower()
        if audio_key and not audio_key.lower().endswith(extension):
            self.pending_path, self.pending_turn = path, self.turn
            return self.send({"prompt": f"The recording is actually named {os.path.basename(path)}; "
                                        "get an upload link for that name instead."})
        size_mb = os.path.getsize(path) / 1024 / 1024
        print(f"  Uploading {os.path.basename(path)} ({size_mb:,.1f} MB)...", end=" ", flush=True)
        try:
            status = put_file(url, path)
        except (urllib.error.URLError, OSError) as e:
            print(f"failed: {e}")
            return None
        if status != 200:
            print(f"failed (HTTP {status}).")
            return None
        print("done.")
        return self.send({"prompt": "The upload is done. Attach it to that meeting and start transcription."})

    def upload(self, path: str, meeting: str):
        problem = check_recording(path)
        if problem:
            print(f"  {problem}")
            return
        # Kept until the agent hands out an upload link, even if it first asks which meeting it's for
        self.pending_path, self.pending_turn = path, self.turn
        request = f"I want to upload a recording named {os.path.basename(path)}"
        if meeting:
            request += f" for this meeting: {meeting}"
        else:
            request += ". Use the meeting we've been talking about; if it isn't clear which one, ask me."
        self.send({"prompt": request})

    def handle(self, line: str) -> bool:
        """Handle one message (a typed line or a whole pasted block); False means quit."""
        line = line.strip()
        if not line:
            return True
        command = line.split(maxsplit=1)[0].lower()
        if command in ("/quit", "/exit", "quit", "exit"):
            return False
        if command == "/help":
            print(HELP)
            return True
        if command == "/new":
            self.session_id = new_session()
            self.pending_path = None
            print("  Started a new conversation.")
            return True
        if not re.search(r"\w", line):
            return True  # a stray backtick or bracket is not a request; don't let the agent act on it
        self.turn += 1
        # A file is handed over on a line of its own; a pasted block, a link or a command line that mentions a file
        # (like `curl -T file.mp3 https://...`) is text to send, not a recording to upload
        path, rest = (None, line) if "\n" in line or URL.search(line) else find_recording(line)
        if path:
            self.upload(path, rest)
        elif UPLOAD_WORD.fullmatch(line):
            print("  Give it the recording's path, e.g. C:\\Recordings\\hearing.mp3 Budget Hearing")
        else:
            self.send({"prompt": line})
        return True

    def loop(self):
        print("Meeting Transcript Archive. Type /help for commands, /quit to exit.")
        while True:
            try:
                line = read_message("archive> ")
            except (EOFError, KeyboardInterrupt):
                print()
                return
            try:
                if not self.handle(line):
                    return
            except KeyboardInterrupt:
                # Ctrl+C at a prompt or mid-request; a paused confirmation is cancelled by the next message
                print("\n  (interrupted)")


def main():
    # Windows consoles may not be UTF-8; never crash on a character in a transcript
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Chat with the meeting transcript agent.")
    parser.add_argument("message", nargs="*", help="ask one question and exit")
    parser.add_argument("--upload", metavar="PATH", help="upload an MP3/WAV recording and start transcription")
    parser.add_argument("--meeting", help="which meeting the --upload recording is for")
    parser.add_argument("--agent-url", help="agent endpoint (default: AGENT_URL, or a local agentcore dev)")
    parser.add_argument("--transcripts", default=".", metavar="DIR", help="where to save full transcripts")
    args = parser.parse_args()

    agent_url = args.agent_url or find_agent_url()
    if not agent_url:
        sys.exit("Can't find the agent. Start it with `agentcore dev` in TranscriptAgent/, or set AGENT_URL.")
    chat = Chat(agent_url, args.transcripts)
    if args.upload:
        chat.upload(args.upload, args.meeting or "")
    if args.message:
        chat.handle(" ".join(args.message))
    if not args.upload and not args.message:
        chat.loop()


if __name__ == "__main__":
    main()
