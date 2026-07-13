# Testplan — SC Risk System

> Stand: 2026-05-24  
> Zweck: Qualitätssicherung des Multi-Agenten-Prototyps; Grundlage für Evaluationskapitel (Kap. 5)

---

## Testebenen im Überblick

```
Ebene 1: Unit Tests          → Jeder Agent isoliert, feste Ein-/Ausgaben
Ebene 2: Integrationstests   → Agent-Ketten, Datenweitergabe
Ebene 3: Szenario-Tests      → End-to-End, S1–S4
Ebene 4: Halluzinations-Tests → Entitätsvalidierung, Quellenpflicht
Ebene 5: Stabilitätstests    → Gleicher Input, mehrere Läufe
Ebene 6: Ablationsstudie     → Einzelne Komponenten deaktivieren (für Thesis)
```

---

## Ebene 1: Unit Tests (pro Agent)

### Intake Agent

| Test-ID | Input | Erwartetes Verhalten |
|---------|-------|----------------------|
| UT-I-01 | Nachrichtentext über Kobaltstreik im Kongo | `relevant=True`, `material=cobalt`, `region=DRC` |
| UT-I-02 | Nachrichtentext über Fußball-WM | `relevant=False`, Pipeline stoppt |
| UT-I-03 | Nachrichtentext über allgemeinen Rohstoffpreisanstieg | `relevant=True` (grenzwertig) |
| UT-I-04 | Leerer String / None | Fehlerbehandlung, kein Crash |
| UT-I-05 | Nutzeranfrage: „Wie riskant ist Kobalt aktuell?" | Trigger B erkannt, Keywords extrahiert |

### Risk Assessment Agent充

| Test-ID | Input | Erwartetes Ergebnis |
|---------|-------|----------------------|
| UT-R-01 | Bericht über vollständige Minenblockade, 6 Monate | `severity=5` |
| UT-R-02 | Bericht über kurzfristigen 2-Tage-Streik | `severity=2` |
| UT-R-03 | „Minor price fluctuation in lithium market" | `severity=1–2` |
| UT-R-04 | Severity-Begründung vorhanden? | `reason` Feld nicht leer |
| UT-R-05 | Materialerkennung: „DRC cobalt mine" | `material=cobalt`, `risk_type=supply_disruption` |

### Network Agent

| Test-ID | Input | Erwartetes Ergebnis |
|---------|-------|----------------------|
| UT-N-01 | `material=cobalt`, `region=Africa/DRC` | Nur Nodes mit `material_keywords` enthält `cobalt` |
| UT-N-02 | Alle zurückgegebenen Nodes | `MaterialMatch=True` für jeden Node |
| UT-N-03 | TierWeight-Zuweisung | Distanz 0→1.0, Distanz 1→0.6, Distanz 2→0.35, Distanz 3→0.15 (z. B. Upstream-Event: BGM=0.6, Cell=0.35, Down=0.15) |
| UT-N-04 | `material=cobalt` | Keine Lithium- oder Graphit-Facilities im Output |
| UT-N-05 | AltCapacityRatio S1 vs S2 | S2 (Lithium, 3 inländische Alternativen) > S1 (Kobalt, kaum Alternativen) |

### Data Retrieval Agent

| Test-ID | Input | Erwartetes Ergebnis |
|---------|-------|----------------------|
| UT-D-01 | Liste betroffener Facility-IDs | Alle IDs in `facilities_clean.csv` vorhanden |
| UT-D-02 | Facility mit fehlender Kapazität | `capacity_source=simulated`, Wert geschätzt |
| UT-D-03 | `capacity_source`-Feld | Entweder `naatbatt` oder `simulated`, kein anderer Wert |
| UT-D-04 | Kapazitätsberechnung | `BetroffeneKapazität%` = Summe betroffen / Summe gesamt × 100 |

### Synthesis Agent

| Test-ID | Input | Erwartetes Ergebnis |
|---------|-------|----------------------|
| UT-S-01 | Vollständige Agent-Outputs | Bericht enthält genau 3 Facilities im Top-3 Abschnitt |
| UT-S-02 | Jede Risikoaussage im Bericht | Mind. 1 Quellenangabe (Nachrichtenartikel oder NAATBatt-ID) |
| UT-S-03 | Top-3 Unternehmen | Alle 3 in `facilities_clean.csv` nachweisbar |
| UT-S-04 | RiskScore-Formel | Score = Severity × TierWeight × Vulnerability × (1 − ResilienceDiscount) |

### Validation Agent

| Test-ID | Input | Erwartetes Ergebnis |
|---------|-------|----------------------|
| UT-V-01 | Bericht mit erfundener Firma „BattCo International" | `valid=False`, Iteration 1 ausgelöst |
| UT-V-02 | Risikoaussage ohne Quellenangabe | `valid=False`, Synthesis-Retry |
| UT-V-03 | Valider Bericht (alle Prüfungen bestanden) | `valid=True`, kein Retry |
| UT-V-04 | 2 Fehlläufe hintereinander | Abbruch nach max. 2 Iterationen |
| UT-V-05 | Begründungsfeld leer | `valid=False` |

---

## Ebene 2: Integrationstests

### Datenweitergabe zwischen Agenten

| Test-ID | Kette | Prüfung |
|---------|-------|---------|
| IT-01 | Intake → Risk Assessment | `material` und `region` werden korrekt übergeben |
| IT-02 | Risk Assessment → Network | `severity` und `affected_material` im State vorhanden |
| IT-03 | Network → Data Retrieval | Facility-IDs vollständig und valide |
| IT-04 | Data Retrieval → Synthesis | Kapazitätsdaten mit `capacity_source` Flag |
| IT-05 | Synthesis → Validation → Synthesis | Retry-Schleife funktioniert, State korrekt zurückgesetzt |

### Feedback-Loop

| Test-ID | Beschreibung | Erwartetes Verhalten |
|---------|-------------|----------------------|
| IT-06 | Validation schlägt beim 1. Durchlauf fehl | Synthesis wiederholt, korrigierter Output |
| IT-07 | Validation schlägt 2× fehl | System bricht ab, gibt Fehlermeldung aus |
| IT-08 | Schwerwiegender inhaltlicher Fehler | Neustart ab Risk Assessment Agent |

---

## Ebene 3: Szenario-Tests (End-to-End)

Alle 4 Szenarien werden mit simulierten Nachrichtentexten ausgeführt.
Bewertung: manuell + automatische Feldprüfungen.

### S1 — Kobaltstreik DRC

**Input-Nachricht:**
> „Workers at a major cobalt mine in the Democratic Republic of Congo have initiated an open-ended strike
> following a breakdown in wage negotiations. The facility accounts for approximately 15% of regional output."

| Prüfung | Erwartetes Ergebnis |
|---------|----------------------|
| Erkanntes Material | `cobalt` |
| Erkannte Region | `Africa / DRC` |
| Severity | 4–5 |
| Betroffene Upstream-Nodes | Glencore cobalt (QC), Vale Canada cobalt (ON/MB/NL) |
| `import_dependency` der Top-3 | Alle `True` |
| AltCapacityRatio | Niedrig (wenige inländische Kobalt-Alternativen) |
| `BetroffeneKapazität%` | Plausibel (Schätzung: 40–70 % der kobaltabhängigen Kapazität) |

### S2 — Lithium-Exportbeschränkungen

**Input-Nachricht:**
> „The Chilean government has announced new regulations restricting lithium exports effective immediately,
> citing strategic resource protection. Affected volumes represent a significant share of North American imports."

| Prüfung | Erwartetes Ergebnis |
|---------|----------------------|
| Erkanntes Material | `lithium` |
| Severity | 3–4 |
| Inländische Alternativen identifiziert | Albemarle (NV, NC), Compass Minerals (UT) |
| AltCapacityRatio | Höher als S1 |
| RiskScore gesamt | Niedriger als S1 (durch höheren ResilienceDiscount) |
| **Kalibrierungscheck** | RiskScore_S1 > RiskScore_S2 bei gleichem Severity |

### S3 — Hafenausfall

**Input-Nachricht:**
> „Operations at the Port of Savannah have been suspended indefinitely following severe infrastructure damage.
> The port handles a significant share of battery material imports on the US East Coast."

| Prüfung | Erwartetes Ergebnis |
|---------|----------------------|
| Primärfilter | `import_dependency=True` + geografische Nähe |
| Betroffene Materialien | Mehrere (Kobalt, Nickel, Lithium — alle importabhängig) |
| `nearest_major_port` | Port of Savannah bei betroffenen Facilities |
| Karte | Ports in Blau dargestellt (S3-spezifisch) |

### S4 — Extremwetter

**Input-Nachricht:**
> „A series of severe storms has disrupted operations across multiple battery manufacturing facilities
> in Michigan and Ohio, with production halts expected to last several weeks."

| Prüfung | Erwartetes Ergebnis |
|---------|----------------------|
| Primärfilter | `state` in [MI, OH] |
| Betroffene Segmente | Midstream-BGM, Midstream-Cell (Fertigungsstandorte) |
| `import_dependency` | Nicht primärer Filter (geografisches Ereignis) |
| TierWeight der Top-3 | Midstream dominiert (BGM=0.6, Cell=0.35 bei Upstream-Event) |

---

## Ebene 4: Halluzinations-Tests

### 4.1 Entitätspräzision (automatisch messbar)

```
EntityPrecision = (Firmen im Bericht, die in facilities_clean.csv existieren) /
                  (Alle genannten Firmen im Bericht) × 100 %

Zielwert: 100 %
```

Implementierung: string matching + fuzzy matching (company name normalisiert).

### 4.2 Quellendeckung (automatisch messbar)

```
SourceCoverage = (Risikoaussagen mit ≥ 1 Quellenangabe) /
                 (Alle Risikoaussagen im Bericht) × 100 %

Zielwert: 100 %
```

### 4.3 Injektionstest (manuell)

| Test-ID | Injizierter Fehler | Erwartete Reaktion |
|---------|-------------------|-------------------|
| HT-01 | Erfundener Firmenname in Synthesis-Output | Validation Agent: `valid=False` |
| HT-02 | Risikoaussage ohne Quellenangabe | Validation Agent: Retry |
| HT-03 | Falsches Material (cobalt statt lithium) | Validation Agent oder Network Agent: Inkonsistenz erkannt |
| HT-04 | RiskScore > theoretischem Maximum | Synthesis: Normalisierungsfehler → fängt Unit Test UT-S-04 |

### 4.4 Grenzfall-Tests

| Test-ID | Input | Erwartetes Verhalten |
|---------|-------|----------------------|
| HT-05 | Material nicht in Datenbank (z.B. „Palladium") | Output: „Keine relevanten Facilities gefunden" |
| HT-06 | Keine betroffene Region im Graph | Leere Affected-Liste, kein Bericht generiert |
| HT-07 | Alle Supplier unbetroffener Alternativen | RiskScore nahe 0, kein False Positive |
| HT-08 | Nachricht über vergangenes Ereignis (vor 5 Jahren) | System verarbeitet, markiert als historisch |

---

## Ebene 5: Stabilitätstests (Self-Consistency)

Gleicher Input, 3 Läufe (LLM-Temperatur > 0 → Outputs variieren).

| Metrik | Akzeptanzkriterium |
|--------|-------------------|
| Severity-Varianz | ≤ 0,5 über 3 Läufe |
| Top-3 Überlappung | ≥ 2 von 3 Facilities identisch |
| `BetroffeneKapazität%` | Abweichung ≤ 5 % (rein rechnerisch, sollte deterministisch sein) |
| Material-Identifikation | 3/3 Läufe korrekt |

**Durchführung:** S1 und S2 jeweils 3× laufen lassen, Ergebnisse in Tabelle dokumentieren.
→ Direkt verwendbar in Kap. 5.4 (Diskussion und Limitationen).

---

## Ebene 6: Ablationsstudie (für Thesis)

Komponente deaktivieren → Auswirkung auf Qualität messen.

| Ablation | Was wegfällt | Erwarteter Effekt | Messung |
|----------|-------------|------------------|---------|
| Ohne Validation Agent | Keine Quellen-/Entitätsprüfung | EntityPrecision sinkt, Halluzinationen unerkannt | EntityPrecision vorher/nachher |
| Ohne TierWeight (alle = 1.0) | Keine Differenzierung nach Lieferkettenebene | Downstream-Facilities fälschlicherweise hoch gerankt | Top-3 Zusammensetzung |
| Ohne ResilienceDiscount | Kein Abzug für Alternativen | S2-Score ≈ S1-Score (obwohl Lithium mehr Alternativen hat) | Score-Differenz S1 vs S2 |
| Ohne CapacityShare | Alle Facilities gleich gewichtet | Kleine und große Facilities nicht unterscheidbar | Top-3 Ranking-Änderung |

**Empfehlung:** Ablation mit S1 und S2 durchführen — 2 Szenarien reichen, da der Kontrast (Kobalt ohne Alternativen vs. Lithium mit Alternativen) die Effekte am deutlichsten zeigt.

---

## Testinfrastruktur

### Empfohlene Implementierung

```
tests/
├── unit/
│   ├── test_intake_agent.py
│   ├── test_risk_assessment_agent.py
│   ├── test_network_agent.py
│   ├── test_data_retrieval_agent.py
│   ├── test_synthesis_agent.py
│   └── test_validation_agent.py
├── integration/
│   └── test_pipeline.py
├── scenarios/
│   ├── test_s1_cobalt_strike.py
│   ├── test_s2_lithium_export.py
│   ├── test_s3_port_failure.py
│   └── test_s4_extreme_weather.py
├── hallucination/
│   └── test_hallucination.py
└── fixtures/
    ├── news_inputs.json       # Simulierte Nachrichtentexte für S1–S4
    └── expected_outputs.json  # Erwartete Ergebnisse je Szenario
```

### Testausführung

```bash
pytest tests/unit/          # Schnell, täglich
pytest tests/integration/   # Nach jeder Agent-Änderung
pytest tests/scenarios/     # Wöchentlich / vor Abgabe
pytest tests/hallucination/ # Vor finaler Evaluation
```

### Bewertungsmatrix für Thesis (Kap. 5.1)

| Kriterium | Gewicht | Messmethode |
|-----------|---------|-------------|
| Korrekte Material-/Regionidentifikation | 30 % | Szenario-Tests S1–S4 |
| Berichtsvollständigkeit (Quellenabdeckung) | 25 % | SourceCoverage % |
| Entitätspräzision | 25 % | EntityPrecision % |
| Ausgabe-Konsistenz | 20 % | Self-Consistency über 3 Läufe |
