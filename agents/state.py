"""
LangGraph Pipeline State Schema — SC Risk System

Definiert alle Datentypen und den gemeinsamen PipelineState aller 6 Agents.
Felder werden schrittweise befüllt; jede Gruppe ist einem Agent zugeordnet.
"""

from __future__ import annotations

from typing import Optional
from typing_extensions import Literal, TypedDict


# ──────────────────────────────────────────────────────────────────────────────
#  Einfache Typen / Literal-Enumerationen
# ──────────────────────────────────────────────────────────────────────────────

TriggerType = Literal["A", "B"]
"""A = automatische News (Trigger A), B = Nutzeranfrage (Trigger B)."""

RiskType = Literal[
    "supply_disruption",   # Lieferunterbrechung
    "price_volatility",    # Preisvolatilität
    "regulatory",          # Regulatorisches Risiko
    "logistics",           # Logistikausfall (z.B. Hafenblockade)
    "weather",             # Extremwetterereignis
]

Segment = Literal["Upstream", "Midstream-BGM", "Midstream-Cell", "Downstream"]
"""Supply Chain Tier gemäß `segment`-Feld in NAATBatt."""

CapacitySource = Literal["naatbatt", "unknown"]
"""Datenqualitätsflag: naatbatt=Originalwert aus NAATBatt, unknown=kein Wert in NAATBatt."""

FailureType = Literal["minor", "severe"]
"""
minor  → fehlende Quellenangabe, Entität nicht belegt → Retry ab Synthesis Agent
severe → falsche Materialzuordnung, inkonsistente Klassifikation → Neustart ab Risk Assessment
"""


# ──────────────────────────────────────────────────────────────────────────────
#  Komplexe Datentypen
# ──────────────────────────────────────────────────────────────────────────────

class Node(TypedDict):
    """
    Einzelner Knoten aus knowledge_graph.json.
    Entspricht einer Facility/Unternehmen in der NAATBatt-Datenbank.
    """
    id: str                       # facility_id aus NAATBatt (z.B. "1002")
    company: str
    facility_name: str
    segment: Segment
    product_type: str
    material_keywords: str        # z.B. "cobalt", "lithium", "graphite"
    country: str
    state: str                    # US-Bundesstaat oder Provinz
    city: str
    latitude: float
    longitude: float
    production_capacity_raw: str  # Rohwert als String; "nan" wenn unbekannt — Quelle: capacity_source
    production_units: str         # z.B. "MT contained Li/yr"
    capacity_source: CapacitySource  # "naatbatt" = Originalwert | "unknown" = kein Wert in NAATBatt
    supplier_concentration: bool  # [literaturbasierte Regel] True = wenige globale Anbieter (cobalt: DRC 73%; nmc/nca: China ≥95% PCAM)
    import_dependency: bool       # [literaturbasierte Regel] True wenn Material import-abhängig UND Segment != Upstream (USGS NIR)
    import_origin_region: str     # [simuliert] z.B. "Africa/DRC", "South America / Australia"
    lead_time_weeks: int          # [simuliert] Vorlaufzeit: Upstream=12W, BGM=8W, Cell=6W, Downstream=4W


class FacilityData(TypedDict):
    """
    Aufbereitete Kapazitäts- und Vulnerabilitätsdaten je Facility.
    Wird vom Data Retrieval Agent aus facilities_clean.csv berechnet.
    """
    capacity: float               # Produktionskapazität in MT/yr (0.0 wenn unbekannt)
    capacity_source: CapacitySource
    capacity_known: bool          # True = naatbatt; False = unknown (unbekannt, nicht Null)
    supplier_concentration: bool
    import_dep: bool
    lead_time_norm: float         # lead_time_weeks / 12, normiert auf 0–1
    capacity_share: float         # Rohanteil an Gesamtkapazität (0–1); 0 wenn unbekannt oder nicht vergleichbar
    resilience_discount: float    # 0–0.5 (basiert auf AltCapacityRatio)


class Facility(TypedDict):
    """
    Hochrisiko-Facility im Top-3-Ranking des Syntheseberichts.
    Enthält berechnete Risikodimensionen für Nachvollziehbarkeit.
    """
    id: str
    company: str
    facility_name: str
    segment: Segment
    city: str
    country: str
    state: str                    # US-Bundesstaat oder Provinz (für Karten-Tooltip)
    latitude: float               # Für Streamlit-Kartenmarkierung
    longitude: float              # Für Streamlit-Kartenmarkierung
    risk_score: float             # Rohwert: Severity × TierWeight × Vulnerability × (1 − ResilienceDiscount)
    risk_score_normalized: float  # Normiert auf 0–100 (÷ theoretisches Maximum 5.0 × 20)
    tier_weight: float            # 1.0 / 0.6 / 0.35 / 0.15  (distance-based, see DISTANCE_WEIGHT)
    vulnerability: float          # 0–1 (gewichtete Summe aus 4 Dimensionen)
    resilience_discount: float    # 0–0.5 (basiert auf AltCapacityRatio)


class GlobalMetrics(TypedDict):
    """
    Kapazitätskennzahlen für den Berichts-Header — zwei Bezugsgrößen.
    NA = Nordamerika (NAATBatt-Scope); Global = USGS-Weltproduktion (nur MT-Materialien).
    Werden unabhängig vom RiskScore-Ranking berechnet.
    """
    # Nordamerika-Scope (NAATBatt)
    betroffene_kapazitaet_na_pct: float    # Σ betroffene / Σ NAATBatt-Gesamtkapazität × 100
    alternative_kapazitaet_na_pct: float   # Σ Alternativ / Σ NAATBatt-Gesamtkapazität × 100
    # Global-Scope (USGS — nur für Upstream/Midstream-BGM in MT sinnvoll)
    betroffene_kapazitaet_global_pct: float   # Σ betroffene NAATBatt / USGS Weltproduktion × 100
    alternative_kapazitaet_global_pct: float  # Σ Alternativ NAATBatt / USGS Weltproduktion × 100


# ──────────────────────────────────────────────────────────────────────────────
#  LangGraph Pipeline State
# ──────────────────────────────────────────────────────────────────────────────

class PipelineState(TypedDict, total=False):
    """
    Gemeinsamer State aller 6 Agents im LangGraph-Pipeline.

    Konvention:
    - total=False → alle Felder optional; ungesetzte Felder sind None/fehlen
    - Felder werden schrittweise befüllt, nie überschrieben (außer Validation-Retry)
    - Bei Retry (failure_type="minor") werden [S]+[V]-Felder zurückgesetzt
    - Bei Neustart (failure_type="severe") werden [R]+[N]+[D]+[S]+[V]-Felder zurückgesetzt

    Feldgruppen:
      [SYS] System / Laufzeit
      [I]   Intake Agent
      [R]   Risk Assessment Agent
      [N]   Network Agent
      [D]   Data Retrieval Agent
      [S]   Synthesis Agent
      [V]   Validation Agent
    """

    # ── [SYS] Systemeingabe — Raw Input ─────────────────────────────────────
    raw_input: str
    # Ursprünglicher Text: Nachrichtenartikel (Trigger A) oder Nutzeranfrage (Trigger B)

    # ── [I] Intake Agent — LLM ───────────────────────────────────────────────
    relevant: bool
    trigger_type: TriggerType
    material: str                      # Erkanntes Schlüsselmaterial, z.B. "cobalt"
    region: str                        # Betroffene Region, z.B. "Africa/DRC"
    keywords: list[str]
    filtered_text: str                 # Bereinigter, relevanter Nachrichtentext

    # ── [R] Risk Assessment Agent — LLM ─────────────────────────────────────
    severity: int                      # 1 (gering) – 5 (kritisch)
    risk_type: RiskType
    affected_material: str             # Bestätigt oder vom LLM präzisiert
    affected_region: str               # Bestätigt oder vom LLM präzisiert
    origin_tier: Segment               # Lieferkettenstufe des Ereignisursprungs (LLM)
    reason: str                        # LLM-Begründung; Pflichtfeld für Validation

    # ── [N] Network Agent — Deterministic (NetworkX) ─────────────────────────
    affected_nodes: list[Node]         # MaterialMatch=True AND RegionMatch=True
    alt_nodes: list[Node]              # Gleiches Material, NICHT betroffene Region
    tier_weights: dict[str, float]     # {facility_id: TierWeight}
    downstream_fanout: dict[str, int]  # {facility_id: Anzahl nachgelagerter Nodes}

    # ── [D] Data Retrieval Agent — Deterministic (CSV + Arithmetik) ──────────
    facility_data: dict[str, FacilityData]  # {facility_id: FacilityData}
    betroffene_kapazitaet_pct: float
    alternative_kapazitaet_pct: float

    # ── [S] Synthesis Agent — LLM ────────────────────────────────────────────
    risk_report: str                   # Vollständiger Risikobericht mit Quellenangaben
    top3_facilities: list[Facility]    # Absteigend nach risk_score_normalized
    risk_scores: dict[str, float]      # {facility_id: risk_score_normalized (0–100)}
    global_metrics: GlobalMetrics

    # ── [V] Validation Agent — LLM ───────────────────────────────────────────
    valid: bool
    failure_type: Optional[FailureType]  # None = valide
    issues: list[str]                  # Konkrete Beanstandungen (leer wenn valid=True)
    iteration: int                     # Zähler; Pipeline bricht bei iteration > 2 ab


# ──────────────────────────────────────────────────────────────────────────────
#  Konstanten für deterministisch berechnete Felder
# ──────────────────────────────────────────────────────────────────────────────

TIER_ORDER: dict[str, int] = {
    "Upstream":       0,
    "Midstream-BGM":  1,
    "Midstream-Cell": 2,
    "Downstream":     3,
}

DISTANCE_WEIGHT: dict[int, float] = {
    0: 1.0,   # Gleiche Stufe wie Ereignisursprung → direkt betroffen
    1: 0.6,   # Eine Stufe entfernt
    2: 0.35,  # Zwei Stufen entfernt
    3: 0.15,  # Drei Stufen entfernt
}


def compute_tier_weight(facility_segment: Segment, origin_tier: Segment) -> float:
    """Berechnet TierWeight als Funktion der Stufen-Distanz zum Ereignisursprung."""
    distance = abs(TIER_ORDER[facility_segment] - TIER_ORDER[origin_tier])
    return DISTANCE_WEIGHT[distance]

VULNERABILITY_WEIGHTS: dict[str, float] = {
    "import_dependency":  0.30,
    "supplier_concentration": 0.30,
    "capacity_share":     0.25,
    "lead_time_norm":     0.15,
}

LEAD_TIME_NORM_DIVISOR: float = 12.0
# lead_time_norm = lead_time_weeks / LEAD_TIME_NORM_DIVISOR

RESILIENCE_DISCOUNT_CAP: float = 0.5
# ResilienceDiscount = min(AltCapacityRatio / 2, RESILIENCE_DISCOUNT_CAP)

RISK_SCORE_MAX_THEORETICAL: float = 5.0
# Severity(5) × TierWeight(1.0) × Vulnerability(1.0) × (1−0) = 5.0
# risk_score_normalized = risk_score / RISK_SCORE_MAX_THEORETICAL * 100

MAX_VALIDATION_ITERATIONS: int = 2

# ── USGS Weltproduktion (Quelle: USGS Mineral Commodity Summaries 2026, Feb 2026) ─
# Einheit: MT/yr (Minenproduktion, Berichtsjahr 2025). Nur für Upstream/Midstream-BGM-Vergleich geeignet.
# Midstream-Cell (GWh) und Downstream werden NICHT mit diesen Zahlen verglichen.
USGS_GLOBAL_PRODUCTION_MT: dict[str, float] = {
    "cobalt":     310_000,    # MT, Weltminenproduktion 2025; DRC 73 % + Indonesia 14 %
    "lithium":    290_000,    # MT (Lithiumgehalt, ohne US-Produktion), Weltminenproduktion 2025; +31 % ggü. 2024
    "graphite": 1_800_000,    # MT, Weltminenproduktion 2025; China ~77 %
    "nickel":   3_900_000,    # MT, Weltminenproduktion 2025; +5 % ggü. 2024
    "manganese":21_000_000,   # MT, Weltminenproduktion 2025 (Schätzung)
    "copper":   23_000_000,   # MT, Weltminenproduktion 2025 (Schätzung)
}
# Verwendung: betroffene_kapazitaet_global_pct = Σ affected_capacity / USGS_GLOBAL_PRODUCTION_MT[material] × 100
# Hinweis: NAATBatt-Kapazitäten decken nur nordamerikanische Anlagen ab → global% ist ein konservativer Untergrenzenwert.
