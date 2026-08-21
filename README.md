# SC Risk System

LLM-Multi-Agenten-System zur automatisierten Risikoanalyse in der Batterie-Lieferkette (Kobalt, Lithium, Nickel), basierend auf der NAATBatt-Datenbank.

## Setup

```bash
pip install -r requirements.txt
```

`.env` im Projektroot anlegen:

```
LLM_PROVIDER=fau
FAU_LLMAPI_KEY=<dein-key>
```

## Ausführen

```bash
streamlit run ui/app.py
```

## Daten neu generieren (optional)

```bash
python data_prep/build_facilities.py   # naatbatt-database-*.xlsx -> facilities_clean.csv
python data_prep/build_graph.py        # facilities_clean.csv -> knowledge_graph.json
```

## Struktur

| Ordner | Inhalt |
|---|---|
| `agents/` | Die 6 Pipeline-Agenten (Intake, Risk Assessment, Network, Data Retrieval, Synthesis, Validation) + State-Definitionen |
| `pipeline/` | LangGraph-Orchestrierung |
| `ui/` | Streamlit-Interface |
| `data_prep/` | Skripte zur Datenaufbereitung |
| `data/` | NAATBatt-Rohdaten + aufbereitete Dateien |
