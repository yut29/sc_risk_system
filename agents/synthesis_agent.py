"""
Synthesis Agent — RiskScore computation + LLM report generation

Input (from PipelineState):
  severity, risk_type, material, region, origin_tier, reason
  affected_nodes, alt_nodes, tier_weights, downstream_fanout
  facility_data, betroffene_kapazitaet_pct, alternative_kapazitaet_pct
  raw_input (2026-08-11, "Source Context" section, see below)

Output (written to PipelineState):
  risk_report     : str             structured narrative report
  top3_facilities : list[Facility]  top 3 by risk_score_normalized
  risk_scores     : dict[str, float]
  global_metrics  : GlobalMetrics

raw_input added (2026-08-11): until now this agent worked purely from computed graph/score
data, never re-reading the original article — so context a human analyst would naturally pick
up from the source (historical precedent, analyst commentary, stated recovery timelines) was
silently dropped even when the article said it outright. Added a "Source Context" report
section that reads raw_input for exactly this — instructed to only report what the source text
actually states (attributed), and to explicitly say "no additional context" rather than pad
with invented generic industry background when the source is short/terse. This is a different
risk surface than risk_assessment_agent's raw_input usage (which anchors severity/type/tier
judgment) — here it's purely supplementary narrative, added only if the source actually
supports it.
"""

import re
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
        # Was temperature=0.2 (undocumented, no rationale in history) until 2026-08-12: this
        # was the only agent in the pipeline not pinned to temperature=0, and it was the only
        # agent where a real bug turned out to be non-deterministic-generation-shaped — P36
        # (Top-3 header format flipped between two styles across repeated identical runs) and
        # P39 (Source Context misattributed another material's context to this report's
        # material, reproduced 3/3 times) both trace directly to this setting, not to a prompt
        # wording problem (two separate prompt fixes for P39 failed to help). Switched to 0 to
        # test whether reliability improves — see docs/open_issues.md P41 for the before/after.
        _llm = get_llm(temperature=0)
    return _llm


# ── Deterministic: RiskScore calculation ─────────────────────────────────────

def _format_supply_path(path_ids: list[str], node_by_id: dict[str, dict]) -> str:
    """
    Turns a list of facility IDs into a readable 'Company — Facility Name (Segment) → ...'
    chain. Facility name is required, not just company (2026-08-12 fix): several companies
    in this dataset operate multiple distinct physical sites (e.g. Vale Canada has 10,
    BASF Toda America has several) — a company-only path can't tell you which specific
    facility is at each hop, even though that data (facility_name) is already on the node.
    """
    parts = []
    for pid in path_ids:
        n = node_by_id.get(pid)
        if n is None:
            continue
        company = n.get("company", pid)
        facility_name = n.get("facility_name", "")
        label = f"{company} — {facility_name}" if facility_name else company
        parts.append(f"{label} ({n.get('segment', '')})")
    return " → ".join(parts)


def _compute_scores(state: PipelineState) -> tuple[dict[str, float], list[Facility]]:
    affected_nodes = state.get("affected_nodes", [])
    facility_data  = state.get("facility_data", {})
    tier_weights   = state.get("tier_weights", {})
    supply_chain_paths = state.get("supply_chain_paths", {})
    severity       = state.get("severity", 3)
    material = (state.get("material") or "").lower()

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
        # the EVENT's own material classification, NOT the facility's own
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
        # falls back to the precautionary "unknown -> True" default when material
        # isn't one of the tracked materials at all (KNOWN_MATERIALS) — e.g. a Strategy B
        # facility-specific event's free-text label like "battery cells", which was never
        # given a real classification either way.
        if exposure_type == "propagated":
            if material in KNOWN_MATERIALS:
                import_dep_term = 1.0 if material in IMPORT_MATERIALS else 0.0
                supplier_conc_term = 1.0 if material in HIGH_CONCENTRATION_MATERIALS else 0.0
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
            # Individual terms (2026-08-07, P22) — raw 0-1 values BEFORE VULNERABILITY_WEIGHTS
            # weighting, kept alongside the combined `vulnerability` above instead of being
            # discarded once summed, so a report/UI can show which term actually drove the score.
            "import_dep_term":               round(import_dep_term, 4),
            "supplier_conc_term":             round(supplier_conc_term, 4),
            "single_source_dependency_term":  round(ssd_term, 4),
            "lead_time_norm_term":            round(float(fd["lead_time_norm"]), 4),
            "resilience_discount": rd,
            "supply_path":        supply_path,
            "exposure_type":      exposure_type,
            # (P37) real facility-specific context for differentiated Top-3 justifications —
            # was already sitting in facility_data unused, see FacilityData docstring.
            "capacity":           fd.get("capacity", 0.0),
            "product":            fd.get("product", ""),
            "product_type":       fd.get("product_type", ""),
            "brief_profile":      fd.get("brief_profile", ""),
            "production_units":   fd.get("production_units", ""),
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
    material       = state.get("material", "")
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

## Source Context
The ORIGINAL SOURCE TEXT is provided below in the context — read it for anything beyond the
structured event fields (risk_type/severity/material/region/reason) that a human analyst would
find relevant: historical precedent ("the third such disruption in 18 months"), analyst/expert
commentary or quotes, market reaction, stated recovery timelines, or similar context explicitly
present in the source text.
- Only report what the source text actually says. Attribute it ("the article notes...", "according
  to [named source/official/analyst] quoted in the source..."). Never invent a historical pattern,
  expert opinion, or typical-case generalization that is not literally present in the source text —
  this is exactly the kind of unverifiable claim this system must not fabricate.
- MATERIAL SCOPE CHECK (apply this BEFORE writing anything): this report is scoped to ONE material
  only — see the "Material" field in the EVENT section below. If the source text covers more than
  one material, some passages may concern a DIFFERENT material than the one this report is about
  (e.g. the source discusses both copper and cobalt; this report's Material field says cobalt).
  Silently skip any such passage — do not mention it, do not summarize it, and above all do not
  reword it to be about the report's material instead (e.g. never turn a statement about copper
  concentrate exports into one about cobalt concentrate exports — that is fabricating a claim the
  source never made). Only use passages that are genuinely about the report's own material.
- After applying that filter: if anything usable remains, report it, attributed ("the article
  notes...", "according to [source] quoted in the article..."). Never invent a historical pattern,
  expert opinion, or generalization not literally present in the source text.
- If nothing usable remains (source is short, purely factual, or every relevant passage was about a
  different material per the filter above), write EXACTLY one line and nothing else: "No additional
  context provided in the source material." Never write both real content AND this fallback line —
  it is one or the other.

## Affected Material & Supply Chain Tier
One sentence: material, origin tier, risk type.

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

## Top 3 Risk Facilities
For each facility (numbered 1–3), lead with the specific facility name, not just the parent company —
one company can own several distinctly-named facilities (e.g. "Lundin Mining" has both "Eagle Mine"
and "Humboldt mill"; using only the company name would make them indistinguishable):
**[Facility Name] ([Company]) — [City, State/Country]** (Segment, RiskScore: X/100, Exposure: Primary|Propagated)
- One sentence explaining WHY this facility is high risk — using the four Vulnerability sub-terms
  provided for each facility (ImportDep, SupplierConc, SingleSourceDep, LeadTimeNorm, with their
  weights) to identify which factor(s) actually drive this facility's risk, but describe them in
  PLAIN LANGUAGE for a reader — never write the raw term names (e.g. never write "ImportDep" or
  "SupplierConc" literally in the sentence). Instead describe what each elevated term actually
  means in context: ImportDep → depends on imported supply for this material; SupplierConc → the
  global supplier base for this material is concentrated among very few producers; SingleSourceDep
  → this specific facility itself has few alternate suppliers of its own to fall back on;
  LeadTimeNorm → recovery/replacement would take a long time. Do not just restate "it imports the
  material" for every entry — that's true of all three and explains nothing about why THIS one
  differs from the other two. Say which factor(s) are actually elevated for this specific facility
  and which are not, in your own words (e.g. "this facility's risk comes mainly from having very
  few alternate suppliers of its own to fall back on, rather than the broader import exposure it
  shares with the other two entries here"). If two facilities have nearly identical RiskScores, say
  so and explain briefly why (e.g. same tier-distance from the disruption, but a different mix of
  underlying factors) — do not paper over it with interchangeable boilerplate. Do not imply the
  facility is physically located in the affected region — Primary exposure means it imports from
  there, nothing more.
- Ground the justification in what this facility ACTUALLY makes and how big it is — Product, ProductType,
  Capacity, and CompanyProfile are provided for each facility; use them to say something a reader
  couldn't get from the Vulnerability numbers alone (e.g. what specific product line is exposed, whether
  this is a large or small operation relative to its capacity figure). If a field is "unknown", don't
  mention it — do not guess or pad with generic industry description to fill the gap.
- If Exposure=Propagated, briefly trace the supply path provided (which upstream event/facility it is
  downstream of) — this is the causal chain, state it explicitly rather than skipping it.
- Recommended action: [concrete mitigation step, targeted at the specific driving factor named above
  in plain language, not the raw term names — a risk driven by concentrated global suppliers needs
  supplier diversification, a risk driven by slow replacement/recovery needs inventory or buffer
  stock, these are not interchangeable — AND, where the Product/Capacity/ProductType data actually
  supports it, make the action specific to what this facility makes and how big it is (e.g. a
  small-capacity single-product-line facility has fewer substitution options than a large
  multi-product one — say so if the data shows it). Do not let the action regress into the same
  generic "diversify suppliers" phrasing you just avoided in the justification above — the actual
  driving factor AND the facility's real product/scale should both be visible in what you recommend,
  not just in the sentence above it.]

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
  or better? Only discuss a time horizon if the source text actually gives one (recovery estimate,
  stated timeline) — otherwise say the timeline is unspecified rather than estimating one.
- **Strategic recommendation**: 1-2 concrete, prioritized actions for a supply chain risk manager reading
  this — distinguish immediate mitigation from longer-term structural fixes (e.g. diversification).

Rules:
- Be factual. Only reference data provided in the context — do not invent facts, but you MAY draw
  reasonable inferences and judgments from the data provided (that is the point of this section).
- Do not invent company relationships or capacities not in the input.
- NEVER STATE A SPECIFIC DURATION, QUANTITY, OR DATE THE SOURCE TEXT DOES NOT ACTUALLY GIVE (found
  in review, 2026-08-17 — repeatedly observed inventing a plausible-sounding recovery timeframe
  like "1-3 months" or "weeks to months" for disruptions whose source text explicitly says the
  duration is unknown, then using that invented number to justify the severity rating). This
  applies everywhere in the report, not just Source Context: the "Severity in context" paragraph
  below, the Risk Event summary — anywhere a specific number/timeframe could be written. If the
  source text does not state how long something will last, how much of something is affected, or
  when something will happen, say so plainly ("no timeline was given in the source", "duration is
  unconfirmed") instead of supplying a specific-sounding estimate. A vague source does not license
  a confident, specific-sounding report — an honest "unknown" is correct, a plausible-sounding
  invented number is not, even if it seems like a reasonable guess.
- Prioritize interpretation over restating numbers — a reader should understand *why* the numbers mean what they mean.
- Keep the report under 750 words.
"""

USER_PROMPT_TEMPLATE = """Generate the risk report based on this data:

ORIGINAL SOURCE TEXT (for the "Source Context" section only — do not use this to override or
re-derive any of the structured fields below, those are already final):
---
{raw_input}
---

EVENT:
- Risk type: {risk_type}
- Severity: {severity}/5
- Material: {affected_material}
- Region: {affected_region}
- Origin tier: {origin_tier}
- Assessment: {reason}

SUPPLY CHAIN EXPOSURE SUMMARY:
{exposure_summary}

TOP 3 RISK FACILITIES (highest-scoring, not necessarily close to the highest RiskScore in the
distribution above; each entry already states its Exposure as Primary or Propagated below — copy
that word as-is into the report header, do not invent a different one. Primary means the facility
itself matched the event via import dependency, NOT that it is physically located in the affected
region; Propagated means it was only reached via downstream supply-chain traversal — SupplyPath
shows that chain):
{top3_text}
"""


def _format_top3(top3: list[Facility]) -> str:
    lines = []
    for i, f in enumerate(top3, 1):
        capacity_text = (
            f"{f['capacity']:.0f} {f['production_units']}" if f["capacity"] else "unknown"
        )
        lines.append(
            f"{i}. {f['company']} | {f['facility_name']} | {f['segment']} | "
            f"{f['city']}, {f['state']}, {f['country']} | "
            f"RiskScore={f['risk_score_normalized']}/100 | "
            f"Vulnerability={f['vulnerability']:.3f} "
            f"(ImportDep[30%]={f['import_dep_term']:.2f}, "
            f"SupplierConc[30%]={f['supplier_conc_term']:.2f}, "
            f"SingleSourceDep[25%]={f['single_source_dependency_term']:.2f}, "
            f"LeadTimeNorm[15%]={f['lead_time_norm_term']:.2f}) | "
            f"TierWeight={f['tier_weight']} | "
            f"ResilienceDiscount={f['resilience_discount']:.2f} | "
            # Display word, not the raw internal exposure_type value (found in review,
            # 2026-08-17): this used to write f['exposure_type'] verbatim ("direct"/
            # "propagated"), then relied on a prompt instruction telling the LLM to
            # translate "direct" to "Primary" in its OUTPUT — unreliable, the LLM often
            # just echoed the "direct" it was shown here instead, which validation_agent.py
            # then flagged as wrong. Showing the already-correct display word here means
            # there's nothing left to translate — the LLM only has to copy it, and it now
            # matches ui/app.py's own "Primary"/"Propagated" labels (e.g. line ~501), which
            # were never "direct"/"propagated" to begin with.
            f"Exposure={'Primary' if f['exposure_type'] == 'direct' else 'Propagated'} | "
            f"SupplyPath={f['supply_path']} | "
            f"Product={f['product'] or 'unknown'} | "
            f"ProductType={f['product_type'] or 'unknown'} | "
            f"Capacity={capacity_text} | "
            f"CompanyProfile={f['brief_profile'] or 'unknown'}"
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

def _system_limitation_report(state: PipelineState, title: str, body: str, source_note: str) -> PipelineState:
    """
    Shared builder for all three deterministic "can't analyze this" short-circuits below
    (consolidated 2026-08-17 — these were three separate ~35-line copies of the same
    report shape and the same 5-key return dict; a future PipelineState field added to
    one copy but not the others was exactly the kind of drift risk this collapses away).

    No LLM call in any of these — letting the LLM free-write the full report template
    here is unsafe: observed in testing (2026-07-20) that even with explicit
    instructions, the LLM would correctly flag the limitation in "Supply Chain Exposure
    Analysis", then contradict itself two sections later in "Risk Synthesis" by
    analyzing the 0% capacity figures as if they were a real finding ("the system has
    0.0% affected capacity, which suggests there is no slack..."). A fixed,
    non-generative notice guarantees no self-contradiction and costs no API call.
    """
    report = (
        f"## {title}\n\n"
        f"{body}\n\n"
        f"Assessment reason (from Risk Assessment Agent): {state.get('reason', 'n/a')}\n\n"
        f"Source: {source_note}"
    )
    return {
        **state,
        "risk_report":      report,
        "top3_facilities":  [],
        "risk_scores":      {},
        "global_metrics":   _compute_global_metrics(state),
        "risk_score_stats": {"max": 0.0, "median": 0.0},
    }


def _no_seed_found_report(state: PipelineState) -> PipelineState:
    """seed_generation_status="no_seed_found" — see _system_limitation_report for why this
    is a fixed notice, not an LLM call."""
    material = state.get("material", "")
    region   = state.get("region", "")
    body = (
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
        "as evidence of no risk."
    )
    return _system_limitation_report(
        state, "System Limitation Notice", body, "NAATBatt facility data — no matching facility found."
    )


def _entity_ambiguous_report(state: PipelineState) -> PipelineState:
    """seed_generation_status="entity_ambiguous" — a company was recognized in the text,
    but it has multiple facilities in the graph and the mentioned location (if any)
    wasn't enough to resolve it to exactly one. See _system_limitation_report."""
    company = state.get("mentioned_company", "unknown")
    location = state.get("mentioned_location")
    location_clause = (
        f'the mentioned location ("{location}") did not' if location
        else "no location was mentioned to"
    )
    body = (
        f"The text named a company (\"{company}\") that has multiple facilities in the current "
        f"network, and {location_clause} "
        "narrow this down to exactly one.\n\n"
        "**This system does not guess which facility is meant.** Picking one arbitrarily could "
        "misattribute the disruption to the wrong site. See `docs/architecture.md`, \"Vorschlag: "
        "Multi-Strategie Seed Generator\", for the entity-matching design (deliberately scoped to "
        "not include company-alias mapping or automatic disambiguation).\n\n"
        "**Recommendation: this case requires manual review** — identify the specific facility "
        "(e.g. city/state) and re-run the analysis with that detail included."
    )
    return _system_limitation_report(
        state, "System Limitation Notice — Ambiguous Facility Reference", body,
        "NAATBatt facility data — company matched, facility ambiguous."
    )


def _entity_non_material_report(state: PipelineState) -> PipelineState:
    """seed_generation_status="entity_non_material" — the named company resolved to
    exactly one facility, but that facility is a mechanical/safety/BMS component
    supplier (marked `non_active_material`, docs/open_issues.md P16), not a processor
    of any risk material this system tracks. See _system_limitation_report."""
    company = state.get("mentioned_company", "unknown")
    body = (
        f"The text named a company (\"{company}\") that was resolved to exactly one facility in "
        "the current network, but this facility is a mechanical/safety/component supplier "
        "(e.g. adhesives, thermal systems, cell lid assemblies, BMS modules) — it does not process "
        "any of the raw materials this system tracks (cobalt, lithium, nickel, graphite, "
        "manganese).\n\n"
        "**This system models raw-material supply-chain risk, not general component/BOM "
        "disruption.** A disruption at this facility may still be operationally significant, but it "
        "falls outside this system's material-flow risk model and no propagation is computed."
    )
    return _system_limitation_report(
        state, "System Limitation Notice — Non-Material Facility", body,
        "NAATBatt facility data — company matched, facility is not material-active."
    )


# Mirrors intake_agent.py's SYSTEM_PROMPT "material must be one of" list — same set validation_
# agent.py uses for its Source Context cross-material check (kept as a separate local copy there
# rather than imported, see that file's comment for why).
_ALL_TRACKED_MATERIALS: frozenset[str] = frozenset({
    "cobalt", "lithium", "nickel", "manganese", "graphite", "copper",
    "pvdf", "copper_foil", "aluminum_foil", "separator",
})


def _sanitize_source_context(report: str, state: PipelineState) -> str:
    """
    Deterministic safety net for the Source Context section (2026-08-12, docs/open_issues.md
    P39 follow-up): two separate prompt-level attempts to stop the LLM from misattributing a
    DIFFERENT material's context to the material this report is about both failed — not
    occasionally, but reliably (3/3 repeated runs on a real DRC copper+cobalt test case). Retrying
    generation (what SEVERE validation failures normally trigger) doesn't help either, since this
    is a systematic bias, not random noise — verified: still failed after hitting
    MAX_VALIDATION_ITERATIONS. Same lesson as the 2026-08-02 _structural_checks redesign, applied
    one step further: don't just detect the unreliable LLM judgment in validation, replace it with
    a deterministic action here so the pipeline doesn't need a retry (or a human) to get a correct
    report. If the OTHER material named in additional_events_note appears anywhere in the Source
    Context section, the whole section is replaced with the safe fallback line rather than trusting
    whatever the LLM wrote — losing that section's content is a smaller loss than shipping a
    misattributed claim.
    """
    other_note = (state.get("additional_events_note") or "").lower()
    material = state.get("material", "").lower()
    if not other_note or not material:
        return report

    mentioned_other_materials = {
        m for m in _ALL_TRACKED_MATERIALS - {material} if m in other_note
    }
    if not mentioned_other_materials:
        return report

    section_re = re.compile(
        r"(##\s*Source Context.*?\n)(.*?)(?=\n##\s|\Z)", re.DOTALL | re.IGNORECASE
    )
    match = section_re.search(report)
    if not match:
        return report

    section_body = match.group(2)
    if any(m in section_body.lower() for m in mentioned_other_materials):
        report = section_re.sub(
            r"\1No additional context provided in the source material.\n",
            report,
            count=1,
        )
    return report


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
            raw_input        = state.get("raw_input", ""),
            risk_type        = state.get("risk_type", ""),
            severity         = state.get("severity", 3),
            affected_material= state.get("material", ""),
            affected_region  = state.get("region", ""),
            origin_tier      = state.get("origin_tier", ""),
            reason           = state.get("reason", ""),
            exposure_summary = exposure_summary,
            top3_text        = top3_text,
        )),
    ]

    report = _get_llm().invoke(messages).content.strip()
    report = _sanitize_source_context(report, state)

    return {
        **state,
        "risk_report":      report,
        "top3_facilities":  top3,
        "risk_scores":      risk_scores,
        "global_metrics":   global_metrics,
        "risk_score_stats": score_stats,
    }
