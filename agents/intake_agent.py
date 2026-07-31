"""
Intake Agent — LLM-based relevance filter and keyword extractor

Input (from PipelineState):
  raw_input    : str   news article (Trigger A) or user query (Trigger B)

Output (written to PipelineState):
  relevant           : bool
  trigger_type       : "A" | "B"
  material           : str    e.g. "cobalt"
  region             : str    e.g. "Africa/DRC"
  keywords           : list[str]
  filtered_text      : str    cleaned, relevant excerpt
  mentioned_company  : str | None   e.g. "Panasonic" (facility-specific incidents only)
  mentioned_location : str | None   e.g. "Kansas" (city/state named alongside the company)
"""

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_groq import ChatGroq

load_dotenv(Path(__file__).parent.parent / ".env")

from agents.llm_utils import invoke_json
from agents.state import PipelineState

# ── LLM singleton ─────────────────────────────────────────────────────────────

_llm: Optional[ChatGroq] = None


def _get_llm() -> ChatGroq:
    global _llm
    if _llm is None:
        _llm = ChatGroq(
            model="llama-3.1-8b-instant",
            api_key=os.environ["GROQ_API_KEY"],
            temperature=0,
            request_timeout=20,
            max_retries=1,
        )
    return _llm


# ── Prompts ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the Intake Agent for a Battery Supply Chain Risk System.
Your task is to analyze a text (news article or user query) and extract structured information.

Known critical materials for battery supply chains:
cobalt, lithium, nickel, manganese, graphite, copper

Known risk regions:
- Africa/DRC (cobalt, copper)
- South America / Australia (lithium)
- Asia (China) (graphite, manganese)
- Asia / Pacific (nickel)
- Africa / Asia (manganese)

Respond ONLY with a JSON object — no explanation, no markdown, no code blocks.

JSON schema:
{
  "relevant": true | false,
  "trigger_type": "A" | "B",
  "material": "<primary critical material mentioned, lowercase, or empty string>",
  "region": "<affected supply region, or empty string>",
  "keywords": ["<keyword1>", "<keyword2>", ...],
  "filtered_text": "<the most relevant 2-3 sentences from the input, or the full input if short>",
  "mentioned_company": "<specific company/facility operator named in the text, or null>",
  "mentioned_location": "<city or state named alongside that company, or null>",
  "reasoning": "<one sentence explaining relevance decision>"
}

Rules:
- relevant=true only if the text concerns supply disruption, price volatility, regulatory risk,
  logistics failure, or weather events affecting battery-critical materials or regions
- trigger_type "A" = the input looks like a news article; "B" = a direct user question/query
- material must be one of: cobalt, lithium, nickel, manganese, graphite, copper — or empty
- If not relevant, set material="", region="", keywords=[], filtered_text=""
- mentioned_company: only set this for a FACILITY-SPECIFIC incident (a named company's own plant/mine
  having a fire, strike, shutdown, quality recall, etc.) — NOT for a general regional/material event
  like a country restricting exports. If the text names a company but the event is a regional/material
  disruption (e.g. "Chile restricts lithium exports, affecting Albemarle's supply"), still set
  mentioned_company=null — Albemarle is a downstream effect, not the origin of this event.
- mentioned_location: the specific city/state of the named facility, if the text says so. Do not
  invent one — if the text only names the company without a location, leave this null.
"""

USER_PROMPT_TEMPLATE = """Analyze this input:

---
{raw_input}
---
"""


# ── Main agent function ────────────────────────────────────────────────────────

def run_intake_agent(state: PipelineState) -> PipelineState:
    """
    LangGraph-compatible node function.
    Reads raw_input from state, returns updated state with intake fields.
    """
    raw_input: str = state.get("raw_input", "")
    llm = _get_llm()

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=USER_PROMPT_TEMPLATE.format(raw_input=raw_input)),
    ]

    parsed = invoke_json(llm, messages)

    relevant: bool      = bool(parsed.get("relevant", False))
    trigger_type: str   = parsed.get("trigger_type", "A")
    material: str       = parsed.get("material", "").lower().strip()
    region: str         = parsed.get("region", "").strip()
    keywords: list[str] = parsed.get("keywords", [])
    filtered_text: str  = parsed.get("filtered_text", raw_input)

    mentioned_company  = parsed.get("mentioned_company") or None
    mentioned_location = parsed.get("mentioned_location") or None
    if isinstance(mentioned_company, str):
        mentioned_company = mentioned_company.strip() or None
    if isinstance(mentioned_location, str):
        mentioned_location = mentioned_location.strip() or None

    return {
        **state,
        "relevant":           relevant,
        "trigger_type":       trigger_type,
        "material":           material,
        "region":             region,
        "keywords":           keywords,
        "filtered_text":      filtered_text,
        "mentioned_company":  mentioned_company,
        "mentioned_location": mentioned_location,
    }
