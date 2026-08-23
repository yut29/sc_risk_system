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
  quality_scores, overall_quality, quality_strengths, quality_summary : structured quality
      assessment (2026-08-23), independent of valid/failure_type — see SYSTEM_PROMPT below for
      the four-dimension rubric (groundedness/consistency/completeness/explainability). A report
      can pass every hard check and still score low here, and a single SEVERE issue still forces
      valid=false regardless of these scores.

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

_llm: Optional[BaseChatModel] = None


def _get_llm() -> BaseChatModel:
    global _llm
    if _llm is None:
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

    # Check #5 (Source Context cross-material check, added 2026-08-12 for P39) removed
    # 2026-08-17: it was structurally dead code. synthesis_agent.py's _sanitize_source_context()
    # runs deterministically on every report BEFORE this function ever sees it, and already
    # strips out exactly the cross-material leak this check was looking for — so the condition
    # this check fired on could never occur here. Same underlying fact this check encoded (which
    # other material to watch for, via additional_events_note) still lives in
    # _sanitize_source_context(); no need to check it twice. See run_validation_agent's LLM
    # check (SYSTEM_PROMPT, "NARRATIVE HALLUCINATION") for the broader, still-live check that
    # now also has raw_input available to catch invented claims in general, not just this one
    # specific pattern.

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

TWO SEPARATE GROUND-TRUTH SOURCES — do not mix them up:
- Top-3 facility details (capacity, product, supplier/company profile, RiskScore inputs) come from
  the NAATBatt database / this system's own deterministic computation, NOT from the news article.
  They are correct even though the article never mentions them — that is expected and NOT an
  issue. Never flag a Top-3 fact as unsupported just because it isn't in the ORIGINAL SOURCE TEXT.
- The ORIGINAL SOURCE TEXT below is ground truth ONLY for claims about the EVENT ITSELF: what
  happened, when, why, how long it might last, market/analyst reaction. This is what the Risk
  Event paragraph and Source Context section must be checked against.

Check for:
1. COHERENCE — Is the reasoning in the "Risk Synthesis" section logically self-consistent,
   and does it actually follow from the facts stated earlier in the report (rather than contradicting
   them)?
2. QUALITATIVE COMPLETENESS — Does each Top-3 facility justification say something specific to that
   facility (its import dependency, single-source risk, capacity share), or is it generic boilerplate
   that could be copy-pasted onto any other facility?
3. EVENT NARRATIVE HALLUCINATION — narrower than it sounds, do NOT over-flag here. Qualitative
   analytical judgment ("this is a significant disruption", "this could tighten supply", "this
   signals structural fragility") is EXPECTED and CORRECT — the whole point of the Synthesis
   Agent is to draw exactly this kind of reasonable inference from sparse facts, like a senior
   analyst would; do not flag an inference just because the source didn't spell it out in those
   words. Only flag a SPECIFIC, CONCRETE, CHECKABLE detail — a duration ("1-3 months"), a
   quantity, a date, a named cause — that is stated as if it were a known fact when the ORIGINAL
   SOURCE TEXT below either doesn't mention it at all or explicitly says it's unknown/unconfirmed.
   The test: could a reader mistake this for something the source actually reported, and would
   they be wrong? A vague, uncertain source does not license a specific-sounding invented number;
   it does license reasonable qualitative judgment about significance and implications (flag a
   genuine fabricated specific as "severe" if it's used to justify the severity rating or a
   Top-3 facility's risk assessment, "minor" otherwise).

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

# ── Quality assessment (2026-08-23) — separate LLM call, deliberately NOT merged into
# SYSTEM_PROMPT above. First attempt combined both into one prompt/call; even with an explicit
# "judge issues/valid first, ignore the quality rubric below" instruction, adding the quality
# rubric measurably weakened the EVENT NARRATIVE HALLUCINATION check in the same call — a report
# with an unsupported "estimated to be 1-3 months" duration (Szenario S4, Abschnitt 5.5) stopped
# being flagged (issues=[], valid=True) as soon as the quality section was present, in 5/5
# consecutive test runs, immediately after being caught in ~8/10 runs before that addition. This
# is the reasoning that dictated a second, independent LLM call instead: two different judgment
# tasks in one prompt interfere even when told not to, so a hard-checking task must not share a
# call with anything else.

SYSTEM_PROMPT_QUALITY = """You are assessing the quality of an already-finalized supply chain
risk report. A separate, stricter process has already checked this report for hard factual and
structural errors and lists its findings below (KNOWN ISSUES) — that pass/fail decision is final
and not yours to make or revisit. Your job is to rate overall report quality on four dimensions,
each an integer 1 (worst) to 5 (best), for a reader who wants to know not just "is it correct" but
"how good is the analysis" — this includes reflecting any KNOWN ISSUES in your scores, particularly
when the report ships despite unresolved findings (e.g. after exhausting retries): do not give a
high groundedness score if KNOWN ISSUES lists an unresolved fabricated fact, even though deciding
pass/fail itself is not your job.

- groundedness: are the report's factual claims traceable to the input text, the NAATBatt data, or
  the deterministic computation? 5 = fully grounded, no unsupported claim. 1 = core conclusions
  rest mainly on invented information. Reduce this score for each unresolved fabricated/unsupported
  claim listed in KNOWN ISSUES below. A report that explicitly states a fact is unknown/unspecified
  when the source doesn't provide it should score HIGH here — do not penalize an honest "unknown".
- consistency: this checks agreement BETWEEN parts of the report, regardless of whether any single
  part is individually grounded (that is groundedness's job, not this one). Two sub-checks: (a) do
  the report's own numbers/fields agree with each other and with the structured data provided below
  (RiskScore, Exposure, segment, material)? (b) do different SECTIONS of the free-form prose agree
  with each other — e.g. if one section says a duration/outcome is "unknown" or "uncertain" while
  another section later states a specific, confident value for that same thing, that is a
  consistency failure even if neither sentence individually looks unusual in isolation. 5 = no
  contradictions anywhere, including between prose sections. 1 = the report conflicts with the
  structured results it was given, or contradicts itself between sections.
- completeness: is every REQUIRED SECTION of the report template present and populated (Risk Event,
  Supply Chain Exposure Analysis, Top-3 Risk Facilities, Risk Synthesis)? This is a structural
  check — the report format is fixed by the Synthesis Agent, so a report assembled from the
  provided data will usually score 5 here. Only judge elements that actually apply to this
  scenario — do not penalize a field that has no relevance here (e.g. no company name in a
  regional event). 5 = fully complete for this scenario. 1 = severely incomplete.
- explainability: this is NOT the same question as completeness — a report can have an
  explanation for every Top-3 facility (satisfying completeness) while that explanation is still
  low-quality. Judge whether EACH Top-3 justification names the SPECIFIC mechanism connecting this
  particular facility to this particular event, using its own numbers/attributes (which vulnerability
  term dominates, its capacity share, its supplier count, its tier distance) — not a phrase that
  could be pasted onto any other facility in any other report unchanged.
  Example scoring this LOW (2-3): "Company A has a high RiskScore because it is exposed to this
  risk and has limited alternatives." — true but generic, gives no facility-specific mechanism.
  Example scoring this HIGH (5): "Company A receives a high RiskScore because it is directly
  exposed to the affected material, has only one direct supplier (raising SingleSourceDependency),
  and represents a comparatively large share of the region's modeled production capacity." —
  names the specific factors and ties them to this facility's actual data.
  Do not give 5 by default merely because an explanation sentence exists for each facility; only
  give 5 if most Top-3 justifications individually clear the bar above. 1 = conclusions are stated
  without any reasoning at all.

Respond ONLY with a JSON object — no explanation, no markdown, no code blocks.

JSON schema:
{
  "quality_scores": {"groundedness": 1-5, "consistency": 1-5, "completeness": 1-5, "explainability": 1-5},
  "quality_strengths": ["<specific strength1>", ...],
  "quality_summary": "<one-sentence overall assessment>"
}
"""

USER_PROMPT_TEMPLATE_QUALITY = """CONTEXT:
- Risk type: {risk_type}
- Severity: {severity}/5
- Affected material: {affected_material}
- Affected region: {affected_region}
- RiskScore distribution: highest={risk_score_max}/100, median={risk_score_median}/100
- Exposure summary: {exposure_summary}

TOP 3 FACILITIES PROVIDED TO SYNTHESIS AGENT:
{top3_summary}

KNOWN ISSUES (already found by the separate hard-check process — do not re-decide pass/fail, but
DO reflect these in your scores, especially groundedness, if this list is non-empty):
{known_issues}

ORIGINAL SOURCE TEXT:
---
{raw_input}
---

FINAL RISK REPORT (already validated for hard errors — assess its quality only):
---
{risk_report}
---
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

ORIGINAL SOURCE TEXT (added 2026-08-17 — ground truth for the NARRATIVE HALLUCINATION check;
previously this check had no way to tell an invented specific detail from a real one, since it
never saw the actual article/query the report is supposed to be based on):
---
{raw_input}
---

GENERATED RISK REPORT:
---
{risk_report}
---
"""


_QUALITY_DIMENSIONS = ("groundedness", "consistency", "completeness", "explainability")


def _extract_quality(llm_result: dict) -> tuple[dict[str, int], Optional[float], list[str], str]:
    """
    Parses/clamps the LLM's quality_scores into valid 1-5 ints (never trust an unclamped LLM
    number — same reasoning as everywhere else in this system: a 24B model asked for "1-5" can
    still emit 0, 6, or a float). overall_quality is the plain average, computed here in Python
    rather than taken from the LLM — arithmetic is not an LLM's job in this system, same
    principle as the RiskScore calculation itself (Abschnitt 4.3.3).
    """
    raw_scores = llm_result.get("quality_scores") or {}
    scores: dict[str, int] = {}
    for dim in _QUALITY_DIMENSIONS:
        try:
            v = int(raw_scores.get(dim, 3))
        except (TypeError, ValueError):
            v = 3
        scores[dim] = min(5, max(1, v))

    overall = round(sum(scores.values()) / len(_QUALITY_DIMENSIONS), 2)
    strengths = llm_result.get("quality_strengths") or []
    if not isinstance(strengths, list):
        strengths = []
    summary = llm_result.get("quality_summary") or ""
    return scores, overall, strengths, summary


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
            "quality_scores":   {},
            "overall_quality":  None,
            "quality_strengths": [],
            "quality_summary":  "Not applicable — fixed system-limitation notice, no generated "
                                "analysis to assess.",
        }

    # Hard stop: exceeded max iterations → accept report as-is with warning. Preserve
    # the actual issues found on the last real check (2026-08-17 fix — this used to
    # discard them and show only the generic "accepted with caveats" line, so a reader
    # had no way to know WHAT was flagged, just that something was and got shipped
    # anyway; ui/app.py already surfaces `issues` to the user, so this is now visible).
    if iteration > MAX_VALIDATION_ITERATIONS:
        return {
            **state,
            "valid":        True,
            "failure_type": None,
            "issues":       state.get("issues", []) + [
                "Max iterations reached — report accepted with the unresolved issue(s) above."
            ],
            "iteration":    iteration,
            # Quality fields from the last completed check (below) carry over unchanged —
            # nothing new was assessed on this short-circuited call.
            "quality_scores":    state.get("quality_scores", {}),
            "overall_quality":   state.get("overall_quality"),
            "quality_strengths": state.get("quality_strengths", []),
            "quality_summary":   state.get("quality_summary", ""),
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
            raw_input        = state.get("raw_input", ""),
            risk_report      = state.get("risk_report", ""),
        )),
    ]

    llm_result = invoke_json(_get_llm(), messages)
    llm_issues: list[str] = llm_result.get("issues", [])
    all_issues.extend(llm_issues)

    # ── Step 2b: quality assessment — separate LLM call, see SYSTEM_PROMPT_QUALITY's
    # docstring-comment above for why this is not merged into the call above.
    quality_messages = [
        SystemMessage(content=SYSTEM_PROMPT_QUALITY),
        HumanMessage(content=USER_PROMPT_TEMPLATE_QUALITY.format(
            risk_type        = state.get("risk_type", ""),
            severity         = state.get("severity", 3),
            affected_material= state.get("material", ""),
            affected_region  = state.get("region", ""),
            risk_score_max    = score_stats.get("max", 0.0),
            risk_score_median = score_stats.get("median", 0.0),
            exposure_summary = _compute_exposure_summary(state) if state.get("affected_nodes") else "N/A",
            top3_summary     = _format_top3_summary(state.get("top3_facilities", [])),
            known_issues     = "\n".join(f"- {i}" for i in all_issues) if all_issues else "None",
            raw_input        = state.get("raw_input", ""),
            risk_report      = state.get("risk_report", ""),
        )),
    ]
    quality_result = invoke_json(_get_llm(), quality_messages)
    quality_scores, overall_quality, quality_strengths, quality_summary = _extract_quality(quality_result)

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

    # ── Step 4: deterministic clamp on quality_scores ────────────────────────
    # Don't fully trust the LLM's self-assessment here either: passing KNOWN ISSUES into the
    # quality prompt (above) makes a low groundedness score likely, but "likely" isn't a
    # guarantee, and a "valid=false, quality=4.8" combination would be a visible, confusing
    # inconsistency for anyone reading the UI (Abschnitt 4.4) or Kapitel 5. Enforce it in code
    # instead of hoping the model applies its own instructions consistently — same reasoning
    # as clamping severity/origin_tier in risk_assessment_agent.py.
    if has_severe:
        quality_scores["groundedness"] = min(quality_scores["groundedness"], 2)
        quality_scores["consistency"]  = min(quality_scores["consistency"], 2)
    elif has_minor:
        quality_scores["groundedness"] = min(quality_scores["groundedness"], 4)
    overall_quality = round(sum(quality_scores.values()) / len(quality_scores), 2)

    return {
        **state,
        "valid":        valid,
        "failure_type": failure_type,
        "issues":       all_issues,
        "iteration":    iteration,
        "quality_scores":    quality_scores,
        "overall_quality":   overall_quality,
        "quality_strengths": quality_strengths,
        "quality_summary":   quality_summary,
    }
