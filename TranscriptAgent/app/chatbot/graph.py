"""LangGraph graph for the read-only chatbot.

    START -> agent -> (no tool calls) -> END
                   -> (tool calls)    -> tools -> agent

Every tool only reads, so there is no confirmation step.
"""
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from prompt import SYSTEM_PROMPT
from tools import TOOLS
INTERRUPTED = "Not run: the previous request ended before this tool ran."


def close_dangling_tool_calls(messages: list) -> list[ToolMessage]:
    """"Not run" results for tool calls a cut-off request left unanswered, so the history stays valid for Bedrock."""
    last_ai = next((i for i in range(len(messages) - 1, -1, -1) if isinstance(messages[i], AIMessage)), None)
    if last_ai is None or not messages[last_ai].tool_calls:
        return []
    answered = {m.tool_call_id for m in messages[last_ai + 1:] if isinstance(m, ToolMessage)}
    return [ToolMessage(content=INTERRUPTED, tool_call_id=call["id"], name=call["name"])
            for call in messages[last_ai].tool_calls if call["id"] not in answered]


def _for_model(message):
    # Bedrock rejects empty tool results, so an empty list would reach the model as a bare "."
    if isinstance(message, ToolMessage) and message.content in ("", []):
        return message.model_copy(update={"content": "[]"})
    return message


def make_agent_node(model):
    """The agent node: the chat model with the read-only tools bound, behind the system prompt."""
    model_with_tools = model.bind_tools(TOOLS)

    async def agent(state: MessagesState):
        history = [_for_model(m) for m in state["messages"]]
        reply = await model_with_tools.ainvoke([SystemMessage(SYSTEM_PROMPT), *history])
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
