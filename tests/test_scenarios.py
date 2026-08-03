"""
Ebene 3: Szenario-Tests (S1-S3) — deterministic end-to-end (docs/test_plan.md).

Covers the Network Agent -> Data Retrieval Agent -> Synthesis scoring chain for
each of the 3 expose.md scenarios. LLM classification (Intake/Risk Assessment) is
fixed via conftest.SCENARIOS instead of called live, since it is non-deterministic
and Groq's free tier rate-limits real calls (see tests/test_hallucination.py for a
slow, opt-in end-to-end test that does call the LLM agents).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.network_agent import run_network_agent
from agents.synthesis_agent import (
    _compute_global_metrics,
    _compute_risk_score_stats,
    _compute_scores,
    run_synthesis_agent,
)
from agents.validation_agent import run_validation_agent


def test_scenario_seed_generation_status_is_supported(scenario_state):
    """S1/S2 are import-dependent material+region disruptions (Strategy A ->
    "rule_matched"). S3 is a facility-specific disruption (Strategy B, entity matching
    -> "entity_matched") — redefined 2026-07-31 to match what was already communicated
    to the advisor (2026-07-20 email): a Facility Disruption scenario instead of a
    port/logistics event, since no port/logistics-node concept exists in the graph.
    All three must report a supported status, never "no_seed_found"."""
    expected = {"S1": "rule_matched", "S2": "rule_matched", "S3": "entity_matched"}
    assert scenario_state["seed_generation_status"] == expected[scenario_state["_scenario_id"]]


def test_unmatched_event_reports_no_seed_found_not_a_silent_zero():
    """Regression test (2026-07-20): a material/region combination that matches nothing
    real must set seed_generation_status="no_seed_found" so this is distinguishable from
    a genuine "0 facilities affected" finding — see SeedGenerationStatus docstring in
    state.py and the "no_seed_found" handling in synthesis_agent.py."""
    state = {
        "affected_material": "unobtainium",
        "affected_region": "Nowhere",
        "origin_tier": "Upstream",
    }
    result = run_network_agent(state)
    assert result["seed_generation_status"] == "no_seed_found"
    assert len(result["affected_nodes"]) == 0


def test_no_seed_found_report_is_deterministic_and_never_contradicts_itself():
    """Regression test (2026-07-20): synthesis_agent used to call the LLM even when
    seed_generation_status="no_seed_found", and the LLM would correctly flag the
    limitation in one section, then contradict itself in "Risk Synthesis" by
    analyzing the placeholder 0% capacity figures as if they were a real finding.
    Now short-circuited to a fixed notice — no LLM call, so it can't self-contradict
    and is byte-for-byte reproducible for the same input."""
    state = {
        "affected_material": "battery cell", "affected_region": "Kansas",
        "origin_tier": "Midstream-Cell", "severity": 4, "risk_type": "supply_disruption",
        "reason": "Fire at a plant halts cell production.",
    }
    state.update(run_network_agent(state))
    assert state["seed_generation_status"] == "no_seed_found"

    result1 = run_synthesis_agent(dict(state))
    result2 = run_synthesis_agent(dict(state))
    assert result1["risk_report"] == result2["risk_report"]  # deterministic, no LLM variance
    assert "System Limitation Notice" in result1["risk_report"]
    assert result1["top3_facilities"] == []
    assert result1["risk_score_stats"] == {"max": 0.0, "median": 0.0}


def test_no_seed_found_report_skips_validation_llm_call():
    """Regression test (2026-07-20): Validation Agent used to run its LLM check against
    the no_seed_found notice too, judging it by normal-report standards (e.g. flagging
    "missing Top-3 justification") and marking it invalid — which would trigger a wasted
    retry loop back to Risk Assessment Agent that could only ever reproduce the same
    no_seed_found result. Must now short-circuit to valid=True with zero issues."""
    state = {
        "affected_material": "battery cell", "affected_region": "Kansas",
        "origin_tier": "Midstream-Cell", "severity": 4, "risk_type": "supply_disruption",
        "reason": "Fire at a plant halts cell production.",
    }
    state.update(run_network_agent(state))
    state.update(run_synthesis_agent(state))
    state.update(run_validation_agent(state))
    assert state["valid"] is True
    assert state["failure_type"] is None
    assert state["issues"] == []


def test_scenario_produces_affected_facilities(scenario_state):
    assert len(scenario_state["affected_nodes"]) > 0, (
        f"{scenario_state['_scenario_id']}: no facilities matched — check material/region config"
    )


def test_scenario_top3_are_distinct_sites(scenario_state):
    """Regression test for the Targray triple-duplicate bug (2026-07-20): Top 3 must be
    3 different physical sites (company+city+state), not the same site's multiple
    material-product-line rows (NAATBatt records one row per material per company/site)."""
    _, top3 = _compute_scores(scenario_state)
    sites = [(f["company"], f["city"], f["state"]) for f in top3]
    assert len(sites) == len(set(sites)), (
        f"{scenario_state['_scenario_id']}: duplicate site in Top 3: {sites}"
    )


def test_scenario_top3_facilities_are_in_affected_nodes(scenario_state):
    """Every Top-3 facility must be NAATBatt-verifiable (present in affected_nodes) —
    also checked live by the Validation Agent's deterministic rule 1."""
    _, top3 = _compute_scores(scenario_state)
    affected_ids = {n["id"] for n in scenario_state["affected_nodes"]}
    for f in top3:
        assert f["id"] in affected_ids


def test_scenario_risk_scores_normalized_range(scenario_state):
    """RiskScore_normalized must stay within the theoretical 0-100 range
    (max = Severity(5) x TierWeight(1.0) x Vulnerability(1.0) x 1, see state.py)."""
    risk_scores, _ = _compute_scores(scenario_state)
    for nid, score in risk_scores.items():
        assert 0 <= score <= 100, f"{scenario_state['_scenario_id']}: {nid} score {score} out of range"


def test_scenario_tier_weight_matches_distance_table(scenario_state):
    """TierWeight must be one of the 4 documented distance-based values (risk_model.md)."""
    valid_weights = {1.0, 0.6, 0.35, 0.15}
    for tw in scenario_state["tier_weights"].values():
        assert tw in valid_weights


def test_scenario_capacity_percentages_within_bounds(scenario_state):
    gm = _compute_global_metrics(scenario_state)
    assert 0 <= gm["betroffene_kapazitaet_na_pct"] <= 100
    assert 0 <= gm["alternative_kapazitaet_na_pct"] <= 100
    # float tolerance
    assert gm["betroffene_kapazitaet_na_pct"] + gm["alternative_kapazitaet_na_pct"] <= 100.01


def test_scenario_no_upstream_facility_directly_affected(scenario_state):
    """Structural property of the current model: import/region-driven events can never
    seed-match an Upstream node, because import_dependency is always False for Upstream
    (it's the production site, not an importer — see build_facilities.py). Documented via
    a test so a future change to this logic surfaces here explicitly rather than silently."""
    upstream_affected = [n for n in scenario_state["affected_nodes"] if n["segment"] == "Upstream"]
    assert len(upstream_affected) == 0, (
        f"{scenario_state['_scenario_id']}: found Upstream facilities in affected_nodes — "
        f"if intentional, update this test's assumption and risk_model.md's documented limitation."
    )


def test_scenario_no_primary_exposure_facility_is_downstream_of_another(scenario_state):
    """Regression test (2026-07-20, root-seed fix): a seed is defined structurally — any
    segment can seed, but only if no OTHER material+region candidate can already reach
    it via the graph (_find_root_seeds). So no facility with 'Primary exposure'
    (path length 1, i.e. it IS a seed) should ever appear inside another Primary
    facility's supply_path — that would mean it was double-counted as an independent
    seed despite being reachable from another seed."""
    supply_chain_paths = scenario_state["supply_chain_paths"]
    primary_ids = {
        n["id"] for n in scenario_state["affected_nodes"]
        if len(supply_chain_paths.get(n["id"], [n["id"]])) == 1
    }
    for pid in primary_ids:
        for other_id in primary_ids:
            if other_id == pid:
                continue
            other_path = supply_chain_paths.get(other_id, [other_id])
            assert pid not in other_path, (
                f"{scenario_state['_scenario_id']}: seed {pid} appears in seed "
                f"{other_id}'s path {other_path} — should have been demoted to propagated"
            )


def test_risk_score_stats_reflect_actual_distribution(scenario_state):
    """_compute_risk_score_stats() replaced compute_risk_tiers() (2026-08-03) — the old
    function only ever read len(risk_scores), never the actual score values, so its
    "High/Medium/Low" output was a fixed ~20/30/50 split of facility COUNT for every
    event, regardless of how risky it actually was (found while investigating why S1's
    Top-3 facilities all showed identical scores). max/median must be real, order-
    consistent statistics of the actual RiskScore values, not a count-derived proxy."""
    risk_scores, _ = _compute_scores(scenario_state)
    stats = _compute_risk_score_stats(risk_scores)
    if not risk_scores:
        assert stats == {"max": 0.0, "median": 0.0}
        return
    values = sorted(risk_scores.values())
    assert stats["max"] == round(values[-1], 2)
    assert 0.0 <= stats["median"] <= stats["max"]
    # median must track the actual middle of the sorted values, not a fixed proportion
    # of the count — changing a single score must be able to move it.
    mutated = dict(risk_scores)
    some_id = next(iter(mutated))
    mutated[some_id] = stats["max"] + 10.0
    mutated_stats = _compute_risk_score_stats(mutated)
    assert mutated_stats["max"] == round(stats["max"] + 10.0, 2)
