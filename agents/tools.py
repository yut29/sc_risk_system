"""
Shared LangChain tools for agents — lets an LLM query the real facility dataset
on demand instead of classifying purely from prompt text, no ground-truth check.
"""

import re
from functools import lru_cache
from pathlib import Path

import pandas as pd
from langchain_core.tools import tool

FACILITIES_FILE = Path(__file__).parent.parent / "data" / "facilities_clean.csv"

# facilities_clean.csv's `state` column is mostly bare US/CA postal abbreviations (see
# build_facilities.py — Upstream's import_origin_region also reuses this same field), but an
# LLM-classified event region is a full place name ("Ontario", "Quebec"). Needed for
# query_facilities_by_region() below (Strategy C, network_agent.py) so "Quebec" actually
# matches facilities whose state field says "QC" — plain substring matching alone silently
# fails for most of these (e.g. "qc" is not a substring of "quebec").
US_STATE_ABBREV = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
CA_PROVINCE_ABBREV = {
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba", "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador", "NS": "Nova Scotia", "ON": "Ontario",
    "PE": "Prince Edward Island", "QC": "Quebec", "SK": "Saskatchewan",
}
_ALL_ABBREV = {**US_STATE_ABBREV, **CA_PROVINCE_ABBREV}


@lru_cache(maxsize=1)
def _load_facilities() -> pd.DataFrame:
    return pd.read_csv(FACILITIES_FILE)


def find_facilities_by_region(region: str, limit: int = 15) -> list[dict]:
    """
    Deterministic lookup (not an LLM tool — called directly by network_agent.py's Strategy C)
    for facilities physically located in or near `region`, matched against city/state/country
    with abbreviation expansion in both directions (full name -> abbrev, abbrev -> full name).

    Returns a closed list of real facility dicts (id/company/city/state/country/segment/
    material_keywords) — the caller must never treat this as exhaustive or invent entries
    beyond what's returned, per the anti-hallucination design in docs/architecture.md
    ("Strategy C — LLM-Assisted Retrieval").
    """
    df = _load_facilities()
    region_lower = region.strip().lower()
    if not region_lower:
        return []

    # Split on ","/"/" first ("Ontario, Canada", "Chile/Argentina") — an LLM-produced region
    # string is often multi-part, and exact-equality abbreviation expansion (below) would
    # otherwise never fire since "ontario" != "ontario, canada" as a whole string.
    parts = [p.strip() for p in re.split(r"[,/]", region_lower) if p.strip()]
    expanded = {region_lower} | set(parts)
    for term in list(expanded):
        for abbrev, full in _ALL_ABBREV.items():
            if full.lower() == term:
                expanded.add(abbrev.lower())
            elif abbrev.lower() == term:
                expanded.add(full.lower())

    def _matches(row) -> bool:
        haystack = f"{row['city']} {row['state']} {row['country']}".lower()
        for term in expanded:
            if not term:
                continue
            if len(term) <= 3:
                # Short tokens (state/province abbreviations like "on", "bc") are unsafe for
                # raw substring checks — "on" is a substring of the city name "Champion" —
                # require a whole-word match instead.
                if re.search(rf"\b{re.escape(term)}\b", haystack):
                    return True
            elif term in haystack:
                return True
        return False

    matched = df[df.apply(_matches, axis=1)].head(limit)
    return [
        {
            "facility_id": str(r["facility_id"]),
            "company": r["company"],
            "city": r["city"],
            "state": r["state"],
            "country": r["country"],
            "segment": r["supply_chain_segment"],
            "material_keywords": r["material_keywords"] or "(none tagged)",
        }
        for _, r in matched.iterrows()
    ]


@tool
def query_facilities(material: str, segment: str = "") -> str:
    """Look up how many North American facilities in the real dataset handle a given
    battery-supply-chain material, before judging an event's severity or scope.

    Args:
        material: material keyword to search for, e.g. "cobalt", "lithium", "copper".
        segment: optional supply-chain tier filter — one of "Upstream", "Midstream-BGM",
            "Midstream-Cell", "Downstream". Leave empty to search all tiers.

    Returns:
        A short text summary: how many facilities match, broken down by tier, with a
        few example company names. If zero facilities match, says so explicitly —
        that itself is useful signal (the material may not be tracked in this dataset).
    """
    df = _load_facilities()
    material = material.strip().lower()
    mask = df["material_keywords"].fillna("").str.contains(material, regex=False)
    if segment:
        mask &= df["supply_chain_segment"] == segment

    matched = df[mask]
    if matched.empty:
        scope = f" in segment {segment!r}" if segment else ""
        return f"0 facilities found handling material {material!r}{scope} in the NA dataset."

    by_segment = matched["supply_chain_segment"].value_counts().to_dict()
    examples = matched["company"].drop_duplicates().head(5).tolist()
    return (
        f"{len(matched)} facilities handle {material!r}"
        + (f" (segment={segment!r})" if segment else "")
        + f". By tier: {by_segment}. Example companies: {examples}."
    )
