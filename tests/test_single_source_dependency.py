"""
SingleSourceDependency / CapacityShare — history in three parts, all 2026-08-03 same-day
revisions.

Part 1 (2026-07-31, extended to Midstream-BGM): Downstream's CapacityShare is structurally
always 0.0 (vehicle/pack counts aren't comparable to Upstream/Cell capacity units), which
used to make every Downstream facility's Vulnerability collapse to the same value whenever
the other 3 sub-factors also matched. Fixed by substituting SingleSourceDependency
(1/direct_supplier_count) into the Vulnerability capacity slot for Downstream/Midstream-BGM
only, leaving Upstream/Midstream-Cell on the original CapacityShare.

Part 2 (2026-08-03, later same day, explicit instruction): reverted that tier-split — the
Vulnerability capacity slot now ALWAYS used CapacityShare (dead/0.0 again for Downstream/
Midstream-BGM, since their capacity units are structurally non-comparable), and
ResilienceDiscount now ALWAYS used SingleSourceDependency (previously only Downstream/
Midstream-BGM; Upstream/Midstream-Cell used AltCapacityRatio). One mechanism per formula
slot, not per tier.

Part 3 (2026-08-03, later still, same day): swapped which mechanism feeds which formula slot.
Vulnerability's third slot (0.25 weight) now uses SingleSourceDependency; ResilienceDiscount
now uses CapacityShare: `resilience_discount = (1 - capacity_share) / 2`. Rationale: this
resolves a direction tension that used to require an awkward caveat — "large CapacityShare ->
large Vulnerability" needed explaining as "systemic impact if this facility goes down", NOT
"this facility itself is fragile" (market-leading facilities are typically MORE robust
individually, not less — the opposite of what a raw Vulnerability-weighted reading suggests).
Framed as a resilience question instead, the direction needs no caveat: a facility that
commands most of its tier's capacity has no one to cover for it if it goes down (small
discount); a small player has plenty of alternative capacity elsewhere (large discount).
SingleSourceDependency (this facility's own supplier count) is the genuinely self-related
signal and now lives in Vulnerability instead.

Verified consequences of the Part-3 swap:
  - CapacityShare's degenerate/inapplicable case (non-comparable units for Downstream/
    Midstream-BGM, or capacity_known=False, or a Strategy-B event with no affected_material
    at all) already defaults to 1.0 (worst-case, precautionary default) in
    data_retrieval_agent.py. Feeding that into `(1 - capacity_share) / 2` naturally yields
    resilience_discount=0.0 (no discount) for all of those cases, with no extra "is this
    unknown?" guard needed — unlike the old SingleSourceDependency-based version, which
    needed an explicit `if single_source_dependency > 0` check to distinguish "genuinely 0"
    from "no data" (SingleSourceDependency's zero doesn't carry the same distinction).
  - Net effect for Upstream/Midstream-Cell facilities with capacity_known=True AND a real,
    classified affected_material (only reachable via Strategy A, i.e. never Upstream itself —
    see below): ResilienceDiscount now genuinely differentiates based on real NAATBatt
    capacity data, fixing a documented limitation from Part 2 (Upstream/Cell's
    SingleSourceDependency-based discount was flat/degenerate there).
  - Net effect for Downstream/Midstream-BGM: ResilienceDiscount is flat 0.0 again (same
    degenerate case CapacityShare always had there) — but this is NOT a net loss of
    differentiation, because SingleSourceDependency still differentiates these facilities,
    just via Vulnerability now instead of ResilienceDiscount (same value, different formula
    slot, different mathematical weight: additive 0.25 vs. multiplicative up to 0.5).
  - Upstream specifically: Strategy A (material+region) can never seed an Upstream node
    (import_dependency is always False there), so the "genuine capacity-based discount"
    improvement above is reachable in principle but never actually exercised by S1/S2/S3.
    Strategy B (entity-matched) CAN seed an Upstream node, but Strategy B events carry no
    affected_material at all — so capacity_share is always 1.0 there too (unknown-material
    default), same flat resilience_discount=0.0 as before, just arrived at via a different
    default cascade (capacity_share=1.0 because material is unknown, not because
    single_source_dependency=0 from being a graph root).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.network_agent import run_network_agent
from agents.data_retrieval_agent import run_data_retrieval_agent
from agents.synthesis_agent import _compute_scores
from agents.state import VULNERABILITY_WEIGHTS, compute_tier_weight, RISK_SCORE_MAX_THEORETICAL


def _panasonic_state():
    state = {
        "mentioned_company": "Panasonic", "mentioned_location": "De Soto",
        "origin_tier": "Midstream-Cell", "severity": 4, "risk_type": "supply_disruption",
    }
    state.update(run_network_agent(state))
    state.update(run_data_retrieval_agent(state))
    return state


def _cobalt_state():
    state = {
        "material": "cobalt", "region": "Africa/DRC",
        "origin_tier": "Upstream", "severity": 3, "risk_type": "supply_disruption",
    }
    state.update(run_network_agent(state))
    state.update(run_data_retrieval_agent(state))
    return state


def _upstream_entity_state():
    """Strategy B (entity-matched) disruption at a real Upstream-tier NAATBatt facility —
    the only path that can ever put an Upstream node into affected_nodes (Strategy A can't,
    see module docstring). No affected_material is set for this scenario type."""
    state = {
        "mentioned_company": "Albemarle Corporation", "mentioned_location": "Silver Peak",
        "origin_tier": "Upstream", "severity": 3, "risk_type": "supply_disruption",
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
    based on single-source dependency (still true after the Part 3 swap: SingleSourceDependency
    lives in Vulnerability now, not ResilienceDiscount, but it's the same differentiating
    value either way)."""
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


def test_single_source_dependency_now_feeds_vulnerability_not_capacity_share():
    """2026-08-03 (Part 3): Vulnerability's third slot (0.25 weight) now uses
    SingleSourceDependency, not CapacityShare — CapacityShare moved to ResilienceDiscount
    instead (see module docstring). Verify by reconstructing Vulnerability from
    VULNERABILITY_WEIGHTS + fd and comparing against the actual computed RiskScore; a
    version that still used capacity_share here would disagree, since Panasonic's
    capacity_share (1.0) and single_source_dependency (1/3) differ."""
    state = _panasonic_state()
    risk_scores, _ = _compute_scores(state)
    panasonic_id = next(n["id"] for n in state["affected_nodes"] if n["company"] == "Panasonic")
    fd = state["facility_data"][panasonic_id]
    node = next(n for n in state["affected_nodes"] if n["id"] == panasonic_id)

    assert fd["capacity_share"] != fd["single_source_dependency"], (
        "test is only meaningful if these two differ for this facility"
    )

    expected_vulnerability = (
        VULNERABILITY_WEIGHTS["import_dependency"] * float(fd["import_dep"])
        + VULNERABILITY_WEIGHTS["supplier_concentration"] * float(fd["supplier_concentration"])
        + VULNERABILITY_WEIGHTS["single_source_dependency"] * float(fd["single_source_dependency"])
        + VULNERABILITY_WEIGHTS["lead_time_norm"] * float(fd["lead_time_norm"])
    )
    tw = compute_tier_weight(node["segment"], state["origin_tier"])
    rd = fd["resilience_discount"]
    expected_raw = state["severity"] * tw * expected_vulnerability * (1 - rd)
    expected_normalized = round(expected_raw / RISK_SCORE_MAX_THEORETICAL * 100, 2)

    assert abs(risk_scores[panasonic_id] - expected_normalized) < 1e-6


# ── Midstream-BGM (2026-08-03) ───────────────────────────────────────────────────

def test_midstream_bgm_facilities_are_no_longer_tied_on_risk_score():
    """Regression test: before this fix, every Midstream-BGM facility's Vulnerability was
    identical (CapacityShare structurally always 0.0 there — mixed MT/mm²/GWh units, see
    data_retrieval_agent.py), so any Upstream-origin event's Midstream-BGM exposures all
    scored the same RiskScore regardless of actual graph structure. Must now differ based
    on single-source dependency, same mechanism as Downstream."""
    state = _cobalt_state()
    risk_scores, _ = _compute_scores(state)
    bgm_scores = {
        n["id"]: risk_scores[n["id"]]
        for n in state["affected_nodes"]
        if n["segment"] == "Midstream-BGM" and n["id"] in risk_scores
    }
    assert len(set(bgm_scores.values())) > 1, (
        "all Midstream-BGM facilities still tied on RiskScore — "
        "single_source_dependency isn't differentiating them"
    )


def test_midstream_bgm_single_source_dependency_computed_from_graph_in_degree():
    """Mirrors the Downstream case: single_source_dependency = 1/direct_supplier_count,
    where direct_supplier_count counts this Midstream-BGM node's Upstream-tier suppliers
    (in-degree) — not Cell-tier suppliers, since Midstream-BGM sits one edge earlier in
    the Upstream->BGM->Cell->Downstream chain."""
    state = _cobalt_state()
    for n in state["affected_nodes"]:
        if n["segment"] != "Midstream-BGM":
            continue
        fd = state["facility_data"][n["id"]]
        expected = 1.0 / n["direct_supplier_count"] if n["direct_supplier_count"] > 0 else 0.0
        assert fd["single_source_dependency"] == expected


# ── ResilienceDiscount (2026-08-03, Part 3: CapacityShare-based) ─────────────────

def test_resilience_discount_derived_from_capacity_share():
    """ResilienceDiscount must equal (1 - capacity_share) / 2 for every facility, regardless
    of tier — verified across all affected Upstream/Midstream-Cell facilities in a Strategy A
    (material+region) scenario with real NAATBatt capacity data, where capacity_share
    actually varies instead of sitting flat at its 1.0 default. No min(...,0.5) cap needed:
    capacity_share is always in [0,1], so (1-capacity_share)/2 is always in [0,0.5] on its
    own."""
    state = _cobalt_state()
    checked_varying = False
    for n in state["affected_nodes"]:
        if n["segment"] not in ("Upstream", "Midstream-Cell"):
            continue
        fd = state["facility_data"][n["id"]]
        expected = (1.0 - fd["capacity_share"]) / 2.0
        assert abs(fd["resilience_discount"] - expected) < 1e-9
        if 0.0 < fd["capacity_share"] < 1.0:
            checked_varying = True
    assert checked_varying, (
        "expected at least one Upstream/Midstream-Cell facility with a real, "
        "non-degenerate capacity_share in this scenario"
    )


def test_capacity_share_differentiates_resilience_discount_for_known_material_cell_facilities():
    """2026-08-03 (Part 3) fixes a documented Part-2 limitation: Upstream/Midstream-Cell
    facilities used to get a flat resilience_discount (SingleSourceDependency-based, and
    Upstream's in-degree is structurally always 0). Now that ResilienceDiscount is
    CapacityShare-based, Midstream-Cell facilities reached via a Strategy A event with a real,
    classified material genuinely differ based on real NAATBatt capacity data."""
    state = _cobalt_state()
    risk_scores, _ = _compute_scores(state)
    cell_discounts = {
        n["id"]: state["facility_data"][n["id"]]["resilience_discount"]
        for n in state["affected_nodes"]
        if n["segment"] == "Midstream-Cell" and n["id"] in risk_scores
        and state["facility_data"][n["id"]]["capacity_known"]
    }
    assert len(set(cell_discounts.values())) > 1, (
        "all capacity-known Midstream-Cell facilities still tied on resilience_discount — "
        "capacity_share isn't differentiating them"
    )


def test_strategy_b_no_material_forces_capacity_share_and_discount_to_worst_case():
    """Degenerate case (2026-08-03, Part 3): Strategy B (entity-matched) events carry no
    affected_material at all, so the CapacityShare denominator (total capacity for that
    material/tier) is always 0 -> capacity_share defaults to 1.0 (worst-case, precautionary
    default — see data_retrieval_agent.py::_capacity_share) even for a facility with
    capacity_known=True, like Albemarle Corporation/Silver Peak here. That 1.0 flows straight
    into resilience_discount=(1-1)/2=0.0 — the conservative "no discount" outcome, but arrived
    at via the capacity_share default cascade, not any Upstream-specific graph-structure
    guard (there isn't one anymore, see module docstring Part 3)."""
    state = _upstream_entity_state()
    upstream_nodes = [n for n in state["affected_nodes"] if n["segment"] == "Upstream"]
    assert len(upstream_nodes) == 1, "expected exactly the seed facility itself"
    fd = state["facility_data"][upstream_nodes[0]["id"]]
    assert fd["capacity_known"] is True, "expected a facility with real NAATBatt capacity data"
    assert fd["capacity_share"] == 1.0
    assert fd["resilience_discount"] == 0.0
