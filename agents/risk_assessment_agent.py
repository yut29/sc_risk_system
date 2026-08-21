"""
Risk Assessment Agent — LLM-based risk classification

Input (from PipelineState):
  raw_input       : str
  material        : str   (from Intake Agent)
  region          : str   (from Intake Agent)

Output (written to PipelineState):
  severity        : int        1 (low) – 5 (critical)
  risk_type       : RiskType
  origin_tier     : Segment    supply chain tier of event origin
  reason          : str        mandatory justification for Validation Agent

Reads raw_input instead of filtered_text (2026-08-10, docs/open_issues.md P29 follow-up):
filtered_text is Intake's own "2-3 sentences" condensation, produced with no code-level
length limit — purely a soft prompt instruction. Empirically (real long-article testing,
P29) this occasionally cut a sentence that later turned out to be the single most
severity-relevant fact in the source (e.g. a mine collapse article's death toll made it
into filtered_text, but "provincial authorities suspended operations at the site" — the
actual operational-impact fact — got cut because it appeared later in the article than the
3-sentence cap allowed). Without that sentence, this agent had to infer/guess the
operational impact ("could lead to a disruption") instead of stating a confirmed fact
("has resulted in suspension") — same severity number in that test, but a weaker-grounded
`reason`, which matters for Validation Agent's hallucination/coherence checks and ends up
quoted in the final report. Switched to raw_input, using the pre-identified material/region
as focus anchors instead — verified (P29 follow-up) this stays correctly focused even on
a genuinely multi-topic 7800-char article, since material/region already tell it which of
several candidate events to assess. filtered_text itself was removed entirely shortly after
(2026-08-10, same day) once this was its last remaining reader besides a UI debug view —
see intake_agent.py's docstring.

query_facilities tool removed (2026-08-07, same day it was added): tried wiring it in to
inform severity ("how concentrated is this material in NA"), but that double-counts
SupplierConcentration/ImportDependency — both already separate Vulnerability terms
downstream (docs/risk_model.md) — under a different name; severity must be judged purely
from the disruption's own duration/reach, not from NA facility counts. Tried repurposing it
to inform origin_tier instead, but that doesn't hold up either: origin_tier is "what kind of
facility did THIS event disrupt" (a mine strike = Upstream, a cathode plant fire =
Midstream-BGM), answerable from the text alone — not from how a material's facilities happen
to be distributed across tiers in the NA dataset overall. None of this agent's four outputs
(severity/risk_type/origin_tier/reason) benefit from querying real facility data, so there's
no legitimate tool-call decision left for this agent to make — reverted to plain invoke_json.
`query_facilities` itself stays in agents/tools.py, unused for now — kept as tested,
reusable infrastructure in case a genuinely tool-shaped need turns up elsewhere (e.g.
synthesis_agent explaining a Top-3 facility's supplier relationships).

affected_material/affected_region removed (2026-08-07): this agent used to also re-derive
material/region ("confirmed or refined by LLM"), but empirically (8 test cases, incl.
deliberately ambiguous ones — chemical-formula material names, multi-material text, misleading
mention order) it reproduced Intake Agent's material/region byte-for-byte in 7/8 cases; the one
divergence (filling in "North America" where Intake left region empty) didn't change
network_agent.py's seed_generation_status either way (verified: both "" and "North America"
produce no_seed_found for that case) — so it was extra LLM judgment surface with no observed
behavior benefit. Removed the duplicate field entirely rather than just defaulting to it: every
consumer (network_agent.py, data_retrieval_agent.py, synthesis_agent.py, validation_agent.py,
ui/app.py) now reads Intake's `material`/`region` directly — there is only ever one field for
this, not a same-value pair to keep in sync.
"""

from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage

from agents.llm_utils import get_llm, invoke_json
from agents.state import PipelineState, TIER_ORDER

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
You analyze news articles or user queries about battery supply chain disruptions. The text
may be long and cover more than one topic or event — the pre-identified material/region
below tell you which specific event to assess; base your judgment only on the parts of the
text relevant to that event, and ignore other topics even if they appear more prominently
(e.g. earlier in the text, or in a longer section) than the one you were pointed to.

Supply chain tiers:
- Upstream: raw material mining & extraction (cobalt mines, lithium brine, graphite mines)
- Midstream-BGM: battery-grade material processing (cathode/anode active materials, electrolyte, separators)
- Midstream-Cell: cell manufacturing (cylindrical, prismatic, pouch cells)
- Downstream: module/pack assembly, EV/ESS manufacturers

Severity scale (1–5) — judge ONLY from how bad the disruption ITSELF is (its own duration
and geographic/systemic reach), never from how many North American facilities depend on the
material — that's a separate, downstream-exposure question this system already answers
elsewhere (network traversal + SupplierConcentration), not part of severity:
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
  "origin_tier": "<Upstream|Midstream-BGM|Midstream-Cell|Downstream>",
  "reason": "<2-3 sentences: what happened, why it matters for battery supply chain, severity justification>"
}
"""

USER_PROMPT_TEMPLATE = """Assess the supply chain risk in this text.
Pre-identified material: {material}
Pre-identified region: {region}

Text:
---
{raw_input}
---
"""


# ── Main agent function ────────────────────────────────────────────────────────

def run_risk_assessment_agent(state: PipelineState) -> PipelineState:
    """
    LangGraph-compatible node function.
    Reads raw_input / material / region from state,
    returns updated state with risk classification fields.
    """
    raw_input: str = state.get("raw_input", "")
    material: str  = state.get("material", "")
    region: str    = state.get("region", "")

    llm = _get_llm()

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=USER_PROMPT_TEMPLATE.format(
            material=material,
            region=region,
            raw_input=raw_input,
        )),
    ]

    parsed = invoke_json(llm, messages)

    # Defensive parsing (found in review, 2026-08-17): `.get(key, default)` only substitutes
    # the default when the key is ABSENT, not when the LLM explicitly emits "severity": null
    # or an origin_tier string outside the 4 literal Segment values — a very plausible failure
    # mode for a 24B model. Unguarded, either one crashes deep downstream instead of here
    # (int(None) -> TypeError; TIER_ORDER[bad_tier] -> KeyError in network_agent.py, several
    # calls removed from this agent, much harder to diagnose). Fixed at the source, same
    # philosophy as intake_agent.py's CANONICAL SPELLING / UNSUPPORTED MATERIAL handling:
    # normalize/validate the LLM's output once, right where it's parsed.
    try:
        severity = int(parsed.get("severity") or 3)
    except (TypeError, ValueError):
        severity = 3
    severity = min(5, max(1, severity))

    origin_tier = parsed.get("origin_tier")
    if origin_tier not in TIER_ORDER:
        origin_tier = "Upstream"

    return {
        **state,
        "severity":    severity,
        "risk_type":   parsed.get("risk_type") or "supply_disruption",
        "origin_tier": origin_tier,
        "reason":      parsed.get("reason") or "",
    }
