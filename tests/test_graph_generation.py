"""
Graph generation (build_graph.py) — structural checks, not just Network Agent behavior.

These test the generated knowledge_graph.json directly, catching bugs in the
graph-building script itself (e.g. a country-name mismatch silently excluding a whole
country's facilities) rather than downstream matching logic.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.network_agent import _load_graph


def test_mexican_downstream_facilities_are_reachable():
    """Regression test (2026-07-31): build_graph.py's NORTH_AMERICA_COUNTRIES was
    {"US", "CA", "Canada", "MX"} — but facilities_clean.csv's country field only ever
    contains the literal values "US"/"Canada"/"Mexico" (no abbreviations). "Mexico" was
    never in that set, so all Mexican Downstream facilities silently had in-degree 0 —
    permanently unreachable from any Cell node, regardless of the event. Fixed to
    {"US", "Canada", "Mexico"} (the actual values used in the data)."""
    G = _load_graph()
    mx_down = [
        nid for nid, attrs in G.nodes(data=True)
        if attrs.get("segment") == "Downstream" and attrs.get("country") == "Mexico"
    ]
    assert mx_down, "no Mexican Downstream facilities found — dataset may have changed shape"
    for nid in mx_down:
        assert G.in_degree(nid) > 0, (
            f"{nid} ({G.nodes[nid].get('company')}) has in-degree 0 — "
            f"unreachable from any Cell node"
        )


def test_verified_real_supply_edges_are_intact():
    """Regression test (2026-07-31): build_graph.py's VERIFIED_REAL_SUPPLY_LINKS
    originally used an edge attribute named "source" for the citation text — but
    graph_to_json()'s edge serialization does {"source": s, "target": t, **attrs}, so
    the citation string silently overwrote the real source-node id on every edge
    (**attrs is applied after "source": s in the dict literal). The edge still counted
    in edge_count, but on reload from knowledge_graph.json it pointed nowhere real.
    Renamed to "citation" to fix. This test loads the actual persisted graph and checks
    each verified link resolves to a real, connected edge."""
    G = _load_graph()
    expected_links = [
        ("Panasonic", "Tesla"),
        ("Panasonic", "Toyota"),
        ("Panasonic", "Lucid Motors"),
        ("Toyota", "Toyota"),  # TBMNC (Liberty, NC) -> Toyota's own Georgetown, KY plant
    ]
    verified_edges = [
        (u, v) for u, v, attrs in G.edges(data=True)
        if attrs.get("relationship") == "verified_real_supply"
    ]
    assert len(verified_edges) == len(expected_links)
    found_pairs = {
        (G.nodes[u].get("company", "").strip(), G.nodes[v].get("company", "").strip())
        for u, v in verified_edges
    }
    for src_company, tgt_company in expected_links:
        assert (src_company, tgt_company) in found_pairs, (
            f"verified edge {src_company} -> {tgt_company} missing or not connected to "
            f"the right nodes after JSON round-trip"
        )
