"""
SC Risk System — Streamlit UI
Run: streamlit run ui/app.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import streamlit as st

from pipeline.graph import stream_pipeline
from agents.state import RISK_SCORE_MAX_THEORETICAL, VULNERABILITY_WEIGHTS

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="SC Risk System",
    layout="wide",
)

# ── Sample inputs ─────────────────────────────────────────────────────────────

# Source URLs for the real articles below (2026-08-12) — kept alongside the text for
# attribution/traceability, shown to the user as a caption when a sample is loaded (see below).
SAMPLE_SOURCES = {
    "S1 — Cobalt export ban (DRC)":
        "https://www.stonex.com/en/insights/drc-bans-copper-and-cobalt-exports-what-do-we-need-to-know/",
    "S2 — Lithium mine suspension (China)":
        "https://finance.yahoo.com/news/catl-suspends-operations-major-lithium-094919144.html",
    "S3 — Facility fire (Panasonic, Kansas)":
        "https://blackout-news.de/en/news/panasonic-battery-fire-in-kansas-second-incident-in-three-months/",
}

SAMPLES = {
    # Real news articles (2026-08-12) — replaced short synthetic paragraphs with full, real,
    # sourced articles found via web search, so the demo reflects the same "long, real text"
    # robustness this pipeline was actually tested against (docs/open_issues.md P29-P41), not
    # a hand-tuned sentence that happens to match the system's own vocabulary. Source URLs in
    # SAMPLE_SOURCES above.
    "S1 — Cobalt export ban (DRC)": (
        "DRC Bans Copper and Cobalt Exports - What Do We Need to Know?\n"
        "By: Natalie Scott-Gray, Senior Metals Demand Analyst, EMEA and Asia region\n"
        "Thu, 06 Aug 2026, 12:42\n\n"
        "The Democratic Republic of Congo (DRC) has imposed an immediate ban on exports of "
        "copper and cobalt concentrates, marking a significant escalation in its strategy "
        "to promote domestic mineral processing and capture greater value from its mining "
        "sector.\n\n"
        "The measure, contained in a government order dated June 29 and reviewed by "
        "Reuters, was signed by Mines Minister Louis Kabamba Watum, Foreign Trade Minister "
        "Julien Paluku Kahongya, and Economy Minister Daniel Mukoko Samba. The order states "
        "that \"exports of copper and cobalt concentrates are prohibited with immediate "
        "effect,\" although the mines minister may grant one-year export waivers in cases "
        "deemed strategically important.\n\n"
        "Alongside the export ban, the government has introduced a new tax framework "
        "targeting economically significant mining by-products. Under the new regime, "
        "qualifying by-products will be taxed using a 55% valuation coefficient. Mining "
        "companies are also required to declare economically significant by-products in "
        "their export documentation immediately, ahead of the tax regime's implementation. "
        "The new tax measures will take effect following a three-month transition "
        "period.\n\n"
        "The DRC, the world's largest producer of cobalt and second-largest supplier of "
        "copper, is seeking to increase in-country processing capacity and boost state "
        "revenues from its vast mineral resources.\n\n"
        "What is the impact to the copper market?\n\n"
        "The DRC's newly announced ban on copper concentrate exports appears significant "
        "on paper, however, the practical impact on global copper concentrate flows is "
        "likely to be more nuanced. Indeed, while the DRC is a major producer of mine "
        "copper (~3.4Mt of copper in 2025, equivalent to around 15% of global mined copper "
        "supply), the majority of output comes from SX-EW cathode, leaving only ~605,000 "
        "Cu (18%) produced as concentrate.\n\n"
        "The importance of this distinction is that the market impact of the export ban is "
        "overwhelmingly focused on a single asset. Kamoa-Kakula accounted for "
        "approximately 389,000 of copper concentrate production in 2025, or around 64% of "
        "all DRC concentrate output. Every other major producer, including Tenke "
        "Fungurume, Kisanfu, Sicomines, Kamoto, Metalkol and Deziwa, predominantly "
        "produces refined cathode rather than concentrate. Consequently, whether "
        "Kamoa-Kakula receives an export waiver is likely to be the single largest swing "
        "factor determining how much DRC concentrate ultimately reaches the seaborne "
        "market.\n\n"
        "Looking at domestic processing capacity, the DRC currently smelts the equivalent "
        "of only 174,000t of copper concentrate production, implying that approximately "
        "71% of concentrate output would normally be available for export. This suggests "
        "the legislation has the potential to materially alter trade flows if fully "
        "enforced. In reality, however, this picture is already changing. During 2025, "
        "Kamoa-Kakula processed only ~15,000t of copper concentrate through its own "
        "smelter, meaning approximately 96% of its concentrate production was exported. "
        "However, the commissioning of its new 500,000tpa smelter in November 2025 is "
        "expected to materially reduce this reliance on exports as operations ramp "
        "towards nameplate capacity by year-end.\n\n"
        "The key takeaway is that the export ban may ultimately have little impact on "
        "global copper concentrate exports by the end of the year. The outcome will "
        "depend primarily on the speed at which Kamoa-Kakula's new smelter reaches "
        "steady-state production, whether export waivers are granted during the "
        "transition period, and the extent to which existing concentrate stockpiles can "
        "continue to be exported. As such, the policy itself may prove less important "
        "than the pace of domestic smelting capacity coming online."
    ),
    "S2 — Lithium mine suspension (China)": (
        "CATL Suspends Operations at Major Lithium Mine Due to Expired Licence\n"
        "By GlobalData — August 12, 2025 — 2 min read\n\n"
        "China's battery manufacturer CATL announced it has halted production at a "
        "significant lithium extraction facility in Yichun, Jiangxi province, after its "
        "operating permit lapsed. The company is currently working to restore the "
        "authorization needed to resume mining activities.\n\n"
        "The Yichun operation — the Jianxiawo mine, the largest in China's Yichun lithium "
        "hub — produces approximately 46,000 tonnes annually of lithium carbonate "
        "equivalent, representing roughly 3% of anticipated global output in 2025, and "
        "accounts for about 6% of global output on some estimates. This marks the first "
        "publicly reported suspension of its kind in the area. The company expects the "
        "closure to last approximately three months while seeking license renewal.\n\n"
        "Market reaction to the news proved dramatic. Lithium carbonate futures traded on "
        "the Guangzhou Futures Exchange jumped 8%, hitting daily limits. Stocks of lithium "
        "extraction firms responded strongly, with Australian and Chinese companies "
        "experiencing substantial gains — some surging over 10%.\n\n"
        "Chinese lithium producers showed particular strength, with Ganfeng Lithium "
        "rising more than 4% and Tianqi Lithium climbing approximately 11%. "
        "Australian-listed Liontown Resources led broader sector gains with a 25% "
        "increase, alongside significant rallies for Pilbara Minerals, IGO, Core Lithium, "
        "and Mineral Resources. Albemarle gained nearly 16%, Piedmont Lithium rose 18%, "
        "Lithium Americas climbed 14%, and Chile's SQM advanced 12%.\n\n"
        "Analysts note the abrupt cut could disrupt domestic supply chains while "
        "benefiting foreign producers. Morgan Stanley estimates that the planned 60,000 "
        "tonne surplus in 2025 may narrow, pushing prices upward.\n\n"
        "Lithium markets have remained turbulent since July, when China escalated "
        "enforcement against sector overcapacity, prompting speculation that supply-side "
        "interventions could constrain commodity output. The industry has grappled with "
        "persistent oversupply and slower-than-anticipated electric vehicle adoption. "
        "Prices have declined nearly 90% from 2022 peaks, forcing producers to reassess "
        "their capital programs.\n\n"
        "Following lithium oversupply conditions in 2023-2024, the market is positioned "
        "to tighten. Fastmarkets expects production cuts and accelerating consumption to "
        "narrow the oversupply gap. Lithium demand is set to surge as clean energy "
        "transitions and electric vehicle adoption accelerates, with China leading global "
        "demand in 2025."
    ),
    "S3 — Facility fire (Panasonic, Kansas)": (
        "Panasonic battery fire in Kansas – second incident in three months\n"
        "August 6, 2026\n\n"
        "A fire broke out at the Panasonic plant in De Soto, Kansas, around 2 a.m. on July "
        "29, 2026. The battery fire affected racks in a testing area after a water-flow "
        "alarm alerted emergency responders. Employees evacuated the building due to "
        "smoke, while the fire suppression system contained the flames to the racks. There "
        "were no injuries, and according to Panasonic, the day shift continued with only "
        "minor disruptions. The cause of the fire remained undetermined, though it marked "
        "the second battery-related incident since May.\n\n"
        "Panasonic battery fire contained to testing area\n\n"
        "Firefighters from the Northwest Consolidated Fire District arrived at Astra "
        "Parkway following the alarm. Crews discovered smoke inside the building and "
        "subsequently located burning battery racks in the testing area. They upgraded "
        "the incident to a structure fire, while the automatic suppression system "
        "contained the blaze.\n\n"
        "As a result, the Panasonic battery fire remained confined to the affected racks. "
        "Firefighters subsequently inspected the area and began mop-up operations. "
        "Panasonic announced an investigation but did not initially release details "
        "regarding the extent of the damage.\n\n"
        "Earlier incident generated heat and smoke\n\n"
        "Emergency crews had previously responded to a similar alarm at the same factory "
        "on May 5. On that occasion, a chemical reaction involving battery materials "
        "generated intense heat and smoke, prompting Panasonic to evacuate the building. "
        "However, the fire department confirmed there was no open flame and reported no "
        "injuries.\n\n"
        "In the May incident, the facility's fire protection systems also activated, "
        "limiting the consequences. Emergency personnel worked with Panasonic to remove "
        "the affected material, with several fire departments assisting in the operation. "
        "The recent incident differs significantly, however, as firefighters actually "
        "discovered burning battery racks this time.\n\n"
        "Multi-billion-dollar plant plays a key role in US manufacturing\n\n"
        "Panasonic opened the plant on July 14, 2025, following an investment of "
        "approximately four billion dollars. The factory produces cylindrical 2170 "
        "lithium-ion cells, and the company plans for an annual capacity of around 32 "
        "gigawatt-hours. Panasonic anticipates creating up to 4,000 direct jobs, with the "
        "site intended to strengthen the North American electric vehicle supply chain.\n\n"
        "Lithium-ion cells can release flammable gases when exposed to high temperatures "
        "or damage. US fire authorities also warn of chain reactions, explosions, and "
        "reignition after a battery fire has been extinguished. Following the Panasonic "
        "battery fire, the investigation must therefore determine why another "
        "battery-related incident occurred within a span of three months.\n\n"
        "Author: Blackout News"
    ),
    # Deliberately a different pattern from S1/S2 (material+region rule match) and S3
    # (named-company match): no material, no company, just a North American regional event —
    # exercises Strategy C (LLM-assisted regional fallback, network_agent.py), the one seeding
    # path the other three samples don't reach. See docs/open_issues.md P32/P40 — this exact
    # scenario (Michigan) was the real test case used to verify Strategy C end to end.
    "User query — Regional event (Michigan)": (
        "What happens to battery material and component manufacturing in Michigan if a "
        "severe winter storm knocks out power across the state for several days?"
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
    if chosen in SAMPLE_SOURCES:
        st.caption(f"Source: [{SAMPLE_SOURCES[chosen]}]({SAMPLE_SOURCES[chosen]})")

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

# Not relevant — simplified 2026-08-12 to two buckets: genuinely unrelated to supply chain risk
# ("Not relevant") vs. a real supply chain event this system just can't support analyzing yet
# ("Not supported"). relevant=false now only means one of these two things — an unsupported
# MATERIAL no longer sets relevant=false at all (intake_agent.py "UNSUPPORTED MATERIAL" rule,
# 2026-08-12 follow-up): that's a real event, so relevant honestly stays true, material="", and
# it flows through the normal pipeline to land on the "no_seed_found" branch below, same as any
# other event this system can't pin to a facility — no more relying on parsing LLM prose for a
# magic substring to tell the two apart. The only thing still short-circuited here as
# relevant=false is the bare-URL case (agents/intake_agent.py _is_bare_url) — genuinely no
# content to judge relevance from at all, not a real classified event.
if not result.get("relevant", False):
    _not_relevant_note = result.get("additional_events_note")
    if _not_relevant_note and "cannot fetch web pages" in _not_relevant_note:
        st.warning("⚠️ **Not supported by this system** — this looks like a real supply chain "
                    "event, but it's outside what this system can currently analyze.")
    else:
        st.warning("⚪ **Not relevant** — The input does not appear to concern battery supply chain risk.")
    st.stop()

# Structurally unseedable (2026-08-12) — mirrors pipeline/graph.py's route_after_intake: if
# intake found neither a region nor a named company, the pipeline stops right after intake
# (skips risk_assessment/network/etc entirely, saving 4-5 wasted LLM calls), so severity/
# risk_type/seed_generation_status were never computed and are None here, not just zero. Check
# the same two fields intake actually produced, not seed_generation_status (which doesn't exist
# in this state at all) — same "Not supported" message as the no_seed_found case below, just
# reached without ever running the rest of the pipeline.
if not result.get("region") and not result.get("mentioned_company"):
    st.warning("⚠️ **Not supported by this system** — this looks like a real supply chain "
               "event, but it's outside what this system can currently analyze. This is not a "
               "\"no risk\" finding.")
    st.stop()

st.divider()

# ── KPI row ──────────────────────────────────────────────────────────────────

k1, k2, k3, k4 = st.columns(4)
severity   = result.get("severity", 0)
risk_type  = result.get("risk_type", "")
material   = result.get("material", "")
region     = result.get("region", "")

k1.metric("Severity", f"{severity} / 5")
k2.metric("Risk Type", RISK_TYPE_LABEL.get(risk_type, risk_type))
k3.metric("Material", material.capitalize())
k4.metric("Region", region)

_seed_status = result.get("seed_generation_status")
# Same simplified two-bucket message as the "Not relevant" block above (2026-08-12) — these three
# statuses (logistics/no-region/unmodeled-region all collapse into "no_seed_found", plus
# entity_ambiguous, entity_non_material) all mean "a real event, but this system can't seed a
# network for it" — not a "no risk found" result. st.stop() so the rest of the page (Top 3,
# RiskScore breakdown, report) doesn't render a full page of misleading zeros under the warning.
if _seed_status in ("no_seed_found", "entity_ambiguous", "entity_non_material"):
    st.warning("⚠️ **Not supported by this system** — this looks like a real supply chain event, "
               "but it's outside what this system can currently analyze. This is not a "
               "\"no risk\" finding.")
    st.stop()
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
material_label = (result.get("material") or "").capitalize() or "Material"

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
    if _seed_status in ("entity_matched", "llm_fallback_used"):
        # Facility-specific (Strategy B) events: material is a free-text LLM label
        # (e.g. "battery cells"), not one of the six material_keywords this sub-network is
        # built from (cobalt/lithium/nickel/manganese/graphite/copper) — network_agent.py's
        # _material_match() therefore never matches anything, so alt_nodes is always empty
        # and material_nodes == affected_nodes exactly. Showing "N / N, 100% penetration"
        # here would be a tautology (denominator always equals numerator), not a real
        # measurement — same class of issue as the removed "Risk level" card. Skipped
        # entirely for this event type rather than displaying a misleading always-100%.
        # llm_fallback_used (Strategy C, 2026-08-06) shares the same issue: its seeds were
        # found via location, not material matching, so material is unreliable here
        # too — same caption applies.
        st.caption("Not applicable for a facility-specific or LLM-assisted-fallback event — "
                   "there is no material sub-network to compare against here (see help below).",
                   help="This module compares the event's reach against all facilities that "
                        "handle the same raw material anywhere in the network. Facility-"
                        "specific disruptions and Strategy C fallback events (like this one) "
                        "aren't matched by material, so that comparison denominator doesn't "
                        "exist — every facility this event reaches is already counted under "
                        "'Supply chain exposure' above.")
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
        help="Facilities that are themselves the seed of this event — matched either by import "
             "dependency on the affected region (a North American plant that imports from the "
             "disrupted region, e.g. a cobalt/DRC event), or by facility-name matching for a "
             "facility-specific event (e.g. a fire at a named company's own plant). NOT facilities "
             "physically located in the affected region — NAATBatt only covers North America, so "
             "every facility shown here, Primary or Propagated, is NA-based.",
    )
    pc2.metric(
        "Propagated exposure", propagated_count,
        delta=f"{_prop_pct:.1f}% of exposed" if affected_nodes else None, delta_color="inverse",
        help="Facilities reached only via downstream graph traversal from a primary-exposure facility — "
             "exposed because they depend on it, not because they import from the affected region themselves.",
    )

    if _seed_status in ("entity_matched", "llm_fallback_used"):
        # Same root cause as the skipped "network impact" card above: material_nodes ==
        # affected_nodes exactly for facility-specific/Strategy-C events, so every segment
        # here would show a tautological "N / N" (always 100%) rather than a real comparison.
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
            "**Primary** — the facility itself is the seed of this event: either it satisfies the "
            "import-dependency-on-the-affected-region rule (a regional material disruption, e.g. "
            "cobalt/DRC), or it was matched directly by facility name for a facility-specific event "
            "(e.g. a fire at a named company's own plant). Its path contains only the facility "
            "itself. Not a geographic match either way — NAATBatt only covers North America, so a "
            "'Primary' facility is always NA-based, even for an event originating abroad.\n\n"
            "**Propagated** — the facility is not itself a seed; "
            "it was reached through multi-tier supply chain traversal from a Primary seed."
        ),
    )
    for i, f in enumerate(top3, 1):
        if f.get("supply_path"):
            label = "Primary" if f.get("exposure_type") == "direct" else "🔀 Propagated"
            st.markdown(f"{i}. [{label}] {f['supply_path']}")

    with st.expander("Full RiskScore calculation (step by step)", expanded=False):
        st.caption(
            "Every number below is read directly from this run's computed state — nothing here "
            "is re-derived or approximated for display. RiskScore = Severity × TierWeight × "
            "Vulnerability × (1 − ResilienceDiscount), normalized to 0–100."
        )
        severity = result.get("severity", 0)
        w = VULNERABILITY_WEIGHTS
        for i, f in enumerate(top3, 1):
            imp   = f.get("import_dep_term", 0.0)
            sup   = f.get("supplier_conc_term", 0.0)
            ssd   = f.get("single_source_dependency_term", 0.0)
            lead  = f.get("lead_time_norm_term", 0.0)
            vuln  = f.get("vulnerability", 0.0)
            tw    = f.get("tier_weight", 0.0)
            rd    = f.get("resilience_discount", 0.0)
            raw   = f.get("risk_score", 0.0)
            norm  = f.get("risk_score_normalized", 0.0)
            name  = f.get("facility_name") or f["company"]
            st.markdown(f"**{i}. {name}**")
            st.code(
                f"Vulnerability = {w['import_dependency']:.2f}×ImportDep({imp:.2f}) "
                f"+ {w['supplier_concentration']:.2f}×SupplierConc({sup:.2f}) "
                f"+ {w['single_source_dependency']:.2f}×SingleSourceDep({ssd:.2f}) "
                f"+ {w['lead_time_norm']:.2f}×LeadTimeNorm({lead:.2f})\n"
                f"            = {w['import_dependency']*imp:.3f} + {w['supplier_concentration']*sup:.3f} "
                f"+ {w['single_source_dependency']*ssd:.3f} + {w['lead_time_norm']*lead:.3f}\n"
                f"            = {vuln:.3f}\n"
                f"\n"
                f"RiskScore   = Severity({severity}) × TierWeight({tw}) × Vulnerability({vuln:.3f}) "
                f"× (1 − ResilienceDiscount({rd:.2f}))\n"
                f"            = {severity} × {tw} × {vuln:.3f} × {1-rd:.2f}\n"
                f"            = {raw:.4f}\n"
                f"\n"
                f"Normalized  = {raw:.4f} / {RISK_SCORE_MAX_THEORETICAL} × 100 = {norm:.2f} / 100",
                language=None,
            )
else:
    # no_seed_found / entity_ambiguous / entity_non_material never reach here — st.stop() above
    # already exits the page for those. This is the "seeds matched but none had usable risk
    # data" case for rule_matched/entity_matched/llm_fallback_used.
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
        # Grouped by which agent actually writes each field (verified against each
        # agent's own `return {**state, ...}` — not just its module docstring, which can
        # drift from the code). Large per-facility fields (affected_nodes, alt_nodes,
        # facility_data, risk_scores, tier_weights, downstream_fanout) are already shown
        # elsewhere in the UI (Top-3 table, map, exposure cards) and excluded here.
        # supply_chain_paths and facility_data are per-facility dicts too (one entry per
        # affected facility, e.g. 128 for a broad S1-style event) — shown as a small sample
        # (_SAMPLE_N entries) rather than dumped in full, same reasoning as the exclusions.
        _SAMPLE_N = 3
        _STATE_GROUPS = {
            "Eingabe":          ["raw_input"],
            "Intake":           ["relevant", "trigger_type", "material", "region", "keywords",
                                  "mentioned_company", "mentioned_location"],
            "Risk Assessment":  ["severity", "risk_type", "origin_tier", "reason"],
            "Network":          ["seed_generation_status", "total_network_facilities", "strategy_c_trace"],
            "Data Retrieval":   ["betroffene_kapazitaet_pct", "alternative_kapazitaet_pct"],
            "Synthesis":        ["risk_report", "top3_facilities", "global_metrics",
                                  "risk_score_stats"],
            "Validation":       ["valid", "failure_type", "issues", "iteration"],
        }
        _SAMPLED_FIELDS = {"Network": "supply_chain_paths", "Data Retrieval": "facility_data"}
        _EXCLUDED = {"affected_nodes", "alt_nodes", "facility_data",
                     "risk_scores", "tier_weights", "downstream_fanout", "supply_chain_paths"}
        _grouped_keys = {k for keys in _STATE_GROUPS.values() for k in keys} | set(_SAMPLED_FIELDS.values())

        for group_name, keys in _STATE_GROUPS.items():
            group_data = {k: result[k] for k in keys if k in result}
            if group_data:
                st.markdown(f"**{group_name}**")
                st.json(group_data)
            sampled_key = _SAMPLED_FIELDS.get(group_name)
            if sampled_key and result.get(sampled_key):
                full = result[sampled_key]
                sample = dict(list(full.items())[:_SAMPLE_N])
                st.caption(f"`{sampled_key}` — Beispiel: {len(sample)} von {len(full)} Anlagen")
                st.json(sample)

        _rest = {k: v for k, v in result.items()
                 if k not in _grouped_keys and k not in _EXCLUDED}
        if _rest:
            st.markdown("**Sonstige**")
            st.json(_rest)
