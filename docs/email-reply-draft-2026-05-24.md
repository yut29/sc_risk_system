# Entwurf: E-Mail-Antwort an Baris — 2026-05-24

> Bezug: Baris' Feedback-Mail vom 2026-05-15  
> Status: **Entwurf** — bitte prüfen und anpassen

---

**An**: Baris.Albayrak@faps.fau.de  
**Betreff**: Re: AW: Kurzes Update: Masterprojekt – Yutong Liu

---

Hi Baris,

ich hoffe, deine Geschäftsreise läuft gut! Ich habe die letzte Woche genutzt, um deine Fragen ausführlicher durchzudenken. Hier mein aktueller Stand zu den vier Punkten:

---

**1. Risikomodell – Wie kommen die Zahlen zustande?**

Der Risk Score wird **pro Facility** berechnet, um die Top-3 Risiko-Facilities zu identifizieren.
Daneben gibt es separate globale Kennzahlen (z.B. „28 % betroffene Kapazität"), die unabhängig davon aus den Kapazitätsdaten berechnet werden.

Die Formel kombiniert vier Ebenen:

```
RiskScore = Severity × TierWeight × Vulnerability × (1 − ResilienceDiscount)
```

- **Severity** (1–5): LLM-Bewertung aus dem Nachrichtentext
- **TierWeight**: Position in der Lieferkette — Upstream (1,0) bis Downstream (0,3), da upstream-Facilities unmittelbar betroffen sind
- **Vulnerability** (0–1): Gewichtete Summe aus vier Feldern:
  - ImportDependency (30 %) — Importabhängige Facilities haben keine lokale Ausweichmöglichkeit
  - SingleSourceFlag (30 %) — Wenige Anbieter = kein Marktmechanismus zum Ausgleich
  - CapacityShare (25 %) — Wie groß ist der Marktanteil dieser Facility?
  - LeadTime normiert (15 %) — Längere Vorlaufzeit verschärft den kurzfristigen Engpass
- **ResilienceDiscount** (0–50 %): Abzug, wenn alternative Kapazität verfügbar ist

Der Begriff „Supply Chain Tier" (Lieferkettenebene) entspricht dem `segment`-Feld in der NAATBatt-Datenbank — kein zusätzlicher Graph-Traversal nötig.

Ich bin mir bei den genauen Gewichten noch nicht sicher — plane, sie anhand der vier Evaluationsszenarien zu validieren und ggf. anzupassen.

---

**2. Ablaufdiagramm – Relationen zwischen Agenten**

Ich habe bereits ein erstes Flussdiagramm als SVG erstellt (`multi_agent_pipeline_flow.svg`). Ich werde es in den nächsten Tagen verfeinern und als sauberes Diagramm in die Dokumentation einpflegen. Der Ablauf:

```
Trigger A/B → Intake Agent → Risk Assessment → Network Agent
→ Data Retrieval → Synthesis → Validation Agent
                                    ↑ (bei Fehler, max. 2 Iterationen)
                                    └── zurück zu Synthesis (oder Risk Assessment)
```

Soll ich das Diagramm beim nächsten Treffen zeigen oder schon vorab schicken?

---

**3. Schnittstellenlogik – Welche Daten werden wo ausgetauscht?**

Ich werde eine Tabelle/Diagramm erstellen, die für jeden Agent-Übergang definiert:
- Input-Format (welche Felder, welcher Typ)
- Output-Format
- Datenquelle (Nachrichtentext, NAATBatt-DB, Graphstruktur)

Das plane ich als nächsten Schritt parallel zur Agent-Implementierung zu dokumentieren.

---

**4. Halluzinations-Prüfung – Tests**

Der Validation Agent prüft bereits strukturell:
1. Ist jede Risikoaussage mit einer Quelle belegt?
2. Ist jedes genannte Unternehmen in der NAATBatt-DB referenzierbar?
3. Hat jede Risikoeinschätzung eine nachvollziehbare Begründung?

Zusätzlich plane ich Unit-Tests für die einzelnen Agenten mit vordefinierten Eingaben und erwarteten Ausgaben – so kann ich bei den drei Evaluationsszenarien gezielt prüfen, ob die Ausgaben konsistent sind.

---

Hast du Paper-Empfehlungen zu Risikomodellen für Supply Chains oder zu Evaluationsmethoden für LLM-Agentensysteme? Das würde mir sehr helfen.

Viele Grüße  
Yutong

---

> **Entwurfsnotizen:**
> - Frage zum Risikomodell ist bewusst offen gehalten – Baris' Meinung einholen
> - Diagramm-Frage: je nach Präferenz "vorab schicken" streichen
> - Paper-Frage war bereits in der letzten Mail → kann weggelassen werden, wenn zu repetitiv
