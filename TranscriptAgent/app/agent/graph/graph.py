"""LangGraph graph for the transcript archive agent.

    START -> agent -> (no tool calls)         -> END
                   -> (ordinary tool calls)   -> tools -> agent
                   -> (destructive tool call) -> confirm -> agent
                                                 (plain yes: runs the calls; anything else: runs nothing)

The confirm node pauses the graph with interrupt() until the user answers, so a delete (or replacing a
meeting's transcribed audio) can only run after an explicit yes, whatever the model passes as arguments.
The nodes themselves live in nodes.py.
"""
import asyncio
from typing import Literal

from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from transcript_tools import TOOLS

from .nodes import AgentContext, confirm, make_agent_node, needs_confirmation


def _needs_any_confirmation(calls: list) -> bool:
    return any(needs_confirmation(call) for call in calls)


async def route_after_agent(state: MessagesState) -> Literal["tools", "confirm", "__end__"]:
    calls = getattr(state["messages"][-1], "tool_calls", None) or []
    if not calls:
        return END
    # needs_confirmation reads the database, so keep it off the event loop
    if await asyncio.to_thread(_needs_any_confirmation, calls):
        return "confirm"
    return "tools"


def build_graph(model, checkpointer=None):
    """Compile the agent graph around a chat model that supports tool calling."""
    graph = StateGraph(MessagesState, context_schema=AgentContext)
    graph.add_node("agent", make_agent_node(model))
    graph.add_node("tools", ToolNode(TOOLS))
    graph.add_node("confirm", confirm, destinations=("agent",))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route_after_agent, ["tools", "confirm", END])
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer)
