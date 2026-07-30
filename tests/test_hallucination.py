"""
Ebene 4: Halluzinations-Tests — Entitätsvalidierung, Quellenpflicht (docs/test_plan.md §4.1/4.2).

Exercises the Validation Agent's deterministic checks (_deterministic_checks) directly with
synthetic state — fast, no LLM call needed, since these are pure rule-based checks. A single
slow/opt-in test at the bottom runs the real, full LLM pipeline end-to-end.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from agents.validation_agent import _deterministic_checks


def _node(nid, company="Real Corp", segment="Upstream"):
    return {"id": nid, "company": company, "facility_name": "", "segment": segment, "country": "US"}


def _facility(nid, company="Real Corp", score=80.0):
    return {"id": nid, "company": company, "risk_score_normalized": score}


# ── EntityPrecision (test_plan.md §4.1) ─────────────────────────────────────────

def test_entity_precision_flags_facility_not_in_affected_nodes():
    """A Top-3 facility absent from affected_nodes can't be verified against NAATBatt -> SEVERE."""
    state = {
        "top3_facilities": [_facility("99999", "Fictional Corp")],
        "affected_nodes": [],  # "99999" is NOT here
        "facility_data": {},
        "risk_report": "Source: NAATBatt facility data. Fictional Corp faces cobalt risk.",
        "affected_material": "cobalt",
    }
    issues = _deterministic_checks(state)
    assert any("SEVERE" in i and "99999" in i for i in issues)


def test_entity_precision_passes_for_verifiable_facility():
    node = _node("123")
    state = {
        "top3_facilities": [_facility("123")],
        "affected_nodes": [node],
        "facility_data": {"123": {}},
        "risk_report": "Source: NAATBatt facility data. Real Corp faces cobalt risk.",
        "affected_material": "cobalt",
    }
    issues = _deterministic_checks(state)
    assert not any("SEVERE" in i for i in issues)


def test_facility_data_missing_flags_minor():
    node = _node("555", "No Data Corp")
    state = {
        "top3_facilities": [_facility("555", "No Data Corp")],
        "affected_nodes": [node],
        "facility_data": {},  # no entry for "555"
        "risk_report": "Source: NAATBatt facility data. No Data Corp faces cobalt risk.",
        "affected_material": "cobalt",
    }
    issues = _deterministic_checks(state)
    assert any("MINOR" in i and "555" in i for i in issues)


# ── SourceCoverage (test_plan.md §4.2) ──────────────────────────────────────────

def test_source_coverage_flags_missing_citation():
    state = {
        "top3_facilities": [], "affected_nodes": [], "facility_data": {},
        "risk_report": "This report has no citation whatsoever about cobalt.",
        "affected_material": "cobalt",
    }
    issues = _deterministic_checks(state)
    assert any("MINOR" in i and "source citation" in i.lower() for i in issues)


def test_source_coverage_passes_with_naatbatt_mention():
    state = {
        "top3_facilities": [], "affected_nodes": [], "facility_data": {},
        "risk_report": "Based on NAATBatt facility data, cobalt supply is at risk.",
        "affected_material": "cobalt",
    }
    issues = _deterministic_checks(state)
    assert not any("source citation" in i.lower() for i in issues)


# ── Material misclassification ──────────────────────────────────────────────────

def test_material_misclassification_flagged_severe():
    state = {
        "top3_facilities": [], "affected_nodes": [], "facility_data": {},
        "risk_report": "Source: NAATBatt facility data. Nickel supply chains face disruption.",
        "affected_material": "cobalt",
    }
    issues = _deterministic_checks(state)
    assert any("SEVERE" in i and "cobalt" in i for i in issues)


def test_correct_material_does_not_flag_misclassification():
    state = {
        "top3_facilities": [], "affected_nodes": [], "facility_data": {},
        "risk_report": "Source: NAATBatt facility data. Cobalt supply chains face disruption.",
        "affected_material": "cobalt",
    }
    issues = _deterministic_checks(state)
    assert not any("misclassification" in i.lower() for i in issues)


# ── Slow / opt-in: full pipeline with real LLM calls ────────────────────────────

@pytest.mark.slow
@pytest.mark.skipif(not os.environ.get("GROQ_API_KEY"), reason="needs GROQ_API_KEY + live API call")
def test_full_pipeline_report_mentions_top3_companies_and_material():
    """End-to-end: run S1 through the real pipeline (all 6 agents, real LLM calls) and check
    the generated report is self-consistent — every Top-3 company and the material name
    appear in the report text. Slow and uses Groq API quota; not run by default
    (`pytest -m slow` to include it)."""
    from pipeline.pipeline import run_pipeline

    result = run_pipeline(
        "Major strike at Glencore cobalt mines in the Democratic Republic of Congo halts "
        "production, affecting roughly 8% of global cobalt output relied on by North "
        "American battery manufacturers."
    )
    assert result.get("relevant") is True
    report = result.get("risk_report", "").lower()
    assert "source" in report
    assert "cobalt" in report
    for f in result.get("top3_facilities", []):
        assert f["company"].lower() in report, f"{f['company']} not mentioned in generated report"
