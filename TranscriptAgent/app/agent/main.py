from functools import lru_cache

from strands import Agent
from strands.agent.conversation_manager.null_conversation_manager import NullConversationManager
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from model.load import load_model
from parse import parse_payload
from memory.session import get_memory_session_manager
from transcript_tools import TOOLS

DEFAULT_SYSTEM_PROMPT = """You manage and explore an archive of public meeting transcripts for clerks and residents.
Use the tools for every fact; never invent meetings, dates, or quotes.
If you don't know a governing body's id, call list_governing_bodies first.
Dates are YYYY-MM-DD. A meeting has a transcript only when transcript_available is true.
To add a recording: request_audio_upload, give the user the URL to PUT the file to, wait for them to say it's done,
then attach_uploaded_audio and start_transcription. Transcription takes minutes; use check_transcription when asked.
Before any delete, state exactly what will be removed (deleting a governing body removes all its meetings)
and only proceed with confirmed=True after the user explicitly says yes.
When quoting a transcript, name the meeting and date."""

def _make_conversation_manager():
    return NullConversationManager()

@lru_cache(maxsize=128)
def _get_agent(session_id: str, actor_id: str) -> Agent:
    """Given a session_id and actor_id, return or construct the corresponding Strands agent.
    Note: caching helps avoid repeated identity fetches on non-bedrock model loads
    and supports in-memory session management for local dev."""
    return Agent(
        model=load_model(),
        session_manager=get_memory_session_manager(session_id, actor_id),
        conversation_manager=_make_conversation_manager(),
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        tools=TOOLS,
    )

def create_app():
    app = BedrockAgentCoreApp()
    log = app.logger

    @app.entrypoint
    async def invoke(payload, context):
        log.info("Invoking Agent.....")

        session_id = getattr(context, "session_id", None) or "default-session"
        prompt, actor_id = parse_payload(payload)

        log.info(f"Invoking with session_id={session_id} and actor_id={actor_id}")
        agent = _get_agent(session_id, actor_id)

        async for event in agent.stream_async(prompt):
            if not isinstance(event, dict) or "event" not in event:
                continue
            cbs = event["event"].get("contentBlockStart")
            if cbs is not None and not cbs.get("start"):
                continue
            yield event

    return app

app = create_app()
if __name__ == "__main__":
    app.run()
