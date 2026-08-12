"""
Strategy B — facility-specific disruption seeding (2026-07-31).

Tests _entity_match_seeds() via run_network_agent(), using real company names/locations
from the current dataset rather than mocks, so these break loudly if the underlying data
changes shape. Also covers two bugs found while building this: an empty material/region
matching everything, and same-site multi-material-row companies being misjudged as
ambiguous (same root cause as the Top-3 dedup bug in synthesis_agent.py).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.network_agent import _load_graph, run_network_agent
from agents.synthesis_agent import run_synthesis_agent
from agents.validation_agent import run_validation_agent


def test_upstream_to_bgm_edges_respect_degree_cap():
    """Regression test (2026-07-31): Upstream->Midstream-BGM edges had no degree cap
    (pure keyword matching, fully connected), unlike the other two edge layers. Found
    via this exact test scenario: a single Upstream seed (Albemarle Silver Peak) fanned
    out to 30 BGM facilities in one hop, cascading to 220 affected facilities total.
    build_graph.py now applies K_UPSTREAM_BGM=6 (same value/method as K_BGM_CELL)."""
    G = _load_graph()
    upstream_out_degrees = [
        G.out_degree(nid) for nid, attrs in G.nodes(data=True)
        if attrs.get("segment") == "Upstream"
    ]
    assert upstream_out_degrees, "no Upstream nodes found in the graph"
    assert max(upstream_out_degrees) <= 6, (
        f"an Upstream node has {max(upstream_out_degrees)} outgoing edges — "
        f"K_UPSTREAM_BGM cap of 6 is not being respected in the generated graph"
    )


def test_single_seed_fanout_is_bounded_after_degree_cap():
    """The concrete case that surfaced the bug: one precise entity-matched seed must
    not cascade to a large fraction of the whole 386-facility network."""
    result = run_network_agent({
        "mentioned_company": "Albemarle", "mentioned_location": "Silver Peak",
        "origin_tier": "Upstream",
    })
    assert result["seed_generation_status"] == "entity_matched"
    # Was 220/386 (57%) before the K_UPSTREAM_BGM cap; should now be well below that.
    assert len(result["affected_nodes"]) < 150


def test_single_facility_company_resolves_without_location():
    """A company with exactly one facility should seed directly, no location needed."""
    result = run_network_agent({
        "mentioned_company": "Ionic Mineral Technologies",
        "mentioned_location": None,
        "origin_tier": "Upstream",
    })
    assert result["seed_generation_status"] == "entity_matched"
    assert len(result["affected_nodes"]) > 0


def test_multi_site_company_with_location_resolves_uniquely():
    """Albemarle has facilities at Silver Peak NV and Kings Mountain NC — genuinely
    distinct sites. Naming the location must resolve to exactly one."""
    result = run_network_agent({
        "mentioned_company": "Albemarle",
        "mentioned_location": "Silver Peak",
        "origin_tier": "Upstream",
    })
    assert result["seed_generation_status"] == "entity_matched"


def test_multi_site_company_without_location_is_ambiguous():
    """Same Albemarle case, but with no location given — must NOT guess which site."""
    result = run_network_agent({
        "mentioned_company": "Albemarle",
        "mentioned_location": None,
        "origin_tier": "Upstream",
    })
    assert result["seed_generation_status"] == "entity_ambiguous"
    assert len(result["affected_nodes"]) == 0


def test_same_site_multiple_material_rows_is_not_ambiguous():
    """Regression test: Ionic Mineral Technologies has 2 rows in the dataset, but both
    at the same (company, city, state) — one physical site, multiple material-line
    rows (same NAATBatt pattern as the Top-3 dedup bug). Must resolve, not flag
    ambiguous, even without a location hint."""
    result = run_network_agent({
        "mentioned_company": "Ionic Mineral Technologies",
        "mentioned_location": None,
        "origin_tier": "Upstream",
    })
    assert result["seed_generation_status"] == "entity_matched"


def test_unrecognized_company_name_is_ambiguous_not_silent():
    result = run_network_agent({
        "mentioned_company": "TotallyFictionalCompanyXYZ",
        "origin_tier": "Upstream",
    })
    assert result["seed_generation_status"] == "entity_ambiguous"
    assert len(result["affected_nodes"]) == 0


def test_no_material_no_region_no_company_is_no_seed_found():
    """Nothing to go on at all -> no_seed_found, not entity_ambiguous (mentioned_company
    was never set, so Strategy B shouldn't even claim to have tried)."""
    result = run_network_agent({"origin_tier": "Upstream"})
    assert result["seed_generation_status"] == "no_seed_found"


def test_empty_material_and_region_do_not_match_everything():
    """Regression test (2026-07-31): _material_match/_region_match used "x in y" checks
    that treat an empty string as a substring of everything, so an empty/missing
    material or region used to match every node in the graph instead of none."""
    result = run_network_agent({
        "material": "", "region": "", "origin_tier": "Upstream",
    })
    assert result["seed_generation_status"] == "no_seed_found"
    assert len(result["affected_nodes"]) == 0


def test_entity_ambiguous_report_is_deterministic_and_mentions_company():
    state = {
        "mentioned_company": "Albemarle", "mentioned_location": None,
        "severity": 3, "risk_type": "supply_disruption",
        "reason": "Strike at an Albemarle facility.",
    }
    state.update(run_network_agent(state))
    result1 = run_synthesis_agent(dict(state))
    result2 = run_synthesis_agent(dict(state))
    assert result1["risk_report"] == result2["risk_report"]
    assert "Albemarle" in result1["risk_report"]
    assert "Ambiguous Facility Reference" in result1["risk_report"]
    assert result1["top3_facilities"] == []


def test_entity_ambiguous_skips_validation_llm_call():
    """Same rationale as the no_seed_found validation short-circuit: judging a fixed
    notice by normal-report standards would produce spurious issues and a wasted retry."""
    state = {
        "mentioned_company": "Albemarle", "mentioned_location": None,
        "severity": 3, "risk_type": "supply_disruption",
        "reason": "Strike at an Albemarle facility.",
    }
    state.update(run_network_agent(state))
    state.update(run_synthesis_agent(state))
    state.update(run_validation_agent(state))
    assert state["valid"] is True
    assert state["failure_type"] is None
    assert state["issues"] == []


def test_non_material_component_supplier_does_not_seed_a_material_flow():
    """Regression test (2026-07-31, docs/open_issues.md P16): even after fixing
    build_graph.py's BGM->Cell wildcard-match bug, Strategy B entity matching still
    resolved component suppliers like ArlanXEO as valid seeds (it only checks company/
    location, never material relevance) and computed a bogus downstream propagation for
    a company that doesn't process any risk material. ArlanXEO is now tagged
    non_active_material and must short-circuit to entity_non_material with zero
    affected nodes, not entity_matched."""
    result = run_network_agent({
        "mentioned_company": "ArlanXEO", "mentioned_location": None,
        "origin_tier": "Midstream-Cell",
    })
    assert result["seed_generation_status"] == "entity_non_material"
    assert len(result["affected_nodes"]) == 0


def test_entity_non_material_report_is_deterministic_and_mentions_company():
    state = {
        "mentioned_company": "ArlanXEO", "mentioned_location": None,
        "severity": 3, "risk_type": "supply_disruption",
        "reason": "Fire at an ArlanXEO facility.",
    }
    state.update(run_network_agent(state))
    result1 = run_synthesis_agent(dict(state))
    result2 = run_synthesis_agent(dict(state))
    assert result1["risk_report"] == result2["risk_report"]
    assert "ArlanXEO" in result1["risk_report"]
    assert "Non-Material Facility" in result1["risk_report"]
    assert result1["top3_facilities"] == []


def test_facility_disruption_without_material_does_not_inflate_capacity_denominator():
    """Regression test (2026-07-31): a facility-specific disruption (Strategy B) has no
    material — data_retrieval_agent.py's material_rows filter used
    `.str.contains(material, na=False)`, and pandas treats an empty string as a substring
    of every non-null value, so an empty material matched all 386 facilities instead of
    none. This inflated the CapacityShare/capacity-percentage denominators with
    unrelated materials rather than correctly yielding "not applicable" (0). Same bug
    class as _material_match()'s guard in network_agent.py."""
    from agents.data_retrieval_agent import run_data_retrieval_agent

    state = {
        "mentioned_company": "Ionic Mineral Technologies", "mentioned_location": None,
        "origin_tier": "Upstream",
    }
    state.update(run_network_agent(state))
    assert state["seed_generation_status"] == "entity_matched"
    state.update(run_data_retrieval_agent(state))
    # capacity_share itself now defaults to 1.0 (max/worst-case), not 0.0, whenever it's
    # inapplicable/unknown (2026-08-03, explicit instruction) — this test's actual concern,
    # the GLOBAL percentage denominators, is unaffected by that and still checked below.
    for fd in state["facility_data"].values():
        assert fd["capacity_share"] == 1.0
    assert state["betroffene_kapazitaet_pct"] == 0.0
    assert state["alternative_kapazitaet_pct"] == 0.0


def test_entity_non_material_skips_validation_llm_call():
    state = {
        "mentioned_company": "ArlanXEO", "mentioned_location": None,
        "severity": 3, "risk_type": "supply_disruption",
        "reason": "Fire at an ArlanXEO facility.",
    }
    state.update(run_network_agent(state))
    state.update(run_synthesis_agent(state))
    state.update(run_validation_agent(state))
    assert state["valid"] is True
    assert state["failure_type"] is None
    assert state["issues"] == []
