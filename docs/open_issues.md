# Offene Probleme — SC Risk System
> Stand: 2026-06-30 | Quelle: Baris-Feedback + interne Analyse

---

## P1 — TierWeight-Inkonsistenz
**Problem:** Tabelle im Dokument zeigt andere Gewichtungswerte als die tatsächlich im Code verwendeten Beispiele.  
**Auswirkung:** RiskScore-Ergebnisse lassen sich nicht nachvollziehen / reproduzieren.  
**Status:** ✅ Gelöst — architecture.md, state.py und test_plan.md auf distanzbasierte Werte (1.0 / 0.6 / 0.35 / 0.15) aktualisiert.

---

## P2 — Kantenanzahl unklar (sourced vs. simulated)
**Problem:** Unklar, ob dokumentierte Kanten in der Gesamtzahl enthalten sind oder zusätzlich zählen.  
**Auswirkung:** Missverständnis bei der Dokumentation; Baris hat es explizit angemerkt.  
**Status:** ✅ Gelöst — edge_type-Unterscheidung entfernt (2026-07-06): NAATBatt enthält keine echten Lieferbeziehungen; alle 1537 Kanten sind simuliert (Keyword-Matching + geografische Nähe). Keine sourced/simulated-Trennung mehr nötig.

---

## P3 — Fehlende Kapazität ≠ Nullkapazität (simulated = 0)
**Problem:** 241 Anlagen ohne Kapazitätsdaten bekommen `production_capacity_raw = 0`. Das bedeutet im Modell "keine Kapazität", obwohl eigentlich "unbekannt".  
**Auswirkung:** CapacityShare = 0 für 62 % der Anlagen → Vulnerability unterschätzt.  
**Status:** ✅ Gelöst — Zwei-Status-Modell: `naatbatt` (Originalwert, capacity_known=True) / `unknown` (kein Wert, capacity_known=False). Unbekannte Werte als Datenlücke, nicht Nullkapazität; CapacityShare + AltCapacityRatio nur bei capacity_known=True. Upstream-Imputation (3 Anlagen, Median 10.500 MT) verworfen: Materialmix (graphite/manganese/lithium) macht einheitlichen Median fachlich nicht vertretbar, 0.8%-Anteil vernachlässigbar.

---

## P4 — Midstream-BGM: Einheiten nicht vergleichbar
**Problem:** BGM-Anlagen haben völlig verschiedene Einheiten: MT/yr (Kathode), mm²/yr (Separator), L/yr (Elektrolyt), GWh/yr. Diese Zahlen können nicht sinnvoll addiert oder verglichen werden.  
**Auswirkung:** CapacityShare und AltCapacityRatio für Midstream-BGM sind faktisch bedeutungslos.  
**Status:** ❌ Datengrenze von NAATBatt — CapacityShare auf Upstream beschränkt, als Limitation in risk_model.md dokumentiert.

---

## P5 — AltCapacityRatio: keine Datenbasis für Unknown-Anlagen
**Problem:** Wenn die betroffene Fabrik keine bekannte Kapazität hat (`simulated`), kann AltCapacityRatio keinen sinnvollen Vergleich liefern. Unbekannte Kapazität ≠ keine Kapazität → Behandlung als 0 wäre irreführend.  
**Auswirkung:** ResilienceDiscount für ~62% der Anlagen ohne Kapazitätsdaten war nicht aussagekräftig.  
**Status:** ✅ Gelöst — AltCapacityRatio wird nur berechnet wenn `capacity_known = True`; sonst `AltCapacityRatio = NA`, `ResilienceDiscount = 0` (konservativ). Dokumentiert in risk_model.md.

---

## P6 — Downstream-Kapazität praktisch unnutzbar
**Problem:** 81 % fehlende Werte, und die vorhandenen Werte haben völlig verschiedene Einheiten (GWh, Stück, aircraft/yr, Volts(!), MW).  
**Auswirkung:** Kapazitätsberechnung für Downstream nicht sinnvoll.  
**Status:** ❌ Datengrenze — als Limitation dokumentiert.

---

## Zusammenfassung

| # | Problem | Status |
|---|---------|--------|
| P1 | TierWeight-Inkonsistenz | ✅ Gelöst |
| P2 | Kantenanzahl unklar | ✅ Gelöst |
| P3 | simulated = 0 vs. unbekannt | ✅ Gelöst |
| P4 | BGM-Einheiten nicht vergleichbar | ❌ Datengrenze |
| P5 | AltCapacity ohne Datenbasis | ✅ Gelöst |
| P6 | Downstream-Kapazität unnutzbar | ❌ Datengrenze |
