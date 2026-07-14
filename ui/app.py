"""
SC Risk System — Streamlit UI
Run: streamlit run ui/app.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import streamlit as st

from pipeline.pipeline import stream_pipeline

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="SC Risk System",
    layout="wide",
)

# ── Sample inputs ─────────────────────────────────────────────────────────────

SAMPLES = {
    "S1 — Cobalt mine strike (DRC)": (
        "Major strike at Glencore cobalt mines in the Democratic Republic of Congo "
        "entered its third week Monday, with workers demanding higher wages amid record "
        "cobalt prices. The walkout has halted production at facilities supplying roughly "
        "8% of global cobalt output, raising concerns among battery manufacturers in North "
        "America who rely on DRC-sourced material for NMC cathodes."
    ),
    "S2 — Lithium export restriction (Chile)": (
        "Chile's government announced sweeping new regulations requiring state approval "
        "for all lithium export contracts, effective immediately. The measure, framed as "
        "a national resource protection policy, could delay shipments by 3–6 months and "
        "affects roughly 30% of global lithium carbonate supply destined for North American "
        "battery manufacturers."
    ),
    "S3 — Port disruption (Vancouver)": (
        "A major cyberattack has disrupted operations at the Port of Vancouver, halting "
        "shipments of battery-grade graphite and lithium carbonate destined for US cell "
        "manufacturers. Authorities estimate a 2-week delay minimum before normal "
        "operations resume."
    ),
    "User query (Trigger B)": (
        "What happens to North American battery supply if China bans graphite exports?"
    ),
}

AGENT_LABELS = {
    "intake":          "Intake — Nachrichtenanalyse",
    "risk_assessment": "Risk Assessment — Materialien & Schweregrad",
    "network":         "Network — Lieferkettengraph",
    "data_retrieval":  "Data Retrieval — Anlagendaten",
    "synthesis":       "Synthesis — Risikobericht",
    "validation":      "Validation — Qualitätsprüfung",
}

SEVERITY_COLOR = {1: "🟢", 2: "🟡", 3: "🟠", 4: "🔴", 5: "🔴"}
RISK_TYPE_LABEL = {
    "supply_disruption": "Supply Disruption",
    "price_volatility":  "Price Volatility",
    "regulatory":        "Regulatory",
    "logistics":         "Logistics",
    "weather":           "Weather",
}

# ── Layout ────────────────────────────────────────────────────────────────────

st.title("Battery Supply Chain Risk System")
st.caption("LLM Multi-Agent Pipeline · NAATBatt Data (March 2026)")

st.divider()

# Input panel
col_input, col_sample = st.columns([3, 1])

with col_sample:
    st.markdown("**Sample inputs**")
    chosen = st.selectbox("", list(SAMPLES.keys()), label_visibility="collapsed")
    if st.button("Load sample", use_container_width=True):
        st.session_state["input_text"] = SAMPLES[chosen]

with col_input:
    raw_input = st.text_area(
        "Enter a news article or supply chain query:",
        value=st.session_state.get("input_text", ""),
        height=150,
        placeholder="Paste a news article or type a question about battery supply chain risk...",
    )

run_btn = st.button("🔍 Analyze Risk", type="primary", use_container_width=True)

# ── Pipeline execution ────────────────────────────────────────────────────────

if run_btn and raw_input.strip():
    _STEP_ORDER = ["intake", "risk_assessment", "network", "data_retrieval", "synthesis", "validation"]
    _N = len(_STEP_ORDER)

    progress_bar  = st.progress(0, text="Pipeline startet…")
    step_label    = st.empty()

    try:
        result = None
        # show first step as "running" before the generator yields anything
        step_label.markdown(f"🔄 {AGENT_LABELS['intake']}…")
        for node_name, state in stream_pipeline(raw_input.strip()):
            label = AGENT_LABELS.get(node_name, node_name)
            idx   = _STEP_ORDER.index(node_name) + 1 if node_name in _STEP_ORDER else _N
            progress_bar.progress(idx / _N, text=f"✅ {label}")
            # preview the next step while the loop waits for the next LLM call
            if idx < _N:
                next_label = AGENT_LABELS.get(_STEP_ORDER[idx], _STEP_ORDER[idx])
                step_label.markdown(f"🔄 {next_label}…")
            result = state
        progress_bar.progress(1.0, text="✅ Analyse abgeschlossen")
        step_label.empty()
        if result is not None:
            st.session_state["result"] = result
        else:
            st.warning("Pipeline lieferte kein Ergebnis.")
    except Exception as e:
        st.error(f"Pipeline error: {e}")
        st.stop()

# ── Results ───────────────────────────────────────────────────────────────────

result = st.session_state.get("result")

if result is None:
    st.info("Enter a news article or query above and click **Analyze Risk** to start.")
    st.stop()

# Not relevant
if not result.get("relevant", False):
    st.warning("⚪ **Not relevant** — The input does not appear to concern battery supply chain risk.")
    st.stop()

st.divider()

# ── KPI row ──────────────────────────────────────────────────────────────────

k1, k2, k3, k4 = st.columns(4)
severity   = result.get("severity", 0)
risk_type  = result.get("risk_type", "")
material   = result.get("affected_material", "")
region     = result.get("affected_region", "")

k1.metric("Severity", f"{SEVERITY_COLOR.get(severity, '')} {severity} / 5")
k2.metric("Risk Type", RISK_TYPE_LABEL.get(risk_type, risk_type))
k3.metric("Material", material.capitalize())
k4.metric("Region", region)

st.divider()

# ── Supply chain exposure ─────────────────────────────────────────────────────
# Three distinct denominators, kept visually separate so they're never read as
# interchangeable:
#   - Global   : affected / ALL facilities in the dataset, any material (386)
#   - Material : affected / facilities in this material's relevant sub-network
#   - Propagation: how the material-network exposure breaks down (direct vs. via graph)

affected_nodes = result.get("affected_nodes", [])
alt_nodes = result.get("alt_nodes", [])
supply_chain_paths = result.get("supply_chain_paths", {})
total_network = result.get("total_network_facilities", 0)
material_label = (result.get("affected_material") or "").capitalize() or "Material"

material_nodes = affected_nodes + alt_nodes  # every facility in this material's sub-network, affected or not

affected_companies = {n.get("company", "") for n in affected_nodes}
material_companies = {n.get("company", "") for n in material_nodes}

direct_count = sum(
    1 for n in affected_nodes
    if len(supply_chain_paths.get(n["id"], [n["id"]])) == 1
)
propagated_count = len(affected_nodes) - direct_count

_pct = (len(affected_nodes) / total_network * 100) if total_network else 0.0
_mat_pct = (len(affected_nodes) / len(material_nodes) * 100) if material_nodes else 0.0
_prop_pct = (propagated_count / len(affected_nodes) * 100) if affected_nodes else 0.0

with st.container(border=True):
    st.subheader("Supply chain exposure")
    st.caption("How much of the entire North American battery supply chain network (all materials) this event touches.")
    st.metric(
        "Affected facilities (share of full network)",
        f"{len(affected_nodes)} / {total_network}",
        delta=f"{_pct:.1f}% of network", delta_color="inverse",
        help="Affected facilities / ALL facilities in the dataset, regardless of material. "
             "Shows how large this event is relative to the whole network.",
    )

with st.container(border=True):
    st.subheader(f"{material_label} network impact")
    st.caption(f"Within just the {material_label.lower()} sub-network (facilities that handle "
               f"{material_label.lower()} or sit downstream of one that does): how deep did this event reach?")
    mc1, mc2 = st.columns(2)
    mc1.metric(
        "Affected nodes", f"{len(affected_nodes)} / {len(material_nodes)}",
        delta=f"{_mat_pct:.1f}% penetration", delta_color="inverse",
        help=f"Affected / total facilities within the {material_label} sub-network specifically "
             f"(a much smaller, more relevant denominator than the full {total_network}-facility dataset).",
    )
    mc2.metric(
        "Affected manufacturers", f"{len(affected_companies)} / {len(material_companies)}",
        help="Same, but counted by distinct company (one company can own several facilities).",
    )

with st.container(border=True):
    st.subheader("Risk propagation")
    st.caption("Of the affected facilities: how many matched the event's import dependency directly "
               "vs. only reached through the supply chain graph.")
    pc1, pc2 = st.columns(2)
    pc1.metric(
        "Primary exposure", direct_count,
        help="Facilities matched by import dependency on the affected region (e.g. a North American "
             "plant that imports from the disrupted region) — NOT facilities physically located in "
             "that region. NAATBatt only covers North America, so these are always NA-based.",
    )
    pc2.metric(
        "Propagated exposure", propagated_count,
        delta=f"{_prop_pct:.1f}% of affected" if affected_nodes else None, delta_color="inverse",
        help="Facilities reached only via downstream graph traversal from a primary-exposure facility — "
             "affected because they depend on it, not because they import from the affected region themselves.",
    )

    st.markdown("**Affected facilities by supply chain stage** (within the material sub-network above)")
    seg_order = ["Upstream", "Midstream-BGM", "Midstream-Cell", "Downstream"]
    seg_help = {
        "Upstream":       "Mining / raw material extraction.",
        "Midstream-BGM":  "Battery-grade material processing (cathode/anode active materials, electrolyte, separators).",
        "Midstream-Cell": "Cell manufacturing (cylindrical, prismatic, pouch cells).",
        "Downstream":     "Module/pack assembly, EV/ESS manufacturers.",
    }
    seg_affected: dict[str, int] = {s: 0 for s in seg_order}
    seg_total:    dict[str, int] = {s: 0 for s in seg_order}
    for n in affected_nodes:
        seg_affected[n.get("segment", "unknown")] = seg_affected.get(n.get("segment", "unknown"), 0) + 1
    for n in material_nodes:
        seg_total[n.get("segment", "unknown")] = seg_total.get(n.get("segment", "unknown"), 0) + 1

    seg_cols = st.columns(4)
    for col, seg in zip(seg_cols, seg_order):
        col.metric(seg, f"{seg_affected[seg]} / {seg_total[seg]}", help=seg_help[seg])

st.divider()

# ── Capacity metrics ──────────────────────────────────────────────────────────
# Scope: Upstream (mining) only — the only tier with comparable units (MT/yr).
# This is a production-capacity metric, not a full supply-chain impact metric —
# see the interpretation note and the exposure numbers above for the Midstream/Downstream picture.

gm = result.get("global_metrics", {})

with st.container(border=True):
    st.subheader("North American upstream mining capacity")
    st.caption("Affected vs. remaining production capacity among North American mining facilities for this material.")
    u1, u2 = st.columns(2)
    u1.metric("Affected capacity",  f"{gm.get('betroffene_kapazitaet_na_pct', 0):.1f}%")
    u2.metric("Remaining capacity", f"{gm.get('alternative_kapazitaet_na_pct', 0):.1f}%")

    st.markdown("**Share of global supply** (USGS world production)")
    g1, g2 = st.columns(2)
    g1.metric("Affected capacity",  f"{gm.get('betroffene_kapazitaet_global_pct', 0):.2f}%")
    g2.metric("Remaining capacity", f"{gm.get('alternative_kapazitaet_global_pct', 0):.2f}%")

    if gm.get("betroffene_kapazitaet_na_pct", 0) == 0 and affected_nodes:
        non_upstream = [n for n in affected_nodes if n.get("segment") != "Upstream"]
        if non_upstream:
            st.info(
                f"No upstream mining facility in the current network is located in the "
                f"affected region — so 0% of North American mining capacity is directly hit. "
                f"This does **not** mean there is no risk: **{len(non_upstream)} Midstream/Downstream "
                f"facilities** are still flagged as affected via import dependency (see Top 3 below). "
                f"**Import dependency exposure is not captured by mining capacity impact** — "
                f"see Supply Chain Exposure above for the fuller picture."
            )

st.divider()

# ── Top 3 + Map ───────────────────────────────────────────────────────────────

st.subheader("Top 3 High-Risk Facilities")

top3 = result.get("top3_facilities", [])

if top3:
    left, right = st.columns([1, 1])

    with left:
        rows = []
        for i, f in enumerate(top3, 1):
            rows.append({
                "Rank":      i,
                "Company":   f["company"],
                "Segment":   f["segment"],
                "Location":  f"{f.get('city', '')}, {f.get('state', '')}, {f['country']}",
                "Score":     f"{f['risk_score_normalized']:.1f} / 100",
                "Vuln.":     f"{f['vulnerability']:.3f}",
                "Tier Wt.":  f"{f['tier_weight']}",
                "Exposure":  "Primary" if f.get("exposure_type") == "direct" else "Propagated",
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    with right:
        map_data = pd.DataFrame([
            {
                "lat": f["latitude"],
                "lon": f["longitude"],
                "name": f["company"],
            }
            for f in top3
            if f.get("latitude") and f.get("longitude")
        ])
        if not map_data.empty:
            st.map(map_data, zoom=2)

    st.markdown(
        "**Supply chain path** (seed event → facility)",
        help=(
            "**Primary** — the facility itself already satisfies the event-matching criteria "
            "(import dependency on the affected region) and is therefore a risk propagation seed; "
            "its path contains only the facility itself. This is a supply-chain/import-dependency "
            "match, not a geographic one — NAATBatt only covers North America, so a 'Primary' facility "
            "is always NA-based, even for an event originating abroad.\n\n"
            "**Propagated** — the facility does not match the event criteria itself; "
            "it was reached through multi-tier supply chain traversal from a Primary seed."
        ),
    )
    for i, f in enumerate(top3, 1):
        if f.get("supply_path"):
            label = "🔴 Primary" if f.get("exposure_type") == "direct" else "🔀 Propagated"
            st.markdown(f"{i}. [{label}] {f['supply_path']}")

st.divider()

# ── Full report ───────────────────────────────────────────────────────────────

st.subheader("Risk Report")
st.markdown(result.get("risk_report", ""))

st.divider()

# ── Pipeline details (expandable) ─────────────────────────────────────────────

with st.expander("Pipeline details", expanded=False):
    val_col, iter_col = st.columns(2)
    val_col.metric("Validation",  "✅ Valid" if result.get("valid") else "⚠️ Issues found")
    iter_col.metric("Iterations", result.get("iteration", 1))

    issues = result.get("issues", [])
    if issues:
        st.markdown("**Validation issues:**")
        for iss in issues:
            st.markdown(f"- {iss}")

    with st.expander("Raw pipeline state (debug)", expanded=False):
        debug = {k: v for k, v in result.items()
                 if k not in ("affected_nodes", "alt_nodes", "facility_data",
                              "risk_scores", "tier_weights", "downstream_fanout")}
        st.json(debug)
