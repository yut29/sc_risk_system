"""
SingleSourceDependency (2026-07-31) — Vulnerability sub-factor for Downstream facilities.

Downstream's CapacityShare is structurally always 0.0 (vehicle/pack counts aren't
comparable to Upstream/Cell capacity units), which used to make every Downstream
facility's Vulnerability collapse to the same value whenever the other 3 sub-factors
also matched (common, since import_dependency/supplier_concentration/lead_time_norm are
generic per-tier values). Found while investigating why Tesla/Toyota/Lucid Motors — all
newly connected via a verified real supply edge (Panasonic -> Tesla/Toyota/Lucid, see
build_graph.py's VERIFIED_REAL_SUPPLY_LINKS) — tied at an identical RiskScore with every
other Downstream facility, giving no way to distinguish them.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.network_agent import run_network_agent
from agents.data_retrieval_agent import run_data_retrieval_agent
from agents.synthesis_agent import _compute_scores


def _panasonic_state():
    state = {
        "mentioned_company": "Panasonic", "mentioned_location": "De Soto",
        "origin_tier": "Midstream-Cell", "severity": 4, "risk_type": "supply_disruption",
    }
    state.update(run_network_agent(state))
    state.update(run_data_retrieval_agent(state))
    return state


def test_single_source_dependency_computed_from_graph_in_degree():
    """Lucid Motors has exactly 1 connected Cell-tier supplier in the graph (in-degree 1,
    Panasonic only) -> single_source_dependency must be 1.0 (fully single-sourced within
    the NA graph). Toyota has 2 (Panasonic + its own TBMNC plant, a verified real
    vertically-integrated supply link added 2026-07-31) -> 0.5. A facility with more
    connected suppliers must have a proportionally lower value."""
    state = _panasonic_state()
    by_company = {n["company"]: n["id"] for n in state["affected_nodes"]}
    lucid_id = by_company["Lucid Motors"]
    assert state["facility_data"][lucid_id]["single_source_dependency"] == 1.0

    toyota_id = by_company["Toyota"]
    assert state["facility_data"][toyota_id]["single_source_dependency"] == 0.5

    tesla_id = by_company["Tesla"]
    tesla_fd = state["facility_data"][tesla_id]
    assert 0 < tesla_fd["single_source_dependency"] < 1.0


def test_downstream_facilities_are_no_longer_tied_on_risk_score():
    """Regression test: before this fix, every Downstream facility's Vulnerability was
    identical (CapacityShare always 0.0 there), so a single seed's propagated exposures
    all scored the same RiskScore regardless of actual graph structure. Must now differ
    based on single-source dependency."""
    state = _panasonic_state()
    risk_scores, _ = _compute_scores(state)
    downstream_scores = {
        n["id"]: risk_scores[n["id"]]
        for n in state["affected_nodes"]
        if n["segment"] == "Downstream" and n["id"] in risk_scores
    }
    assert len(set(downstream_scores.values())) > 1, (
        "all Downstream facilities still tied on RiskScore — "
        "single_source_dependency isn't differentiating them"
    )


def test_upstream_and_cell_capacity_share_unaffected():
    """Non-Downstream segments must keep using the original CapacityShare — this change
    is scoped to Downstream only, since CapacityShare IS meaningfully computable for
    Upstream/Midstream-Cell (comparable MT/yr and GWh/yr units)."""
    state = _panasonic_state()
    panasonic_id = next(n["id"] for n in state["affected_nodes"] if n["company"] == "Panasonic")
    fd = state["facility_data"][panasonic_id]
    assert fd["single_source_dependency"] == 0.0  # not Downstream, field must stay inert
