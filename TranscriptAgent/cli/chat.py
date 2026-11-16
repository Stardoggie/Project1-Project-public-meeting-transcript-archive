"""Terminal chat for the meeting transcript agent; give it a recording's file path and it uploads it.

    python chat.py                                   interactive chat
    python chat.py "What meetings are there?"        ask one question and exit
    python chat.py --upload PATH --meeting "Budget Hearing, Springfield City Council"
    python chat.py --upload PATH                     upload; chats until the agent knows which meeting
    python chat.py --deployed                        chat with the agent deployed to AWS instead

Start the agent first with `agentcore dev` in TranscriptAgent/, or deploy it with `agentcore deploy`.
--deployed signs requests with your AWS credentials, so it needs boto3 (the archive's .venv has it).
"""
import argparse
import os
import re
import sys
import urllib.error
import uuid
from client import DeployedAgentError, find_agent_url, find_runtime_arn, invoke, invoke_deployed, post_file, put_file, summarize
from console import ask, read_message
ALLOWED_EXTENSIONS = {".mp3", ".wav"}
MAX_BYTES = 500 * 1024 * 1024
# Example paths in the style of this computer
RECORDINGS = "C:\\Recordings" if os.name == "nt" else "~/Recordings"
MY_RECORDINGS = "C:\\My Recordings" if os.name == "nt" else "~/My Recordings"
SEP = os.sep
HELP = f"""Type a question or request, e.g. "What meetings does Springfield City Council have?"

To add a recording, give it the file's path, by typing it or dragging the file into this window. It uploads the
file and starts transcription. Say which meeting it's for in the same message or earlier in the chat; otherwise
the agent asks:
  {RECORDINGS}{SEP}hearing.mp3 Budget Hearing, Springfield City Council
  "{MY_RECORDINGS}{SEP}hearing.wav" is for the zoning meeting

  /new    start a new conversation
  /help   show this help
  /quit   exit
"""
# Where a recording's path ends, and where an unquoted path can start
AUDIO_END = re.compile(r"\.(?:mp3|wav)(?=$|[\s,;:!?)\]]|\.(?:\s|$))", re.IGNORECASE)  # also before a full stop
PATH_START = re.compile(r"(?:^|(?<=\s))(?:[A-Za-z]:[\\/]|\\\\|\.{1,2}[\\/]|~[\\/])")
QUOTED = re.compile(r"([\"'])(.+?)\1")
# A quoted string that is a whole path: it starts like one and ends at an extension, or is one audio file name
WHOLE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|\.{1,2}[\\/]|~[\\/]).*\.\w+|\S+\.(?:mp3|wav)", re.IGNORECASE | re.DOTALL)
DRAGGED = re.compile(r"'((?:[^']|'')*)'")  # PowerShell's single quotes, where '' is an apostrophe
URL = re.compile(r"https?://", re.IGNORECASE)
UPLOAD_WORD = re.compile(r"^\s*/?upload\b[\s:,-]*", re.IGNORECASE)
# Backticks or emphasis that may wrap a link, like `url` or **url**
LINK_WRAPPER = r"(?:`|\*\*|\*|__|_)?"


def new_session() -> str:
    return f"cli-{uuid.uuid4().hex}"  # AgentCore wants session ids of 33+ characters


def _inner_text(match) -> str:
    # **bold** fills group 1, `code` fills group 2
    return match.group(1) or match.group(2)


def plain(text: str) -> str:
    """Drop markdown bold/code markers so replies read cleanly in a terminal."""
    return re.sub(r"\*\*(.+?)\*\*|`([^`]+)`", _inner_text, text)


def without_link(text: str, url: str) -> str:
    """The text with every copy of the link (any query string) cut out, in case a reply repeats it despite the system
    prompt: a whole markdown link, or a bare one, with its backticks, emphasis or <> but not the punctuation after it."""
    # the link without its query string, then anything up to (not including) the punctuation that ends a sentence
    link = re.escape(url.split("?", 1)[0]) + r"(?:[^\s<>()`*\[\]]*[^\s<>()`*\[\],.;:!?'\"])?"
    markdown_link = LINK_WRAPPER + r"\[[^\]]*\]\(<?" + link + r">?\)" + LINK_WRAPPER
    bare_link = LINK_WRAPPER + r"<?" + link + r">?" + LINK_WRAPPER
    cut = re.sub(markdown_link, "", text)
    cut = re.sub(bare_link, "", cut)
    if cut == text:
        return text.strip()
    cut = re.sub(r"[ \t]+([,.;:!?])", r"\1", cut)  # no space left before the punctuation after the link
    return re.sub(r"[ \t]{2,}", " ", cut).strip()


def safe_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "transcript"


def _looks_like_path(text: str) -> bool:
    """True for something ending in .mp3/.wav, or with a folder separator and a file extension."""
    if AUDIO_END.search(text):
        return True
    has_folder = "\\" in text or "/" in text
    has_extension = bool(os.path.splitext(text)[1])
    return has_folder and has_extension


def _rest(text: str, start: int, end: int) -> str:
    """The message with the path cut out: what's left says which meeting it's for."""
    rest = f"{text[:start]} {text[end:]}"
    rest = UPLOAD_WORD.sub("", rest)
    return re.sub(r"\s+", " ", rest).strip(" ,;:-.")


def local_path(path: str) -> str:
    """The path as this computer reads it, with ~ expanded. On Linux and macOS, a Windows-style path that doesn't
    exist as typed (~\\Music\\hearing.mp3, .\\hearing.mp3) is tried with / instead, since \\ isn't a separator there."""
    if os.sep == "/" and "\\" in path and not os.path.exists(os.path.expanduser(path)):
        path = path.replace("\\", "/")
    return os.path.expanduser(path)


def _opening_quotes(text: str) -> list[int]:
    """Where a quoted path could start. A quote straight after a letter or digit is an apostrophe (Here's, mayor's),
    not an opening quote."""
    positions = []
    for index, char in enumerate(text):
        after_letter = index > 0 and text[index - 1].isalnum()
        if char in "\"'" and not after_letter:
            positions.append(index)
    return positions


def _closing_quotes(text: str, start: int, quote: str) -> list[int]:
    """Every later copy of the quote, outermost first, so an apostrophe inside ('C:\\Recs\\Bob's.mp3') can't cut a
    file name short."""
    positions = [index for index in range(start, len(text)) if text[index] == quote]
    return list(reversed(positions))


def find_recording(line: str) -> tuple[str | None, str]:
    """Find a file path in what the user typed; returns (path, rest of the message), or (None, line) if there's none.

    Handles quoted paths, paths dragged into the window (PowerShell adds "& "), unquoted paths (even with spaces)
    and bare file names in the current folder. A path-like one that doesn't exist is still returned, so the user is
    told it wasn't found instead of the agent guessing.
    """
    text = line.strip()
    if text.startswith("& "):  # PowerShell prefixes dragged paths with the call operator
        text = text[2:].lstrip()
        dragged = DRAGGED.match(text)
        if dragged:
            candidate = dragged.group(1).replace("''", "'").strip()
            if os.path.isfile(local_path(candidate)) or _looks_like_path(candidate):
                return candidate, _rest(text, dragged.start(), dragged.end())
    # Try a quote at every position, so an apostrophe (Here's, mayor's) can't hide or swallow a quoted path
    for pos in _opening_quotes(text):
        match = QUOTED.match(text, pos)
        if not match:
            continue
        for close in _closing_quotes(text, match.end(), text[pos]):
            candidate = text[pos + 1:close].strip()
            if os.path.isfile(local_path(candidate)):
                return candidate, _rest(text, pos, close + 1)
        candidate = match.group(2).strip()
        if os.path.isfile(local_path(candidate)) or WHOLE_PATH.fullmatch(candidate):
            return candidate, _rest(text, match.start(), match.end())
    for end_match in AUDIO_END.finditer(text):
        end = end_match.end()
        starts = [m.start() for m in PATH_START.finditer(text, 0, end)]
        word_starts = [m.start() for m in re.finditer(r"(?<!\S)\S", text[:end])]  # a bare name can have spaces too
        # Earliest start first, so an unquoted path or file name with spaces is matched whole
        for start in sorted(set(starts + word_starts)):
            if os.path.isfile(local_path(text[start:end])):
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


def said_yes(answer: str) -> bool:
    """The answer to a [y/N] prompt: only y or yes agrees; Enter or anything else is no."""
    return answer.strip().lower() in ("y", "yes")


class Chat:
    def __init__(self, agent_url: str, transcript_dir: str = ".", runtime_arn: str | None = None):
        self.agent_url = agent_url
        self.runtime_arn = runtime_arn  # set when chatting with the deployed agent instead of a local one
        self.transcript_dir = transcript_dir
        self.session_id = new_session()
        self.turn = 0  # counts the user's messages
        self.pending_path = None  # the recording to send when the agent hands out an upload link
        self.pending_turn = 0  # the message that gave pending_path
        self.failed = False  # a request couldn't reach the agent, or it returned an error

    def _request(self, payload: dict) -> list[dict]:
        """One request to whichever agent this chat talks to: the deployed runtime, or the local one."""
        if self.runtime_arn:
            return invoke_deployed(self.runtime_arn, self.session_id, payload)
        return invoke(self.agent_url, self.session_id, payload)

    def send(self, payload: dict) -> dict | None:
        try:
            result = summarize(self._request(payload))
        except DeployedAgentError as e:
            print(f"  The deployed agent couldn't answer: {e}")
            self.failed = True
            return None
        except urllib.error.HTTPError as e:  # reached the agent, but it failed
            print(f"  The agent at {self.agent_url} returned an error: HTTP {e.code} {e.reason}.")
            self.failed = True
            return None
        except (urllib.error.URLError, OSError) as e:
            print(f"  Can't reach the agent at {self.agent_url} ({e}). Is `agentcore dev` running?")
            self.failed = True
            return None
        if result["upload_url"]:
            # The agent is ready for a recording: upload it here instead of showing the link
            self.show(result, hide=result["upload_url"])
            # A confirmation in the same reply is answered first, or the upload message would answer it
            self.confirm(result)
            return self.finish_upload(result["upload_url"], result["audio_key"], result["upload_fields"])
        self.show(result)
        return self.confirm(result)

    def show(self, result: dict, hide: str | None = None):
        """Print replies and transcripts in order, then errors; hide is an upload link to cut out of the replies."""
        for kind, value in result["items"]:
            if kind == "transcript":
                self.show_transcript(value)
                continue
            if hide:
                value = without_link(value, hide)
            if value:
                print("\n" + plain(value) + "\n")
        for error in result["errors"]:
            print(f"  Error: {error}")

    def show_transcript(self, shown: dict):
        name = f"{shown['title']} {shown['meeting_date']}"
        path = os.path.join(self.transcript_dir, safe_filename(name) + ".txt")
        print(f"\n----- Full transcript: {shown['title']} ({shown['meeting_date']}) -----\n")
        print(shown["transcript"])
        try:
            os.makedirs(self.transcript_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(shown["transcript"])
        except OSError as e:
            print(f"\n----- end of transcript ({len(shown['transcript']):,} characters) -----")
            print(f"  Couldn't save the transcript to {path}: {e}")
            return
        print(f"\n----- end of transcript ({len(shown['transcript']):,} characters), saved to {path} -----")

    def confirm(self, result: dict) -> dict:
        """Answer a pending confirmation with a [y/N] prompt; Enter or anything but yes cancels."""
        while result and result["confirmation"]:
            question = result["confirmation"].replace(" Reply yes to continue or no to cancel.", "")
            answer = ask(f"  {question} [y/N] ")
            result = self.send({"confirm": said_yes(answer)})
        return result

    def finish_upload(self, url: str, audio_key: str | None = None, fields: dict | None = None) -> dict | None:
        """Upload the recording to the link the agent handed out, then have it attach and transcribe.
        fields are the signed form fields when S3 wants a form upload (POST) rather than a plain PUT."""
        path = self.pending_path
        self.pending_path = None
        # A path from this message or the one before (e.g. answering "which meeting?") is used straight away; an
        # older one may belong to a request the user dropped, so check before sending it somewhere new
        given_earlier = self.turn - self.pending_turn > 1
        if path and given_earlier:
            answer = ask(f"  Upload {os.path.basename(path)} (the file you gave earlier) for this? [y/N] ")
            if not said_yes(answer):
                path = None
        while not path:
            answer = ask("  Path to the recording (type it or drag the file here; Enter to cancel): ").strip()
            if not answer:
                print("  Not uploaded.")
                return None
            found, _ = find_recording(answer)
            path = local_path(found or answer.strip("\"'"))
            problem = check_recording(path)
            if problem:
                print(f"  {problem}")
                path = None
        # The link's name fixes the format Transcribe expects; if the agent guessed .mp3 for a .wav, get a matching one
        extension = os.path.splitext(path)[1].lower()
        if audio_key and not audio_key.lower().endswith(extension):
            self.pending_path = path
            self.pending_turn = self.turn
            return self.send({"prompt": f"The recording is actually named {os.path.basename(path)}; "
                                        "get an upload link for that name instead."})
        size_mb = os.path.getsize(path) / 1024 / 1024
        print(f"  Uploading {os.path.basename(path)} ({size_mb:,.1f} MB)...", end=" ", flush=True)
        try:
            if fields:
                status = post_file(url, fields, path)
            else:
                status = put_file(url, path)
        except urllib.error.HTTPError as e:
            # S3 refused it, e.g. EntityTooLarge or a wrong type; its reason is in the XML it sends back
            code = re.search(r"<Code>(\w+)</Code>", e.read().decode("utf-8", "replace"))
            reason = code.group(1) if code else f"HTTP {e.code}"
            print(f"failed: S3 refused the file ({reason}).")
            return None
        except (urllib.error.URLError, OSError) as e:
            print(f"failed: {e}")
            return None
        if status not in (200, 201, 204):
            print(f"failed (HTTP {status}).")
            return None
        print("done.")
        return self.send({"prompt": "The upload is done. Attach it to that meeting and start transcription."})

    def upload(self, path: str, meeting: str):
        path = local_path(path)  # ~\Music\hearing.mp3, typed or from --upload
        problem = check_recording(path)
        if problem:
            print(f"  {problem}")
            self.failed = True
            return
        # Kept until the agent hands out an upload link, even if it first asks which meeting it's for
        self.pending_path = path
        self.pending_turn = self.turn
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
        # Bare quit/exit only as the whole line, so "Exit polls: ..." is a question
        if command in ("/quit", "/exit") or line.lower() in ("quit", "exit"):
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
        if "\n" in line or URL.search(line):
            path, rest = None, line
        else:
            path, rest = find_recording(line)
        if path:
            self.upload(path, rest)
        elif UPLOAD_WORD.fullmatch(line):
            print(f"  Give it the recording's path, e.g. {RECORDINGS}{SEP}hearing.mp3 Budget Hearing")
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
    parser.add_argument("--deployed", action="store_true", help="chat with the agent deployed to AWS")
    parser.add_argument("--transcripts", default=".", metavar="DIR", help="where to save full transcripts")
    args = parser.parse_args()
    if args.deployed:
        runtime_arn = find_runtime_arn()
        if not runtime_arn:
            sys.exit("Can't find the deployed agent. Deploy it with `agentcore deploy` in TranscriptAgent/, "
                     "or set AGENT_RUNTIME_ARN.")
        print(f"Using the deployed agent: {runtime_arn}")
        chat = Chat(runtime_arn, args.transcripts, runtime_arn=runtime_arn)
    else:
        agent_url = args.agent_url or find_agent_url()
        if not agent_url:
            sys.exit("Can't find the agent. Start it with `agentcore dev` in TranscriptAgent/, or set AGENT_URL.")
        chat = Chat(agent_url, args.transcripts)
    if args.upload:
        chat.upload(args.upload, args.meeting or "")
        if chat.failed:
            sys.exit(1)  # the recording can't be used, or the agent couldn't be reached
        if chat.pending_path and not args.message:
            chat.loop()  # the agent asked which meeting it's for; stay so the user can answer
            return
    if args.message:
        chat.handle(" ".join(args.message))
        if chat.failed:
            sys.exit(1)  # a one-off request that didn't get through
    elif not args.upload:
        chat.loop()


if __name__ == "__main__":
    main()
