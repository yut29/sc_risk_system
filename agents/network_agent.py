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

import difflib
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
    # Guard: an empty material string is a substring of everything in Python
    # ("" in "anything" is True), so without this an empty/missing material would
    # match every node — turning "no material identified" into "match all materials".
    if not material:
        return False
    kws = str(node_attrs.get("material_keywords", "")).lower()
    return material.lower() in kws


def _region_match(node_attrs: dict[str, Any], region: str) -> bool:
    """
    True if the node's import_origin_region overlaps with the event region.
    Only meaningful for nodes where import_dependency=True.
    """
    if not region or not node_attrs.get("import_dependency", False):
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


def _bfs_with_predecessors(G: nx.DiGraph, start_ids: list[str]) -> tuple[set[str], dict[str, str]]:
    """
    Same traversal as _bfs_descendants, but also records which node first
    discovered each newly-visited node (predecessor), so a path back to a
    seed can be reconstructed later.
    """
    visited: set[str] = set(start_ids)
    predecessor: dict[str, str] = {}
    queue = deque(start_ids)
    while queue:
        cur = queue.popleft()
        for nxt in G.successors(cur):
            if nxt not in visited:
                visited.add(nxt)
                predecessor[nxt] = cur
                queue.append(nxt)
    return visited, predecessor


def _reconstruct_path(predecessor: dict[str, str], node_id: str) -> list[str]:
    """Walk backward from node_id to its seed via the predecessor map."""
    path = [node_id]
    cur = node_id
    while cur in predecessor:
        cur = predecessor[cur]
        path.append(cur)
    path.reverse()
    return path


def _find_root_seeds(G: nx.DiGraph, candidates: list[str]) -> list[str]:
    """
    A candidate (material+region match) is only a genuine seed — a propagation
    ROOT — if no OTHER candidate can reach it via the graph. If it's reachable from
    another candidate, it's not an independent entry point; it's downstream of one,
    so it should be scored as propagated exposure, not counted as a second seed for
    the same underlying event. This replaces a hard segment restriction (e.g.
    "only Midstream-BGM can seed") with a structural one that works for any segment:
    a seed is defined by what it *is* (a root of the candidate set), not by which
    tier it happens to sit in.
    """
    candidate_set = set(candidates)
    reached_by_other: set[str] = set()
    for c in candidates:
        visited: set[str] = set()
        queue = deque(G.successors(c))
        while queue:
            cur = queue.popleft()
            if cur in visited:
                continue
            visited.add(cur)
            if cur in candidate_set and cur != c:
                reached_by_other.add(cur)
            queue.extend(G.successors(cur))
    return [c for c in candidates if c not in reached_by_other]


def _entity_match_seeds(
    all_nodes: dict[str, dict],
    mentioned_company: Optional[str],
    mentioned_location: Optional[str],
) -> tuple[list[str], Optional[str]]:
    """
    Strategy B: facility-specific disruption seeding (e.g. "Fire at Panasonic Kansas
    plant"). Fuzzy-matches mentioned_company against node `company` fields — a
    facility-specific event doesn't need import_dependency/region at all, it's a
    direct hit on a named entity.

    Scoped as "Version 1" (see architecture.md "Vorschlag: Multi-Strategie Seed
    Generator" and the todo-strategy-b-entity-matching memory): no alias/synonym table
    (e.g. "GM" -> "Ultium Cells LLC") — that's unbounded maintenance work and a
    distraction from the supply-chain-risk-modeling core of the project. If a company
    has multiple facilities (common in this dataset — e.g. Vale Canada has 10), the
    mentioned_location must narrow it down to exactly one; if it can't, this deliberately
    does NOT guess — returns ("entity_ambiguous", []) instead of picking a candidate.

    Returns (seed_ids, status) where status is "entity_matched" | "entity_ambiguous" |
    "entity_non_material" | None (None = mentioned_company was empty, Strategy B simply
    didn't apply).
    """
    if not mentioned_company:
        return [], None

    def _all_non_material(nids: list[str]) -> bool:
        # non_active_material marks mechanical/safety/BMS component suppliers (2026-07-31,
        # docs/open_issues.md P16) — this system's material-flow risk model doesn't apply
        # to them, so they must not be treated as a real supply-chain risk seed.
        return all(all_nodes[nid].get("material_keywords") == "non_active_material" for nid in nids)

    company_names = sorted({
        attrs.get("company", "") for attrs in all_nodes.values() if attrs.get("company")
    })

    matches = difflib.get_close_matches(mentioned_company, company_names, n=1, cutoff=0.6)
    if not matches:
        # Fuzzy ratio can miss short/abbreviated names ("GM" vs "General Motors Company") —
        # fall back to a substring check, but only accept it if it's unambiguous.
        mc_lower = mentioned_company.lower()
        substring_hits = [
            c for c in company_names if mc_lower in c.lower() or c.lower() in mc_lower
        ]
        if len(substring_hits) == 1:
            matches = substring_hits
        else:
            return [], "entity_ambiguous"

    matched_company = matches[0]
    candidates = [nid for nid, attrs in all_nodes.items() if attrs.get("company") == matched_company]

    def _site_key(nid: str) -> tuple:
        a = all_nodes[nid]
        return (a.get("company"), a.get("city"), a.get("state"))

    # NAATBatt records one row per company/material-product-line, not per physical
    # site (same pattern as the Top-3 duplicate bug, synthesis_agent.py) — multiple
    # candidate rows at the SAME (company, city, state) are one physical site, not an
    # ambiguity to resolve.
    if len({_site_key(nid) for nid in candidates}) == 1:
        if _all_non_material(candidates):
            return [], "entity_non_material"
        return candidates, "entity_matched"

    if mentioned_location:
        loc_lower = mentioned_location.lower()
        narrowed = [
            nid for nid in candidates
            if loc_lower in str(all_nodes[nid].get("city", "")).lower()
            or loc_lower in str(all_nodes[nid].get("state", "")).lower()
        ]
        if narrowed and len({_site_key(nid) for nid in narrowed}) == 1:
            if _all_non_material(narrowed):
                return [], "entity_non_material"
            return narrowed, "entity_matched"

    # Multiple distinct physical sites under this company, location missing or
    # didn't narrow to exactly one — do not guess.
    return [], "entity_ambiguous"


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
    # A seed is defined structurally, not by tier: any node matching material+region
    # (a "candidate") is a genuine seed only if no OTHER candidate can already reach it
    # via the graph (_find_root_seeds). This lets any segment seed in principle — no
    # hardcoded "must be Midstream-BGM" — while still preventing double-counting when
    # e.g. a Cell facility that also matches material+region is already downstream of
    # a Midstream-BGM candidate for the same event.
    #
    # SCOPE: this whole match (material+region+import_dependency) only models ONE event
    # type — an import-dependent raw-material disruption abroad ("regional_supply").
    # It does NOT handle a named-facility disruption (needs entity extraction, no such
    # mechanism exists) or a tier-wide domestic disruption without a foreign region
    # (needs a different seed strategy entirely, since _region_match() requires
    # import_dependency+region that a domestic event wouldn't have).
    # See docs/architecture.md "Bekannte Grenzen des Seeding-Mechanismus" for the full writeup.
    candidate_ids: list[str] = [
        nid for nid, attrs in all_nodes.items()
        if _material_match(attrs, material) and _region_match(attrs, region)
    ]
    rule_seed_ids: list[str] = _find_root_seeds(G, candidate_ids)

    # Strategy B: facility-specific disruption (e.g. "Fire at Panasonic Kansas plant").
    # Independent of Strategy A — a news item can name a specific company instead of
    # (or in addition to) matching the material+region import-dependency pattern.
    entity_seed_ids, entity_status = _entity_match_seeds(
        all_nodes,
        state.get("mentioned_company"),
        state.get("mentioned_location"),
    )

    seed_ids: list[str] = list(set(rule_seed_ids) | set(entity_seed_ids))

    # "no_seed_found" must NOT be read as "no NA exposure" — see SeedGenerationStatus
    # docstring in state.py. Only Strategy A (rule matching) and Strategy B (entity
    # matching) exist so far, so this fires for any event type this system doesn't yet
    # model (tier-wide domestic, logistics/port — see architecture.md).
    if entity_status == "entity_matched":
        seed_generation_status: str = "entity_matched"
    elif rule_seed_ids:
        seed_generation_status = "rule_matched"
    elif entity_status == "entity_non_material":
        seed_generation_status = "entity_non_material"
    elif entity_status == "entity_ambiguous":
        seed_generation_status = "entity_ambiguous"
    else:
        seed_generation_status = "no_seed_found"

    # ── Step 2: BFS expansion — all nodes reachable from seeds ───────────────
    reachable, predecessor = _bfs_with_predecessors(G, seed_ids)

    # ── Step 3: affected_nodes — all reachable from seeds ────────────────────
    # MaterialMatch only required for seeds; downstream nodes are included
    # regardless of their own keywords (they're affected via supply chain links).
    # Downstream nodes rarely carry raw-material keywords in NAATBatt.
    affected_ids: set[str] = reachable

    # Path from seed to each affected node (for display: "why is this facility affected").
    # If a node is reachable via multiple seeds/paths, this is just one such path,
    # not an exhaustive list of all possible routes.
    supply_chain_paths: dict[str, list[str]] = {
        nid: _reconstruct_path(predecessor, nid) for nid in affected_ids
    }

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
            cell_supplier_count=G.in_degree(nid),
        )

    affected_nodes = [to_node(nid) for nid in sorted(affected_ids)]
    alt_nodes      = [to_node(nid) for nid in sorted(alt_ids)]

    return {
        **state,
        "affected_nodes":         affected_nodes,
        "alt_nodes":              alt_nodes,
        "tier_weights":           tier_weights,
        "downstream_fanout":      fanout,
        "supply_chain_paths":     supply_chain_paths,
        "total_network_facilities": len(all_nodes),  # all facilities, any material — dataset-wide constant
        "seed_generation_status": seed_generation_status,
    }
