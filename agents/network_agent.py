"""
Network Agent — Deterministic graph traversal

Input (from PipelineState):
  affected_material : str      e.g. "cobalt"
  affected_region   : str      e.g. "Africa/DRC"
  origin_tier       : Segment  e.g. "Upstream"

Output (written to PipelineState):
  affected_nodes    : list[Node]
  alt_nodes         : list[Node]
  tier_weights      : dict[str, float]
  downstream_fanout : dict[str, int]
"""

import json
from collections import deque
from pathlib import Path
from typing import Any, Optional

import networkx as nx

from agents.state import (
    Node,
    PipelineState,
    Segment,
    compute_tier_weight,
)

GRAPH_FILE = Path(__file__).parent.parent / "data" / "knowledge_graph.json"

# Region aliases: event region string → substrings that count as a match
# Allows "Africa/DRC" to match nodes with "Africa (DRC)" or "Africa/DRC"
REGION_ALIASES: dict[str, list[str]] = {
    "africa/drc":            ["africa", "drc", "congo"],
    "south america":         ["south america", "chile", "argentina", "bolivia"],
    "south america / australia": ["south america", "chile", "argentina", "bolivia", "australia"],
    "asia (china)":          ["asia", "china"],
    "asia / pacific":        ["asia", "pacific", "indonesia", "philippines"],
    "australia":             ["australia"],
}


# ── Graph singleton (loaded once per process) ────────────────────────────────

_graph_cache: Optional[nx.DiGraph] = None


def _load_graph() -> nx.DiGraph:
    global _graph_cache
    if _graph_cache is not None:
        return _graph_cache

    with open(GRAPH_FILE, encoding="utf-8") as f:
        data = json.load(f)

    G = nx.DiGraph()
    for node in data["nodes"]:
        nid = node["id"]
        G.add_node(nid, **{k: v for k, v in node.items() if k != "id"})
    for edge in data["edges"]:
        G.add_edge(edge["source"], edge["target"],
                   relationship=edge.get("relationship"),
                   material=edge.get("material"))

    _graph_cache = G
    return G


# ── Match helpers ─────────────────────────────────────────────────────────────

def _material_match(node_attrs: dict[str, Any], material: str) -> bool:
    kws = str(node_attrs.get("material_keywords", "")).lower()
    return material.lower() in kws


def _region_match(node_attrs: dict[str, Any], region: str) -> bool:
    """
    True if the node's import_origin_region overlaps with the event region.
    Only meaningful for nodes where import_dependency=True.
    """
    if not node_attrs.get("import_dependency", False):
        return False
    origin = str(node_attrs.get("import_origin_region", "")).lower()
    region_lower = region.lower()

    # Direct substring match
    if region_lower in origin or origin in region_lower:
        return True

    # Alias-based match
    for alias_key, substrings in REGION_ALIASES.items():
        if alias_key in region_lower or region_lower in alias_key:
            if any(s in origin for s in substrings):
                return True

    return False


# ── Core traversal ────────────────────────────────────────────────────────────

def _bfs_descendants(G: nx.DiGraph, start_ids: list[str]) -> set[str]:
    """All nodes reachable from start_ids via directed edges."""
    visited: set[str] = set(start_ids)
    queue = deque(start_ids)
    while queue:
        cur = queue.popleft()
        for nxt in G.successors(cur):
            if nxt not in visited:
                visited.add(nxt)
                queue.append(nxt)
    return visited


def _downstream_fanout(G: nx.DiGraph, node_id: str) -> int:
    """Number of Downstream-segment nodes reachable from node_id."""
    reachable = _bfs_descendants(G, [node_id])
    return sum(
        1 for nid in reachable
        if nid != node_id and G.nodes[nid].get("segment") == "Downstream"
    )


# ── Main agent function ───────────────────────────────────────────────────────

def run_network_agent(state: PipelineState) -> PipelineState:
    """
    LangGraph-compatible node function.
    Reads affected_material / affected_region / origin_tier from state,
    returns updated state with affected_nodes, alt_nodes, tier_weights, downstream_fanout.
    """
    material: str = state.get("affected_material") or state.get("material", "")
    region: str   = state.get("affected_region")   or state.get("region", "")
    origin_tier: Segment = state.get("origin_tier", "Upstream")

    G = _load_graph()
    all_nodes: dict[str, dict] = dict(G.nodes(data=True))

    # ── Step 1: seed nodes — direct material + region match ──────────────────
    seed_ids: list[str] = [
        nid for nid, attrs in all_nodes.items()
        if _material_match(attrs, material) and _region_match(attrs, region)
    ]

    # ── Step 2: BFS expansion — all nodes reachable from seeds ───────────────
    reachable: set[str] = _bfs_descendants(G, seed_ids)

    # ── Step 3: affected_nodes — all reachable from seeds ────────────────────
    # MaterialMatch only required for seeds; downstream nodes are included
    # regardless of their own keywords (they're affected via supply chain links).
    # Downstream nodes rarely carry raw-material keywords in NAATBatt.
    affected_ids: set[str] = reachable

    # ── Step 4: alt_nodes — same material, NOT affected ──────────────────────
    alt_ids: set[str] = {
        nid for nid, attrs in all_nodes.items()
        if _material_match(attrs, material) and nid not in affected_ids
    }

    # ── Step 5: tier_weights for affected + alt nodes ─────────────────────────
    tier_weights: dict[str, float] = {}
    for nid in affected_ids | alt_ids:
        seg = all_nodes[nid].get("segment", "Upstream")
        if seg in ("Upstream", "Midstream-BGM", "Midstream-Cell", "Downstream"):
            tier_weights[nid] = compute_tier_weight(seg, origin_tier)

    # ── Step 6: downstream_fanout — only for Upstream nodes (fanout meaningless for others)
    upstream_affected = {
        nid for nid in affected_ids
        if all_nodes[nid].get("segment") == "Upstream"
    }
    fanout: dict[str, int] = {
        nid: _downstream_fanout(G, nid) for nid in upstream_affected
    }

    # ── Build Node dicts ──────────────────────────────────────────────────────
    def to_node(nid: str) -> Node:
        attrs = all_nodes[nid]
        return Node(
            id=nid,
            company=attrs.get("company", ""),
            facility_name=attrs.get("facility_name", ""),
            segment=attrs.get("segment", ""),
            product_type=attrs.get("product_type", ""),
            material_keywords=attrs.get("material_keywords", ""),
            country=attrs.get("country", ""),
            state=attrs.get("state", ""),
            city=attrs.get("city", ""),
            latitude=attrs.get("latitude") or 0.0,
            longitude=attrs.get("longitude") or 0.0,
            production_capacity_raw=attrs.get("production_capacity_raw", "nan"),
            production_units=attrs.get("production_units", ""),
            capacity_source=attrs.get("capacity_source", "unknown"),
            supplier_concentration=bool(attrs.get("supplier_concentration", False)),
            import_dependency=bool(attrs.get("import_dependency", False)),
            import_origin_region=attrs.get("import_origin_region", ""),
            lead_time_weeks=int(attrs.get("lead_time_weeks", 0)),
        )

    affected_nodes = [to_node(nid) for nid in sorted(affected_ids)]
    alt_nodes      = [to_node(nid) for nid in sorted(alt_ids)]

    return {
        **state,
        "affected_nodes":    affected_nodes,
        "alt_nodes":         alt_nodes,
        "tier_weights":      tier_weights,
        "downstream_fanout": fanout,
    }
