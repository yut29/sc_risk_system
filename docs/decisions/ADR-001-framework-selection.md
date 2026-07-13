# ADR-001: Agent Framework Selection — CrewAI vs LangGraph

**Status**: ✅ 已决策  
**Datum**: 2026-05-11  
**Entscheider**: Yutong Liu

---

## Kontext

Das System benötigt ein Python-Framework zur Orchestrierung von 6 spezialisierten Agenten mit:
- Sequentiellem Ablauf (mit bedingten Rücksprüngen beim Validation Agent)
- Max. 2 Iterationen für den Validierungsmechanismus
- Klarer Aufgabentrennung zwischen Agenten
- Einfacher Integration von Tool-Calls (NewsAPI, NetworkX, NAATBatt-DB)

## Optionen

### Option A: CrewAI
- Höheres Abstraktionsniveau; Agenten als "Crew" definiert
- Einfacher Einstieg, weniger Boilerplate
- Weniger Kontrolle über genaue Ablaufsteuerung

### Option B: LangGraph
- Graph-basierte Ablaufsteuerung (Nodes + Edges)
- Bedingte Kanten → gut für Validation-Loop mit max. 2 Iterationen
- Mehr Kontrolle, aber mehr Boilerplate

## Entscheidung

**Gewählt**: [x] LangGraph

**Begründung**: LangGraph wurde praktisch getestet (3-Agenten-Pipeline implementiert). Die graph-basierte Ablaufsteuerung (Nodes + Edges mit bedingten Kanten) ermöglicht präzisere Kontrolle über den Validation-Loop und bedingte Rücksprünge als Google ADK oder CrewAI.

**Ausschlussgründe für CrewAI**: Weniger präzise Ablaufsteuerung beim Vergleich mit Google ADK-Prototyp; LangGraph zeigte bessere Steuerbarkeit für bedingte Abläufe.

**Bestätigt durch**: E-Mail Baris Albayrak, 2026-05-15 — „Freut mich, dass LangGraph für deine Anwendung passt."

---

## PoC-Ergebnisse

- [x] LangGraph: 3-Agenten-Pipeline erfolgreich implementiert
- [x] LangGraph: Ablaufsteuerung besser als Google ADK-Prototyp
- [x] LangGraph: Bedingte Kanten für Validation-Loop geeignet
