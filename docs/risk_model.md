# Risikomodell — SC Risk System

> Stand: 2026-06-01

---

## Was berechnen wir?

Für jede betroffene Fabrik (Facility) berechnen wir einen **RiskScore** (0–100).
Ein hoher Score bedeutet: Diese Fabrik ist sehr gefährdet, und es gibt kaum Alternativen.

Die drei Fabriken mit dem höchsten Score erscheinen im Risikobericht.

---

## Die Hauptformel

```
RiskScore = Severity × TierWeight × Vulnerability × (1 − ResilienceDiscount)
```

Danach wird normalisiert:

```
RiskScore (0–100) = RiskScore_roh / 5.0 × 100
```

Das Maximum ist 5.0 (Severity=5, TierWeight=1.0, Vulnerability=1.0, kein Discount).

---

## Schritt 1 — Severity (Wie schlimm ist das Ereignis?)

| Wert | Bedeutung | Beispiel |
|------|-----------|---------|
| 1 | Sehr gering | Kleiner lokaler Streik, nur 1 Tag |
| 2 | Gering | Kurze Lieferverzögerung |
| 3 | Mittel | Monatelanger Streik |
| 4 | Hoch | Exportverbot für kritisches Material |
| 5 | Kritisch | Kompletter Lieferstopp, keine Alternative |

**Quelle:** Das LLM liest den Nachrichtentext und gibt eine Zahl von 1 bis 5 zurück.

---

## Schritt 2 — TierWeight (Wie nah ist die Fabrik am Problem?)

TierWeight hängt davon ab, wie weit eine Fabrik vom **Ereignisursprung** (`origin_tier`) entfernt ist.
Das LLM bestimmt `origin_tier` aus dem Nachrichtentext (z.B. „Kobaltmine" → Upstream, „Zellfabrik-Brand" → Midstream-Cell).

```
TierWeight = DISTANCE_WEIGHT[ |TIER_ORDER[facility] − TIER_ORDER[origin_tier]| ]
```

**Stufen-Reihenfolge (TIER_ORDER):**

```
Upstream = 0 → Midstream-BGM = 1 → Midstream-Cell = 2 → Downstream = 3
```

**Distanz-Gewichte (DISTANCE_WEIGHT):**

| Distanz | TierWeight | Bedeutung |
|---------|-----------|-----------|
| 0 | 1.0 | Gleiche Stufe wie Ereignis → direkt betroffen |
| 1 | 0.6 | Eine Stufe entfernt |
| 2 | 0.35 | Zwei Stufen entfernt |
| 3 | 0.15 | Drei Stufen entfernt |

**Beispiele:**

*S1 Kobaltstreik (origin_tier = Upstream):*
```
Upstream       → Distanz 0 → TierWeight 1.0
Midstream-BGM  → Distanz 1 → TierWeight 0.6
Midstream-Cell → Distanz 2 → TierWeight 0.35
Downstream     → Distanz 3 → TierWeight 0.15
```

*Werksunfall Midstream-Cell (origin_tier = Midstream-Cell):*
```
Upstream       → Distanz 2 → TierWeight 0.35
Midstream-BGM  → Distanz 1 → TierWeight 0.6
Midstream-Cell → Distanz 0 → TierWeight 1.0   ← Unfallort
Downstream     → Distanz 1 → TierWeight 0.6
```

**Quelle:** `origin_tier` vom Risk Assessment Agent (LLM); `segment`-Feld aus NAATBatt.

---

## Schritt 3 — Vulnerability (Wie verwundbar ist die Fabrik?)

```
Vulnerability = 0.30 × ImportDep
              + 0.30 × SupplierConcentration
              + 0.25 × CapacityShare
              + 0.15 × LeadTime
```

Ergebnis liegt immer zwischen 0 und 1.

### Was bedeuten die vier Felder?

**ImportDep** (0 oder 1) — Geopolitisches Risiko: Ist das Material importabhängig?
- `1` = Nordamerika ist Nettoimporteur dieses Materials (USGS Net Import Reliance); Segment ≠ Upstream
- `0` = Upstream-Anlage (sie ist selbst Produktionsstandort, kein Importeur) oder Material lokal verfügbar
- Quelle: USGS Mineral Commodity Summaries 2026 (Feb 2026); NIR: Co=79%, Graphit=100%, Mn=100%, Li>50%, Ni≈100% primary

**SupplierConcentration** (0 oder 1) — Marktstrukturrisiko: Gibt es weltweit nur wenige Anbieter?
- `1` = Globaler Markt durch wenige Unternehmen dominiert → Ausfall eines Lieferanten kaum kompensierbar
- `0` = Viele Anbieter weltweit vorhanden; Markt ist kompetitiv
- Klassifikation (literaturbasierte Regel):
  - **cobalt** → `1`: DRC 73% + Indonesia 14% = 87% der Weltminenproduktion; 2–3 Konzerne dominieren (USGS MCS 2026)
  - **nmc / nca** → `1`: China ≥95% PCAM-Weltmarktanteil; <5 Nicht-China-Großlieferanten (IEA GCMO 2025)
  - alle anderen Materialien → `0`: Lithium (10+ Minenunternehmen weltweit), Graphit (viele chin. Anbieter + 15+ Länder), Nickel (globaler Surplus seit 2022), Mangan (4 Länder je 10–38%)
- Quelle: USGS Mineral Commodity Summaries 2026; IEA Global Critical Minerals Outlook 2025

**CapacityShare** (0.0 – 1.0) — Wie groß ist der Marktanteil dieser Fabrik?

CapacityShare wird **nur für Upstream-Anlagen** berechnet. Upstream-Kapazitäten sind in MT/yr einheitlich und damit vergleichbar. Midstream- und Downstream-Anlagen haben inkonsistente Einheiten (MT / GWh / mm² / Stück), weshalb ein sinnvoller Vergleich nicht möglich ist.

```
CapacityShare = Kapazität dieser Fabrik / Gesamtkapazität (Upstream, gleiches Material)
```
- Beispiel: Fabrik produziert 12.000 MT, gesamt 85.000 MT → CapacityShare = 0.14
- Für Midstream und Downstream: CapacityShare = 0 (Einheiten nicht vergleichbar)

**Kapazitätsdatenstatus:**

| Status | capacity_source | capacity_known | Bedeutung |
|--------|----------------|---------------|-----------|
| Observed | `naatbatt` | True | Echter NAATBatt-Wert — fließt in CapacityShare ein |
| Unknown | `unknown` | False | Kein Wert in NAATBatt — fehlende Daten ≠ Nullkapazität; CapacityShare = 0 (konservativ) |

Fehlende Kapazitätsdaten werden **nicht als Nullkapazität** behandelt. Bei unbekannten Werten (`capacity_known = False`) wird `capacity_share_eff = 0` gesetzt — konservative Schätzung, keine Nullkapazitätsannahme. Kapazitätsbasierte Indikatoren werden **nur berechnet, wenn ein NAATBatt-Originalwert vorliegt** (`capacity_known = True`). (Hinweis: Baris Albayrak, 2026-06-30)

**LeadTime** (0.0 – 1.0) — Wie lange dauert es, das Material woanders zu beschaffen?
```
LeadTime_norm = lead_time_weeks / 12
```
- Beispiel: 24 Wochen → 24/12 = 2.0 → wird auf 1.0 begrenzt
- Typische Werte: Upstream 30 Wochen, Midstream-BGM 8 Wochen

### Warum diese Gewichte?

| Feld | Gewicht | Begründung |
|------|---------|-----------|
| ImportDep | 30 % | Geopolitisches Risiko: Nordamerika nettoimportabhängig → sofortiger Engpass bei Handelsstörung |
| SupplierConcentration | 30 % | Marktstrukturrisiko: Wenige globale Anbieter → kein Marktausgleich bei Ausfall |
| CapacityShare | 25 % | Großer Marktanteil = großer systemischer Schaden |
| LeadTime | 15 % | Lange Wartezeit verschlimmert kurzfristigen Engpass |

---

## Schritt 4 — ResilienceDiscount (Gibt es Alternativen?)

Wenn es viele Ersatzlieferanten gibt, wird der RiskScore reduziert.

```
AltCapacityRatio = Σ Kapazität der Ersatzfabriken / Kapazität der betroffenen Fabrik

ResilienceDiscount = min(AltCapacityRatio / 2,  0.5)
```

Der Discount ist auf 0.5 begrenzt — auch bei guten Alternativen bleibt immer ein Restrisiko
(Umstellungszeit, Verträge, Qualitätsprüfung).

**Data Quality Filter:** Capacity-based indicators are calculated only when all three criteria are met:
1. The capacity value is available (`capacity_known = True`, i.e. `naatbatt`)
2. The production unit is comparable within the supply-chain stage
3. The facility belongs to the same product category as the affected facility

In practice:

| Tier | AltCapacityRatio / CapacityShare | Begründung |
|------|----------------------------------|-----------|
| Upstream + `capacity_known=True` | berechnet | MT/yr einheitlich (29 Anlagen) |
| Upstream + `capacity_known=False` | NA → Discount = 0 | Fehlende Datenbasis |
| Midstream-Cell + `capacity_known=True` | berechnet | GWh/yr einheitlich (27/54 Anlagen nach Bereinigung) |
| Midstream-Cell + `capacity_known=False` | NA → Discount = 0 | Einheit nicht konvertierbar oder falsches Produktsegment |
| Midstream-BGM | NA → Discount = 0 | MT / mm² / GWh / L gemischt — kein sinnvoller Vergleich |
| Downstream | NA → Discount = 0 | GWh / Stück / Volts gemischt |

Ausgeschlossen aus Midstream-Cell: Clarios (MWh/yr → konvertiert zu GWh/yr), Schlenk Metallfolien (Aluminiumfolie, MT/yr, kein Zelläquivalent), Nuvvon (Ah, Labormaßstab). Unbekannte oder einheitsinkompatible Kapazität wird nicht als Nullkapazität behandelt.

| AltCapacityRatio | Discount | Bedeutung |
|-----------------|----------|-----------|
| NA (unbekannte Kapazität) | 0 % | Kein Discount ohne vergleichbare Datenbasis |
| 0 | 0 % | Keine Alternativen |
| 0.5 | 25 % | Ersatz deckt die Hälfte |
| ≥ 1.0 | 50 % (Maximum) | Ausreichend Ersatz vorhanden |

---

## Beispielrechnung — S1: Kobaltstreik DRC

| Parameter | Wert | Herkunft |
|-----------|------|---------|
| Severity | 4 | LLM: „monatelanger Streik, keine Ersatzlieferung" |
| TierWeight | 1.0 | Upstream-Mine |
| ImportDep | 1 | Kobalt kommt aus dem Ausland |
| SupplierConcentration | 1 | Cobalt: DRC 73% + Indonesia 14%; 2–3 Konzerne dominieren |
| CapacityShare | 0.14 | 12.000 / 85.000 MT |
| LeadTime | 1.0 | 30 Wochen → gecappt auf 1.0 |

```
Vulnerability = 0.30×1 + 0.30×1 + 0.25×0.14 + 0.15×1.0   # ImportDep=1, SupplierConcentration=1
              = 0.30 + 0.30 + 0.035 + 0.15
              = 0.785

AltCapacityRatio = 38.000 / 24.000 = 1.58
ResilienceDiscount = min(1.58 / 2, 0.5) = 0.5

RiskScore_roh  = 4 × 1.0 × 0.785 × (1 − 0.5)
               = 4 × 1.0 × 0.785 × 0.5
               = 1.57

RiskScore (0–100) = 1.57 / 5.0 × 100 = 31.4
```

---

## Globale Kennzahlen (separat vom RiskScore)

Diese Zahlen stehen im Berichts-Header und zeigen das Gesamtbild des Ereignisses.
Sie werden **nicht** für das Ranking der einzelnen Fabriken verwendet.

```
BetroffeneKapazität%  = Σ Kapazität betroffener Fabriken / Σ Gesamtkapazität × 100

AlternativeKapazität% = Σ Kapazität der Ersatzfabriken   / Σ Gesamtkapazität × 100
```

Beispiel S1:
```
BetroffeneKapazität%  = 24.000 / 85.000 × 100 = 28 %
AlternativeKapazität% = 38.000 / 85.000 × 100 = 45 %
```

---

## Risiko-Klassifikation

| RiskScore | Kategorie | Empfehlung |
|-----------|-----------|-----------|
| 0 – 25 | Niedrig | Beobachten |
| 25 – 50 | Mittel | Proaktiv bewerten |
| 50 – 75 | Hoch | Maßnahmen entwickeln |
| 75 – 100 | Kritisch | Sofort handeln |

---

## Überblick: Wer berechnet was?

```
Intake Agent          → material, region, filtered_text (via LLM)
Risk Assessment Agent → Severity (1–5), risk_type, reason (via LLM)
Network Agent         → TierWeight, affected_nodes, alt_nodes (Deterministic)
Data Retrieval Agent  → Vulnerability-Felder, AltCapacityRatio,
                        BetroffeneKapazität%, AlternativeKapazität% (Deterministic)
Synthesis Agent       → RiskScore_roh, RiskScore (0–100), Top-3-Ranking (LLM + Deterministic)
```
