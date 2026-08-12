"""
Shared fixtures for the pytest suite.

Scenario configs mirror the 3 evaluation scenarios in expose.md / test_plan.md.
Deterministic parts of the pipeline (Network Agent, Data Retrieval Agent) are run
directly with a fixed event classification as input instead of going through the
Intake/Risk Assessment LLM agents — this makes the tests fast, repeatable, and
independent of Groq API availability/rate limits, while still exercising the real
graph traversal, capacity math, and RiskScore logic against the real dataset.

S3 redefinition (2026-07-31): originally modeled as a port/logistics event via the
same material+region mechanism as S1/S2 — an acknowledged approximation, since no
port/logistics-node concept exists anywhere in the graph (see docs/architecture.md
"Bekannte Grenzen des Seeding-Mechanismus" point 5, historical). This was already
superseded in practice: the 2026-07-20 email to the advisor replaced the planned
port/extreme-weather scenario with a Facility Disruption scenario instead (Strategy
B / entity matching), specifically because a port scenario would need additional
logistics nodes/relationships that don't exist — but tests/conftest.py was never
actually updated to match. Now uses Panasonic's De Soto, KS Midstream-Cell plant
(the same "fire at Panasonic Kansas plant" example already used as the running
illustration in network_agent.py's docstrings) as a facility-specific disruption.

Downstream propagation realism check (2026-07-31): verified via WebSearch that none of
the simulated (geography-only) Cell->Downstream edges from Panasonic or SK Battery
America matched real, publicly reported customer relationships. Added 4
manually-curated, source-cited edges to build_graph.py's VERIFIED_REAL_SUPPLY_LINKS:
Panasonic -> Tesla/Toyota/Lucid Motors (InsideEVs/KCUR coverage of the De Soto plant),
plus Toyota's own vertically-integrated supply chain (TBMNC, Liberty NC -> Toyota's
Georgetown KY assembly plant, confirmed via Automotive Logistics/Toyota Newsroom) — a
small, explicitly-marked exception (`relationship="verified_real_supply"`), not a
general methodology change; the rest of the graph stays simulated. See
docs/open_issues.md P18.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

SCENARIOS = {
    "S1": {  # Kobaltminenstreik im Kongo
        "material": "cobalt",
        "region": "Africa/DRC",
        "origin_tier": "Upstream",
        "severity": 5,
        "risk_type": "supply_disruption",
    },
    "S2": {  # Lithium-Exportbeschränkungen (Chile)
        "material": "lithium",
        "region": "South America / Australia",
        "origin_tier": "Upstream",
        "severity": 4,
        "risk_type": "regulatory",
    },
    "S3": {  # Facility Disruption: Fire at Panasonic's De Soto, KS Midstream-Cell plant
        "mentioned_company": "Panasonic",
        "mentioned_location": "De Soto",
        "origin_tier": "Midstream-Cell",
        "severity": 4,
        "risk_type": "supply_disruption",
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
