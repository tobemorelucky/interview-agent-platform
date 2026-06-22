"""LangGraph assembly for the Phase 4 Step 7 experience agent flow."""

from collections.abc import Awaitable, Callable

from langgraph.graph import END, START, StateGraph

from interview_api.modules.experience.agents.state import ExperienceAgentState

AgentNode = Callable[[ExperienceAgentState], Awaitable[dict]]


def build_experience_extraction_graph(
    *,
    extraction_node: AgentNode,
    extraction_validation_node: AgentNode,
    routing_node: AgentNode,
    reliability_node: AgentNode,
    quality_gate_node: AgentNode,
    save_result_node: AgentNode,
):
    graph = StateGraph(ExperienceAgentState)
    graph.add_node("extraction", extraction_node)
    graph.add_node("extraction_validation", extraction_validation_node)
    graph.add_node("routing", routing_node)
    graph.add_node("reliability", reliability_node)
    graph.add_node("quality_gate", quality_gate_node)
    graph.add_node("save_result", save_result_node)

    graph.add_edge(START, "extraction")
    graph.add_edge("extraction", "extraction_validation")
    graph.add_edge("extraction_validation", "routing")
    graph.add_edge("routing", "reliability")
    graph.add_edge("reliability", "quality_gate")
    graph.add_edge("quality_gate", "save_result")
    graph.add_edge("save_result", END)

    return graph.compile()
