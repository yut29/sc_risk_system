# Exposé zum Masterprojekt

**Ein LLM-basiertes Multi-Agenten-System zur automatisierten Risikoanalyse in der Batterie-Lieferkette**

Yutong Liu  
Betreuer: Baris Albayrak  
Lehrstuhl für Fertigungsautomatisierung und Produktionssystematik (FAPS)  
April 2026

---

## 1. Ausgangslage und Zielsetzung

### Einordnung und Problemstellung

Diese Arbeit ist im Bereich der KI-gestützten Produktionsinformatik mit dem Schwerpunkt Supply Chain Management angesiedelt. In einer globalisierten Wirtschaft sind Lieferketten komplexer und gleichzeitig anfälliger geworden. Geopolitische Spannungen, Naturkatastrophen, Pandemien und Arbeitskonflikte können Warenströme unterbrechen, die sich über viele Länder und Lieferstufen erstrecken. Die Häufigkeit solcher Ereignisse nimmt zu: 2024 stiegen globale Lieferkettenunterbrechungen um 38 % gegenüber dem Vorjahr, Arbeitskonflikte allein um 47 % (Resilinc, 2025).

Besonders betroffen ist die Batterieindustrie. Kritische Rohstoffe wie Lithium, Kobalt und Nickel werden in wenigen Regionen der Welt abgebaut und über mehrstufige Netzwerke zu Batteriezellen, Modulen und Packs verarbeitet. Diese geografische Konzentration macht die Branche anfällig: Eine Störung in einer Region – etwa ein Streik in einem kongolesischen Kobaltbergwerk – kann Auswirkungen auf die gesamte nordamerikanische Batterie-Wertschöpfungskette haben.

Kommerzielle Plattformen wie Prewave oder Resilinc überwachen zwar Lieferkettenrisiken, sind aber kostenintensiv, auf Großunternehmen ausgerichtet und liefern keine nachvollziehbaren Erklärungen für ihre Einschätzungen. Auf wissenschaftlicher Seite zeigen erste Studien, dass LLM-basierte Ansätze Nachrichtentexte für die Risikoerkennung nutzen können (Shahsavari et al., 2024). Ein zentrales Problem bleibt jedoch ungelöst: Bestehende Ansätze verbinden Nachrichtendaten, Lieferkettenstruktur und Unternehmensdaten nicht systematisch. Einzelne LLM-Modelle ohne Kontrollmechanismen sind zudem anfällig für Halluzinationen und liefern schwer nachvollziehbare Ergebnisse.

### Startpunkt und Vorarbeiten

Als Ausgangspunkt dient ein bereits durchgeführter Proof of Concept. Dabei wurde ein einfaches System mit einem LLM und Streamlit entwickelt, das Nachrichtentexte analysiert, mit einer simulierten Datentabelle verknüpft und strukturierte Ergebnisse ausgibt – darunter Schweregrad, Risikoart und Begründung. Der PoC zeigte, dass LLMs grundsätzlich erklärbare Risikohinweise aus Texten ableiten können. Gleichzeitig wurden Grenzen sichtbar: Ohne klare Aufgabentrennung entstehen inkonsistente Ergebnisse, besonders beim Kombinieren mehrerer Datenquellen. Die anschließende Literaturrecherche bestätigte, dass eine Architektur mit spezialisierten Agenten die Zuverlässigkeit und Nachvollziehbarkeit gegenüber Einzelmodell-Ansätzen verbessern kann (Jannelli et al., 2025; Almahri et al., 2025).

### Forschungsziel

Ziel dieses Projekts ist die Entwicklung und prototypische Implementierung eines LLM-basierten Multi-Agenten-Systems zur automatisierten Risikoerkennung in der Batterie-Lieferkette. Als Datenbasis dient die öffentlich zugängliche **NAATBatt North American Lithium-ion Battery Supply Chain Database** (NREL, Stand September 2025), die über 1.000 Unternehmen mit mehr als 1.100 Anlagen erfasst. Das System soll eingehende Nachrichten auf relevante Risiken prüfen, betroffene Lieferkettenpfade identifizieren und nachvollziehbare Risikoberichte mit Handlungsempfehlungen erzeugen. Ergänzend soll es auf gezielte Nutzeranfragen kontextbezogene Analysen durchführen.

### Hauptfragestellung und Leitfragen

> **Wie kann ein Multi-Agenten-System auf Basis von Large Language Models gestaltet werden, das unstrukturierte Nachrichtendaten mit strukturierten Lieferkettendaten zuverlässig verknüpft, um erklärbare Risikowarnungen zu generieren?**

Leitfragen:
1. Wie müssen die Rollen der einzelnen Agenten aufgeteilt und koordiniert werden, damit das System konsistente Risikoanalysen über mehrere Datenquellen hinweg liefert?
2. Wie kann ein Validierungsmechanismus zwischen den Agenten implementiert werden, der Halluzinationen reduziert und die Nachvollziehbarkeit der Ergebnisse sicherstellt?
3. Wie lässt sich der entwickelte Prototyp anhand konkreter Szenarien aus der Batterie-Lieferkette demonstrieren und bewerten?

---

## 2. Methoden und Vorgehen

### Forschungsansatz

Design Science Research (Hevner et al., 2004). Drei Phasen: Systemdesign → Implementierung → szenariobasierte Evaluation.

### Warum Multi-Agenten statt Single-LLM?

Ein einzelnes LLM neigt bei komplexen Aufgaben zu inkonsistenten Ergebnissen und Halluzinationen, weil es keine Möglichkeit hat, seine eigenen Ausgaben zu prüfen. Der Multi-Agenten-Ansatz löst dies durch:
- **Aufgabentrennung**: Jeder Agent übernimmt nur eine klar definierte Teilaufgabe → Fehler lokalisierbar
- **Validierung**: Dedizierter Validation Agent prüft Ausgaben anhand expliziter Kriterien
- **Erklärbarkeit**: Jede Aussage im Bericht mit konkreter Quelle verknüpft (Nachrichtenartikel oder NAATBatt-Eintrag)

### Die sechs Agenten

| # | Agent | Aufgabe |
|---|-------|---------|
| 1 | **Intake Agent** | Eingang & Vorfilterung; Trigger A (News) oder Trigger B (Nutzeranfrage) |
| 2 | **Risk Assessment Agent** | Risikoklassifikation (Art, Schweregrad, betroffene Materialien/Stufen) |
| 3 | **Network Agent** | Graphtraversierung (NetworkX); betroffene Unternehmen & Alternativlieferanten |
| 4 | **Data Retrieval Agent** | NAATBatt-Datenbankabfrage; fehlende Daten → simuliert & markiert |
| 5 | **Synthesis Agent** | Strukturierter Risikobericht mit max. 3 Hochrisiko-Unternehmen + Handlungsempfehlungen |
| 6 | **Validation Agent** | Prüft: Quellenbelege, NAATBatt-Referenzen, Begründungsnachvollziehbarkeit; max. 2 Iterationen |

### Technologie-Stack

| Komponente | Technologie |
|-----------|-------------|
| Sprache | Python |
| Agent Framework | CrewAI **oder** LangGraph (nach PoC entschieden) |
| Graphanalyse | NetworkX |
| LLM | OpenAI GPT-4o-mini / Anthropic Claude API |
| Entwicklung/Test | Llama via Groq oder Ollama |
| Nachrichtendaten | NewsAPI.org |
| UI | Streamlit |

### Evaluation — 3 Testszenarien

| Szenario | Ereignis |
|----------|---------|
| 1 | Kobaltminenstreik im Kongo |
| 2 | Lithium-Exportbeschränkungen |
| 3 | Hafenausfall an kritischem Logistikknoten |

Prüfkriterien je Szenario: korrekte Lieferkettenpfad-Identifikation, Berichtsvollständigkeit & Nachvollziehbarkeit, konsistente Validierungsfunktion.

---

## 3. Gliederung

1. Einleitung
2. Grundlagen und verwandte Arbeiten
   - 2.1 Supply Chain Risk Management
   - 2.2 Large Language Models in der Produktion
   - 2.3 Multi-Agenten-Systeme
   - 2.4 Knowledge Graphs in Lieferketten
   - 2.5 Verwandte Arbeiten und Forschungslücke
3. Systemdesign
   - 3.1 Anforderungsanalyse
   - 3.2 Gesamtarchitektur des Multi-Agenten-Systems
   - 3.3 Beschreibung der einzelnen Agenten
   - 3.4 Modellierung der Lieferkette als Graph
   - 3.5 Feedback- und Validierungsmechanismus
4. Implementierung
   - 4.1 Technologie-Stack und Werkzeuge
   - 4.2 Datenbasis: NAATBatt-Datenbank
   - 4.3 Implementierung der Agenten
   - 4.4 Benutzeroberfläche
5. Evaluation
   - 5.1 Evaluationskonzept und Bewertungskriterien
   - 5.2 Szenario 1: Kobaltminenstreik
   - 5.3 Szenario 2: Lithium-Exportbeschränkungen
   - 5.4 Szenario 3: Hafenausfall
   - 5.5 Diskussion und Limitationen
6. Fazit und Ausblick
7. Literaturverzeichnis
8. Anhang

---

## 4. Literatur

Siehe [references.md](references.md) für vollständige Literaturliste mit Projektbezug.
