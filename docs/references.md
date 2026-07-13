# Literaturverzeichnis & Projektbezug

## Kernliteratur

### Multi-Agenten & LLM-Frameworks

**Quan et al. (2026)** — MARS-Framework  
Quan, Y., Liu, Z., Benaben, F., & Montreuil, B. (2026). Leveraging large language models to enhance multi-agent risk assessment in supply chain networks. *International Journal of Production Research*. https://doi.org/10.1080/00207543.2026.2619562  
→ **Projektbezug**: Zentrale Inspiration für die 6-Agenten-Architektur und den Feedback-Loop (Validation Agent).

---

**Jannelli et al. (2025)** — Agentic LLMs in Supply Chains  
Jannelli, V., Schoepf, S., Bickel, M., Netland, T., & Brintrup, A. (2025). Agentic LLMs in the supply chain: towards autonomous multi-agent consensus-seeking. *International Journal of Production Research*. https://doi.org/10.1080/00207543.2025.2604311  
→ **Projektbezug**: Agent-Koordination bei heterogenen Datenstrukturen; fließt ins Design der Agent-Orchestrierung ein.

---

**Almahri et al. (2025)** — Knowledge Graphs & LLMs  
Almahri, S., Xu, L., & Brintrup, A. (2025). Enhancing supply chain visibility with knowledge graphs and large language models. *International Journal of Production Research*. https://doi.org/10.1080/00207543.2025.2575841  
→ **Projektbezug**: Zero-Shot-LLM-Ansatz zur Knowledge-Graph-Konstruktion; Grundlage für `data_prep/build_graph.py` und die graphbasierte Lieferkettenmodellierung.

---

### Knowledge Graph & Risikoerkennung

**Kosasih et al. (2024)** — Knowledge Graph Reasoning  
Kosasih, E., Margaroli, F., Gelli, S., Aziz, A., Wildgoose, N., & Brintrup, A. (2024). Towards knowledge graph reasoning for supply chain risk management using graph neural networks. *International Journal of Production Research, 62*(15), 5596–5612. https://doi.org/10.1080/00207543.2022.2100841  
→ **Projektbezug**: Modellierung von Lieferkettennetzwerken als Knowledge Graphs; motiviert die Graph-Struktur in `data/knowledge_graph.json`.

---

### News-basierte Risikoerkennung

**Shahsavari et al. (2024)** — LUEI-Framework  
Shahsavari, M., Hussain, O. K., Saberi, M., & Sharma, P. (2024). Event identification for supply chain risk management through news analysis by using large language models. *The Review of Socionetwork Strategies, 18*, 255–278. https://doi.org/10.1007/s12626-024-00169-z  
→ **Projektbezug**: Methodische Grundlage für Intake Agent (Relevanzfilterung) und Risk Assessment Agent (Ereignisextraktion).

---

### LLMs & Tabellarische Daten

**Haoyu & Zhiruo (2024)** — LLMs für tabellarische Daten  
Haoyu, D., Zhiruo, W. (2024). Large language models for tabular data: Progresses and future directions. *Proceedings of the 47th International ACM SIGIR Conference on Research and Development in Information Retrieval*. https://doi.org/10.1145/3626772.366138  
→ **Projektbezug**: NAATBatt-Datenbank liegt als strukturierte Tabelle vor; relevant für Data Retrieval Agent.

---

## Methodologie

**Hevner et al. (2004)** — Design Science Research  
Hevner, A. R., March, S. T., Park, J., & Ram, S. (2004). Design science in information systems research. *MIS Quarterly, 28*(1), 75–105.  
→ **Projektbezug**: Forschungsrahmen für das Gesamtprojekt (Design → Implementierung → Evaluation).

---

## Datenquellen

**NAATBatt / NREL (2025)** — North American Lithium-ion Battery Supply Chain Database  
Stand: September 2025. >1.000 Unternehmen, >1.100 Anlagen.  
→ Primäre Datenbasis; bereinigt in `data/facilities_clean.csv`, als Graph in `data/knowledge_graph.json`.

**Resilinc (2025)** — Supply Chain Disruption Report  
→ Statistik im Hintergrund: +38 % Lieferkettenunterbrechungen 2024, +47 % Arbeitskonflikte.
