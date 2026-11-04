"""LangGraph graph for the transcript archive agent.

    START -> agent -> (no tool calls)         -> END
                   -> (ordinary tool calls)   -> tools -> agent
                   -> (destructive tool call) -> confirm -> agent
                                                 (yes: runs the calls; anything else: runs nothing)

The confirm node pauses the graph with interrupt() until the user answers, so a delete (or replacing a
meeting's transcribed audio) can only run after an explicit yes, whatever the model passes as arguments.
"""
import json
import re
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, interrupt

from transcript_tools import TOOLS, meeting_has_transcript

SYSTEM_PROMPT = """You manage and explore an archive of public meeting transcripts for clerks and residents.
Use the tools for every fact; never invent meetings, dates, or quotes.
If you don't know a governing body's id, call list_governing_bodies first.
Dates are YYYY-MM-DD. A meeting has a transcript only when transcript_available is true.
To add a recording: request_audio_upload, give the user the URL to PUT the file to, wait for them to say it's done,
then attach_uploaded_audio and start_transcription. Transcription takes minutes; use check_transcription when asked.
When the user asks to delete something, call the delete tool directly: the app shows the user exactly what will be
removed and asks them to confirm before it runs. If a tool result says the user did not confirm, nothing was changed.
When quoting a transcript, name the meeting and date."""

DELETE_TOOLS = {"delete_governing_body", "delete_meeting"}
YES = re.compile(r"\s*(y|yes|yeah|yep|confirm|confirmed|go ahead|do it|proceed|ok|okay|sure)\b", re.IGNORECASE)
NO = re.compile(r"\s*(n|no|nope|cancel|stop|don't|do not|never mind|nevermind)\b\W*$", re.IGNORECASE)
DECLINED = "Not run: the user did not confirm this action, so nothing was changed."
TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def needs_confirmation(call: dict) -> bool:
    """True for tool calls that destroy data: deletes, and replacing audio on a meeting that has a transcript."""
    if call["name"] in DELETE_TOOLS:
        return True
    if call["name"] == "attach_uploaded_audio":
        return meeting_has_transcript(call["args"].get("meeting_id"))
    return False


def describe(call: dict) -> str:
    """One line telling the user what a guarded tool call will do."""
    args = call["args"]
    if call["name"] == "delete_governing_body":
        return f"delete governing body {args.get('body_id')} and all of its meetings, transcripts, entities and key phrases"
    if call["name"] == "delete_meeting":
        return f"delete meeting {args.get('meeting_id')} and its transcript and analysis"
    return f"replace the recording for meeting {args.get('meeting_id')}, clearing its transcript, entities and key phrases"


def is_yes(reply) -> bool:
    return reply is True or (isinstance(reply, str) and bool(YES.match(reply)))


def run_confirmed(call: dict) -> ToolMessage:
    """Run a tool call the user approved; deletes get confirmed=True from here, never from the model."""
    args = {**call["args"], "confirmed": True} if call["name"] in DELETE_TOOLS else call["args"]
    try:
        result = TOOLS_BY_NAME[call["name"]].invoke(args)
        content = result if isinstance(result, str) else json.dumps(result, default=str)
    except Exception as e:
        content = f"Error: {type(e).__name__}: {e}"
    return ToolMessage(content=content, name=call["name"], tool_call_id=call["id"])


def _for_model(message):
    # Bedrock rejects empty tool results, so an empty list would reach the model as a bare "."
    if isinstance(message, ToolMessage) and message.content in ("", []):
        return message.model_copy(update={"content": "[]"})
    return message


def build_graph(model, checkpointer=None):
    """Compile the agent graph around a chat model that supports tool calling."""
    model_with_tools = model.bind_tools(TOOLS)

    async def agent(state: MessagesState):
        history = [_for_model(m) for m in state["messages"]]
        reply = await model_with_tools.ainvoke([SystemMessage(SYSTEM_PROMPT), *history])
        return {"messages": [reply]}

    def route_after_agent(state: MessagesState) -> Literal["tools", "confirm", "__end__"]:
        calls = getattr(state["messages"][-1], "tool_calls", None) or []
        if not calls:
            return END
        if any(needs_confirmation(call) for call in calls):
            return "confirm"
        return "tools"

    def confirm(state: MessagesState) -> Command[Literal["agent"]]:
        last = state["messages"][-1]
        guarded = [call for call in last.tool_calls if needs_confirmation(call)]
        actions = [describe(call) for call in guarded]
        # Pauses here; the next request's answer comes back as `reply`
        reply = interrupt({
            "question": "Please confirm: " + "; ".join(actions) + ". Reply yes to continue or no to cancel.",
            "actions": actions,
        })
        if is_yes(reply):
            # Run them here so the model's own tool call stays as it wrote it (without confirmed)
            return Command(goto="agent", update={"messages": [run_confirmed(call) for call in last.tool_calls]})
        declined = [ToolMessage(content=DECLINED, name=call["name"], tool_call_id=call["id"]) for call in last.tool_calls]
        # Anything other than a plain no is a new request, so pass it on to the model
        if isinstance(reply, str) and reply.strip() and not NO.match(reply):
            declined.append(HumanMessage(reply))
        return Command(goto="agent", update={"messages": declined})

    graph = StateGraph(MessagesState)
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode(TOOLS))
    graph.add_node("confirm", confirm, destinations=("agent",))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route_after_agent, ["tools", "confirm", END])
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer)
