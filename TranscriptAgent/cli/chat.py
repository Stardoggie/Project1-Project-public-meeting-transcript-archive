"""Terminal chat for the meeting transcript agent, with recording uploads by file path.

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

ALLOWED_EXTENSIONS = {".mp3", ".wav"}
MAX_BYTES = 500 * 1024 * 1024
HELP = """Type a question or request, e.g. "What meetings does Springfield City Council have?"

  upload <path> <meeting>   upload an MP3/WAV and start transcription, e.g.
                            upload "C:\\Recordings\\hearing.mp3" Budget Hearing, Springfield City Council
                            (you can drag the file into this window to paste its path)
  /new                      start a new conversation
  /help                     show this help
  /quit                     exit
"""


def new_session() -> str:
    return f"cli-{uuid.uuid4().hex}"  # AgentCore wants session ids of 33+ characters


def plain(text: str) -> str:
    """Drop markdown bold/code markers so replies read cleanly in a terminal."""
    return re.sub(r"\*\*(.+?)\*\*|`([^`]+)`", lambda m: m.group(1) or m.group(2), text)


def safe_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "transcript"


def parse_upload(rest: str) -> tuple[str, str]:
    """Split 'upload' arguments into (path, meeting); the path may be quoted or pasted by drag and drop."""
    rest = rest.strip()
    if rest.startswith("& "):  # PowerShell prefixes dragged paths with the call operator
        rest = rest[2:].lstrip()
    if rest[:1] in ("'", '"'):
        end = rest.find(rest[0], 1)
        if end == -1:
            raise ValueError("the file path is missing its closing quote")
        return rest[1:end], rest[end + 1:].strip()
    path, _, meeting = rest.partition(" ")
    return path, meeting.strip()


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

    def send(self, payload: dict, show: bool = True) -> dict | None:
        try:
            result = summarize(invoke(self.agent_url, self.session_id, payload))
        except (urllib.error.URLError, OSError) as e:
            print(f"  Can't reach the agent at {self.agent_url} ({e}). Is `agentcore dev` running?")
            return None
        if show:
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
            answer = input(f"  {question} [y/N] ").strip().lower()
            result = self.send({"confirm": answer in ("y", "yes")})
        return result

    def upload(self, path: str, meeting: str):
        if not path:
            print('  Usage: upload <path> <meeting>, e.g. upload "C:\\Recordings\\hearing.mp3" Budget Hearing')
            return
        problem = check_recording(path)
        if problem:
            print(f"  {problem}")
            return
        if not meeting:
            print("  Say which meeting it's for, e.g.: upload hearing.mp3 Budget Hearing, Springfield City Council")
            return
        name = os.path.basename(path)
        asked = self.send({"prompt": f"I want to upload a recording named {name} for this meeting: {meeting}"},
                          show=False)
        if asked is None:
            return
        if not asked["upload_url"]:
            # The agent couldn't tell which meeting, or refused the file; show why
            self.show(asked)
            return
        size_mb = os.path.getsize(path) / 1024 / 1024
        print(f"  Uploading {name} ({size_mb:,.1f} MB)...", end=" ", flush=True)
        try:
            status = put_file(asked["upload_url"], path)
        except (urllib.error.URLError, OSError) as e:
            print(f"failed: {e}")
            return
        if status != 200:
            print(f"failed (HTTP {status}).")
            return
        print("done.")
        self.send({"prompt": "The upload is done. Attach it to that meeting and start transcription."})

    def handle(self, line: str) -> bool:
        """Handle one line of input; False means quit."""
        line = line.strip()
        if not line:
            return True
        command = line.split(maxsplit=1)[0].lower()
        if command in ("/quit", "/exit", "quit", "exit"):
            return False
        if command == "/help":
            print(HELP)
        elif command == "/new":
            self.session_id = new_session()
            print("  Started a new conversation.")
        elif command in ("upload", "/upload"):
            try:
                path, meeting = parse_upload(line.split(maxsplit=1)[1] if " " in line else "")
            except ValueError as e:
                print(f"  {e}")
                return True
            self.upload(path, meeting)
        else:
            self.send({"prompt": line})
        return True

    def loop(self):
        print("Meeting Transcript Archive. Type /help for commands, /quit to exit.")
        while True:
            try:
                line = input("archive> ")
            except (EOFError, KeyboardInterrupt):
                print()
                return
            try:
                if not self.handle(line):
                    return
            except KeyboardInterrupt:
                # Ctrl+C at a [y/N] prompt or mid-request; a paused confirmation is cancelled by the next message
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
        chat.send({"prompt": " ".join(args.message)})
    if not args.upload and not args.message:
        chat.loop()


if __name__ == "__main__":
    main()
