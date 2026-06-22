"""LangGraph assembly for the Phase 4 Step 7A extraction flow."""

from collections.abc import Awaitable, Callable

from langgraph.graph import END, START, StateGraph

from interview_api.modules.experience.agents.state import ExperienceAgentState

AgentNode = Callable[[ExperienceAgentState], Awaitable[dict]]


def build_experience_extraction_graph(
    *,
    extraction_node: AgentNode,
    validation_node: AgentNode,
    save_result_node: AgentNode,
):
    graph = StateGraph(ExperienceAgentState)
    graph.add_node("extraction", extraction_node)
    graph.add_node("validation", validation_node)
    graph.add_node("save_result", save_result_node)

    graph.add_edge(START, "extraction")
    graph.add_edge("extraction", "validation")
    graph.add_edge("validation", "save_result")
    graph.add_edge("save_result", END)

    # TODO Step 7B: add Routing Agent and Reliability Agent after extraction.
    return graph.compile()
