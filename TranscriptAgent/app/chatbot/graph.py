"""LangGraph graph for the read-only chatbot.

    START -> agent -> (no tool calls) -> END
                   -> (tool calls)    -> tools -> agent

Every tool only reads, so there is no confirmation step.
"""
import logging
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from prompt import SYSTEM_PROMPT
from tools import TOOLS
log = logging.getLogger(__name__)
INTERRUPTED = "Not run: the previous request ended before this tool ran."


def close_dangling_tool_calls(messages: list) -> list[ToolMessage]:
    """"Not run" results for tool calls a cut-off request left unanswered, so the history stays valid for Bedrock."""
    last_ai = next((i for i in range(len(messages) - 1, -1, -1) if isinstance(messages[i], AIMessage)), None)
    if last_ai is None or not messages[last_ai].tool_calls:
        return []
    answered = {m.tool_call_id for m in messages[last_ai + 1:] if isinstance(m, ToolMessage)}
    return [ToolMessage(content=INTERRUPTED, tool_call_id=call["id"], name=call["name"])
            for call in messages[last_ai].tool_calls if call["id"] not in answered]


REASONING_BLOCKS = {"reasoning_content", "reasoning", "thinking", "redacted_thinking"}


def _for_model(message):
    # Bedrock rejects empty tool results, so an empty list would reach the model as a bare "."
    if isinstance(message, ToolMessage) and message.content in ("", []):
        return message.model_copy(update={"content": "[]"})
    return message


def _without_reasoning(message):
    """An earlier turn's reply without its thinking blocks, which Claude no longer needs."""
    if not isinstance(message, AIMessage) or not isinstance(message.content, list):
        return message
    content = [b for b in message.content if not (isinstance(b, dict) and b.get("type") in REASONING_BLOCKS)]
    return message.model_copy(update={"content": content or ""})


def _guarded(message):
    """The visitor's question marked as the text the guardrail checks."""
    blocks = [message.content] if isinstance(message.content, str) else message.content
    content = [{"type": "guard_content", "text": b} if isinstance(b, str)
               else {"type": "guard_content", "text": b["text"]} if b.get("type") == "text" else b
               for b in blocks]
    return message.model_copy(update={"content": content})


def history_for_model(messages: list, guarded: bool) -> list:
    """The conversation as sent to the model.

    Within one question, every call sends the earlier messages exactly as before: Claude signs its thinking blocks
    against everything that came before them, and rejects them if any of it changed. Earlier questions' thinking blocks
    are dropped, so the current question can be marked for the guardrail now and sent unmarked later. A question the
    guardrail blocked is then not checked again, which would block every later question.
    """
    last_question = next((i for i in range(len(messages) - 1, -1, -1) if isinstance(messages[i], HumanMessage)), None)
    history = []
    for i, message in enumerate(messages):
        message = _for_model(message)
        if last_question is not None and i < last_question:
            message = _without_reasoning(message)
        elif guarded and i == last_question:
            message = _guarded(message)
        history.append(message)
    return history


def make_agent_node(model):
    """The agent node: the chat model with the read-only tools bound, behind the system prompt."""
    model_with_tools = model.bind_tools(TOOLS)
    guarded = getattr(model, "guardrail_config", None) is not None

    async def agent(state: MessagesState):
        history = history_for_model(state["messages"], guarded)
        reply = await model_with_tools.ainvoke([SystemMessage(SYSTEM_PROMPT), *history])
        if reply.response_metadata.get("stopReason") == "guardrail_intervened":
            # the reply is then the guardrail's blocked message; the visitor's text isn't logged
            log.warning("The guardrail blocked a question or an answer")
        return {"messages": [reply]}

    return agent


def build_graph(model, checkpointer=None):
    """Compile the chatbot graph around a chat model that supports tool calling."""
    graph = StateGraph(MessagesState)
    graph.add_node("agent", make_agent_node(model))
    graph.add_node("tools", ToolNode(TOOLS))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition, ["tools", END])
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer)
