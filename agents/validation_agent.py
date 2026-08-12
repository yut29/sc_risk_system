"""
Validation Agent — Source verification + hallucination check

Input (from PipelineState):
  risk_report, top3_facilities, reason
  material, region, risk_type, severity
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
  2. _structural_checks    — regex-extracts the RIGIDLY TEMPLATED Top-3 Facility section (see
     synthesis_agent.py's format spec) and compares it against computed state values. Moved out
     of the LLM prompt because the validation LLM applied these numeric/tier-logic rules
     unreliably across reruns — they're pure arithmetic/lookup, not reading comprehension, so code
     does them exactly instead of "reliably enough". (Capacity Impact section removed 2026-08-12
     per user request — this layer no longer checks it.)
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

# Mirrors intake_agent.py's SYSTEM_PROMPT "material must be one of" list (2026-08-12) — used
# only by _deterministic_checks' Source Context cross-material check (see check #5 below), not
# duplicated from agents/state.py's IMPORT_MATERIALS/KNOWN_MATERIALS because those two serve a
# different purpose (import-dependency classification) and don't include the 4 newer materials
# with real origin data (pvdf/copper_foil/aluminum_foil/separator) yet.
_ALL_TRACKED_MATERIALS: frozenset[str] = frozenset({
    "cobalt", "lithium", "nickel", "manganese", "graphite", "copper",
    "pvdf", "copper_foil", "aluminum_foil", "separator",
})

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
    af_material = state.get("material", "").lower()
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

    # 5. Source Context must not smuggle in a DIFFERENT material's analysis as if it were
    # about af_material (2026-08-12, docs/open_issues.md P39). Prompt-level instructions to
    # keep the Source Context section scoped to af_material were tried twice and both failed
    # reliably (3/3 repeated runs on a real DRC copper+cobalt test case rewrote a passage
    # explicitly about copper into one about cobalt) — same lesson as the 2026-08-02
    # _structural_checks redesign: an LLM instruction that fails deterministically, not just
    # occasionally, needs a code-level check, not another prompt tweak. additional_events_note
    # is where intake records "this event also affects material X" (see intake_agent.py's
    # MULTI-MATERIAL SINGLE EVENT rule) — any material named there that ISN'T af_material is a
    # material this report must NOT discuss the impact of.
    note = (state.get("additional_events_note") or "").lower()
    if note and af_material:
        for other_material in _ALL_TRACKED_MATERIALS - {af_material}:
            if other_material in note:
                section_match = re.search(
                    r"##\s*Source Context.*?\n(.*?)(?=\n##\s|\Z)", report, re.DOTALL | re.IGNORECASE
                )
                source_context = section_match.group(1) if section_match else ""
                if other_material in source_context.lower():
                    issues.append(
                        f"SEVERE: Source Context section mentions '{other_material}', a "
                        f"material this report explicitly does not cover (see "
                        f"additional_events_note) — likely misattributed to '{af_material}' "
                        f"instead of the material it actually concerns."
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
    r"\*\*(?P<header>.+?)\*\*\s*"
    r"\((?P<segment>[^,]+),\s*RiskScore:\s*(?P<score>[\d.]+)/100,\s*Exposure:\s*(?P<exposure>\w+)\)"
)
# Matches the whole bolded header blob rather than requiring a specific internal structure
# (2026-08-11 fix): originally required "**Facility (Company) — Location**" exactly (company
# captured only from before the em-dash). Empirically (5 repeated runs, same input) found
# synthesis_agent's LLM call (temperature=0.2, not 0) flips unpredictably between that format
# and a pipe-separated one ("**Company | Facility | Location**") — the LLM appears to sometimes
# anchor on _format_top3()'s pipe-delimited INPUT formatting instead of the em-dash OUTPUT
# format the prompt specifies. Both formats are semantically identical (same data, different
# punctuation), so the fix doesn't chase the LLM's formatting — it makes the check format-
# agnostic: capture the whole header blob and substring-match the real company name against
# it (see below), instead of relying on a fixed capture-group position that only one of the
# two observed formats satisfies. The (Segment, RiskScore: X/100, Exposure: Y) suffix was
# consistent across both observed formats, so that part of the regex is unchanged.

_CAPACITY_TOLERANCE_PCT = 0.6  # allow for LLM rounding/formatting drift


def _structural_checks(state: PipelineState) -> list[str]:
    """
    Regex-extracts the rigidly-templated Top-3 Facility section and compares it
    against the deterministic state values. Falls back to a MINOR "format
    deviation" issue if the expected pattern isn't found at all, rather than
    silently skipping the check.
    """
    issues: list[str] = []
    report = state.get("risk_report", "")
    top3 = state.get("top3_facilities", [])

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
            reported_header = m.group("header").strip()
            if f["company"].lower() not in reported_header.lower():
                issues.append(
                    f"SEVERE: Top-3 entry #{i + 1} header '{reported_header}' does not contain "
                    f"the provided facility's company '{f['company']}' — possible hallucinated "
                    f"entity."
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
Numeric/structural facts — Top-3 facility company/segment/RiskScore/Exposure fields, source
citation, material mention — are already checked deterministically by code before you see this
report (see _structural_checks / _deterministic_checks in validation_agent.py). Do NOT re-derive
or second-guess those numbers; your job is limited to the parts of the report that require reading
comprehension, not arithmetic or lookup.

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
   Top-3 numbers.

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
Numeric facts (Top-3 fields, source citation) are already checked by code — focus only on the
free-form prose: Risk Event paragraph, Supply Chain Exposure Analysis, Top-3 justification
quality, and Risk Synthesis.

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
            affected_material= state.get("material", ""),
            affected_region  = state.get("region", ""),
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
