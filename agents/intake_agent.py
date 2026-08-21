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
  mentioned_company  : str | None   e.g. "Panasonic" (facility-specific incidents only)
  mentioned_location : str | None   e.g. "Kansas" (city/state named alongside the company)
  additional_events_note : str | None   set when the input bundles more than one candidate
                                          event (see SYSTEM_PROMPT rule on multi-event texts)

filtered_text removed (2026-08-10): used to be a "2-3 sentence" condensed excerpt, but had
no functional consumer left after risk_assessment_agent.py switched to reading raw_input
directly (see its docstring / docs/open_issues.md P29 follow-up) — the only place that
still touched it was ui/app.py's raw-state debug view. Removed rather than kept as a dead
field: a summarization step with no reader is just another thing that can silently drift
out of sync with the event it's supposed to describe (exactly what P29 found).

silicon/cnt/electrolyte removed from the recognized material vocabulary (2026-08-11,
docs/open_issues.md P31): P26/P28 had added all 7 component/precursor materials with no
real origin-country data, falling back to a "material matches, region ignored" precautionary
default. Researching real data (P31) found source-backed country concentration for 4 of the
7 (pvdf/copper_foil/aluminum_foil/separator, now in IMPORT_ORIGIN_DISTRIBUTION) but nothing
citable for silicon/cnt/electrolyte — and for silicon specifically, the facility data itself
shows real US domestic capacity (Group14, Sila listed as Upstream/BGM facilities), so
assuming import dependence would contradict the data rather than fill a gap. Rather than keep
offering region-blind analysis for these 3, they're excluded from intake's recognized
vocabulary until real data is researched — a news event about them is currently just not
relevant/classifiable, same honest-limitation treatment as logistics events (P21). This only
changes what Intake will CLASSIFY an event as; the underlying facility data
(material_keywords in facilities_clean.csv) still correctly tags which companies produce
these materials — that's a true fact about the facility, independent of whether this system
can currently do region-based risk analysis for it.
"""

import re
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage

load_dotenv(Path(__file__).parent.parent / ".env")

from agents.llm_utils import get_llm, invoke_json
from agents.state import PipelineState

# ── LLM singleton ─────────────────────────────────────────────────────────────

_llm: Optional[BaseChatModel] = None


def _get_llm() -> BaseChatModel:
    global _llm
    if _llm is None:
        _llm = get_llm(temperature=0)
    return _llm


# ── Prompts ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the Intake Agent for a Battery Supply Chain Risk System.
Your task is to analyze a text (news article or user query) and extract structured information.

Known critical materials for battery supply chains:
cobalt, lithium, nickel, manganese, graphite, copper — plus these component/precursor
materials with researched, source-backed origin-country data (2026-08-11): pvdf (binder),
copper_foil (current collector — distinct from raw "copper" above), aluminum_foil (current
collector), separator

Known risk regions — each bullet lists the individual real places relevant to that material;
region must always be exactly ONE of these single place names, NEVER a joined/combined string
like "X / Y" or "X (A, B) / Y" even if several are listed together below or the source text
discusses more than one (found in review, 2026-08-17: a joined region string like "South America
/ Australia" silently fails to match ANY facility for the countries named first in the join —
downstream matching only works for one clean place name at a time). If the text names one
specific place, use that. If it genuinely discusses several of these places without one being
clearly primary, pick whichever is most central to the actual disruption described, the same way
you already pick one material/event under the MULTI-EVENT TEXTS rule below:
- Democratic Republic of the Congo (cobalt, copper)
- Chile, Argentina, or Australia (lithium)
- China, Mozambique, Madagascar, or Tanzania (graphite)
- Indonesia or Philippines (nickel)
- South Africa, Gabon, or Australia (manganese)
- China (dominant), South Korea, or Japan (copper_foil, separator)
- China (dominant) or Europe (aluminum_foil, pvdf)

If the text names a specific country, use that country's name as the region — do not
generalize it up to a broader category (e.g. a Chile-specific event should yield
region="Chile", not region="South America").

FOREIGN (non-US/Canada/Mexico) EVENTS: use COUNTRY-level granularity only, even if the
text names a more specific province/state/city — e.g. an event in "Yichun, Jiangxi
province, China" should yield region="China", never region="Jiangxi". This system's
origin-country data (which region is matched against) is only ever modeled at the country
level for foreign sources — a sub-national region here doesn't correspond to anything and
silently fails to match, discarding a real country-level match the text did support.

DOMESTIC (US / Canada / Mexico) EVENTS: if the event happens within the US, Canada, or
Mexico and the text names a specific state or province, use THAT state/province name as
the region — do NOT generalize it up to the country level. E.g. an ice storm disrupting
plants "across Michigan" should yield region="Michigan", never region="United States" or
"USA". Facility locations in this system's data are tracked at the state/province level,
not just country — a country-level region for a domestic event is too coarse to match
anything and effectively discards the only location detail that could have been useful.
This is the opposite granularity rule from the FOREIGN EVENTS rule above because this
system's OWN facility location data is only ever state/province-level for domestic
facilities, but only ever country-level for foreign origin data — match whichever
granularity the downstream data actually has, not the most specific detail the text
happens to mention.
Both of these rules apply ONLY to regional/material-type events (rule a). Neither applies to
facility-specific incidents (rule b) — those still get region="" regardless of what
state/province/country the named facility happens to be in; that location belongs in
mentioned_location instead, never in region. Do not let this rule override rule (b).

CANONICAL SPELLING: always output the country's full official name exactly as it appears
in the "Known risk regions" list above, never an abbreviation, alias, or short form found
in the source text — even though real news text overwhelmingly prefers the short forms.
E.g. always write "Democratic Republic of the Congo", never "DR Congo", "DRC",
"Congo-Kinshasa", or "the Congo" (all of which real articles use interchangeably); write
"United States" not "US"/"USA"; write "South Korea" not "Korea". Downstream code matches
this string exactly against known origin-country data, so an unrecognized abbreviation
silently fails to match — do not introduce variation here.

Respond ONLY with a JSON object — no explanation, no markdown, no code blocks.

JSON schema:
{
  "relevant": true | false,
  "trigger_type": "A" | "B",
  "material": "<primary critical material mentioned, lowercase, or empty string>",
  "region": "<affected supply region, or empty string>",
  "keywords": ["<keyword1>", "<keyword2>", ...],
  "mentioned_company": "<specific company/facility operator named in the text, or null>",
  "mentioned_location": "<city or state named alongside that company, or null>",
  "additional_events_note": "<one sentence describing EITHER another distinct candidate event not selected, OR another material also affected by this same selected event, or null if neither applies>",
  "reasoning": "<one sentence explaining relevance decision>"
}

Rules:
- relevant=true if the text concerns EITHER (a) a battery supply chain risk of any kind
  (material shortage, price volatility, regulatory action, logistics disruption — including a
  shipping chokepoint/strait closure or blockade, e.g. Hormuz, Malacca, Suez, Panama Canal, even
  though these are usually reported as oil/general-trade stories, not battery-specific ones —
  weather event, labor/capacity disruption, etc. — do not require it to match one of the specific
  materials/regions listed above, those are just examples), OR (b) a facility-specific
  disruption (fire, strike, shutdown, quality recall, etc.) at a named company's own battery
  supply chain plant/mine — this second category has no material/region of its own; the named
  company and facility ARE the event, not a downstream effect of one
- trigger_type "A" = the input looks like a news article; "B" = a direct user question/query
- material must be one of: cobalt, lithium, nickel, manganese, graphite, copper, pvdf,
  copper_foil, aluminum_foil, separator — or empty
- If not relevant, set material="", region="", keywords=[]
- UNSUPPORTED MATERIAL: if the text otherwise clearly describes a genuine battery-material
  supply disruption (rule a) but the specific material it names is NOT one of the ones listed
  above (e.g. silicon, electrolyte, carbon nanotube/CNT, rare earths, sodium/Na-ion, aluminum as
  a standalone metal) — relevant is STILL true (this genuinely is a battery supply chain event,
  do not lie about that), but material="" (empty, since none of the tracked values actually
  apply — do not force it into the closest tracked material, that would misrepresent what the
  text says). Set additional_events_note to one sentence naming the real material and noting
  this system doesn't have origin-country data for it, for context (e.g. "Real event, but about
  silicon, which this system doesn't have sourced origin-country data for."). Downstream, an
  event with relevant=true and material="" naturally produces "no facility could be identified"
  rather than a fabricated result — that is the correct, honest outcome here, the same as any
  other event this system can't pin to a specific facility or region.
- MULTI-EVENT TEXTS: a long input (e.g. a market roundup or multi-topic news digest) may describe
  several distinct events that would each independently qualify as relevant under rule (a) or (b).
  You can only extract ONE event's material/region/keywords per response — do not default to
  whichever one happens to appear first in the text. Instead, select the single event that
  represents the most severe/highest-impact ACTUAL supply disruption (an ongoing shortage,
  stoppage, embargo, damage, or capacity loss) — this outranks a price movement, a regulatory
  proposal, or a forward-looking announcement, even one that appears earlier in the text. A price
  DROP caused by improved supply is easing news, not a disruption — never select it over a genuine
  disruption elsewhere in the same text. If you found more than one genuine candidate event,
  briefly name the other one(s) in additional_events_note; otherwise leave it null.
  keywords, mentioned_company, and mentioned_location must all describe this SAME selected event —
  never mix in details from a different paragraph/event in the text.
- MULTI-MATERIAL SINGLE EVENT: this is different from MULTI-EVENT TEXTS above — a single event can
  itself affect more than one tracked material at once (e.g. one government order banning exports of
  BOTH copper and cobalt concentrates at the same time). material still takes only one value — when
  choosing, prefer whichever material is more central to battery supply chains (cobalt, lithium,
  nickel, manganese, graphite, pvdf, copper_foil, aluminum_foil, separator) over copper, which this
  system tracks mainly as a baseline/comparison case, not a battery-critical material — this is not
  about which material suffered a worse disruption (the same single action affects both equally),
  it's about which one this system can meaningfully analyze. You MUST still note the other affected
  material(s) in additional_events_note, e.g. "This same event also affects copper, not analyzed in
  this run." Do not leave additional_events_note null just because there was only one event — check
  separately whether that one event touches more than one tracked material.
- mentioned_company: only set this for a FACILITY-SPECIFIC incident (a named company's own plant/mine
  having a fire, strike, shutdown, quality recall, etc.) — NOT for a general regional/material event
  like a country restricting exports. If the text names a company but the event is a regional/material
  disruption (e.g. "Chile restricts lithium exports, affecting Albemarle's supply"), still set
  mentioned_company=null — Albemarle is a downstream effect, not the origin of this event. This rule
  applies EVEN IN long, entity-dense texts (market reports, policy analyses) that name many
  companies, joint ventures, or government bodies around the core event — naming a company near the
  event does not make that company the event's origin. Ask yourself: "did THIS company's own
  plant/mine break down, or is the country/region/material itself what's disrupted?" — only the
  former justifies mentioned_company.
- mentioned_location: the MOST SPECIFIC city/state/province mentioned alongside the named
  facility — prefer city over state/province if both are given (e.g. text says "De Soto,
  Kansas" → mentioned_location="De Soto, Kansas" or "De Soto", not just "Kansas"; only fall
  back to state/province alone if the text never names a city). This is a different field
  from region and the DOMESTIC EVENTS rule above (which governs region's state/province-level
  granularity for regional events) does not apply here — mentioned_location should stay as
  specific as the text allows regardless of that rule. ONLY include actual geographic
  place names (city/state/province) here — NEVER the facility's own proper name (mine name,
  plant name, site name), even if the text mentions it right next to the location (e.g. text
  says "Glencore's Raglan Mine in Quebec" → mentioned_location="Quebec", NOT "Raglan Mine,
  Quebec" — "Raglan Mine" is a facility name, not a place, and mixing it in breaks the
  downstream city/state matching, which only checks real geographic fields). Do not invent a
  location — if the text only names the company without any location, leave this null.
"""

USER_PROMPT_TEMPLATE = """Analyze this input:

---
{raw_input}
---
"""


# A bare URL (nothing else in the input) — 2026-08-12, real finding: this system has no web-
# fetching tool anywhere (verified by grepping the whole codebase), so pasting just a link gives
# the LLM nothing but the URL string itself to work with. Empirically this doesn't fail safely —
# tested with a real URL whose slug happened to closely mirror its actual headline: the LLM
# confidently extracted material/region AND wrote a specific, plausible-sounding severity
# justification purely by guessing from the slug words, with no real page content behind any of
# it. Worse, a URL with no readable slug at all (just a query-string post ID) still produced a
# confident, fully fabricated material+region — no real information present, pure invention, most
# likely triggered by loose association with the domain name. Rather than trust a prompt
# instruction to reliably stop this (same class of instruction-following failure as P39 — asking
# an LLM to recognize "I don't actually know this" is not reliable), short-circuit before the LLM
# ever sees it: a bare URL is deterministically never analyzable input.
_BARE_URL_RE = re.compile(r"^https?://\S+$")


def _is_bare_url(raw_input: str) -> bool:
    return bool(_BARE_URL_RE.match(raw_input.strip()))


# ── Main agent function ────────────────────────────────────────────────────────

def run_intake_agent(state: PipelineState) -> PipelineState:
    """
    LangGraph-compatible node function.
    Reads raw_input from state, returns updated state with intake fields.
    """
    raw_input: str = state.get("raw_input", "")

    if _is_bare_url(raw_input):
        return {
            **state,
            "relevant":               False,
            "trigger_type":           "B",
            "material":               "",
            "region":                 "",
            "keywords":               [],
            "mentioned_company":      None,
            "mentioned_location":     None,
            "additional_events_note": (
                "Input is just a URL, not article text — this system cannot fetch web pages, "
                "so a link alone cannot be analyzed. Paste the article's actual text instead."
            ),
        }

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

    mentioned_company  = parsed.get("mentioned_company") or None
    mentioned_location = parsed.get("mentioned_location") or None
    if isinstance(mentioned_company, str):
        mentioned_company = mentioned_company.strip() or None
    if isinstance(mentioned_location, str):
        mentioned_location = mentioned_location.strip() or None

    additional_events_note = parsed.get("additional_events_note") or None
    if isinstance(additional_events_note, str):
        additional_events_note = additional_events_note.strip() or None

    return {
        **state,
        "relevant":               relevant,
        "trigger_type":           trigger_type,
        "material":               material,
        "region":                 region,
        "keywords":               keywords,
        "mentioned_company":      mentioned_company,
        "mentioned_location":     mentioned_location,
        "additional_events_note": additional_events_note,
    }
