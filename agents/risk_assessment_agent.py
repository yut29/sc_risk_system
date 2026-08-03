"""
Risk Assessment Agent — LLM-based risk classification

Input (from PipelineState):
  filtered_text   : str
  material        : str   (from Intake Agent)
  region          : str   (from Intake Agent)

Output (written to PipelineState):
  severity        : int        1 (low) – 5 (critical)
  risk_type       : RiskType
  affected_material: str       confirmed or refined by LLM
  affected_region  : str       confirmed or refined by LLM
  origin_tier      : Segment   supply chain tier of event origin
  reason           : str       mandatory justification for Validation Agent
"""

from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage

from agents.llm_utils import get_llm, invoke_json
from agents.state import PipelineState

load_dotenv(Path(__file__).parent.parent / ".env")

# ── LLM singleton ─────────────────────────────────────────────────────────────

_llm: Optional[BaseChatModel] = None


def _get_llm() -> BaseChatModel:
    global _llm
    if _llm is None:
        _llm = get_llm(temperature=0)
    return _llm


# ── Prompts ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the Risk Assessment Agent for a Battery Supply Chain Risk System.
You analyze filtered news or user queries about battery supply chain disruptions.

Supply chain tiers:
- Upstream: raw material mining & extraction (cobalt mines, lithium brine, graphite mines)
- Midstream-BGM: battery-grade material processing (cathode/anode active materials, electrolyte, separators)
- Midstream-Cell: cell manufacturing (cylindrical, prismatic, pouch cells)
- Downstream: module/pack assembly, EV/ESS manufacturers

Severity scale (1–5):
1 = Minor signal, limited scope, short-term
2 = Moderate disruption, regional, weeks
3 = Significant disruption, multi-regional, 1–3 months
4 = Severe, major supply routes affected, 3–6 months
5 = Critical, systemic risk, >6 months or permanent

Risk types:
- supply_disruption : physical halt (strike, mine closure, force majeure)
- price_volatility  : rapid price spike/crash affecting procurement
- regulatory        : export bans, tariffs, sanctions, policy changes
- logistics         : port blockade, transport failure, route disruption
- weather           : natural disaster, extreme weather event

Respond ONLY with a JSON object — no explanation, no markdown, no code blocks.

JSON schema:
{
  "severity": <1|2|3|4|5>,
  "risk_type": "<supply_disruption|price_volatility|regulatory|logistics|weather>",
  "affected_material": "<confirmed material, lowercase>",
  "affected_region": "<confirmed region>",
  "origin_tier": "<Upstream|Midstream-BGM|Midstream-Cell|Downstream>",
  "reason": "<2-3 sentences: what happened, why it matters for battery supply chain, severity justification>"
}
"""

USER_PROMPT_TEMPLATE = """Assess the supply chain risk in this text.
Pre-identified material: {material}
Pre-identified region: {region}

Text:
---
{filtered_text}
---
"""


# ── Main agent function ────────────────────────────────────────────────────────

def run_risk_assessment_agent(state: PipelineState) -> PipelineState:
    """
    LangGraph-compatible node function.
    Reads filtered_text / material / region from state,
    returns updated state with risk classification fields.
    """
    filtered_text: str = state.get("filtered_text", state.get("raw_input", ""))
    material: str      = state.get("material", "")
    region: str        = state.get("region", "")

    llm = _get_llm()

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=USER_PROMPT_TEMPLATE.format(
            material=material,
            region=region,
            filtered_text=filtered_text,
        )),
    ]

    parsed = invoke_json(llm, messages)

    return {
        **state,
        "severity":         int(parsed.get("severity", 3)),
        "risk_type":        parsed.get("risk_type", "supply_disruption"),
        "affected_material": parsed.get("affected_material", material).lower().strip(),
        "affected_region":  parsed.get("affected_region", region).strip(),
        "origin_tier":      parsed.get("origin_tier", "Upstream"),
        "reason":           parsed.get("reason", ""),
    }
