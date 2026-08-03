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
    "S3 — Facility disruption (Panasonic, Kansas)": (
        "A fire has broken out at Panasonic's battery cell manufacturing plant in De "
        "Soto, Kansas, halting production indefinitely. The facility, one of the "
        "largest EV battery plants in North America, supplies cells to Tesla, Toyota, "
        "and Lucid Motors. Local officials say it is too early to estimate how long "
        "the shutdown will last."
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

_seed_status = result.get("seed_generation_status")
if _seed_status == "no_seed_found":
    st.error(
        "⚠️ **Not classifiable by this system — this is NOT a \"no risk found\" result.** "
        "The system currently only detects (1) import-dependent raw-material disruptions abroad, "
        "or (2) facility-specific disruptions at a named, uniquely-identifiable company. "
        "This event didn't match either pattern — it may be a domestic tier-wide issue or a "
        "logistics/port event, neither of which this system can currently classify. "
        "**Treat any numbers below as not meaningful and review this case manually** — do not "
        "read the absence of results as evidence of no exposure."
    )
elif _seed_status == "entity_ambiguous":
    _company = result.get("mentioned_company", "this company")
    st.warning(
        f"⚠️ **Facility reference ambiguous — this is NOT a \"no risk found\" result.** "
        f"\"{_company}\" was recognized, but it has multiple facilities in the network and the "
        f"location mentioned (if any) wasn't enough to identify exactly one. The system "
        f"deliberately does not guess which site is meant. **Treat any numbers below as not "
        f"meaningful** — re-run with a more specific location if possible."
    )
elif _seed_status == "entity_non_material":
    _company = result.get("mentioned_company", "this company")
    st.warning(
        f"⚠️ **\"{_company}\" resolved to a non-material facility — this is NOT a \"no risk "
        f"found\" result.** The named company was matched uniquely, but it is a mechanical/"
        f"safety/component supplier (e.g. adhesives, thermal systems, BMS modules) that doesn't "
        f"process any of the raw materials this system tracks. This system models raw-material "
        f"supply-chain risk, not general component/BOM disruption. **Treat any numbers below as "
        f"not meaningful.**"
    )
elif _seed_status == "entity_matched":
    st.success(
        f"✅ Seed identified via facility-name matching: **{result.get('mentioned_company', '')}**"
        + (f" ({result.get('mentioned_location')})" if result.get("mentioned_location") else "")
        + " — not via the material/region import-dependency rule."
    )

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
    st.caption("How much of the entire North American battery supply chain network (all materials) this event "
               "structurally reaches — reachability, not severity.")
    st.metric(
        "Potentially exposed facilities (share of full network)",
        f"{len(affected_nodes)} / {total_network}",
        delta=f"{_pct:.1f}% of network", delta_color="inverse",
        help="Potentially exposed facilities / ALL facilities in the dataset, regardless of material. "
             "Shows how large this event is relative to the whole network.",
    )

with st.container(border=True):
    st.subheader(f"{material_label} network impact")
    if _seed_status == "entity_matched":
        # Facility-specific (Strategy B) events: affected_material is a free-text LLM label
        # (e.g. "battery cells"), not one of the six material_keywords this sub-network is
        # built from (cobalt/lithium/nickel/manganese/graphite/copper) — network_agent.py's
        # _material_match() therefore never matches anything, so alt_nodes is always empty
        # and material_nodes == affected_nodes exactly. Showing "N / N, 100% penetration"
        # here would be a tautology (denominator always equals numerator), not a real
        # measurement — same class of issue as the removed "Risk level" card. Skipped
        # entirely for this event type rather than displaying a misleading always-100%.
        st.caption("Not applicable for a facility-specific event — there is no material "
                   "sub-network to compare against here (see help below).",
                   help="This module compares the event's reach against all facilities that "
                        "handle the same raw material anywhere in the network. Facility-"
                        "specific disruptions (like this one) aren't matched by material, "
                        "so that comparison denominator doesn't exist — every facility this "
                        "event reaches is already counted under 'Supply chain exposure' above.")
    else:
        st.caption(f"Within just the {material_label.lower()} sub-network (facilities that handle "
                   f"{material_label.lower()} or sit downstream of one that does): how deep did this event reach?")
        mc1, mc2 = st.columns(2)
        mc1.metric(
            "Potentially exposed facilities", f"{len(affected_nodes)} / {len(material_nodes)}",
            delta=f"{_mat_pct:.1f}% penetration", delta_color="inverse",
            help=f"Potentially exposed / total facilities within the {material_label} sub-network specifically "
                 f"(a much smaller, more relevant denominator than the full {total_network}-facility dataset).",
        )
        mc2.metric(
            "Potentially exposed companies", f"{len(affected_companies)} / {len(material_companies)}",
            help="Same, but counted by distinct company (one company can own several facilities).",
        )

with st.container(border=True):
    st.subheader("Risk propagation")
    st.caption("Of the potentially exposed facilities: how many matched the event's import dependency "
               "directly vs. only reached through the supply chain graph.")
    pc1, pc2 = st.columns(2)
    pc1.metric(
        "Primary exposure", direct_count,
        help="Facilities matched by import dependency on the affected region (e.g. a North American "
             "plant that imports from the disrupted region) — NOT facilities physically located in "
             "that region. NAATBatt only covers North America, so these are always NA-based.",
    )
    pc2.metric(
        "Propagated exposure", propagated_count,
        delta=f"{_prop_pct:.1f}% of exposed" if affected_nodes else None, delta_color="inverse",
        help="Facilities reached only via downstream graph traversal from a primary-exposure facility — "
             "exposed because they depend on it, not because they import from the affected region themselves.",
    )

    if _seed_status == "entity_matched":
        # Same root cause as the skipped "network impact" card above: material_nodes ==
        # affected_nodes exactly for facility-specific events, so every segment here would
        # show a tautological "N / N" (always 100%) rather than a real comparison.
        st.caption("Segment breakdown vs. the material sub-network is not applicable here — "
                   "see the note above.")
    else:
        st.markdown("**Potentially exposed facilities by supply chain stage** (within the material sub-network above)")
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
# "North American Upstream Mining Capacity" module hidden (2026-07-20) — Upstream-only
# scope made this near-always 0%/100% for import-driven events (see architecture.md /
# risk_model.md discussion), which read as confusing or contradictory next to the
# Supply Chain Exposure card above. gm is kept available in `result` for the LLM report
# (Capacity Impact section) even though the UI no longer surfaces it as its own card.
#
# "Risk level" (High/Medium/Low quantile card) removed 2026-08-03 — turned out
# compute_risk_tiers() never read the actual RiskScore values, only len(risk_scores),
# so its "proportion" was a fixed ~20/30/50 split of facility count for every event,
# regardless of how risky it actually was. Replaced everywhere (incl. the LLM prompts
# that ground Synthesis/Validation against hallucinated tier claims) with
# risk_score_stats — the real max/median of the RiskScore distribution.
gm = result.get("global_metrics", {})

# ── Top 3 + Map ───────────────────────────────────────────────────────────────

st.subheader("Top 3 Risk Facilities")

top3 = result.get("top3_facilities", [])

if top3:
    left, right = st.columns([1, 1])

    with left:
        rows = []
        for i, f in enumerate(top3, 1):
            rows.append({
                "Rank":      i,
                "Facility":  f.get("facility_name") or f["company"],
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
                "name": f"{f.get('facility_name') or f['company']} ({f['company']})",
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
elif result.get("seed_generation_status") in ("no_seed_found", "entity_ambiguous", "entity_non_material"):
    st.caption("No candidate facilities generated. Please refer to the system limitation notice above.")
else:
    st.caption("No high-risk facilities identified — this event matched the supported pattern "
               "but the resulting network had no facilities with usable risk data.")

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
