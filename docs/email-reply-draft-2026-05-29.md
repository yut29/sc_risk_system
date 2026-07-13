# Entwurf: E-Mail-Antwort an Baris — 2026-05-29

> Bezug: Baris' Feedback-Mail vom 2026-05-15  
> Status: **Entwurf** — bitte prüfen und anpassen

---

**An**: Baris.Albayrak@faps.fau.de  
**Betreff**: Re: AW: Kurzes Update: Masterprojekt – Yutong Liu

---

Hi Baris,

ich hoffe, du hast dich gut erholt! Ich habe die letzten zwei Wochen genutzt, um alle vier offenen Punkte ausführlich auszuarbeiten. Hier der aktuelle Stand:

---

**1. Risikomodell – Wie kommen die Zahlen zustande?**

Der RiskScore wird **pro Facility** berechnet und dient dem Top-3-Ranking im Bericht.
Globale Kennzahlen wie „28 % betroffene Kapazität" werden separat davon berechnet.

```
RiskScore_i = Severity × TierWeight_i × Vulnerability_i × (1 − ResilienceDiscount_i)
```

- **Severity** (1–5): LLM-Bewertung aus dem Nachrichtentext
- **TierWeight**: Upstream (1,0) → Midstream-BGM (0,8) → Midstream-Cell (0,5) → Downstream (0,3)
- **Vulnerability** (0–1): Gewichtete Summe aus vier Feldern:
  - ImportDependency (30 %) – keine lokale Ausweichmöglichkeit
  - SingleSourceFlag (30 %) – wenige Anbieter, kein Marktausgleich
  - CapacityShare (25 %) – Marktanteil dieser Facility
  - LeadTime normiert (15 %) – Vorlaufzeit bis Wiederbeschaffung
- **ResilienceDiscount** (0–50 %): `min(AltCapacityRatio / 2, 0,5)`

Normierung auf 0–100: theoretisches Maximum ist 5,0 (Severity 5 × TierWeight 1,0 × Vulnerability 1,0 × 1).

Die genauen Gewichte plane ich anhand der vier Evaluationsszenarien zu validieren – insbesondere ob S1 (Kobalt, kaum Alternativen) tatsächlich höher als S2 (Lithium, domestische Alternativen) bewertet wird.

---

**2. Ablaufdiagramm – Relationen zwischen Agenten**

Das Diagramm ist fertiggestellt – als **Swimlane-Diagramm** (draw.io, Visio-kompatibel).
Jeder Agent hat eine eigene Schwimmbahn mit Prozessschritten und Entscheidungsknoten.

Zwei Rückkopplungsschleifen sind explizit eingezeichnet:

```
Validation Agent
  ├── ✗ Leichter Fehler (max. 2×) ──────────────→ Retry ab Synthesis Agent
  └── ✗ Schwerwiegend (z.B. falsche Klassifikation) → Neustart ab Risk Assessment
```

Ich kann das Diagramm gerne vorab schicken (als PDF oder Visio-Datei) – sag kurz Bescheid.

---

**3. Schnittstellenlogik – Welche Daten werden wo ausgetauscht?**

Ich habe alle Agent-Übergänge vollständig typisiert. Hier die Kernfelder je Übergang:

| Übergang | Schlüsselfelder |
|----------|----------------|
| Intake → Risk Assessment | `relevant`, `trigger_type` (A/B), `material`, `region`, `filtered_text` |
| Risk Assessment → Network | `severity` (1–5), `risk_type`, `affected_material`, `affected_region`, `reason` |
| Network → Data Retrieval | `affected_nodes` (list), `alt_nodes` (list), `tier_weights`, `downstream_fanout` |
| Data Retrieval → Synthesis | `facility_data` (Kapazität, capacity_source, import_dep, lead_time_norm, capacity_share), `BetroffeneKapazität%`, `AlternativeKapazität%` |
| Synthesis → Validation | `risk_report`, `top3_facilities`, `risk_scores`, `global_metrics` |
| Validation → Pipeline | `valid`, `failure_type` (minor/severe/None), `issues`, `iteration` |

Alle Felder sind als Python TypedDict (`PipelineState`) definiert – direkt als LangGraph-State nutzbar.

Wichtig: `Network Agent` und `Data Retrieval Agent` arbeiten vollständig deterministisch (keine LLM-Aufrufe) – der Network Agent traversiert den Graphen, der Data Retrieval Agent liest direkt aus der NAATBatt-CSV. Nur die markierten LLM-Ausgaben (Intake, Risk Assessment, Synthesis, Validation) sind validierungsbedürftig.

---

**4. Halluzinations-Prüfung – Wie wird korrekte Ausgabe sichergestellt?**

Ich habe ein mehrstufiges Testkonzept ausgearbeitet:

**Strukturelle Validierung (Validation Agent, automatisch):**
1. Ist jede Risikoaussage mit einer Quelle belegt? → `SourceCoverage = 100 %` (Zielwert)
2. Ist jedes genannte Unternehmen in der NAATBatt-DB referenzierbar? → `EntityPrecision = 100 %`
3. Hat jede Risikoeinschätzung eine nachvollziehbare Begründung? → Feld `reason` nicht leer

**Stabilitätstests (Self-Consistency):**
Gleicher Input, 3 Läufe → akzeptabel wenn: Severity-Varianz ≤ 0,5; Top-3-Überlappung ≥ 2/3 Facilities.

**Ablationsstudie (für Thesis Kap. 5):**
Komponenten einzeln deaktivieren und Qualitätsverlust messen:
- Ohne Validation Agent → EntityPrecision sinkt, Halluzinationen unerkannt
- Ohne TierWeight (alle = 1,0) → Downstream-Facilities fälschlicherweise hoch gerankt
- Ohne ResilienceDiscount → S2-Score ≈ S1-Score trotz mehr Alternativen

Diese Ablation zeigt direkt den Mehrwert der einzelnen Modellkomponenten – ich plane sie mit Szenario 1 (Kobalt) und Szenario 2 (Lithium) durchzuführen.

---

Hast du Anmerkungen zum Risikomodell, insbesondere zu den Gewichten der Vulnerability-Dimensionen? Ich bin unsicher, ob ImportDependency und SingleSourceFlag beide mit 30 % gewichtet sein sollten oder ob eine davon stärker gewichtet werden sollte.

Viele Grüße  
Yutong

---

> **Entwurfsnotizen:**
> - Frage zu Vulnerability-Gewichten bewusst offen: Baris' Einschätzung einholen
> - Diagramm-Angebot: "vorab schicken" ggf. streichen, wenn Baris das nicht erwartet
> - Paper-Frage wurde weggelassen (war in der vorherigen Mail schon gestellt)
