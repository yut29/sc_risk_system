"""
Facility cleaning (build_facilities.py) — checks on the generated facilities_clean.csv,
catching bugs in material_keywords extraction rather than downstream graph/matching logic.
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

FACILITIES_CSV = Path(__file__).parent.parent / "data" / "facilities_clean.csv"


def test_ateios_lib_manufacturing_implies_lithium():
    """Regression test (2026-07-31): extract_keywords() only reads the `product` column.
    Ateios Systems has product_type="LIB Manufacturing" (Lithium-Ion Battery) but
    product="Electrode" (no material term), so the LIB signal was silently dropped and
    material_keywords was empty. Fixed with a targeted check of `product_type` for the
    string "lib" (verified dataset-wide to match only this one row, no false positives
    e.g. from words containing "lib" as a substring)."""
    df = pd.read_csv(FACILITIES_CSV)
    row = df[df["company"] == "Ateios Systems"]
    assert not row.empty, "Ateios Systems not found — dataset may have changed shape"
    assert "lithium" in str(row.iloc[0]["material_keywords"]).split(",")


def test_silicon_anode_facilities_are_not_tagged_graphite():
    """Regression test (2026-07-31): a generic product_type+product merge was tried to
    fix the Ateios case above, but it made the existing "anode" -> graphite rule fire on
    product_type category labels like "Anode Battery Grade Materials" even for
    non-graphite (silicon/LTO) anode material makers. Group14 Technologies and Sila
    Nanotechnologies are silicon-anode material producers and must never be tagged
    graphite."""
    df = pd.read_csv(FACILITIES_CSV)
    for company in ("Group14 Technologies", "Sila Nanotechnologies"):
        row = df[df["company"] == company]
        assert not row.empty, f"{company} not found — dataset may have changed shape"
        assert "graphite" not in str(row.iloc[0]["material_keywords"]).split(",")


def test_cell_manufacturers_get_manual_chemistry_from_public_sources():
    """Regression test (2026-07-31): these Midstream-Cell manufacturers have generic,
    non-specific product text ("Other/Unknown", "Cell", "Solid-state battery") so the
    keyword rules never assigned them a chemistry, even though they are all publicly
    documented lithium-ion cell makers. Manually verified via each company's own
    published product/technology pages (see MATERIAL_KEYWORD_OVERRIDES docstring in
    build_facilities.py) and added as explicit overrides, since inferring chemistry from
    product text alone is not possible here."""
    df = pd.read_csv(FACILITIES_CSV)
    expected = {
        "ABSL Power Solutions, Inc.": {"lithium"},
        "American Lithium Energy": {"lithium"},
        "Nanotech Energy": {"lithium"},
        "Nuvvon Inc": {"lithium", "nickel"},
        "Samsung SDI America Inc.": {"lithium", "nmc", "cobalt", "nickel", "manganese"},
        "Solid Power Inc.": {"lithium", "nmc", "cobalt", "nickel", "manganese"},
    }
    for company, kws in expected.items():
        row = df[df["company"] == company]
        assert not row.empty, f"{company} not found — dataset may have changed shape"
        assert kws <= set(str(row.iloc[0]["material_keywords"]).split(","))


def test_non_material_component_suppliers_do_not_auto_match_every_bgm():
    """Regression test (2026-07-31): build_graph.py's BGM->Cell matching treats an empty
    material_keywords as a wildcard match for every BGM search (`if not c_kws or ...`).
    3M/ADA/ArlanXEO/Avery Dennison/Intriplex/Vertical Partners West are genuine
    mechanical/safety/BMS component suppliers (verified via their product text — thermal
    systems, adhesives, cell lid assemblies etc.), not active-material processors, so
    they must never receive a material-flow edge. Tagged with the inert marker
    "non_active_material" instead of being left empty, so the existing
    `c_kws & cell_targets` check naturally excludes them without needing to change
    build_graph.py's matching logic itself."""
    from agents.network_agent import _load_graph

    G = _load_graph()
    companies = {"3M", "ADA Technologies, Inc.", "ArlanXEO", "Avery Dennison Corp", "Intriplex"}
    for nid, attrs in G.nodes(data=True):
        if attrs.get("company") in companies:
            assert attrs.get("material_keywords") == "non_active_material"
            assert G.in_degree(nid) == 0, (
                f"{nid} ({attrs.get('company')}) has in-degree {G.in_degree(nid)} — "
                f"a material-agnostic component supplier should never receive a "
                f"material-flow edge from a BGM node"
            )
