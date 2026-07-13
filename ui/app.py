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

# ── Capacity metrics ──────────────────────────────────────────────────────────

gm = result.get("global_metrics", {})
st.subheader("Capacity Impact")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Affected — NA Upstream",  f"{gm.get('betroffene_kapazitaet_na_pct', 0):.1f}%")
c2.metric("Alternative — NA Upstream", f"{gm.get('alternative_kapazitaet_na_pct', 0):.1f}%")
c3.metric("Affected — Global (USGS)", f"{gm.get('betroffene_kapazitaet_global_pct', 0):.2f}%")
c4.metric("Alternative — Global",    f"{gm.get('alternative_kapazitaet_global_pct', 0):.2f}%")

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
                "Location":  f"{f.get('state', '')}, {f['country']}",
                "Score":     f"{f['risk_score_normalized']:.1f} / 100",
                "Vuln.":     f"{f['vulnerability']:.3f}",
                "Tier Wt.":  f"{f['tier_weight']}",
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
