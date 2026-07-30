"""
Shared fixtures for the pytest suite.

Scenario configs mirror the 3 evaluation scenarios in expose.md / test_plan.md.
Deterministic parts of the pipeline (Network Agent, Data Retrieval Agent) are run
directly with a fixed event classification as input instead of going through the
Intake/Risk Assessment LLM agents — this makes the tests fast, repeatable, and
independent of Groq API availability/rate limits, while still exercising the real
graph traversal, capacity math, and RiskScore logic against the real dataset.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

SCENARIOS = {
    "S1": {  # Kobaltminenstreik im Kongo
        "affected_material": "cobalt",
        "affected_region": "Africa/DRC",
        "origin_tier": "Upstream",
        "severity": 5,
        "risk_type": "supply_disruption",
    },
    "S2": {  # Lithium-Exportbeschränkungen (Chile)
        "affected_material": "lithium",
        "affected_region": "South America / Australia",
        "origin_tier": "Upstream",
        "severity": 4,
        "risk_type": "regulatory",
    },
    "S3": {  # Hafenausfall (graphite/lithium via Asia)
        "affected_material": "graphite",
        "affected_region": "Asia (China)",
        "origin_tier": "Upstream",
        "severity": 3,
        "risk_type": "logistics",
    },
}


@pytest.fixture(params=["S1", "S2", "S3"])
def scenario_state(request):
    from agents.data_retrieval_agent import run_data_retrieval_agent
    from agents.network_agent import run_network_agent

    state = dict(SCENARIOS[request.param])
    state.update(run_network_agent(state))
    state.update(run_data_retrieval_agent(state))
    state["_scenario_id"] = request.param
    return state
