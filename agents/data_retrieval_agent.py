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
    RESILIENCE_DISCOUNT_CAP,
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
    if total_capacity <= 0:
        return 0.0
    return min(facility_capacity / total_capacity, 1.0)


def _alt_capacity_ratio(facility_id: str, alt_node_ids: set[str],
                        facility_capacities: dict[str, float],
                        facility_capacity: float,
                        capacity_known: bool) -> float:
    """
    AltCapacityRatio = Σ alt_capacity / facility_capacity.
    Only computed when the facility has known capacity (naatbatt only).
    If capacity is unknown, returns 0.0 → ResilienceDiscount = 0 (conservative).
    Unknown capacity is NOT treated as zero capacity.
    """
    if not capacity_known or facility_capacity <= 0:
        return 0.0
    alt_total = sum(facility_capacities.get(nid, 0.0) for nid in alt_node_ids)
    return alt_total / facility_capacity


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
    # Only facilities that carry this material keyword and have real capacity data
    material_rows = df[df["material_keywords"].str.contains(material, na=False)]
    total_capacity = material_rows["production_capacity_raw"].apply(_parse_capacity).sum()


    # ── Pre-compute capacities for all relevant nodes ─────────────────────────
    all_node_ids = {n["id"] for n in affected_nodes} | {n["id"] for n in alt_nodes}
    facility_capacities: dict[str, float] = {}
    for nid in all_node_ids:
        if nid in df_idx.index:
            raw = df_idx.at[nid, "production_capacity_raw"]
            facility_capacities[nid] = _parse_capacity(raw)
        else:
            facility_capacities[nid] = 0.0

    alt_node_ids = {n["id"] for n in alt_nodes}

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

        lead_time_norm = lead_time_w / LEAD_TIME_NORM_DIVISOR

        capacity_known = cap_source == "naatbatt"
        seg = node.get("segment", "")

        # CapacityShare: only for tiers with comparable units.
        # Upstream: MT/yr (consistent). Midstream-Cell: GWh/yr (90%, after
        # MWh→GWh conversion and exclusion of non-comparable records in build_facilities).
        # Midstream-BGM (MT/mm²/GWh mixed) and Downstream excluded.
        if seg in ("Upstream", "Midstream-Cell") and capacity_known:
            cap_share = _capacity_share(capacity, total_capacity)
        else:
            cap_share = 0.0

        # AltCapacityRatio: same tier comparability rule as CapacityShare.
        # Upstream (MT/yr) and Midstream-Cell (GWh/yr) are comparable within tier.
        # Unknown capacity or incompatible tier → ResilienceDiscount = 0 (conservative).
        if seg in ("Upstream", "Midstream-Cell"):
            alt_ratio = _alt_capacity_ratio(nid, alt_node_ids,
                                            facility_capacities, capacity,
                                            capacity_known)
        else:
            alt_ratio = 0.0
        resilience_discount = min(alt_ratio / 2.0, RESILIENCE_DISCOUNT_CAP)

        facility_data[nid] = FacilityData(
            capacity=capacity,
            capacity_source=cap_source,
            capacity_known=capacity_known,
            supplier_concentration=supplier_concentration,
            import_dep=import_dep,
            lead_time_norm=lead_time_norm,
            capacity_share=cap_share,
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
