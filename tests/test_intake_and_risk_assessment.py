"""
Ebene 1 (partial): Unit tests for the two LLM-judgment agents themselves — Intake Agent and
Risk Assessment Agent — run in isolation with real LLM calls.

Gap this fills: every other test file in this suite (test_scenarios.py, test_entity_matching.py,
etc.) deliberately bypasses these two agents by hardcoding material/region/severity/origin_tier
directly into the state (see conftest.py's SCENARIOS fixture docstring) — fast and
Groq/FAU-rate-limit-independent, but it means Intake's and Risk Assessment's own judgment
behavior was never itself exercised by an automated test, only verified ad hoc during manual
LLM queries in conversation. This file closes that gap directly.

Slow and opt-in (`pytest -m slow` to include; skipped by default and if no LLM key is
configured) — LLM output isn't byte-for-byte reproducible across runs the way the deterministic
tests are, so assertions here are deliberately loose (substring/range checks, not exact-value
equality) to avoid flaking on harmless phrasing variation.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from agents.intake_agent import run_intake_agent
from agents.risk_assessment_agent import run_risk_assessment_agent

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not (os.environ.get("FAU_LLMAPI_KEY") or os.environ.get("GROQ_API_KEY")),
        reason="needs FAU_LLMAPI_KEY or GROQ_API_KEY + live API call",
    ),
]


# ── Intake Agent ─────────────────────────────────────────────────────────────────

def test_intake_relevant_regional_event_extracts_material_and_region():
    """A material+region disruption (Strategy A shape) should be recognized as relevant and
    yield the correct material/region — the two fields Network Agent's rule-matching depends on."""
    state = {"raw_input": (
        "A months-long strike at major cobalt mines in the Democratic Republic of Congo has "
        "halted roughly 70% of regional cobalt ore production, with no confirmed timeline for "
        "resolution and no alternative near-term supply arranged."
    )}
    result = run_intake_agent(state)
    assert result["relevant"] is True
    assert result["material"] == "cobalt"
    assert "drc" in result["region"].lower() or "africa" in result["region"].lower()


def test_intake_irrelevant_text_is_rejected():
    """Text with nothing to do with battery supply chains must be flagged relevant=False, not
    forced through the pipeline anyway."""
    state = {"raw_input": "The World Cup final drew a record global television audience."}
    result = run_intake_agent(state)
    assert result["relevant"] is False


def test_intake_facility_specific_event_leaves_material_and_region_empty():
    """Facility-specific incidents (Strategy B shape) must NOT get a guessed material/region —
    the system prompt's rule is explicit that this category "has no material/region of its
    own". Regression test for the exact behavior discussed at length this session: Panasonic's
    De Soto plant makes NMC/NCA cells (cobalt, nickel, manganese all apply), but the fire isn't
    a material-level event, so guessing any one of them would misrepresent the event."""
    state = {"raw_input": (
        "A fire broke out at Panasonic's battery cell manufacturing plant in De Soto, Kansas, "
        "forcing an immediate halt to production with no estimated restart date."
    )}
    result = run_intake_agent(state)
    assert result["relevant"] is True
    assert result["material"] == ""
    assert result["region"] == ""
    assert result["mentioned_company"] is not None and "panasonic" in result["mentioned_company"].lower()
    assert result["mentioned_location"] is not None and "de soto" in result["mentioned_location"].lower()


def test_intake_direct_question_extracts_same_as_news_phrasing():
    """A directly-phrased user question (not news-article phrasing) must extract the same
    material/region as an equivalent news statement — trigger_type is metadata only and must
    not gate what gets extracted. Locks in the empirical finding from this session that
    phrasing (question vs. statement) doesn't affect extraction, only content does."""
    state = {"raw_input": (
        "What would happen to our North American battery supply chain if there were a "
        "prolonged strike at cobalt mines in the DRC?"
    )}
    result = run_intake_agent(state)
    assert result["relevant"] is True
    assert result["material"] == "cobalt"
    assert "drc" in result["region"].lower() or "africa" in result["region"].lower()


def test_intake_meta_question_about_system_itself_is_rejected():
    """A question about the system's own mechanics (not a risk event) must be rejected, not
    answered as if it were a risk report request."""
    state = {"raw_input": "How does your risk scoring model work?"}
    result = run_intake_agent(state)
    assert result["relevant"] is False


# ── Risk Assessment Agent ────────────────────────────────────────────────────────

def test_risk_assessment_severe_prolonged_disruption_gets_high_severity():
    """A months-long, near-total production halt with no resolution timeline is a textbook
    severity=4-5 case per the prompt's own scale definition."""
    state = {
        "filtered_text": (
            "A months-long strike at major cobalt mines in the Democratic Republic of Congo "
            "has halted roughly 70% of regional cobalt ore production, with no confirmed "
            "timeline for resolution and no alternative near-term supply arranged."
        ),
        "material": "cobalt", "region": "Africa/DRC",
    }
    result = run_risk_assessment_agent(state)
    assert result["severity"] >= 4
    assert result["risk_type"] == "supply_disruption"
    assert result["reason"].strip() != ""


def test_risk_assessment_minor_short_disruption_gets_low_severity():
    """A brief, small-scale, already-resolving disruption should score low, not be treated the
    same as a systemic multi-month event."""
    state = {
        "filtered_text": (
            "A two-day localized labor dispute briefly slowed operations at one lithium "
            "processing line last week; normal output resumed after a minor wage adjustment "
            "was agreed, with no reported impact on shipments."
        ),
        "material": "lithium", "region": "South America",
    }
    result = run_risk_assessment_agent(state)
    assert result["severity"] <= 2


def test_risk_assessment_origin_tier_matches_mining_event():
    """A mine-level strike is an Upstream-origin event."""
    state = {
        "filtered_text": "A strike at cobalt mines in the DRC has halted ore production.",
        "material": "cobalt", "region": "Africa/DRC",
    }
    result = run_risk_assessment_agent(state)
    assert result["origin_tier"] == "Upstream"


def test_risk_assessment_classifies_export_restriction_as_regulatory():
    """A government export-licensing measure is regulatory, not a physical supply_disruption —
    the two risk_type values feed different narrative framing downstream."""
    state = {
        "filtered_text": (
            "Chile's government announced new state-approval requirements for all lithium "
            "export contracts, a regulatory measure expected to delay shipments by months."
        ),
        "material": "lithium", "region": "South America",
    }
    result = run_risk_assessment_agent(state)
    assert result["risk_type"] == "regulatory"
