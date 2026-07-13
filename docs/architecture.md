# Systemarchitektur — SC Risk System

## Überblick

```
          Trigger A                    Trigger B
       (eingehende News)           (Nutzeranfrage)
              │                          │
              └──────────┬───────────────┘
                         ▼
                   ┌─────────────┐
                   │ Intake Agent │  → irrelevant → verwerfen / Hinweis
                   └──────┬──────┘
                          │ relevant
                          ▼
                ┌──────────────────────┐
                │ Risk Assessment Agent │
                └──────────┬───────────┘
                           │
                           ▼
                  ┌─────────────────┐
                  │  Network Agent   │  (NetworkX + Knowledge Graph)
                  └────────┬────────┘
                           │
                           ▼
              ┌─────────────────────────┐
              │  Data Retrieval Agent    │  (NAATBatt DB)
              └────────────┬────────────┘
                           │
                           ▼
                ┌───────────────────┐
                │  Synthesis Agent   │
                └────────┬──────────┘
                         │
                         ▼
               ┌──────────────────────┐
               │  Validation Agent     │
               └──────┬───────────────┘
                      │ ✗ (max. 2 Iter.)
                      ├──────────────────→ zurück zu Synthesis (oder Risk Assessment)
                      │ ✓
                      ▼
               Risikobericht (Streamlit)
```

---

## Agent-Beschreibungen

### 1. Intake Agent
- **Input**: Nachrichtentext (Trigger A) oder Nutzeranfrage (Trigger B)
- **Output**: Gefilterter, relevanter Input + extrahierte Schlüsselbegriffe
- **Logik**: Prüft Relevanz für Batterie-Lieferkette; bei Trigger B ggf. NewsAPI-Abfrage

### 2. Risk Assessment Agent
- **Input**: Gefilterter Nachrichteninhalt
- **Output**: Risikoklassifikation (Art, Schweregrad, betroffene Materialien/Stufen) + Begründung
- **Risikoarten**: Lieferunterbrechung, Preisvolatilität, regulatorisches Risiko, ...

### 3. Network Agent
- **Input**: Risikoklassifikation + betroffene Materialien/Regionen
- **Output**: Liste betroffener Unternehmen + mögliche Alternativlieferanten
- **Graph**: `data/knowledge_graph.json` (gebaut aus NAATBatt via `data_prep/build_graph.py`)
- **Hinweis**: NAATBatt enthält keine direkten Lieferbeziehungen → simulierte Kanten, im Bericht als solche markiert

### 4. Data Retrieval Agent
- **Input**: Liste betroffener Unternehmen (vom Network Agent)
- **Output**: Produktionskapazitäten aus NAATBatt (`data/facilities_clean.csv`)
- **Hinweis**: Fehlende Daten → simulierte Werte, explizit markiert

### 5. Synthesis Agent
- **Input**: Outputs von Agent 2–4
- **Output**: Strukturierter Risikobericht mit:
  - Auslösendes Ereignis + Quellenangabe
  - Betroffene Materialien & Lieferkettenstufen
  - Max. 3 Hochrisiko-Unternehmen + Handlungsempfehlungen
  - Vollständige Begründung je Risikoaussage

### 6. Validation Agent
- **Prüfkriterien**:
  1. Jede Risikoaussage mit Quelle belegt?
  2. Genannte Unternehmen mit NAATBatt-Eintrag referenziert?
  3. Nachvollziehbare Begründung vorhanden?
- **Bei Fehler**: max. 2 Iterationen; Fehlertyp bestimmt den Pfad:
  - **Leichter Fehler** (`failure_type: minor`) — fehlende Quelle, Entität nicht belegt → Retry ab Synthesis Agent
  - **Schwerwiegender Fehler** (`failure_type: severe`) — falsche Materialzuordnung, Klassifikation inkonsistent → Neustart ab Risk Assessment Agent

---

## Schnittstellenlogik — Datenaustausch zwischen Agenten


### Überblick: Datenfluss

```
Intake Agent 
  → relevant, trigger_type, material, region, keywords, filtered_text
Risk Assessment Agent
  → severity, risk_type, affected_material, affected_region, reason
Network Agent
  → affected_nodes, alt_nodes, tier_weights, downstream_fanout
Data Retrieval Agent
  → facility_data, BetroffeneKapazität%, AlternativeKapazität%
Synthesis Agent
  → risk_report, top3_facilities, risk_scores, global_metrics
Validation Agent
  → valid, failure_type, issues, iteration
```

### Agent-Schnittstellen im Detail

#### 1 → 2: Intake Agent Output
*Quelle: **LLM***

| Feld | Typ | Quelle | Beschreibung |
|------|-----|--------|-------------|
| `relevant` | `bool` | LLM | Ist die Meldung für die Batterie-Lieferkette relevant? |
| `trigger_type` | `str` (`"A"` \| `"B"`) | LLM | A = automatische News, B = Nutzeranfrage |
| `material` | `str` | LLM | Erkanntes Schlüsselmaterial (z.B. `"cobalt"`) |
| `region` | `str` | LLM | Betroffene Region (z.B. `"Africa/DRC"`) |
| `keywords` | `list[str]` | LLM | Extrahierte Schlüsselbegriffe |
| `filtered_text` | `str` | LLM | Bereinigter, relevanter Nachrichtentext |

#### 2 → 3: Risk Assessment Agent Output
*Quelle: **LLM***

| Feld | Typ | Quelle | Beschreibung |
|------|-----|--------|-------------|
| `severity` | `int` (1–5) | LLM | Schweregrad des Ereignisses |
| `risk_type` | `str` | LLM | Risikoart: `supply_disruption` \| `price_volatility` \| `regulatory` \| `logistics` \| `weather` |
| `affected_material` | `str` | LLM | Betroffenes Material (bestätigt oder erweitert) |
| `affected_region` | `str` | LLM | Betroffene Region (bestätigt oder präzisiert) |
| `origin_tier` | `Segment` | LLM | Lieferkettenstufe des Ereignisursprungs: `Upstream` \| `Midstream-BGM` \| `Midstream-Cell` \| `Downstream` |
| `reason` | `str` | LLM | Begründung der Severity-Einschätzung (Pflichtfeld für Validierung) |

#### 3 → 4: Network Agent Output
*Quelle: **Deterministic** (NetworkX-Graphtraversal, kein LLM)*

| Feld | Typ | Quelle | Beschreibung |
|------|-----|--------|-------------|
| `affected_nodes` | `list[Node]` | Deterministic | Betroffene Facilities (MaterialMatch + RegionMatch = True) |
| `alt_nodes` | `list[Node]` | Deterministic | Unbetroffene Alternativen (gleiches Material, andere Region) |
| `tier_weights` | `dict[str, float]` | Deterministic | TierWeight je Facility-ID (distanzbasiert: 1.0 / 0.6 / 0.35 / 0.15) |
| `downstream_fanout` | `dict[str, int]` | Deterministic | Anzahl nachgelagerter Facilities je Upstream-Node (für Handlungsempfehlung) |

*Node-Felder:* `id`, `company`, `facility_name`, `segment`, `material_keywords`, `country`, `state`, `latitude`, `longitude`, `production_capacity_raw`, `capacity_source`, `import_dependency`, `import_origin_region`, `lead_time_weeks`

#### 4 → 5: Data Retrieval Agent Output
*Quelle: **Deterministic** (CSV-Lookup + Arithmetik, kein LLM)*

| Feld | Typ | Quelle | Beschreibung |
|------|-----|--------|-------------|
| `facility_data` | `dict[str, FacilityData]` | Deterministic | Aufbereitete Kapazitätsdaten je Facility-ID |
| `BetroffeneKapazität%` | `float` | Deterministic | Globalkennzahl: betroffene / Gesamtkapazität × 100 |
| `AlternativeKapazität%` | `float` | Deterministic | Globalkennzahl: alternative / Gesamtkapazität × 100 |

*FacilityData-Felder:* `capacity` (float), `capacity_source` (`"naatbatt"` \| `"unknown"`), `capacity_known` (bool — True wenn Wert bekannt), `supplier_concentration` (bool), `import_dep` (bool), `lead_time_norm` (float 0–1), `capacity_share` (float 0–1, nur Upstream/Cell wenn capacity_known; sonst 0.0), `resilience_discount` (float 0–0.5)

#### 5 → 6: Synthesis Agent Output
*Quelle: **LLM** (Berichtstext) + **Deterministic** (RiskScore-Ranking)*

| Feld | Typ | Quelle | Beschreibung |
|------|-----|--------|-------------|
| `risk_report` | `str` | LLM | Vollständiger Risikobericht mit Quellenangaben |
| `top3_facilities` | `list[Facility]` | Deterministic | Top-3 Facilities nach RiskScore (absteigend) |
| `risk_scores` | `dict[str, float]` | Deterministic | RiskScore (0–100) je Facility-ID |
| `global_metrics` | `dict` | Deterministic | `BetroffeneKapazität%`, `AlternativeKapazität%` für Report-Header |

*Facility-Felder:* `id`, `company`, `facility_name`, `segment`, `country`, `state`, `latitude`, `longitude` (Kartenmarkierung), `risk_score`, `risk_score_normalized`, `tier_weight`, `vulnerability`, `resilience_discount`

#### 6 → 7: Validation Agent Output
*Quelle: **LLM** (Prüfurteil) + **Deterministic** (Iterationszähler)*

| Feld | Typ | Quelle | Beschreibung |
|------|-----|--------|-------------|
| `valid` | `bool` | LLM | Besteht der Bericht alle Prüfungen? |
| `failure_type` | `str` \| `None` | LLM | `"minor"` (Retry Synthesis) \| `"severe"` (Neustart Risk Assessment) \| `None` (valide) |
| `issues` | `list[str]` | LLM | Liste konkreter Beanstandungen |
| `iteration` | `int` | Deterministic | Aktuelle Iterationsnummer (Abbruch bei > 2) |

---

## Technologie-Stack

| Komponente | Technologie | Status |
|-----------|-------------|--------|
| Sprache | Python | — |
| Agent Framework | LangGraph | ✅ entschieden 2026-05-11 → [ADR-001](decisions/ADR-001-framework-selection.md) |
| Graphanalyse | NetworkX | — |
| LLM (Produktion) | OpenAI GPT-4o-mini / Anthropic Claude | — |
| LLM (Entwicklung) | Llama via Groq / Ollama | — |
| Nachrichtendaten | NewsAPI.org | — |
| UI | Streamlit | — |

---

## Datenquellen

| Datei | Beschreibung |
|-------|-------------|
| `data/facilities_clean.csv` | Bereinigte NAATBatt-Anlagendaten (285 Anlagen, 199 Unternehmen) |
| `data/knowledge_graph.json` | Lieferkettengraph (Knoten: Unternehmen/Materialien, Kanten: Lieferstufen) |

---

## Risikobewertung

Details: [risk_model.md](risk_model.md)

```
RiskScore_i = Severity × TierWeight_i × Vulnerability_i × (1 − ResilienceDiscount_i)
```

**Vulnerability** = 0.30×ImportDep + 0.30×SingleSource + 0.25×CapacityShare + 0.15×LeadTime

**TierWeight** — distanzbasiert, abhängig vom `origin_tier` des Ereignisses:

```
TierWeight = DISTANCE_WEIGHT[ |TIER_ORDER[facility] − TIER_ORDER[origin_tier]| ]
```

| Distanz zum Ereignisursprung | TierWeight |
|-----------------------------|-----------|
| 0 (gleiche Stufe) | 1.0 |
| 1 | 0.6 |
| 2 | 0.35 |
| 3 | 0.15 |

Beispiel Kobaltstreik (origin_tier = Upstream): BGM → 0.6, Cell → 0.35, Downstream → 0.15

**ResilienceDiscount** = min(AltCapacityRatio / 2, 0.5)

Globale Kennzahlen (separat): `BetroffeneKapazität%`, `AlternativeKapazität%`

---

## Evaluationsszenarien

| # | Szenario | Material | Primärfilter |
|---|----------|----------|-------------|
| S1 | Kobaltminenstreik DRC | Kobalt | MaterialMatch + import_origin=Africa/DRC |
| S2 | Lithium-Exportbeschränkungen | Lithium | MaterialMatch + import_origin=SouthAmerica/AU |
| S3 | Hafenausfall | Mehrere | RegionMatch + import_dependency=True |
| S4 | Extremwetter | Mehrere | RegionMatch (betroffene Bundesstaaten) |
