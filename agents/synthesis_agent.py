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

import json
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_groq import ChatGroq

from agents.state import (
    Facility,
    GlobalMetrics,
    PipelineState,
    RISK_SCORE_MAX_THEORETICAL,
    USGS_GLOBAL_PRODUCTION_MT,
    VULNERABILITY_WEIGHTS,
)

load_dotenv(Path(__file__).parent.parent / ".env")

_llm: Optional[ChatGroq] = None


def _get_llm() -> ChatGroq:
    global _llm
    if _llm is None:
        _llm = ChatGroq(
            model="llama-3.1-8b-instant",
            api_key=os.environ["GROQ_API_KEY"],
            temperature=0.2,
            request_timeout=20,
            max_retries=1,
        )
    return _llm


# ── Deterministic: RiskScore calculation ─────────────────────────────────────

def _compute_scores(state: PipelineState) -> tuple[dict[str, float], list[Facility]]:
    affected_nodes = state.get("affected_nodes", [])
    facility_data  = state.get("facility_data", {})
    tier_weights   = state.get("tier_weights", {})
    severity       = state.get("severity", 3)

    risk_scores: dict[str, float] = {}
    facility_objects: list[dict] = []

    for node in affected_nodes:
        nid = node["id"]
        fd = facility_data.get(nid)
        if fd is None:
            continue

        tw = tier_weights.get(nid, 0.35)

        vulnerability = (
            VULNERABILITY_WEIGHTS["import_dependency"]  * float(fd["import_dep"])
            + VULNERABILITY_WEIGHTS["supplier_concentration"] * float(fd["supplier_concentration"])
            + VULNERABILITY_WEIGHTS["capacity_share"]     * float(fd["capacity_share"])
            + VULNERABILITY_WEIGHTS["lead_time_norm"]     * float(fd["lead_time_norm"])
        )

        rd = float(fd.get("resilience_discount", 0.0))
        raw_score = severity * tw * vulnerability * (1 - rd)
        normalized = round(raw_score / RISK_SCORE_MAX_THEORETICAL * 100, 2)

        risk_scores[nid] = normalized
        facility_objects.append({
            "id":                 nid,
            "company":            node.get("company", ""),
            "facility_name":      node.get("facility_name", ""),
            "segment":            node.get("segment", ""),
            "country":            node.get("country", ""),
            "state":              node.get("state", ""),
            "latitude":           node.get("latitude", 0.0),
            "longitude":          node.get("longitude", 0.0),
            "risk_score":         raw_score,
            "risk_score_normalized": normalized,
            "tier_weight":        tw,
            "vulnerability":      round(vulnerability, 4),
            "resilience_discount": rd,
        })

    top3: list[Facility] = sorted(
        facility_objects, key=lambda x: x["risk_score_normalized"], reverse=True
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
Write a concise, structured risk report in English for supply chain analysts.

Report format (use these exact section headers):

## Risk Event
One paragraph: what happened, where, when. End with: "Source: NAATBatt facility data + LLM analysis."

## Affected Material & Supply Chain Tier
One sentence: material, origin tier, risk type.

## Capacity Impact (North America)
Two bullet points using the provided percentages:
- Affected NA capacity: X%
- Alternative NA capacity: X%
- Affected global capacity (USGS basis): X% [only if > 0]

## Top 3 High-Risk Facilities
For each facility (numbered 1–3):
**[Company] — [City, State/Country]** (Segment, RiskScore: X/100)
- One sentence explaining WHY this facility is high risk (import dependency, single source, capacity share).
- Recommended action: [concrete mitigation step]

## Overall Risk Assessment
Severity X/5 — [one sentence summary]. [One sentence on system resilience based on alternative capacity].

Rules:
- Be factual. Only reference data provided in the context.
- Do not invent company relationships or capacities not in the input.
- Keep the report under 400 words.
"""

USER_PROMPT_TEMPLATE = """Generate the risk report based on this data:

EVENT:
- Risk type: {risk_type}
- Severity: {severity}/5
- Material: {affected_material}
- Region: {affected_region}
- Origin tier: {origin_tier}
- Assessment: {reason}

CAPACITY METRICS:
- Affected NA upstream capacity: {betroffene_na}%
- Alternative NA upstream capacity: {alternative_na}%
- Affected global capacity (USGS): {betroffene_global}%

TOP 3 HIGH-RISK FACILITIES:
{top3_text}

Total affected nodes: {n_affected} facilities across the supply chain.
"""


def _format_top3(top3: list[Facility]) -> str:
    lines = []
    for i, f in enumerate(top3, 1):
        lines.append(
            f"{i}. {f['company']} | {f['facility_name']} | {f['segment']} | "
            f"{f['city'] if 'city' in f else ''}, {f['state']}, {f['country']} | "
            f"RiskScore={f['risk_score_normalized']}/100 | "
            f"Vulnerability={f['vulnerability']:.3f} | "
            f"TierWeight={f['tier_weight']} | "
            f"ResilienceDiscount={f['resilience_discount']:.2f}"
        )
    return "\n".join(lines) if lines else "No high-risk facilities identified."


# ── Main agent function ────────────────────────────────────────────────────────

def run_synthesis_agent(state: PipelineState) -> PipelineState:
    """
    LangGraph-compatible node function.
    Computes RiskScores deterministically, then generates narrative via LLM.
    """
    risk_scores, top3 = _compute_scores(state)
    global_metrics    = _compute_global_metrics(state)

    top3_text = _format_top3(top3)

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
            top3_text        = top3_text,
            n_affected       = len(state.get("affected_nodes", [])),
        )),
    ]

    report = _get_llm().invoke(messages).content.strip()

    return {
        **state,
        "risk_report":     report,
        "top3_facilities": top3,
        "risk_scores":     risk_scores,
        "global_metrics":  global_metrics,
    }
