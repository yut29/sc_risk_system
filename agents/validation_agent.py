"""
Validation Agent — Source verification + hallucination check

Input (from PipelineState):
  risk_report, top3_facilities, reason
  affected_material, affected_region, risk_type, severity
  affected_nodes, facility_data
  iteration (current retry count)

Output (written to PipelineState):
  valid        : bool
  failure_type : "minor" | "severe" | None
  issues       : list[str]
  iteration    : int  (incremented)

Retry routing (handled by LangGraph pipeline):
  minor  → re-run Synthesis Agent  (missing source, unverified entity)
  severe → re-run from Risk Assessment Agent  (wrong material, inconsistent classification)
  Max iterations: 2  (defined in state.MAX_VALIDATION_ITERATIONS)

Three check layers (2026-08-02 redesign — see docs/agent_prompts_zh.md for the reasoning):
  1. _deterministic_checks — pure lookups (Top-3 IDs in affected_nodes, source citation, material
     mention).
  2. _structural_checks    — regex-extracts the RIGIDLY TEMPLATED Capacity Impact and Top-3 Facility
     sections (see synthesis_agent.py's format spec) and compares them against computed state values.
     Moved out of the LLM prompt because the validation LLM applied these numeric/tier-logic rules
     unreliably across reruns — they're pure arithmetic/lookup, not reading comprehension, so code
     does them exactly instead of "reliably enough".
  3. LLM checks            — narrowed to what code genuinely can't verify: coherence of the free-form
     Risk Synthesis prose, whether Top-3 justifications are specific vs generic boilerplate,
     and narrative-level hallucination in prose that isn't part of the fixed-format sections.
"""

import re
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage

from agents.llm_utils import get_llm, invoke_json
from agents.state import (
    FailureType,
    MAX_VALIDATION_ITERATIONS,
    PipelineState,
)
from agents.synthesis_agent import _compute_exposure_summary

load_dotenv(Path(__file__).parent.parent / ".env")

_llm: Optional[BaseChatModel] = None


def _get_llm() -> BaseChatModel:
    global _llm
    if _llm is None:
        # Un-pinned from Groq back to the default FAU provider (2026-08-02, same day):
        # the Groq pin was a workaround for this agent's multi-condition numeric/tier
        # rules being applied unreliably — but those rules have since moved out of the
        # LLM prompt entirely into _structural_checks() (pure regex+lookup, no model
        # involved). The LLM's remaining job (narrative coherence/completeness) is
        # narrow enough that model choice shouldn't matter much, so default back to
        # FAU to avoid Groq's 6000 TPM rate limit, which repeated pipeline runs hit
        # even with only this one agent pinned to it.
        _llm = get_llm(temperature=0)
    return _llm


# ── Deterministic checks ──────────────────────────────────────────────────────

def _deterministic_checks(state: PipelineState) -> list[str]:
    """
    Fast, rule-based checks that don't need an LLM.
    Returns a list of issue strings (empty = all passed).
    """
    issues: list[str] = []
    top3        = state.get("top3_facilities", [])
    report      = state.get("risk_report", "")
    af_material = state.get("affected_material", "").lower()
    affected_ids = {n["id"] for n in state.get("affected_nodes", [])}
    facility_data = state.get("facility_data", {})

    # 1. Top-3 facilities must exist in affected_nodes (NAATBatt-verifiable)
    for f in top3:
        if f["id"] not in affected_ids:
            issues.append(
                f"SEVERE: Facility '{f['company']}' (id={f['id']}) is not in the "
                f"affected_nodes list — not verifiable via NAATBatt."
            )

    # 2. Top-3 facilities must have facility_data (capacity data was retrieved)
    for f in top3:
        if f["id"] not in facility_data:
            issues.append(
                f"MINOR: No facility_data for '{f['company']}' (id={f['id']}) — "
                f"risk score may be unreliable."
            )

    # 3. Report must contain a source reference
    if "source:" not in report.lower() and "naatbatt" not in report.lower():
        issues.append(
            "MINOR: Risk report does not contain a source citation. "
            "Expected 'Source: NAATBatt facility data' or similar."
        )

    # 4. Affected material must appear in the report
    if af_material and af_material not in report.lower():
        issues.append(
            f"SEVERE: Affected material '{af_material}' does not appear in the risk report — "
            f"possible material misclassification."
        )

    return issues


# ── Structural checks (2026-08-02) ──────────────────────────────────────────────
#
# The Synthesis Agent's report follows a rigid template for two sections (Capacity
# Impact, Top 3 Risk Facilities — see synthesis_agent.py's SYSTEM_PROMPT format
# spec), so the numbers/fields in those sections can be extracted with regex and
# compared directly against the computed state values — no LLM judgment needed.
# This replaces several LLM-prompt rules (tier-propagation direction, capacity
# applicability, NA-scope reconciliation) that the validation LLM applied
# unreliably across reruns/models. The "Origin tier mismatch" rule is dropped
# entirely rather than moved here: network_agent.py's traversal is a directed BFS
# forward from the seed tier only, so a facility appearing upstream of origin_tier
# is structurally impossible — nothing to check.

_TOP3_HEADER_RE = re.compile(
    r"\*\*(?P<company>.+?)\s+—\s+.+?\*\*\s*"
    r"\((?P<segment>[^,]+),\s*RiskScore:\s*(?P<score>[\d.]+)/100,\s*Exposure:\s*(?P<exposure>\w+)\)"
)

_CAPACITY_TOLERANCE_PCT = 0.6  # allow for LLM rounding/formatting drift


def _structural_checks(state: PipelineState) -> list[str]:
    """
    Regex-extracts the rigidly-templated Capacity Impact and Top-3 Facility
    sections and compares them against the deterministic state values. Falls
    back to a MINOR "format deviation" issue if the expected pattern isn't found
    at all, rather than silently skipping the check.
    """
    issues: list[str] = []
    report = state.get("risk_report", "")
    origin_tier = state.get("origin_tier", "")
    global_metrics = state.get("global_metrics", {})
    top3 = state.get("top3_facilities", [])

    # ── Capacity Impact (North America) ──────────────────────────────────────
    section_match = re.search(
        r"##\s*Capacity Impact.*?\n(.*?)(?=\n##\s|\Z)", report, re.DOTALL | re.IGNORECASE
    )
    cap_section = section_match.group(1) if section_match else ""

    if origin_tier == "Upstream":
        expected_fields = {
            "Affected NA capacity":    global_metrics.get("betroffene_kapazitaet_na_pct"),
            "Alternative NA capacity": global_metrics.get("alternative_kapazitaet_na_pct"),
        }
        for label, expected_val in expected_fields.items():
            if expected_val is None:
                continue
            m = re.search(re.escape(label) + r".*?([\d.]+)\s*%", cap_section, re.IGNORECASE)
            if not m:
                issues.append(
                    f"MINOR: Capacity Impact section is missing the expected '{label}' figure "
                    f"for an Upstream event — format deviation from the report template."
                )
                continue
            reported_val = float(m.group(1))
            if abs(reported_val - float(expected_val)) > _CAPACITY_TOLERANCE_PCT:
                issues.append(
                    f"MINOR: Capacity Impact section states {label}={reported_val}%, but the "
                    f"computed value is {expected_val}% — numeric mismatch."
                )
    else:
        if "not applicable" not in cap_section.lower():
            issues.append(
                "MINOR: Capacity Impact section for a non-Upstream event doesn't state "
                "'Not applicable' as the report template requires."
            )

    # ── Top 3 Risk Facilities ────────────────────────────────────────────────
    if top3:
        matches = list(_TOP3_HEADER_RE.finditer(report))
        if len(matches) != len(top3):
            issues.append(
                f"MINOR: Report lists {len(matches)} Top-3 facility header(s) but "
                f"{len(top3)} were provided — count mismatch."
            )
        for i, f in enumerate(top3):
            if i >= len(matches):
                break
            m = matches[i]
            reported_company = m.group("company").strip()
            if (f["company"].lower() not in reported_company.lower()
                    and reported_company.lower() not in f["company"].lower()):
                issues.append(
                    f"SEVERE: Top-3 entry #{i + 1} reports company '{reported_company}', which "
                    f"does not match the provided facility '{f['company']}' — possible "
                    f"hallucinated entity."
                )
                continue  # other field checks are meaningless if the company itself is wrong

            if f["segment"].lower() != m.group("segment").strip().lower():
                issues.append(
                    f"MINOR: Top-3 entry for '{f['company']}' reports segment "
                    f"'{m.group('segment').strip()}', but the provided segment is '{f['segment']}'."
                )

            reported_score = float(m.group("score"))
            if abs(reported_score - float(f["risk_score_normalized"])) > _CAPACITY_TOLERANCE_PCT:
                issues.append(
                    f"MINOR: Top-3 entry for '{f['company']}' reports RiskScore "
                    f"{reported_score}/100, but the computed value is "
                    f"{f['risk_score_normalized']}/100."
                )

            expected_exposure = "primary" if f["exposure_type"] == "direct" else "propagated"
            if m.group("exposure").strip().lower() != expected_exposure:
                issues.append(
                    f"MINOR: Top-3 entry for '{f['company']}' reports Exposure="
                    f"'{m.group('exposure').strip()}', but the provided exposure_type is "
                    f"'{f['exposure_type']}' (expected '{expected_exposure}')."
                )

    return issues


# ── LLM checks ────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the Validation Agent for a Battery Supply Chain Risk System.
Numeric/structural facts — Capacity Impact percentages, Top-3 facility company/segment/RiskScore/
Exposure fields, source citation, material mention — are already checked deterministically by code
before you see this report (see _structural_checks / _deterministic_checks in validation_agent.py).
Do NOT re-derive or second-guess those numbers; your job is limited to the parts of the report that
require reading comprehension, not arithmetic or lookup.

Check for:
1. COHERENCE — Is the reasoning in the "Risk Synthesis" section logically self-consistent,
   and does it actually follow from the facts stated earlier in the report (rather than contradicting
   them)?
2. QUALITATIVE COMPLETENESS — Does each Top-3 facility justification say something specific to that
   facility (its import dependency, single-source risk, capacity share), or is it generic boilerplate
   that could be copy-pasted onto any other facility?
3. NARRATIVE HALLUCINATION — Does the free-form prose (Risk Synthesis, Risk Event paragraph,
   Supply Chain Exposure Analysis) assert any specific fact, relationship, or figure not supported by
   the context below? This is about invented claims in the prose, not about the already-verified
   Capacity Impact / Top-3 numbers.

Respond ONLY with a JSON object — no explanation, no markdown, no code blocks.

JSON schema:
{
  "valid": true | false,
  "issues": ["<issue1>", "<issue2>", ...],
  "failure_type": null | "minor" | "severe"
}

Failure type rules:
- null   → report is valid (issues list must be empty)
- "minor"  → fixable by re-running Synthesis Agent (vague justification, weak narrative coherence)
- "severe" → requires re-running Risk Assessment Agent (the narrative asserts something that
  fundamentally contradicts the risk_type/severity/material classification itself)
"""

USER_PROMPT_TEMPLATE = """Validate this risk report's narrative sections against the provided context.
Numeric facts (Capacity Impact %, Top-3 fields, source citation) are already checked by code —
focus only on the free-form prose: Risk Event paragraph, Supply Chain Exposure Analysis, Top-3
justification quality, and Risk Synthesis.

CONTEXT:
- Risk type: {risk_type}
- Severity: {severity}/5
- Affected material: {affected_material}
- Affected region: {affected_region}
- Origin tier: {origin_tier}
- Assessment reason: {reason}
- Total affected facilities: {affected_count}
- RiskScore distribution: highest={risk_score_max}/100, median={risk_score_median}/100
- Exposure summary (ground truth for any company/facility counts mentioned in the prose):
  {exposure_summary}

TOP 3 FACILITIES PROVIDED TO SYNTHESIS AGENT:
{top3_summary}

GENERATED RISK REPORT:
---
{risk_report}
---
"""


def _format_top3_summary(top3: list) -> str:
    lines = []
    for f in top3:
        lines.append(
            f"- {f['company']} | {f['facility_name']} | {f['segment']} | "
            f"score={f['risk_score_normalized']}/100 | "
            f"country={f['country']}"
        )
    return "\n".join(lines) if lines else "None"


# ── Main agent function ────────────────────────────────────────────────────────

def run_validation_agent(state: PipelineState) -> PipelineState:
    """
    LangGraph-compatible node function.
    Runs deterministic checks first, then LLM checks.
    Returns updated state with valid/failure_type/issues/iteration.
    """
    iteration = state.get("iteration", 0) + 1

    # The "no_seed_found" / "entity_ambiguous" / "entity_non_material" reports (see
    # synthesis_agent.py's _no_seed_found_report / _entity_ambiguous_report /
    # _entity_non_material_report) are fixed, non-generative notices — there's no risk
    # claim, entity reference, or capacity figure to validate. Judging them against
    # normal-report expectations produces spurious complaints (observed: "missing Top-3
    # justification") that would trigger a wasted retry loop back to Risk Assessment
    # Agent, which would just reproduce the same result deterministically.
    if state.get("seed_generation_status") in ("no_seed_found", "entity_ambiguous", "entity_non_material"):
        return {
            **state,
            "valid":        True,
            "failure_type": None,
            "issues":       [],
            "iteration":    iteration,
        }

    # Hard stop: exceeded max iterations → accept report as-is with warning
    if iteration > MAX_VALIDATION_ITERATIONS:
        return {
            **state,
            "valid":        True,
            "failure_type": None,
            "issues":       ["Max iterations reached — report accepted with caveats."],
            "iteration":    iteration,
        }

    all_issues: list[str] = []

    # ── Step 1: deterministic + structural checks ────────────────────────────
    det_issues = _deterministic_checks(state)
    all_issues.extend(det_issues)
    struct_issues = _structural_checks(state)
    all_issues.extend(struct_issues)

    # ── Step 2: LLM checks (narrative-only, see SYSTEM_PROMPT) ───────────────
    score_stats = state.get("risk_score_stats", {"max": 0.0, "median": 0.0})
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=USER_PROMPT_TEMPLATE.format(
            risk_type        = state.get("risk_type", ""),
            severity         = state.get("severity", 3),
            affected_material= state.get("affected_material", ""),
            affected_region  = state.get("affected_region", ""),
            origin_tier      = state.get("origin_tier", ""),
            reason           = state.get("reason", ""),
            affected_count   = len(state.get("affected_nodes", [])),
            risk_score_max    = score_stats.get("max", 0.0),
            risk_score_median = score_stats.get("median", 0.0),
            exposure_summary = _compute_exposure_summary(state) if state.get("affected_nodes") else "N/A",
            top3_summary     = _format_top3_summary(state.get("top3_facilities", [])),
            risk_report      = state.get("risk_report", ""),
        )),
    ]

    llm_result = invoke_json(_get_llm(), messages)
    llm_issues: list[str] = llm_result.get("issues", [])
    all_issues.extend(llm_issues)

    # ── Step 3: determine final validity and failure_type ─────────────────────
    # Deterministic SEVERE issues override LLM result
    has_severe = any("SEVERE" in i for i in all_issues)
    has_minor  = any("MINOR" in i for i in all_issues) or (
        not llm_result.get("valid", True) and llm_result.get("failure_type") == "minor"
    )

    if has_severe:
        failure_type: Optional[FailureType] = "severe"
        valid = False
    elif has_minor or not llm_result.get("valid", True):
        llm_ft = llm_result.get("failure_type")
        failure_type = llm_ft if llm_ft in ("minor", "severe") else "minor"
        valid = False
    else:
        failure_type = None
        valid = True

    return {
        **state,
        "valid":        valid,
        "failure_type": failure_type,
        "issues":       all_issues,
        "iteration":    iteration,
    }
