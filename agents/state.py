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

ExposureType = Literal["direct", "propagated"]
"""
direct     → Facility ist selbst Seed-Node (MaterialMatch + RegionMatch erfüllt);
             supply_chain_paths-Eintrag hat Länge 1 (nur die Facility selbst).
propagated → Facility wurde erst über BFS-Traversierung im Wissensgraphen erreicht
             (nachgelagert zu einem Seed-Node); Pfadlänge > 1.
"""

SeedGenerationStatus = Literal[
    "rule_matched", "entity_matched", "entity_ambiguous", "no_seed_found",
    "entity_non_material", "llm_fallback_used"
]
"""
rule_matched         → mindestens ein Root-Seed über Material+Region-Matching gefunden (Strategy A).
entity_matched       → mindestens ein Seed über Firmennamen-Matching gefunden (Strategy B) —
                       facility-spezifisches Ereignis (z.B. "Fire at Panasonic Kansas plant"),
                       unabhängig von import_dependency/Region.
entity_ambiguous     → eine Firma wurde im Text erkannt, aber sie hat mehrere Anlagen im Graph
                       und der genannte Ort (falls vorhanden) reicht nicht aus, um EINE eindeutig
                       zu bestimmen. Bewusst NICHT geraten — siehe architecture.md "Vorschlag:
                       Multi-Strategie Seed Generator", Punkt "nicht raten".
no_seed_found        → kein Seed gefunden (weder Strategy A noch B). WICHTIG: das ist NICHT
                       gleichbedeutend mit "keine NA-Exposition" — der aktuelle Matching-Mechanismus
                       deckt nur zwei Ereignistypen ab (importabhängige Rohstoffstörung; benannte
                       Anlagenstörung), siehe architecture.md "Bekannte Grenzen des
                       Seeding-Mechanismus". Muss im Bericht/UI explizit von einem echten
                       Nullbefund unterschieden werden.
entity_non_material  → Strategy B hat die genannte Firma eindeutig einer Anlage zugeordnet, aber
                       diese Anlage ist als `non_active_material` markiert (mechanischer/
                       sicherheitstechnischer Komponentenlieferant, z.B. Klebstoffe, Dichtungen,
                       BMS-Module — kein Verarbeiter von cobalt/lithium/nickel/graphite/manganese).
                       Das Materialfluss-Risikomodell dieses Systems ist auf diese Anlage nicht
                       anwendbar (2026-07-31, docs/open_issues.md P16). Bewusst NICHT wie
                       entity_matched behandelt, sonst würde z.B. "Brand bei ArlanXEO" eine
                       Batterie-Rohstoff-Risikoausbreitung vortäuschen, obwohl ArlanXEO keine
                       Risikomaterialien verarbeitet.
llm_fallback_used   → Strategy C griff: weder A noch B fanden etwas, aber es gab eine Region;
                       ein deterministischer Retrieval-Schritt (find_facilities_by_region in
                       tools.py) lieferte eine geschlossene Kandidatenliste realer Anlagen in
                       der Region, und die LLM wählte daraus aus (kann keine Anlage erfinden —
                       jede zurückgegebene ID wird gegen die Kandidatenliste re-validiert).
                       Umgesetzt 2026-08-06, siehe docs/architecture.md "Vorschlag:
                       Multi-Strategie Seed Generator" und docs/open_issues.md P19.
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
    import_origin_region: str     # [simuliert] JSON-String {material: Herkunftsland}, z.B.
                                   # '{"cobalt": "Democratic Republic of the Congo", "manganese": "South Africa"}'
                                   # — pro Material, nicht pro Anlage (2026-08-06 Fix, siehe
                                   # infer_import_origin() in build_facilities.py)
    lead_time_weeks: int          # [simuliert] Vorlaufzeit: Upstream=12W, BGM=8W, Cell=6W, Downstream=4W
    direct_supplier_count: int    # In-Degree im Graph = Anzahl direkt verbundener Knoten der
                                   # jeweils vorgelagerten Stufe (Downstream→Cell, Midstream-BGM→
                                   # Upstream; Upstream/Midstream-Cell haben eigene, kapazitätsbasierte
                                   # Kennzahlen und nutzen dieses Feld nicht — siehe
                                   # single_source_dependency in FacilityData). Bis 2026-07-31 nur für
                                   # Downstream verwendet (damals noch "cell_supplier_count" genannt),
                                   # seit 2026-08-03 auch für Midstream-BGM (siehe FacilityData).
                                   # NA-Graph-Umfang: reflektiert nur die im NAATBatt-Datensatz
                                   # enthaltenen (nordamerikanischen) Lieferanten der Vorstufe, nicht
                                   # die reale globale Lieferantenzahl.


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
    capacity_share: float         # Rohanteil an Gesamtkapazität (0–1); nur Upstream/Midstream-
                                   # Cell mit capacity_known=True; sonst 1.0 (Worst-Case-Default,
                                   # nicht 0 — siehe data_retrieval_agent.py). Seit 2026-08-03
                                   # NICHT mehr Teil von Vulnerability, sondern alleinige
                                   # Grundlage von resilience_discount (siehe unten) — Wechsel
                                   # löst die alte Richtungsspannung auf ("großer Marktanteil =
                                   # großes Vulnerability" brauchte die umständliche Erklärung
                                   # "systemischer Schaden, nicht Eigenrobustheit"; als
                                   # Resilience-Frage gestellt ist die Richtung intuitiv: großer
                                   # Marktanteil = niemand kann einspringen = kleiner Rabatt).
    single_source_dependency: float  # [2026-07-31, seit 2026-08-03 für ALLE vier Tiers]
                                      # 1 / direct_supplier_count (0 wenn direct_supplier_count=0,
                                      # strukturell immer der Fall bei Upstream — Graph-Wurzel, kein
                                      # Vorstufen-Lieferant). Seit 2026-08-03 Teil von Vulnerability
                                      # (0.25 Gewicht, siehe VULNERABILITY_WEIGHTS) — eigene
                                      # Lieferantenanzahl ist ein Selbstbezug-Fragilitätssignal
                                      # ("hängt DIESE Anlage an wenigen Lieferanten"), nicht ein
                                      # Systemwiederherstellungssignal (das übernimmt jetzt
                                      # capacity_share/resilience_discount, siehe oben/unten).
                                      # WICHTIG: reflektiert nur NA-Graph-Konnektivität — ein Standort
                                      # kann in der Realität zusätzliche, hier nicht erfasste
                                      # außer-nordamerikanische Lieferanten haben (gleiche
                                      # Nordamerika-Scope-Einschränkung wie an anderer Stelle dokumentiert).
    resilience_discount: float    # 0–0.5, alle vier Tiers einheitlich, seit 2026-08-03 (gleicher
                                   # Tag) CapacityShare-basiert statt SingleSourceDependency-basiert:
                                   # (1 − capacity_share) / 2. Kein Sonderfall für "unbekannt" nötig
                                   # (anders als die alte SSD-Version mit ihrem
                                   # `if single_source_dependency > 0`-Guard): capacity_share ist
                                   # schon immer in [0,1] UND ihr eigener Worst-Case-Default (1.0)
                                   # ergibt hier automatisch (1-1)/2=0.0 — genau das gewünschte
                                   # "kein Rabatt bei Unklarheit"-Verhalten, ohne extra Verzweigung.
                                   # Kein min(...,0.5)-Cap nötig: capacity_share in [0,1] garantiert
                                   # (1-capacity_share)/2 liegt schon von selbst in [0,0.5]. Ersetzt
                                   # die frühere AltCapacityRatio-Methode (globaler
                                   # Alternativkapazitäts-Pool), die für propagierte Knoten die
                                   # falsche Frage beantwortete.
    product: str                  # z.B. "Nickel pellets, powder or rounds" — rohe NAATBatt-
                                   # `product`-Spalte, ungekürzt
    product_type: str             # z.B. "Cathode Battery Grade Materials" — NAATBatt-Kategorie
    brief_profile: str            # NAATBatt-Firmenbeschreibung, freier Text
    production_units: str         # z.B. "MT contained Ni/yr" — Einheit zu `capacity` oben
    # Diese vier (2026-08-12) ergänzt, nachdem Top-3-Begründungen als austauschbare
    # Textbausteine kritisiert wurden (docs/open_issues.md P37) — selbes Problem wie die vier
    # Vulnerability-Teilterme oben: die Daten standen in facilities_clean.csv längst zur
    # Verfügung, wurden aber nie bis zum Synthesis-Agent-Prompt durchgereicht, sodass das LLM
    # keine anlagenspezifischen Fakten (was stellt diese Anlage konkret her, wie groß ist sie
    # wirklich) zur Verfügung hatte und zwangsläufig generisch formulieren musste.


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
    # Die 4 Vulnerability-Terme einzeln (2026-08-07, vorher nur die Summe gespeichert —
    # der Rechenweg war zwar vollständig, aber gleich nach der Gewichtung verworfen, siehe
    # synthesis_agent.py::_compute_scores). Rohwerte 0–1, VOR Gewichtung mit
    # VULNERABILITY_WEIGHTS — für Nachvollziehbarkeit, welcher Term den Score treibt.
    import_dep_term: float
    supplier_conc_term: float
    single_source_dependency_term: float
    lead_time_norm_term: float
    resilience_discount: float    # 0–0.5 (basiert auf AltCapacityRatio)
    supply_path: str              # z.B. "Kamoto Copper (Upstream) → CATL Cathode (Midstream-BGM) → ..."
    exposure_type: ExposureType    # "direct" = Seed-Node selbst (MaterialMatch+RegionMatch) | "propagated" = via BFS erreicht
    capacity: float                # siehe FacilityData oben — für anlagenspezifische Top-3-Begründungen (P37)
    product: str
    product_type: str
    brief_profile: str
    production_units: str


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


class StrategyCCandidate(TypedDict):
    """One facility network_agent.py's find_facilities_by_region() retrieved as a Strategy C
    candidate — kept regardless of whether the LLM selected it, so a rejected candidate is
    visible too, not just the winners."""
    facility_id: str
    company: str
    city: str
    state: str
    country: str
    segment: str
    material_keywords: str
    selected: bool  # True if the LLM included this id in selected_facility_ids


class StrategyCTrace(TypedDict):
    """Strategy C (LLM-assisted regional fallback, 2026-08-07) — full record of what was
    retrieved and why the LLM did/didn't pick from it, for report/UI transparency
    (docs/open_issues.md P22). Only present when seed_generation_status="llm_fallback_used"."""
    region_queried: str
    candidates: list[StrategyCCandidate]
    llm_reasoning: str


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
    # filtered_text entfernt (2026-08-10): war eine "2-3 Sätze"-Kurzfassung ohne festen
    # Code-Längenlimit, aber ohne funktionalen Leser mehr, nachdem risk_assessment_agent.py
    # auf direktes raw_input umgestellt wurde (siehe dessen Docstring / docs/open_issues.md
    # P29-Nachtrag) — nur noch ui/app.py's Debug-Ansicht hatte es je gelesen.
    mentioned_company: Optional[str]   # Konkret genannte Firma bei Facility-Ereignissen, sonst None
    mentioned_location: Optional[str]  # Stadt/Bundesstaat neben der Firma genannt, sonst None
    additional_events_note: Optional[str]
    # Gesetzt (2026-08-08), wenn der Eingabetext MEHRERE eigenständige, potenziell relevante
    # Ereignisse enthält (z.B. ein langer Marktbericht, der sowohl einen Lithiumpreis-Rückgang
    # in Chile als auch eine Kobalt-Förderstörung in der DRK erwähnt). Die Pipeline verarbeitet
    # pro Lauf immer nur EIN Ereignis (material/region/severity/... sind Skalarfelder, kein
    # Array) — dieses Feld macht sichtbar, dass ein zweites, nicht verarbeitetes Ereignis
    # existiert, statt es stillschweigend zu verwerfen (gleiches Transparenzprinzip wie bei
    # nicht unterstützten Ereignistypen, siehe docs/open_issues.md P21). None, wenn nur ein
    # Kandidat gefunden wurde.

    # ── [R] Risk Assessment Agent — LLM ─────────────────────────────────────
    severity: int                      # 1 (gering) – 5 (kritisch)
    risk_type: RiskType
    origin_tier: Segment               # Lieferkettenstufe des Ereignisursprungs (LLM)
    reason: str                        # LLM-Begründung; Pflichtfeld für Validation
    # tool_calls (query_facilities-Nachvollziehbarkeit) wieder entfernt (2026-08-07, gleicher
    # Tag): der Tool-Aufruf selbst wurde noch am selben Tag zurückgebaut, siehe unten und
    # risk_assessment_agent.py Docstring — severity/risk_type/origin_tier/reason profitieren
    # keiner von ihnen von einer Anlagendaten-Abfrage, also gibt es hier nichts mehr
    # nachzuvollziehen.
    # affected_material/affected_region entfernt (2026-08-07): dieser Agent produzierte sie
    # früher separat ("bestätigt oder präzisiert"), aber empirisch (8 Testfälle, siehe
    # risk_assessment_agent.py Docstring) wich das Ergebnis nie sinnvoll vom [I]-Feld
    # material/region ab — reines Duplikat. Alle nachgelagerten Agents (network_agent.py
    # und weiter) lesen jetzt direkt material/region aus [I], kein separates Feld mehr nötig.

    # ── [N] Network Agent — Deterministic (NetworkX) ─────────────────────────
    affected_nodes: list[Node]         # MaterialMatch=True AND RegionMatch=True
    alt_nodes: list[Node]              # Gleiches Material, NICHT betroffene Region
    tier_weights: dict[str, float]     # {facility_id: TierWeight}
    downstream_fanout: dict[str, int]  # {facility_id: Anzahl nachgelagerter Nodes}
    supply_chain_paths: dict[str, list[str]]  # {facility_id: [seed_id, ..., facility_id]}
    total_network_facilities: int     # Alle Facilities im Graph, unabhängig vom Material (Datensatz-Konstante)
    seed_generation_status: SeedGenerationStatus
    # Nur gesetzt wenn seed_generation_status="llm_fallback_used" (Strategy C, 2026-08-07).
    # Vorher wurde die LLM-Begründung + vollständige Kandidatenliste sofort verworfen
    # (network_agent.py::_llm_assisted_seed_fallback gab nur die ausgewählten IDs zurück) —
    # jetzt für Nachvollziehbarkeit im Bericht/UI festgehalten.
    strategy_c_trace: StrategyCTrace

    # ── [D] Data Retrieval Agent — Deterministic (CSV + Arithmetik) ──────────
    facility_data: dict[str, FacilityData]  # {facility_id: FacilityData}
    betroffene_kapazitaet_pct: float
    alternative_kapazitaet_pct: float

    # ── [S] Synthesis Agent — LLM ────────────────────────────────────────────
    risk_report: str                   # Vollständiger Risikobericht mit Quellenangaben
    top3_facilities: list[Facility]    # Absteigend nach risk_score_normalized
    risk_scores: dict[str, float]      # {facility_id: risk_score_normalized (0–100)}
    global_metrics: GlobalMetrics
    risk_score_stats: dict[str, float]  # {"max"/"median": RiskScore} — reale Verteilungswerte, siehe
                                         # _compute_risk_score_stats() in synthesis_agent.py. Ersetzt
                                         # 2026-08-03 das alte risk_tier_counts (High/Medium/Low): jene
                                         # Funktion las nie die tatsächlichen Score-Werte, sondern nur
                                         # len(risk_scores) — lieferte für jedes Ereignis eine fixe
                                         # ~20/30/50-Aufteilung unabhängig von der echten Verteilung.

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
    "single_source_dependency": 0.25,   # was "capacity_share" — swapped 2026-08-03, see
                                         # data_retrieval_agent.py's resilience_discount comment
    "lead_time_norm":     0.15,
}
# Weights rank each factor's real-world driving power for supply chain risk (not our
# confidence in the underlying data): ImportDep/SupplierConcentration are the two biggest
# levers (geopolitical exposure, market concentration); SingleSourceDependency is real but
# narrower in scope (this facility's own supplier count, not the whole material market);
# LeadTime_norm is the weakest/most-derived signal (a rule-based proxy for recovery time, not
# a driver of whether disruption happens at all).

LEAD_TIME_NORM_DIVISOR: float = 21.0
# lead_time_norm = min(lead_time_weeks / LEAD_TIME_NORM_DIVISOR, 1.0)
# Raised from 12.0 to 21.0 (2026-08-03): 12 was the old segment-only max (Upstream base).
# Since lead_time_weeks now also adds material/region/data-quality adjustments (see
# MATERIAL_LEAD_TIME_ADJUSTMENT in build_facilities.py), the real max is
# 12 (Upstream) + 4 (cobalt) + 3 (Africa/China region) + 2 (unknown capacity) = 21. Using
# the OLD divisor of 12 here would have saturated every high-adjustment combination (e.g.
# Midstream-BGM + cobalt: 8+4+3=15, already > 12) to the same capped 1.0 — re-collapsing
# exactly the differentiation this refinement was meant to add. The min(...,1.0) cap still
# applies for defense-in-depth, but with the divisor matching the true max it should no
# longer trigger in practice.

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

# ── Material-level classification (2026-08-03) ──────────────────────────────────
# Mirrors data_prep/build_facilities.py's IMPORT_MATERIALS / HIGH_CONCENTRATION_MATERIALS
# (kept duplicated rather than cross-imported — build_facilities.py is a standalone offline
# script run from data_prep/, importing agents.state there would need extra sys.path setup
# for a small win; MUST be kept in sync manually if either list changes).
#
# Used by synthesis_agent.py's _compute_scores() to give PROPAGATED facilities (exposed only
# via graph traversal, not by their own material_keywords) a correct ImportDep/
# SupplierConcentration value for the EVENT's actual affected_material, instead of either (a)
# checking the facility's own keywords — which will be empty/irrelevant for a facility several
# tiers downstream of the real material (e.g. Tesla doesn't carry a "cobalt" keyword, but IS
# exposed to a cobalt event through its battery supply chain) — or (b) blanket-assuming True
# for every propagated facility regardless of what material is even involved (an earlier,
# looser version of this fix — see git history — that conflated "the event's material is
# genuinely import-dependent" with "we don't know, so assume the worst", losing real signal
# for the common S1/S2-style material-driven case).
IMPORT_MATERIALS: set[str] = {"cobalt", "nickel", "lithium", "manganese", "graphite"}
HIGH_CONCENTRATION_MATERIALS: set[str] = {"cobalt", "nmc", "nca"}
# The full controlled vocabulary intake_agent.py's LLM is instructed to choose `material` from
# (see its SYSTEM_PROMPT) — used to distinguish "a real tracked material that just isn't
# import-dependent/concentrated" (currently only copper) from "not a recognized material at
# all" (e.g. a Strategy B facility-specific event's free-text label like "battery cells", which
# was never given a literature classification one way or the other). Only the latter falls
# back to the precautionary "unknown -> True" default; a real "False" (like copper) is kept as
# a real fact, not silently overridden.
KNOWN_MATERIALS: set[str] = IMPORT_MATERIALS | {"copper"}
