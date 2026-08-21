"""
Network Agent — Graph traversal (Strategy A/B deterministic, Strategy C LLM-assisted fallback)

Input (from PipelineState):
  material/region   : str      from Intake Agent — the single, canonical field for both
                                (2026-08-07: removed the parallel affected_material/
                                affected_region field that risk_assessment_agent.py used to
                                produce — see its module docstring for why)
  origin_tier       : Segment  e.g. "Upstream"

Output (written to PipelineState):
  affected_nodes    : list[Node]
  alt_nodes         : list[Node]
  tier_weights      : dict[str, float]
  downstream_fanout : dict[str, int]
  strategy_c_trace  : StrategyCTrace | None  # full candidate list + LLM reasoning when
                                              # Strategy C ran (2026-08-07, P22)

Strategy A (rule match) and Strategy B (entity match) are pure deterministic graph/string
logic, no LLM. Strategy C (2026-08-06, implements the design sketched in
docs/architecture.md "Vorschlag: Multi-Strategie Seed Generator") is the first LLM call in
this agent — it only runs as a fallback when A and B both find nothing AND the event has a
region, and it can only ever select from a closed, pre-retrieved candidate list (never
invents a facility) — see _llm_assisted_seed_fallback().
"""

import difflib
import json
from collections import deque
from pathlib import Path
from typing import Any, Optional

import networkx as nx
from langchain_core.messages import HumanMessage, SystemMessage

from agents.llm_utils import get_llm, invoke_json
from agents.state import (
    Node,
    PipelineState,
    Segment,
    StrategyCTrace,
    compute_tier_weight,
)
from agents.tools import find_facilities_by_region

GRAPH_FILE = Path(__file__).parent.parent / "data" / "knowledge_graph.json"

# Region aliases: event region string → substrings that count as a match
# Allows "Africa/DRC" to match nodes with "Africa (DRC)" or "Africa/DRC"
REGION_ALIASES: dict[str, list[str]] = {
    # "africa" deliberately excluded (2026-08-06): it's a substring of "South Africa" — a
    # real country name now appearing in manganese's import_origin_region — so keeping it
    # here made a DRC-specific event match any African-origin facility regardless of
    # country. Only found once import_origin_region became per-material (see
    # infer_import_origin() in build_facilities.py): previously manganese's origin was
    # never actually populated for a multi-material facility, so this collision was never
    # reachable. "drc"/"congo" alone are DRC-specific and don't have this problem.
    "africa/drc":            ["drc", "congo"],
    # "south america" kept generic-only (2026-08-12, docs/open_issues.md P46) — a region
    # mention of just "South America" with no specific country still falls through to this
    # broad alias, but "chile" and "argentina" got split into their OWN single-country
    # entries below (mirroring "australia"). Previously all three substrings lived in one
    # list, which caused the exact same bug class as the removed "south america / australia"
    # group just below: found via testing region="Chile" vs region="Argentina" for
    # material=lithium (after splitting IMPORT_ORIGIN_DISTRIBUTION["lithium"] into named
    # countries) — both returned the identical 164 facilities, because a Chile-region event
    # and an Argentina-origin facility both matched the same shared list even though they
    # name different countries. The alias-match logic (see below) only requires region and
    # origin to share ANY substring from the SAME group, not the SAME substring — so any
    # group holding 2+ distinct real country names is unsafe.
    "south america":         ["south america"],
    "chile":                 ["chile"],
    "argentina":             ["argentina"],
    "asia (china)":          ["asia", "china"],
    # "asia / pacific" kept generic-only for the same reason (2026-08-12) — "indonesia" and
    # "philippines" pulled out into their own entries, since nickel's IMPORT_ORIGIN_DISTRIBUTION
    # names both as separate real countries (Indonesia 67%, Philippines 18%) and would have
    # had the identical cross-matching bug the moment both were tested against each other.
    "asia / pacific":        ["asia", "pacific"],
    "indonesia":             ["indonesia"],
    "philippines":           ["philippines"],
    "australia":             ["australia"],
    # "south america / australia" removed (2026-08-12): a leftover combined alias group that
    # merged both regions' substrings into one list. No real IMPORT_ORIGIN_DISTRIBUTION bucket
    # is actually written as a combined "South America / Australia" string (lithium and
    # manganese both keep them as two separate buckets) — this entry had no data to correspond
    # to, and only caused a real cross-region bug: found via testing region="Chile" vs
    # region="Australia" for material=lithium, both returned the EXACT SAME 219 facilities
    # (not just the same count — the identical facility IDs), because both country names sat in
    # this one combined group and the alias-match logic (see below) only requires region and
    # origin to share ANY substring from the SAME group. Chile-specific and Australia-specific
    # events are genuinely different real sources and must not match each other's facilities.
    # The standalone "south america" and "australia" entries above already correctly cover
    # each real case independently — nothing depended on the combined group.
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
    # Exact-token match, not substring (2026-08-11 fix): material_keywords is stored as a
    # comma-joined string (see build_facilities.py: ",".join(x)), so a plain `material in kws`
    # substring check matches unrelated tokens that happen to contain `material` as a prefix —
    # found via "copper": material="copper" matched every "copper_foil" facility (6 of them,
    # 100% of that "match"), even though copper_foil is a distinct, separately-tracked material
    # (a processed current collector, not raw copper) and zero facilities in this dataset
    # actually carry a standalone "copper" keyword. Splitting on comma and requiring an exact
    # token match fixes this without affecting any other material — "copper"/"copper_foil" is
    # the only prefix-collision pair among the 13-word vocabulary.
    kws = str(node_attrs.get("material_keywords", "")).lower()
    tokens = [t.strip() for t in kws.split(",")]
    return material.lower() in tokens


def _origin_for_material(node_attrs: dict[str, Any], material: str) -> str:
    """
    This node's known origin for `material` specifically, or "" if unknown (either the node
    doesn't handle this material, or it does but no origin was ever modeled for it — see
    _region_match()'s docstring on why those two cases are handled differently by callers).
    """
    if not material:
        return ""
    try:
        origins: dict[str, str] = json.loads(node_attrs.get("import_origin_region", "{}"))
    except (json.JSONDecodeError, TypeError):
        origins = {}
    return str(origins.get(material.lower(), "")).lower()


def _region_match(node_attrs: dict[str, Any], region: str, material: str) -> bool:
    """
    True if the node's origin FOR THIS SPECIFIC MATERIAL overlaps with the event region.

    import_origin_region is stored per-material (2026-08-06 fix, see
    infer_import_origin()'s docstring in build_facilities.py) — a multi-material facility
    (e.g. an NMC cathode plant needing cobalt+nickel+manganese) genuinely sources each
    material from a different real country, so matching must look up the origin for the
    event's actual material, not a single facility-wide string that only ever reflected
    whichever material happened to be listed first.

    import_dependency gate removed (2026-08-06): it used to be checked here directly, but
    the `if not origin` guard below already excludes every case import_dependency=False
    covered (those facilities' import_origin_region is always {} — see build_facilities.py),
    making the extra check redundant. Removing it lets Upstream facilities match on their own
    location too (Upstream's import_origin_region holds its own state per material, "I
    source from myself" — see conversation 2026-08-06), which is what makes them reachable at
    all for a regional event that doesn't name a company (P19 in docs/open_issues.md).

    NOTE (2026-08-07): an empty origin here means one of two different things — "this
    material has never been assigned any origin data at all" (e.g. pvdf/cnt/silicon, P26 —
    verified 100% empty, not a real fact) vs. "this specific facility's known origin for a
    well-modeled material happens to be elsewhere" (the normal, grounded case). This function
    can't tell the two apart on its own — the caller (run_network_agent) checks
    material_has_known_origins across ALL matching facilities first, and only falls back to
    calling this function when the material genuinely has real origin data to check against.
    """
    if not region or not material:
        return False
    origin = _origin_for_material(node_attrs, material)
    if not origin:
        return False
    region_lower = region.lower()

    # Direct substring match
    if region_lower in origin or origin in region_lower:
        return True

    # Alias-based match (2026-08-08 fix): match region and origin each independently
    # against the alias group's own substrings list, not against the dict KEY string.
    # The old gate (`alias_key in region_lower or region_lower in alias_key`) required
    # the event's region to literally contain/be-contained-by the key itself, e.g.
    # "africa/drc" — so it only fired for region values like "DRC" or "Africa/DRC".
    # Real news text overwhelmingly writes "DR Congo" (a very common AFP/Reuters
    # abbreviation) or "the Democratic Republic of Congo" (missing the second "the"),
    # neither of which is a substring of the literal key "africa/drc", even though both
    # obviously mean the same place as the "congo"/"drc" substrings already in the list
    # below. Checking region against the substrings list directly (same list already
    # used for origin) fixes this without reintroducing the P24 "africa" over-match bug
    # — "africa" was already removed from every substrings list, not from the key names.
    for substrings in REGION_ALIASES.values():
        if any(s in region_lower for s in substrings) and any(s in origin for s in substrings):
            return True

    return False


# ── Core traversal ────────────────────────────────────────────────────────────

def _bfs_with_predecessors(G: nx.DiGraph, start_ids: list[str]) -> tuple[set[str], dict[str, str]]:
    """
    All nodes reachable from start_ids via directed edges, plus a predecessor map
    recording which node first discovered each newly-visited node — so a path back
    to a seed can be reconstructed later (see _reconstruct_path below).
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


def _bfs_descendants(G: nx.DiGraph, start_ids: list[str]) -> set[str]:
    """All nodes reachable from start_ids via directed edges (predecessors discarded —
    consolidated onto _bfs_with_predecessors 2026-08-17, was a separate copy of the same
    traversal loop)."""
    visited, _ = _bfs_with_predecessors(G, start_ids)
    return visited


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
    # Reuses _bfs_descendants (consolidated 2026-08-17, was its own third copy of the same
    # traversal loop) — starting the frontier at c's successors rather than c itself, since
    # c reaching itself isn't the question here, only whether c reaches an OTHER candidate.
    candidate_set = set(candidates)
    reached_by_other: set[str] = set()
    for c in candidates:
        reachable = _bfs_descendants(G, list(G.successors(c)))
        reached_by_other.update(reachable & candidate_set - {c})
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


_STRATEGY_C_SYSTEM_PROMPT = """You help a battery supply-chain risk system decide whether a
regional event affects any facility from a closed candidate list.

You will be given the event's original text and a list of REAL candidate facilities located
in or near the event's region. Your ONLY job is to select which of these listed facilities,
if any, are plausibly affected — you must NEVER select or mention a facility that is not in
the list.

A candidate is plausibly affected only if:
1. The event describes something that could disrupt operations in its location (e.g. a
   power grid failure, wildfire, transport/infrastructure disruption, extreme weather) — not
   an unrelated local news item, and
2. The event is genuinely relevant to battery supply chains (raw material production,
   processing, or transport for battery-critical materials) — not general/unrelated local news
   that merely happens to share a place name, and
3. The candidate's actual city/state/country genuinely matches where the event is happening —
   NOT just a matching word. Some place names are ambiguous (e.g. "Georgia" the US state vs.
   "Georgia" the country in the Caucasus; "Washington" the US state vs. Washington D.C.).
   Read the event text carefully for disambiguating clues (nearby cities, country names,
   regional context) and check them against each candidate's own city/state/country before
   selecting it. If the event is clearly about a place with the same name but NOT the same
   place as any candidate, select none, even though the name matched.

If in doubt, or if none of the candidates are plausibly affected, select none — do not guess.

Respond ONLY with a JSON object:
{
  "relevant": true | false,
  "selected_facility_ids": ["<id1>", "<id2>", ...],
  "reasoning": "<one sentence>"
}
"""

_STRATEGY_C_USER_TEMPLATE = """Event text:
---
{raw_text}
---

Candidate facilities near the event's region ({region}):
{candidates}
"""


def _llm_assisted_seed_fallback(
    all_nodes: dict[str, dict],
    region: str,
    raw_text: str,
) -> tuple[list[str], Optional[StrategyCTrace]]:
    """
    Strategy C (docs/architecture.md "Vorschlag: Multi-Strategie Seed Generator") — only
    called when Strategy A and B both found nothing but the event has a region. Closes the
    P19 gap (docs/open_issues.md): a regional event that doesn't name a company previously
    had no path to reach e.g. an Upstream facility's own location, since _region_match()
    only matched import-dependency-driven origins.

    Anti-hallucination design (matches the architecture.md spec exactly): retrieval is a
    cheap, deterministic, non-LLM lookup (find_facilities_by_region) that returns a CLOSED
    list of real facilities; the LLM only ever selects from that list, never invents one.
    Every returned id is re-validated against the candidate set before use, so even a
    malformed/hallucinated id from the LLM can't leak through as a seed.

    Returns (selected_seed_ids, trace). trace is None only when there were zero candidates
    to begin with (nothing to explain); whenever the LLM was actually consulted, the full
    candidate list + its reasoning is returned regardless of whether anything was selected,
    so a rejected-everything outcome is still explainable in the report/UI (2026-08-07,
    docs/open_issues.md P22 — this used to be computed then discarded).
    """
    candidates = find_facilities_by_region(region)
    if not candidates:
        return [], None

    candidate_lines = "\n".join(
        f"- id={c['facility_id']} | {c['company']} ({c['city']}, {c['state']}, {c['country']}) "
        f"| segment={c['segment']} | materials={c['material_keywords']}"
        for c in candidates
    )

    llm = get_llm(temperature=0)
    messages = [
        SystemMessage(content=_STRATEGY_C_SYSTEM_PROMPT),
        HumanMessage(content=_STRATEGY_C_USER_TEMPLATE.format(
            raw_text=raw_text, region=region, candidates=candidate_lines,
        )),
    ]
    try:
        parsed = invoke_json(llm, messages)
    except ValueError:
        # malformed LLM output — fail closed, same as "no seed found". Still return a trace
        # noting the failure, rather than silently pretending nothing was attempted.
        trace: StrategyCTrace = {
            "region_queried": region,
            "candidates": [{**c, "selected": False} for c in candidates],
            "llm_reasoning": "(LLM returned malformed JSON — treated as no match)",
        }
        return [], trace

    candidate_ids = {c["facility_id"] for c in candidates}
    selected = [fid for fid in parsed.get("selected_facility_ids", []) if fid in candidate_ids]
    is_relevant = parsed.get("relevant", False)

    trace = {
        "region_queried": region,
        "candidates": [{**c, "selected": c["facility_id"] in selected} for c in candidates],
        "llm_reasoning": parsed.get("reasoning", ""),
    }
    return (selected if is_relevant else []), trace


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
    Reads material / region / origin_tier from state,
    returns updated state with affected_nodes, alt_nodes, tier_weights, downstream_fanout.
    """
    material: str = state.get("material", "")
    region: str   = state.get("region", "")
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
    # SCOPE (updated 2026-08-07 — this candidate_ids match itself is still just Strategy A):
    # this specific match (material+region) models an import-dependent raw-material
    # disruption, foreign OR the facility's own location (Upstream's import_origin_region
    # is its own state since 2026-08-06 — "I source from myself"). Named-facility
    # disruptions are handled separately by Strategy B (_entity_match_seeds, below), and a
    # regional event with no material/region match and no named company falls through to
    # Strategy C (_llm_assisted_seed_fallback). Still NOT modeled by any strategy: a
    # tier-wide disruption with no region at all (e.g. "US cell manufacturing capacity down
    # nationwide"), and logistics/transport events (no supply-route data — see
    # docs/open_issues.md P21). See docs/architecture.md "Bekannte Grenzen des
    # Seeding-Mechanismus" for the full writeup.
    # "Unknown origin" vs. "known to be from elsewhere" are NOT the same thing and must not
    # be treated identically (2026-08-07). For the 5 originally-modeled materials, almost
    # every matching facility has a real drawn/known origin (verified: 0-2 exceptions per
    # material, all explicit IMPORT_DEP_OVERRIDES facts, not missing data) — region_match
    # correctly distinguishes "sources from the affected region" from "sources elsewhere"
    # for those. But materials added later (silicon/pvdf/cnt/copper_foil/aluminum_foil, P26)
    # have NO IMPORT_ORIGIN_DISTRIBUTION data at all yet — verified 100% of their matching
    # facilities have an empty origin, not "known to be elsewhere". Silently requiring
    # region_match for these would exclude every one of them regardless of what the event
    # says, which isn't a grounded decision, just a data gap — same "unknown must not
    # collapse into excluded" principle already used for capacity_known (data_retrieval_agent
    # defaults CapacityShare to the precautionary 1.0, not 0.0, when unknown). So: if NOT ONE
    # material-matching facility has any known origin for this material, region can't
    # discriminate at all — fall back to material-match alone rather than exclude everyone.
    material_matches = [nid for nid, attrs in all_nodes.items() if _material_match(attrs, material)]
    material_has_known_origins = any(
        _origin_for_material(all_nodes[nid], material) for nid in material_matches
    )
    if material_has_known_origins:
        candidate_ids: list[str] = [
            nid for nid in material_matches if _region_match(all_nodes[nid], region, material)
        ]
    else:
        candidate_ids = material_matches if region else []
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
    strategy_c_trace: Optional[StrategyCTrace] = None

    # "no_seed_found" must NOT be read as "no NA exposure" — see SeedGenerationStatus
    # docstring in state.py. Strategy A/B/C are what this system models today (tier-wide
    # domestic events without a region, and logistics/port events, are still unmodeled —
    # see architecture.md).
    if entity_status == "entity_matched":
        seed_generation_status: str = "entity_matched"
    elif rule_seed_ids:
        seed_generation_status = "rule_matched"
    elif entity_status == "entity_non_material":
        seed_generation_status = "entity_non_material"
    elif entity_status == "entity_ambiguous":
        seed_generation_status = "entity_ambiguous"
    elif region:
        # Strategy C (2026-08-06): A and B both found nothing, but there's a region to work
        # with — try the LLM-assisted fallback before giving up. Only fires here, never
        # instead of A/B, and never invents facilities (see _llm_assisted_seed_fallback).
        llm_seed_ids, strategy_c_trace = _llm_assisted_seed_fallback(
            all_nodes, region, state.get("raw_input", "")
        )
        if llm_seed_ids:
            seed_ids = list(set(seed_ids) | set(llm_seed_ids))
            seed_generation_status = "llm_fallback_used"
        else:
            seed_generation_status = "no_seed_found"
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
        # facility_name is missing for ~5% of NAATBatt rows (pandas NaN at CSV-build time,
        # e.g. Vertical Partners West LLC) — build_graph.py stringifies it when writing JSON,
        # so it round-trips as the literal string "nan" (verified: not a float here, despite
        # being a real float NaN in facilities_clean.csv — str(float('nan')) == "nan"
        # happens somewhere in the CSV->JSON step). Either form is truthy in Python
        # (`nan or fallback` never falls back, and "nan" is a non-empty string), so every
        # consumer (report headers, UI table) would otherwise literally print "nan".
        # Normalize once here rather than guard at every call site.
        raw_facility_name = attrs.get("facility_name", "")
        facility_name = (
            "" if isinstance(raw_facility_name, float)
            or str(raw_facility_name).strip().lower() == "nan"
            else raw_facility_name
        )
        return Node(
            id=nid,
            company=attrs.get("company", ""),
            facility_name=facility_name,
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
            direct_supplier_count=G.in_degree(nid),
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
        "strategy_c_trace":       strategy_c_trace,  # None unless Strategy C actually ran
    }
