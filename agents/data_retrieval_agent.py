"""
Data Retrieval Agent — Deterministic CSV lookup + arithmetic

Input (from PipelineState):
  affected_nodes : list[Node]
  alt_nodes      : list[Node]
  affected_material : str        e.g. "cobalt"

Output (written to PipelineState):
  facility_data             : dict[str, FacilityData]
  betroffene_kapazitaet_pct : float   (NA scope, affected / total × 100)
  alternative_kapazitaet_pct: float   (NA scope, alt / total × 100)
"""

from pathlib import Path
from typing import Optional

import pandas as pd

from agents.state import (
    CapacitySource,
    FacilityData,
    LEAD_TIME_NORM_DIVISOR,
    VULNERABILITY_WEIGHTS,
    PipelineState,
)

FACILITIES_FILE = Path(__file__).parent.parent / "data" / "facilities_clean.csv"

# ── CSV singleton ─────────────────────────────────────────────────────────────

_df_cache: Optional[pd.DataFrame] = None


def _load_csv() -> pd.DataFrame:
    global _df_cache
    if _df_cache is None:
        _df_cache = pd.read_csv(FACILITIES_FILE, dtype={"facility_id": str})
    return _df_cache


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_capacity(raw) -> float:
    try:
        val = float(raw)
        return val if val > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _capacity_share(facility_capacity: float, total_capacity: float) -> float:
    # 2026-08-03 (explicit instruction): a zero/unknown denominator (e.g. no material set at
    # all — Strategy B facility-specific events — or no other facility in this tier/material
    # has known capacity) means CapacityShare genuinely can't be computed, same "unknown"
    # class as the segment-level default in run_data_retrieval_agent() below. Precautionary
    # default is 1.0 (max/worst-case), not 0.0.
    if total_capacity <= 0:
        return 1.0
    return min(facility_capacity / total_capacity, 1.0)


# ── Main agent function ───────────────────────────────────────────────────────

def run_data_retrieval_agent(state: PipelineState) -> PipelineState:
    """
    LangGraph-compatible node function.
    Reads affected_nodes / alt_nodes / affected_material from state,
    returns updated state with facility_data and capacity percentages.
    """
    affected_nodes = state.get("affected_nodes", [])
    alt_nodes      = state.get("alt_nodes", [])
    material: str  = state.get("affected_material") or state.get("material", "")

    df = _load_csv()

    # Index CSV by facility_id for O(1) lookup
    df_idx = df.set_index("facility_id")

    # ── Total NA capacity for this material (denominator for all %) ───────────
    # Only facilities that carry this material keyword and have real capacity data.
    # Scoped per segment: Upstream (MT/yr) and Midstream-Cell (GWh/yr) use different
    # units and must not be summed together (see CapacityShare comment below).
    #
    # Guard (2026-07-31): pandas .str.contains("") is True for every non-null string —
    # an empty/missing material (facility-specific disruption events, e.g. Strategy B,
    # carry no affected_material) would otherwise match every one of the 386 facilities,
    # inflating the capacity denominators with unrelated materials instead of correctly
    # yielding "not applicable" (CapacityShare/capacity % already degrade gracefully to 0
    # for a zero/empty denominator — see _capacity_share() below). Same bug class as
    # _material_match()'s guard in network_agent.py.
    material_rows = (
        df[df["material_keywords"].str.contains(material, na=False)] if material
        else df.iloc[0:0]
    )
    total_capacity_by_segment: dict[str, float] = {
        seg: material_rows[material_rows["supply_chain_segment"] == seg][
            "production_capacity_raw"
        ].apply(_parse_capacity).sum()
        for seg in ("Upstream", "Midstream-Cell")
    }


    # ── Pre-compute capacities for all relevant nodes ─────────────────────────
    all_node_ids = {n["id"] for n in affected_nodes} | {n["id"] for n in alt_nodes}
    facility_capacities: dict[str, float] = {}
    for nid in all_node_ids:
        if nid in df_idx.index:
            raw = df_idx.at[nid, "production_capacity_raw"]
            facility_capacities[nid] = _parse_capacity(raw)
        else:
            facility_capacities[nid] = 0.0

    # ── Build FacilityData per node ───────────────────────────────────────────
    facility_data: dict[str, FacilityData] = {}

    for node in affected_nodes + alt_nodes:
        nid = node["id"]
        if nid in facility_data:
            continue  # already processed (node appears in both lists)

        capacity = facility_capacities[nid]

        if nid in df_idx.index:
            row = df_idx.loc[nid]
            src = str(row.get("capacity_source", ""))
            cap_source: CapacitySource = (
                "naatbatt" if src == "naatbatt"
                else "unknown"
            )
            supplier_concentration = bool(row.get("supplier_concentration", False))
            import_dep             = bool(row.get("import_dependency", False))
            lead_time_w   = int(row.get("lead_time_weeks", 0))
        else:
            # Node exists in graph but not in CSV (should not happen in practice)
            cap_source             = "unknown"
            supplier_concentration = node.get("supplier_concentration", False)
            import_dep             = node.get("import_dependency", False)
            lead_time_w            = node.get("lead_time_weeks", 0)

        # Capped at 1.0 (2026-08-03): lead_time_weeks can now exceed 12 once material/
        # region/capacity-data adjustments are added on top of the segment base (see
        # MATERIAL_LEAD_TIME_ADJUSTMENT in build_facilities.py) — without this cap,
        # Vulnerability (and hence RiskScore_normalized) could exceed its documented 0-1
        # (resp. 0-100) ceiling. risk_model.md already documented this cap as intended
        # behavior; it was never actually enforced in code because 12 was previously the
        # highest possible raw value (Upstream's own uncapped segment base), so the
        # missing min() never mattered until now.
        lead_time_norm = min(lead_time_w / LEAD_TIME_NORM_DIVISOR, 1.0)

        capacity_known = cap_source == "naatbatt"
        seg = node.get("segment", "")

        # CapacityShare: only for tiers with comparable units.
        # Upstream: MT/yr (consistent). Midstream-Cell: GWh/yr (90%, after
        # MWh→GWh conversion and exclusion of non-comparable records in build_facilities).
        # Midstream-BGM (MT/mm²/GWh mixed) and Downstream excluded.
        #
        # 2026-08-03 (explicit instruction): default changed from 0.0 to 1.0 (max/worst-case)
        # whenever this can't be computed — wrong segment (BGM/Downstream) OR capacity_known
        # =False (Upstream/Cell with no NAATBatt value). Precautionary principle: "we don't
        # know this facility's market share" should read as "assume it could be large", not
        # "assume it's negligible". This is a deliberate reversal of the previous
        # conservative-low default — see synthesis_agent.py::_compute_scores for the
        # corresponding Vulnerability-term comment.
        if seg in ("Upstream", "Midstream-Cell") and capacity_known:
            cap_share = _capacity_share(capacity, total_capacity_by_segment[seg])
        else:
            cap_share = 1.0

        # SingleSourceDependency — now computed for ALL segments (2026-08-03), not just
        # Downstream/Midstream-BGM, because ResilienceDiscount now uses it uniformly (see
        # below). direct_supplier_count = in-degree from the immediately upstream tier.
        # KNOWN DEGENERATE CASE for Upstream: network_agent.py's graph is a directed chain
        # Upstream->BGM->Cell->Downstream, so Upstream nodes are always the graph's roots —
        # in-degree is structurally 0 for every one of the 29 Upstream facilities, always,
        # with no exceptions (verified). That makes single_source_dependency=0.0 for every
        # Upstream facility unconditionally — which the `if single_source_dependency > 0`
        # guard below then routes to resilience_discount=0.0 (the same conservative default
        # used for "no known suppliers"/unknown data), NOT the maximum 0.5 (verified with a
        # real Upstream facility, Albemarle Corporation/Silver Peak: ssd=0.0 ->
        # resilience_discount=0.0). The real consequence is milder than "always-maximal" but
        # still a loss of signal: every Upstream facility now gets a flat 0.0 discount
        # regardless of whether real alternative capacity exists, instead of the genuine
        # AltCapacityRatio-based value (which could legitimately be anywhere in 0-0.5) it had
        # before this change. Only affects facilities scored via Strategy B (entity-matched)
        # disruptions at an Upstream-tier NAATBatt facility — Strategy A (material+region)
        # can never seed an Upstream node (import_dependency is always False there), so
        # today's S1/S2/S3 scenarios never exercise this case.
        supplier_count = node.get("direct_supplier_count", 0)
        single_source_dependency = 1.0 / supplier_count if supplier_count > 0 else 0.0

        # ResilienceDiscount — swapped to CapacityShare-based (2026-08-03, same day, on top of
        # the earlier SingleSourceDependency-based unification above). Rationale: this
        # resolves the CapacityShare direction tension that used to live in Vulnerability
        # ("big market share = big Vulnerability score" required the awkward "systemic impact,
        # not the facility's own fragility" caveat). Framed as a resilience question instead,
        # the direction is intuitive with no caveat needed: a facility that commands most of
        # its tier's capacity has, by definition, no one else who can cover for it if it goes
        # down -> small discount. A facility that's a small player has plenty of alternative
        # capacity elsewhere -> large discount. SingleSourceDependency moved the other
        # direction, into Vulnerability (own-supplier-count fragility is a self-related
        # vulnerability signal, not a system-recoverability one) — see VULNERABILITY_WEIGHTS.
        #
        # No unknown-data guard needed (unlike the old SSD-based version's
        # `if single_source_dependency > 0` check): cap_share is already guaranteed in [0, 1]
        # by _capacity_share()/the "capacity_known" branch above, AND its own default for the
        # inapplicable/unknown case is already 1.0 (worst-case, precautionary default set
        # above) — which naturally maps to (1-1)/2 = 0.0 discount here, exactly the
        # conservative "no discount when we don't know" behavior we want, with no extra
        # branching. Also no min(...,0.5) cap needed for the same reason as before: cap_share
        # in [0,1] guarantees (1-cap_share)/2 is already in [0, 0.5].
        resilience_discount = (1.0 - cap_share) / 2.0

        facility_data[nid] = FacilityData(
            capacity=capacity,
            capacity_source=cap_source,
            capacity_known=capacity_known,
            supplier_concentration=supplier_concentration,
            import_dep=import_dep,
            lead_time_norm=lead_time_norm,
            capacity_share=cap_share,
            single_source_dependency=single_source_dependency,
            resilience_discount=resilience_discount,
        )

    # ── Global capacity percentages (NA scope, Upstream only) ────────────────
    # Capacity % is only meaningful within the same tier + same unit.
    # Upstream nodes report raw material production (MT/yr); mixing with
    # Cell (GWh) or Downstream values would give nonsensical percentages.
    affected_cap = sum(
        facility_capacities[n["id"]] for n in affected_nodes
        if n.get("segment") == "Upstream"
    )
    alt_cap = sum(
        facility_capacities[n["id"]] for n in alt_nodes
        if n.get("segment") == "Upstream"
    )
    # Denominator: total Upstream capacity for this material in NA
    upstream_rows = material_rows[material_rows["supply_chain_segment"] == "Upstream"]
    total_upstream_capacity = upstream_rows["production_capacity_raw"].apply(_parse_capacity).sum()

    if total_upstream_capacity > 0:
        betroffene_pct  = round(affected_cap / total_upstream_capacity * 100, 1)
        alternative_pct = round(alt_cap      / total_upstream_capacity * 100, 1)
    else:
        betroffene_pct  = 0.0
        alternative_pct = 0.0

    return {
        **state,
        "facility_data":              facility_data,
        "betroffene_kapazitaet_pct":  betroffene_pct,
        "alternative_kapazitaet_pct": alternative_pct,
    }
