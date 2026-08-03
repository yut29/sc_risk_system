"""
Synthesis Agent — RiskScore computation + LLM report generation

Input (from PipelineState):
  severity, risk_type, affected_material, affected_region, origin_tier, reason
  affected_nodes, alt_nodes, tier_weights, downstream_fanout
  facility_data, betroffene_kapazitaet_pct, alternative_kapazitaet_pct

Output (written to PipelineState):
  risk_report     : str             structured narrative report
  top3_facilities : list[Facility]  top 3 by risk_score_normalized
  risk_scores     : dict[str, float]
  global_metrics  : GlobalMetrics
"""

from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage

from agents.llm_utils import get_llm
from agents.state import (
    Facility,
    GlobalMetrics,
    HIGH_CONCENTRATION_MATERIALS,
    IMPORT_MATERIALS,
    KNOWN_MATERIALS,
    PipelineState,
    RISK_SCORE_MAX_THEORETICAL,
    USGS_GLOBAL_PRODUCTION_MT,
    VULNERABILITY_WEIGHTS,
)

load_dotenv(Path(__file__).parent.parent / ".env")

_llm: Optional[BaseChatModel] = None


def _get_llm() -> BaseChatModel:
    global _llm
    if _llm is None:
        _llm = get_llm(temperature=0.2)
    return _llm


# ── Deterministic: RiskScore calculation ─────────────────────────────────────

def _format_supply_path(path_ids: list[str], node_by_id: dict[str, dict]) -> str:
    """Turns a list of facility IDs into a readable 'Company (Segment) → ...' chain."""
    parts = []
    for pid in path_ids:
        n = node_by_id.get(pid)
        if n is None:
            continue
        parts.append(f"{n.get('company', pid)} ({n.get('segment', '')})")
    return " → ".join(parts)


def _compute_scores(state: PipelineState) -> tuple[dict[str, float], list[Facility]]:
    affected_nodes = state.get("affected_nodes", [])
    facility_data  = state.get("facility_data", {})
    tier_weights   = state.get("tier_weights", {})
    supply_chain_paths = state.get("supply_chain_paths", {})
    severity       = state.get("severity", 3)
    affected_material = (state.get("affected_material") or "").lower()

    node_by_id = {n["id"]: n for n in affected_nodes}
    risk_scores: dict[str, float] = {}
    facility_objects: list[dict] = []

    for node in affected_nodes:
        nid = node["id"]
        fd = facility_data.get(nid)
        if fd is None:
            continue

        tw = tier_weights.get(nid, 0.35)

        path_ids = supply_chain_paths.get(nid, [nid])
        supply_path = _format_supply_path(path_ids, node_by_id)
        exposure_type = "direct" if len(path_ids) == 1 else "propagated"

        # 2026-08-03 (corrected same day — see state.py's KNOWN_MATERIALS comment for the
        # full rationale): propagated facilities inherit ImportDep/SupplierConcentration from
        # the EVENT's own affected_material classification, NOT the facility's own
        # material_keywords (which is right for a PRIMARY-exposure facility that directly
        # handles the material, but empty/irrelevant for a PROPAGATED one several tiers
        # downstream — e.g. Tesla/Toyota/Lucid Motors carry no "cobalt" keyword themselves,
        # yet ARE exposed to a cobalt event through their battery supply chain).
        #
        # An earlier version of this fix just set both to True unconditionally for any
        # propagated facility — too blunt: it discarded the real, literature-backed material
        # classification for the common S1/S2-style case (cobalt genuinely IS import-
        # dependent/concentrated — that's a fact worth keeping, not overriding), and it
        # doesn't even correctly express what ImportDep is defined to mean ("is NA import-
        # reliant on THIS material" — a material-level fact, not a per-facility one).
        #
        # Now: look up the event's own material against the same IMPORT_MATERIALS/
        # HIGH_CONCENTRATION_MATERIALS tables every other facility is classified against. Only
        # falls back to the precautionary "unknown -> True" default when affected_material
        # isn't one of the tracked materials at all (KNOWN_MATERIALS) — e.g. a Strategy B
        # facility-specific event's free-text label like "battery cells", which was never
        # given a real classification either way.
        if exposure_type == "propagated":
            if affected_material in KNOWN_MATERIALS:
                import_dep_term = 1.0 if affected_material in IMPORT_MATERIALS else 0.0
                supplier_conc_term = 1.0 if affected_material in HIGH_CONCENTRATION_MATERIALS else 0.0
            else:
                import_dep_term = 1.0
                supplier_conc_term = 1.0
        else:
            import_dep_term = float(fd["import_dep"])
            supplier_conc_term = float(fd["supplier_concentration"])

        # 2026-08-03 (swap, same day as the CapacityShare<->SingleSourceDependency direction
        # discussion): Vulnerability now uses SingleSourceDependency (self-related: does THIS
        # facility depend on very few of its own suppliers), not CapacityShare (moved to
        # ResilienceDiscount below — a system-recoverability question, not a self-fragility
        # one). No unknown-data default subtlety here: single_source_dependency is already 0.0
        # (not "unknown", just "no known suppliers in the NA graph, e.g. Upstream root nodes")
        # whenever it can't be computed, which is the correct low-vulnerability-contribution
        # value for this term regardless of tier.
        ssd_term = float(fd["single_source_dependency"])
        vulnerability = (
            VULNERABILITY_WEIGHTS["import_dependency"]  * import_dep_term
            + VULNERABILITY_WEIGHTS["supplier_concentration"] * supplier_conc_term
            + VULNERABILITY_WEIGHTS["single_source_dependency"] * ssd_term
            + VULNERABILITY_WEIGHTS["lead_time_norm"]     * float(fd["lead_time_norm"])
        )

        # ResilienceDiscount (2026-08-03, swapped to CapacityShare-based): a facility that
        # commands most of its tier's capacity has no one to cover for it if it goes down ->
        # small discount; a small player has plenty of alternative capacity elsewhere -> large
        # discount. Unknown/inapplicable cases already default to capacity_share=1.0
        # (worst-case) in data_retrieval_agent.py, which naturally yields resilience_discount=
        # 0.0 (no discount) here — same precautionary direction as the "unknown -> max" rule,
        # just arriving there via capacity_share's own default rather than a separate guard.
        rd = float(fd.get("resilience_discount", 0.0))
        raw_score = severity * tw * vulnerability * (1 - rd)
        normalized = round(raw_score / RISK_SCORE_MAX_THEORETICAL * 100, 2)

        risk_scores[nid] = normalized
        facility_objects.append({
            "id":                 nid,
            "company":            node.get("company", ""),
            "facility_name":      node.get("facility_name", ""),
            "segment":            node.get("segment", ""),
            "city":               node.get("city", ""),
            "country":            node.get("country", ""),
            "state":              node.get("state", ""),
            "latitude":           node.get("latitude", 0.0),
            "longitude":          node.get("longitude", 0.0),
            "risk_score":         raw_score,
            "risk_score_normalized": normalized,
            "tier_weight":        tw,
            "vulnerability":      round(vulnerability, 4),
            "resilience_discount": rd,
            "supply_path":        supply_path,
            "exposure_type":      exposure_type,
        })

    # Deduplicate by physical site (company + city + state) before ranking Top 3.
    # NAATBatt records one row per company/material-product-line, so a multi-material
    # company at a single site (e.g. Targray in Kirkland, QC) can occupy several
    # facility_id rows with identical risk inputs — without this, Top 3 could show
    # the same site 2-3x instead of surfacing genuinely distinct high-risk sites.
    best_by_site: dict[tuple, dict] = {}
    for obj in facility_objects:
        site_key = (obj["company"], obj["city"], obj["state"])
        existing = best_by_site.get(site_key)
        if existing is None or obj["risk_score_normalized"] > existing["risk_score_normalized"]:
            best_by_site[site_key] = obj

    top3: list[Facility] = sorted(
        best_by_site.values(), key=lambda x: x["risk_score_normalized"], reverse=True
    )[:3]

    return risk_scores, top3


# ── Deterministic: GlobalMetrics ──────────────────────────────────────────────

def _compute_global_metrics(state: PipelineState) -> GlobalMetrics:
    material       = state.get("affected_material", "")
    affected_nodes = state.get("affected_nodes", [])
    alt_nodes      = state.get("alt_nodes", [])
    facility_data  = state.get("facility_data", {})

    def upstream_cap(nodes):
        total = 0.0
        for n in nodes:
            if n.get("segment") == "Upstream":
                fd = facility_data.get(n["id"])
                if fd:
                    total += float(fd.get("capacity", 0.0))
        return total

    affected_up_cap = upstream_cap(affected_nodes)
    alt_up_cap      = upstream_cap(alt_nodes)

    na_pct   = state.get("betroffene_kapazitaet_pct", 0.0)
    alt_na   = state.get("alternative_kapazitaet_pct", 0.0)

    usgs_total = USGS_GLOBAL_PRODUCTION_MT.get(material, 0.0)
    if usgs_total > 0:
        global_affected = round(affected_up_cap / usgs_total * 100, 2)
        global_alt      = round(alt_up_cap      / usgs_total * 100, 2)
    else:
        global_affected = 0.0
        global_alt      = 0.0

    return GlobalMetrics(
        betroffene_kapazitaet_na_pct=na_pct,
        alternative_kapazitaet_na_pct=alt_na,
        betroffene_kapazitaet_global_pct=global_affected,
        alternative_kapazitaet_global_pct=global_alt,
    )


# ── LLM: report narrative ─────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the Synthesis Agent for a Battery Supply Chain Risk System.
Write as a senior supply chain risk analyst producing a professional report for other analysts —
factual and precise, but with genuine expert judgment, not a template fill-in.

Report format (use these exact section headers):

## Risk Event
One paragraph: what happened, where, when. End with: "Source: NAATBatt facility data + LLM analysis."

## Affected Material & Supply Chain Tier
One sentence: material, origin tier, risk type.

## Capacity Impact (North America)
These percentages measure NAATBatt-tracked NORTH AMERICAN domestic mining/production capacity
only — NAATBatt has no coverage outside the US/Canada/Mexico, so a foreign mine or refinery (e.g.
a DRC cobalt mine, an Australian lithium operation) is never itself a node in this graph. They are
NOT a measurement of the disrupted facility's own output or its share of world production — do not
treat them as verifying, contradicting, or needing to reconcile with any percentage the source event
text itself claims (e.g. "8% of global output"). That claim is unverified input, not something this
system computes or can confirm — if you mention it, attribute it to the source ("as reported") and
do not imply the capacity metrics below confirm or refute it.
This metric ONLY applies when origin_tier is "Upstream" — it measures raw-material mining/
production capacity, which structurally cannot be affected by a disruption starting at any other
tier (Midstream-BGM/Cell/Downstream events can never propagate backward to Upstream in this graph).
- If origin_tier is "Upstream": two bullet points using the provided percentages —
  Affected NA capacity: X%; Alternative NA capacity: X%; Affected global capacity (USGS basis): X%
  [only if > 0]. A 0% figure here is expected and normal whenever the disruption originates at a
  facility outside NAATBatt's North American coverage (i.e. most real Upstream events) — it means
  "no NA-domestic Upstream capacity was affected", not "this event has no impact". Do not flag or
  apologize for a 0% Upstream figure as a gap or contradiction.
- If origin_tier is NOT "Upstream": do not report 0% as if it were a measurement. Instead write one
  line: "Not applicable — this event originates at the {origin_tier} tier, not raw-material
  production; NA capacity-share metrics only apply to Upstream events." Do not attempt to reconcile
  or explain away a 0% figure here — there is nothing to reconcile, it is simply out of scope.

## Supply Chain Exposure Analysis
Using the exposure summary provided in the context, explain — don't just restate the numbers:
- How many facilities/companies are **potentially exposed** (reachable in the supply chain graph
  from this event) in total, and how that breaks down by supply chain tier. Use "potentially exposed"
  or "reachable", not "affected" — reachability is a structural/graph property, not a severity claim.
- The difference between Primary exposure (North American facilities that themselves import the
  affected material from the affected region — an import-dependency match, NOT a geographic one;
  they are not located in the affected region, NAATBatt only covers North America) and Propagated
  exposure (exposed only because they sit downstream of a Primary-exposure facility in the graph).
- The highest/median RiskScore gap in the exposure summary is the key signal, not the raw counts —
  state plainly that most reachable facilities score far below the highest exposure once TierWeight
  distance-discounting is applied, and only the handful of facilities near the highest score (see
  Top 3 below) warrant real attention. This is the key distinction: reachable ≠ high-risk.
- Only if origin_tier is "Upstream" AND Affected NA capacity is 0% or low while many facilities are
  potentially exposed: explicitly reconcile this — mining/Upstream capacity being untouched does not
  mean there is no risk, point to the high-scoring Midstream/Downstream exposure (see the highest/
  median gap) as the actual risk driver. Skip this reconciliation entirely for non-Upstream events —
  see Capacity Impact above.

## Top 3 Risk Facilities
For each facility (numbered 1–3), lead with the specific facility name, not just the parent company —
one company can own several distinctly-named facilities (e.g. "Lundin Mining" has both "Eagle Mine"
and "Humboldt mill"; using only the company name would make them indistinguishable):
**[Facility Name] ([Company]) — [City, State/Country]** (Segment, RiskScore: X/100, Exposure: Primary|Propagated)
- One sentence explaining WHY this facility is high risk (import dependency, single source, capacity share).
  Do not imply the facility is physically located in the affected region — Primary exposure means it
  imports from there, nothing more.
- If Exposure=Propagated, briefly trace the supply path provided (which upstream event/facility it is
  downstream of) — this is the causal chain, state it explicitly rather than skipping it.
- Recommended action: [concrete mitigation step]

## Risk Synthesis
A multi-paragraph expert assessment (this is the section that earns your "senior analyst" title —
go beyond restating earlier sections). Cover each of these angles explicitly, in your own words:
- **Geopolitical / market structure**: what does supplier concentration and import dependency for
  this material mean here — is this a one-off event or a symptom of a structurally fragile supply base?
- **Network propagation**: what does the Primary-vs-Propagated exposure split say about how this
  disruption travels through the supply chain — is the risk concentrated at one tier or does it cascade?
- **Resilience**: given the alternative/remaining capacity figures, how much slack does the system
  actually have — is that slack usable in practice (same tier, comparable lead time) or theoretical?
- **Severity in context**: why does this event warrant its severity rating — what would make it worse
  or better, and over what time horizon?
- **Strategic recommendation**: 1-2 concrete, prioritized actions for a supply chain risk manager reading
  this — distinguish immediate mitigation from longer-term structural fixes (e.g. diversification).

Rules:
- Be factual. Only reference data provided in the context — do not invent facts, but you MAY draw
  reasonable inferences and judgments from the data provided (that is the point of this section).
- Do not invent company relationships or capacities not in the input.
- Prioritize interpretation over restating numbers — a reader should understand *why* the numbers mean what they mean.
- Keep the report under 750 words.
"""

USER_PROMPT_TEMPLATE = """Generate the risk report based on this data:

EVENT:
- Risk type: {risk_type}
- Severity: {severity}/5
- Material: {affected_material}
- Region: {affected_region}
- Origin tier: {origin_tier}
- Assessment: {reason}

CAPACITY METRICS (Upstream-tier only — meaningless/always 0% for a non-Upstream origin_tier;
see Capacity Impact section rules above):
- Affected NA upstream capacity: {betroffene_na}%
- Alternative NA upstream capacity: {alternative_na}%
- Affected global capacity (USGS): {betroffene_global}%

SUPPLY CHAIN EXPOSURE SUMMARY:
{exposure_summary}

TOP 3 RISK FACILITIES (highest-scoring, not necessarily close to the highest RiskScore in the
distribution above; Exposure = Direct means Primary exposure — the facility itself matched the
event via import dependency, NOT that it is physically located in the affected region; Propagated means
it was only reached via downstream supply-chain traversal — SupplyPath shows that chain):
{top3_text}
"""


def _format_top3(top3: list[Facility]) -> str:
    lines = []
    for i, f in enumerate(top3, 1):
        lines.append(
            f"{i}. {f['company']} | {f['facility_name']} | {f['segment']} | "
            f"{f['city']}, {f['state']}, {f['country']} | "
            f"RiskScore={f['risk_score_normalized']}/100 | "
            f"Vulnerability={f['vulnerability']:.3f} | "
            f"TierWeight={f['tier_weight']} | "
            f"ResilienceDiscount={f['resilience_discount']:.2f} | "
            f"Exposure={f['exposure_type']} | "
            f"SupplyPath={f['supply_path']}"
        )
    return "\n".join(lines) if lines else "No high-risk facilities identified."


def _compute_exposure_summary(state: PipelineState) -> str:
    """
    Segment breakdown + direct/propagated counts across all affected_nodes (not just Top 3).
    Only called when seed_generation_status == "rule_matched" — the "no_seed_found" case is
    short-circuited in run_synthesis_agent() before this is ever invoked (see
    _no_seed_found_report).
    """
    affected_nodes = state.get("affected_nodes", [])
    supply_chain_paths = state.get("supply_chain_paths", {})

    segment_counts: dict[str, int] = {}
    for n in affected_nodes:
        seg = n.get("segment", "unknown")
        segment_counts[seg] = segment_counts.get(seg, 0) + 1

    direct_count = sum(
        1 for n in affected_nodes
        if len(supply_chain_paths.get(n["id"], [n["id"]])) == 1
    )
    propagated_count = len(affected_nodes) - direct_count
    manufacturers = len({n.get("company", "") for n in affected_nodes})
    seg_text = ", ".join(f"{seg}={cnt}" for seg, cnt in sorted(segment_counts.items()))

    return (
        f"Total affected facilities: {len(affected_nodes)} across {manufacturers} companies "
        f"(by segment: {seg_text or 'none'}). "
        f"Primary exposure (North American facilities that themselves import the material from the "
        f"affected region — an import-dependency match, not a geographic one): {direct_count}. "
        f"Propagated exposure (reached only via downstream supply chain links from a Primary-exposure "
        f"facility): {propagated_count}."
    )


def _compute_risk_score_stats(risk_scores: dict[str, float]) -> dict[str, float]:
    """
    Replaces the old compute_risk_tiers() High/Medium/Low bucketing (removed
    2026-08-03) — that function only ever read len(risk_scores), never the actual
    score values, so its "20% High / 30% Medium / 50% Low" output was a fixed
    proportion of facility COUNT, constant for every event regardless of how risky
    it actually was. Reports the real shape of the distribution instead: max (the
    worst single exposure) and median (the typical facility among all reachable
    ones). RiskScore_normalized is heavily right-skewed in practice — TierWeight
    discounts most facilities that aren't at the event's origin tier hard
    (0.6/0.35/0.15) — so max >> median is the expected, informative signal ("a
    small number of facilities are genuinely exposed, most reachable ones are not"),
    not an artifact to bucket away.
    """
    if not risk_scores:
        return {"max": 0.0, "median": 0.0}
    values = sorted(risk_scores.values())
    n = len(values)
    mid = n // 2
    median = values[mid] if n % 2 else (values[mid - 1] + values[mid]) / 2
    return {"max": round(values[-1], 2), "median": round(median, 2)}


# ── Main agent function ────────────────────────────────────────────────────────

def _no_seed_found_report(state: PipelineState) -> PipelineState:
    """
    Deterministic short-circuit — no LLM call. When no seed was found, letting the LLM
    free-write the full report template is unsafe: observed in testing (2026-07-20) that
    even with explicit instructions, the LLM would correctly flag the limitation in the
    "Supply Chain Exposure Analysis" section, then contradict itself two sections later
    in "Risk Synthesis" by analyzing the 0% capacity figures as if they were a
    real finding ("the system has 0.0% affected capacity, which suggests there is no
    slack..."). A fixed, non-generative notice guarantees no self-contradiction and
    costs no API call / rate-limit budget.
    """
    material = state.get("affected_material", "")
    region   = state.get("affected_region", "")
    report = (
        "## System Limitation Notice\n\n"
        f"This event (material: {material or 'unknown'}, region: {region or 'unknown'}, "
        f"risk type: {state.get('risk_type', 'unknown')}, severity: {state.get('severity', '?')}/5) "
        "could not be classified into either event type this system currently supports: (1) an "
        "import-dependent raw-material disruption originating abroad (matched via "
        "`import_dependency` + `import_origin_region`), or (2) a facility-specific disruption at a "
        "named company (matched via company-name/location resolution).\n\n"
        "**This is NOT a \"no North American exposure\" finding.** It means this event didn't match "
        "either supported pattern. Likely reasons: a domestic tier-wide issue without a foreign "
        "origin, or a logistics/port event — neither of which the current matching strategies can "
        "detect. See `docs/architecture.md`, \"Bekannte Grenzen des Seeding-Mechanismus\", for "
        "supported vs. unsupported event types.\n\n"
        "**Recommendation: this case requires manual review.** Do not treat the absence of results "
        "as evidence of no risk.\n\n"
        f"Assessment reason (from Risk Assessment Agent): {state.get('reason', 'n/a')}\n\n"
        "Source: NAATBatt facility data — no matching facility found."
    )
    return {
        **state,
        "risk_report":      report,
        "top3_facilities":  [],
        "risk_scores":      {},
        "global_metrics":   _compute_global_metrics(state),
        "risk_score_stats": {"max": 0.0, "median": 0.0},
    }


def _entity_ambiguous_report(state: PipelineState) -> PipelineState:
    """
    Deterministic short-circuit for seed_generation_status="entity_ambiguous" — a
    company was recognized in the text, but it has multiple facilities in the graph
    and the mentioned location (if any) wasn't enough to resolve it to exactly one.
    Same rationale as _no_seed_found_report: no LLM call, no risk of self-contradiction.
    """
    company = state.get("mentioned_company", "unknown")
    location = state.get("mentioned_location")
    location_clause = (
        f'the mentioned location ("{location}") did not' if location
        else "no location was mentioned to"
    )
    report = (
        "## System Limitation Notice — Ambiguous Facility Reference\n\n"
        f"The text named a company (\"{company}\") that has multiple facilities in the current "
        f"network, and {location_clause} "
        "narrow this down to exactly one.\n\n"
        "**This system does not guess which facility is meant.** Picking one arbitrarily could "
        "misattribute the disruption to the wrong site. See `docs/architecture.md`, \"Vorschlag: "
        "Multi-Strategie Seed Generator\", for the entity-matching design (deliberately scoped to "
        "not include company-alias mapping or automatic disambiguation).\n\n"
        "**Recommendation: this case requires manual review** — identify the specific facility "
        f"(e.g. city/state) and re-run the analysis with that detail included.\n\n"
        f"Assessment reason (from Risk Assessment Agent): {state.get('reason', 'n/a')}\n\n"
        "Source: NAATBatt facility data — company matched, facility ambiguous."
    )
    return {
        **state,
        "risk_report":      report,
        "top3_facilities":  [],
        "risk_scores":      {},
        "global_metrics":   _compute_global_metrics(state),
        "risk_score_stats": {"max": 0.0, "median": 0.0},
    }


def _entity_non_material_report(state: PipelineState) -> PipelineState:
    """
    Deterministic short-circuit for seed_generation_status="entity_non_material" — the
    named company resolved to exactly one facility, but that facility is a mechanical/
    safety/BMS component supplier (marked `non_active_material`, docs/open_issues.md
    P16), not a processor of any risk material this system tracks (cobalt, lithium,
    nickel, graphite, manganese). Same rationale as the other short-circuits: no LLM
    call, no risk of a fabricated material-flow narrative for a facility that doesn't
    handle raw materials at all.
    """
    company = state.get("mentioned_company", "unknown")
    report = (
        "## System Limitation Notice — Non-Material Facility\n\n"
        f"The text named a company (\"{company}\") that was resolved to exactly one facility in "
        "the current network, but this facility is a mechanical/safety/component supplier "
        "(e.g. adhesives, thermal systems, cell lid assemblies, BMS modules) — it does not process "
        "any of the raw materials this system tracks (cobalt, lithium, nickel, graphite, "
        "manganese).\n\n"
        "**This system models raw-material supply-chain risk, not general component/BOM "
        "disruption.** A disruption at this facility may still be operationally significant, but it "
        "falls outside this system's material-flow risk model and no propagation is computed.\n\n"
        f"Assessment reason (from Risk Assessment Agent): {state.get('reason', 'n/a')}\n\n"
        "Source: NAATBatt facility data — company matched, facility is not material-active."
    )
    return {
        **state,
        "risk_report":      report,
        "top3_facilities":  [],
        "risk_scores":      {},
        "global_metrics":   _compute_global_metrics(state),
        "risk_score_stats": {"max": 0.0, "median": 0.0},
    }


def run_synthesis_agent(state: PipelineState) -> PipelineState:
    """
    LangGraph-compatible node function.
    Computes RiskScores deterministically, then generates narrative via LLM.
    """
    status = state.get("seed_generation_status")
    if status == "no_seed_found":
        return _no_seed_found_report(state)
    if status == "entity_ambiguous":
        return _entity_ambiguous_report(state)
    if status == "entity_non_material":
        return _entity_non_material_report(state)

    risk_scores, top3 = _compute_scores(state)
    global_metrics    = _compute_global_metrics(state)
    score_stats       = _compute_risk_score_stats(risk_scores)

    top3_text        = _format_top3(top3)
    exposure_summary = (
        f"{_compute_exposure_summary(state)} "
        f"RiskScore distribution among affected facilities: highest={score_stats['max']}/100, "
        f"median={score_stats['median']}/100 — the gap between the two is the actual signal "
        f"(most reachable facilities are far below the highest exposure once TierWeight distance-"
        f"discounting applies; a small highest/median gap would mean broad, not narrow, exposure)."
    )

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=USER_PROMPT_TEMPLATE.format(
            risk_type        = state.get("risk_type", ""),
            severity         = state.get("severity", 3),
            affected_material= state.get("affected_material", ""),
            affected_region  = state.get("affected_region", ""),
            origin_tier      = state.get("origin_tier", ""),
            reason           = state.get("reason", ""),
            betroffene_na    = global_metrics["betroffene_kapazitaet_na_pct"],
            alternative_na   = global_metrics["alternative_kapazitaet_na_pct"],
            betroffene_global= global_metrics["betroffene_kapazitaet_global_pct"],
            exposure_summary = exposure_summary,
            top3_text        = top3_text,
        )),
    ]

    report = _get_llm().invoke(messages).content.strip()

    return {
        **state,
        "risk_report":      report,
        "top3_facilities":  top3,
        "risk_scores":      risk_scores,
        "global_metrics":   global_metrics,
        "risk_score_stats": score_stats,
    }
