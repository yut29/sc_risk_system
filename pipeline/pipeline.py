"""
LangGraph Pipeline — SC Risk System

Graph topology:
  intake → risk_assessment → network → data_retrieval → synthesis → validation
                ↑                                            ↑
                └──────── severe retry ────────────────────┘
                                    ↑
                          minor retry ──────────────────────┘

Entry point: run_pipeline(raw_input) → PipelineState
"""

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from langgraph.graph import StateGraph, END

from agents.state import PipelineState, MAX_VALIDATION_ITERATIONS
from agents.intake_agent import run_intake_agent
from agents.risk_assessment_agent import run_risk_assessment_agent
from agents.network_agent import run_network_agent
from agents.data_retrieval_agent import run_data_retrieval_agent
from agents.synthesis_agent import run_synthesis_agent
from agents.validation_agent import run_validation_agent


# ── Routing logic ─────────────────────────────────────────────────────────────

def route_after_intake(state: PipelineState) -> str:
    if not state.get("relevant", False):
        return END
    return "risk_assessment"


def route_after_validation(state: PipelineState) -> str:
    if state.get("iteration", 0) >= MAX_VALIDATION_ITERATIONS:
        return END
    if not state.get("valid", False):
        ft = state.get("failure_type")
        if ft == "severe":
            return "risk_assessment"   # restart from risk classification
        if ft == "minor":
            return "synthesis"         # redo report only
    return END


# ── Graph construction ────────────────────────────────────────────────────────

def build_pipeline() -> StateGraph:
    g = StateGraph(PipelineState)

    g.add_node("intake",          run_intake_agent)
    g.add_node("risk_assessment", run_risk_assessment_agent)
    g.add_node("network",         run_network_agent)
    g.add_node("data_retrieval",  run_data_retrieval_agent)
    g.add_node("synthesis",       run_synthesis_agent)
    g.add_node("validation",      run_validation_agent)

    g.set_entry_point("intake")

    g.add_conditional_edges("intake", route_after_intake,
                            {"risk_assessment": "risk_assessment", END: END})

    g.add_edge("risk_assessment", "network")
    g.add_edge("network",         "data_retrieval")
    g.add_edge("data_retrieval",  "synthesis")

    g.add_conditional_edges("validation", route_after_validation, {
        "risk_assessment": "risk_assessment",
        "synthesis":       "synthesis",
        END:               END,
    })

    g.add_edge("synthesis", "validation")

    return g.compile()


# ── Public entry point ────────────────────────────────────────────────────────

_pipeline = None


def run_pipeline(raw_input: str) -> PipelineState:
    global _pipeline
    if _pipeline is None:
        _pipeline = build_pipeline()

    initial_state: PipelineState = {
        "raw_input": raw_input,
        "iteration": 0,
    }
    return _pipeline.invoke(initial_state)


def stream_pipeline(raw_input: str):
    """Yields (node_name, accumulated_state) after each agent completes."""
    global _pipeline
    if _pipeline is None:
        _pipeline = build_pipeline()

    initial_state: PipelineState = {"raw_input": raw_input, "iteration": 0}
    accumulated: dict = dict(initial_state)

    for event in _pipeline.stream(initial_state):
        for node_name, state_update in event.items():
            accumulated.update(state_update)
            yield node_name, accumulated
