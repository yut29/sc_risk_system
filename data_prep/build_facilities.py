"""
从 NAATBatt 数据库提取并清洗设施数据，生成 facilities_clean.csv。
只保留 Commercial 设施，计算 material_keywords / supplier_concentration / capacity_source。

数据源: Append2 sheet (单表，包含所有层级)
v2 (2026-06-10): 适配 March 2026 版本，Midstream 按 Product/Facility Type 拆分为 BGM/Cell
"""

import json
import random
import re
import pandas as pd
from pathlib import Path

NAATBATT_FILE = Path(__file__).parent.parent / "data" / "naatbatt-database-31march2026.xlsx"
OUTPUT_FILE   = Path(__file__).parent.parent / "data" / "facilities_clean.csv"

NORTH_AMERICA = {"US", "USA", "CA", "Canada", "MX", "Mexico"}

# Expand bare 2-letter state/province abbreviations to full names for Upstream's own
# import_origin_region (2026-08-06, fixing a real bug found while testing the removal of
# _region_match()'s import_dependency gate in network_agent.py): a bare "UT" or "CA" is
# dangerously short for substring matching against an event region string — "ut" is a
# literal substring of "south" (as in "South America"), "ca" of "america", causing false
# positives with zero geographic relation to Utah/California. Only US states + Canadian
# provinces need this — the Mexican `state` values in this dataset are already full names
# ("Baja California Sur", "Coahuila", "Querétaro", "Sonora").
US_STATE_ABBREV = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
CA_PROVINCE_ABBREV = {
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba", "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador", "NS": "Nova Scotia", "ON": "Ontario",
    "PE": "Prince Edward Island", "QC": "Quebec", "SK": "Saskatchewan",
}
# "N.L." (with periods) is NAATBatt's abbreviation for Nuevo León, Mexico — distinct from
# Canada's "NL" (Newfoundland and Labrador, no periods) above; the one facility using it
# (Sungwoo Hitech, Pesqueria) has country="Mexico" so there's no real ambiguity, just a
# format the dict above doesn't already cover.
MX_STATE_ABBREV = {"N.L.": "Nuevo León"}
STATE_ABBREV_TO_FULL = {**US_STATE_ABBREV, **CA_PROVINCE_ABBREV, **MX_STATE_ABBREV}

LEAD_TIME = {
    "Upstream":       12,
    "Midstream-BGM":  8,
    "Midstream-Cell": 6,
    "Downstream":     4,
}

# Lead-time refinement (2026-08-03): the pure per-segment base above made every facility
# within the same segment identical regardless of material/region/data availability — a
# documented model simplification (docs/data_processing.md §3). Combined with
# supplier_concentration/import_dependency, which are also constant per material+segment
# (see below), this left individual companies within the same segment+material combination
# indistinguishable on 3 of Vulnerability's 4 terms. Extended to a rule-based proxy —
# still fully rule-based, not measured, but more granular: tier + material criticality
# (aligned to the SAME USGS NIR / HIGH_CONCENTRATION_MATERIALS data already cited above,
# not a second independent judgment) + import-origin region + capacity data availability.
#
# NOTE: this does NOT by itself guarantee every company gets a unique score. Region is a
# weighted-random per-facility draw skewed toward the real dominant source country (e.g.
# 73% of cobalt facilities draw "Africa (DRC)", see IMPORT_ORIGIN_DISTRIBUTION) — realistic
# concentration means most facilities of a concentrated material still collide on this
# adjustment. Tie-breaking for Downstream/Midstream-BGM is handled separately by
# SingleSourceDependency (graph-based direct_supplier_count, see
# agents/data_retrieval_agent.py) — this formula is a complementary refinement to make the
# LeadTime term itself more informative, not a substitute for that mechanism.
MATERIAL_LEAD_TIME_ADJUSTMENT = {
    "cobalt":    4,  # NIR=79% AND in HIGH_CONCENTRATION_MATERIALS (DRC 73%+Indonesia 14%=87%) — only
                     # material that's both import-dependent and geographically concentrated
    "graphite":  2,  # NIR=100% but diversified supply (China + 15+ countries per risk_model.md) —
                     # not in HIGH_CONCENTRATION_MATERIALS, so lower than cobalt despite full NIR
    "manganese": 2,  # NIR=100% but diversified (4 countries, 10-38% each)
    "nickel":    1,  # NIR≈100% primary but "global surplus since 2022" (risk_model.md) — easier to
                     # re-source despite high import dependence
    "lithium":   1,  # NIR>50%, lowest import-dependence of the five + most diversified (10+ miners)
    "copper":    0,  # not in IMPORT_MATERIALS — not treated as import-critical in this model
}


# Countries IMPORT_ORIGIN_DISTRIBUTION now names directly (2026-08-06, P20) instead of via a
# continent-prefixed bucket string — need an explicit lookup for these so
# _region_lead_time_adjustment() below doesn't silently lose their distance tier just because
# the string no longer starts with "Africa"/"Asia".
_HIGH_DISTANCE_COUNTRIES = {"democratic republic of the congo", "china"}
_MED_DISTANCE_COUNTRIES = {"indonesia", "philippines"}


def _region_lead_time_adjustment(region: str) -> int:
    """
    Rough shipping-distance/logistics-complexity proxy from the assigned
    import_origin_region string (see IMPORT_ORIGIN_DISTRIBUTION) — grouped by continent
    prefix since the exact strings carry material-specific bracketed detail
    (e.g. "Africa (DRC)" vs "Africa (South Africa, Gabon)"). Still-bundled multi-country
    buckets (South America (Chile/Argentina) etc., pending sourced per-country split, P20)
    keep falling through to the continent-prefix rules below.
    """
    r = region.lower()
    if r in _HIGH_DISTANCE_COUNTRIES:
        return 3
    if r in _MED_DISTANCE_COUNTRIES:
        return 2
    if region.startswith("Africa"):
        return 3
    if region.startswith("Asia"):
        return 2
    if region.startswith("South America"):
        return 2
    if region == "Australia":
        return 1
    return 1  # "Other / Diversified" or unmatched

IMPORT_MATERIALS = {
    "cobalt", "nickel", "lithium", "manganese", "graphite",
    # Ergänzt 2026-08-11 (docs/open_issues.md — Rohstoff-Recherche für die 7 P26/P28-Materialien):
    # anders als silicon/cnt/electrolyte (keine belastbare Länderkonzentration gefunden, dort
    # bewusst NICHT ergänzt) haben diese 4 Komponentenmaterialien reale, zitierfähige globale
    # Produktionskonzentrationsdaten — siehe IMPORT_ORIGIN_DISTRIBUTION unten für Quellen.
    "copper_foil", "aluminum_foil", "separator", "pvdf",
}

# IMPORT_ORIGIN_DISTRIBUTION: realistic supplier-country split instead of a single
# dominant region — every NA facility handling e.g. cobalt was previously tagged
# "Africa (DRC)", implying 100% of them import from the DRC specifically, which
# overstated how many facilities a DRC-only event could plausibly hit. Shares below
# reflect real market concentration (USGS MCS 2026 / IEA GCMO 2025, see
# risk_model.md §SupplierConcentration); "Other / Diversified" facilities are not
# tied to any single dominant-source event. Assignment is a per-facility weighted
# random draw (agents/state.py-independent, in build_facilities.py), seeded by
# facility_id so it's reproducible across re-runs — see infer_import_origin().
IMPORT_ORIGIN_DISTRIBUTION: dict[str, list[tuple[str, float]]] = {
    "cobalt": [
        ("Democratic Republic of the Congo", 0.73),  # USGS MCS 2026: DRC 73% of world mine production
        ("Indonesia",                        0.14),  # USGS MCS 2026: Indonesia 14%
        ("Other / Diversified",              0.13),
    ],
    "nickel": [
        ("Indonesia",            0.67),  # USGS MCS 2026: Indonesia ~67%
        ("Philippines",          0.18),
        ("Other / Diversified",  0.15),
    ],
    "lithium": [
        # Split into named countries 2026-08-12 (docs/open_issues.md P46; was a combined
        # "South America (Chile/Argentina)" bucket, TODO since P20). Global lithium mine
        # production by country, ~2025/2026: Australia 36%, Chile 22%, China 16%, Argentina
        # 10% (rounded market-share estimates from multiple industry sources, e.g.
        # worldpopulationreview.com "Lithium Production by Country 2026", Statista); these
        # four together account for the large majority of global output, remainder spread
        # across smaller producers (Brazil, Zimbabwe, Canada, Portugal, etc.).
        ("Australia",             0.36),
        ("Chile",                 0.22),
        ("China",                 0.16),
        ("Argentina",             0.10),
        ("Other / Diversified",   0.16),
    ],
    "manganese": [
        ("Africa (South Africa, Gabon)",  0.55),  # TODO(P20): split into South Africa/Gabon with sourced shares
        ("Australia",                     0.25),
        ("Other / Diversified",           0.20),
    ],
    "graphite": [
        ("China",                                      0.77),  # USGS MCS 2026: China ~77%
        ("Africa (Mozambique, Madagascar, Tanzania)",  0.15),  # TODO(P20): split into 3 countries with sourced shares
        ("Other / Diversified",                         0.08),
    ],
    # Ergänzt 2026-08-11 — 4 der 7 P26/P28-Komponentenmaterialien, für die eine reale
    # Länderkonzentration recherchierbar war (Marktforschungsberichte, nicht USGS-Niveau, aber
    # echte zitierfähige Quellen, keine erfundenen Zahlen). silicon/cnt/electrolyte bewusst NICHT
    # ergänzt: keine belastbare Konzentrationsangabe gefunden, und für silicon zeigt der Datensatz
    # selbst bereits US-Inlandskapazität (Group14, Sila u.a. als Upstream/BGM-Facilities gelistet)
    # — eine pauschale Importabhängigkeits-Annahme wäre dort nicht durch Daten gedeckt.
    "copper_foil": [
        # Lithium-ion battery copper foil shipments, China 82.9% of global deliveries 2025
        # (metalnomist.com, "Lithium-Ion Battery Copper Foil Shipments Surge...", Jul 2026)
        ("China",                 0.83),
        ("South Korea / Japan",   0.12),
        ("Other / Diversified",   0.05),
    ],
    "aluminum_foil": [
        # China 66.1% revenue share of battery-grade aluminum foil market 2025
        # (grandviewresearch.com, "Aluminum Foil Market Size, Share & Trends Report")
        ("China",                0.66),
        ("Europe",               0.14),  # Europe ~13.6% of global lithium battery aluminum foil market
        ("Other / Diversified",  0.20),
    ],
    "separator": [
        # China >50% of global lithium-ion battery separator production (multiple market
        # research sources, e.g. mobilityforesights.com); Asia-Pacific overall 64.74% market
        # share 2025, with Japan (Asahi Kasei, Toray) and South Korea (SK) as other major producers
        ("China",                0.55),
        ("Japan / South Korea",  0.30),
        ("Other / Diversified",  0.15),
    ],
    "pvdf": [
        # China ~68% of global lithium battery-grade PVDF production capacity 2025
        # (verifiedmarketreports.com / cognitivemarketresearch.com); other top-3/4 producers
        # Solvay (Belgium), Arkema (France), Kureha/Daikin (Japan) cover most of the remainder
        ("China",                 0.68),
        ("Europe / Japan",        0.22),
        ("Other / Diversified",   0.10),
    ],
}

# SupplierConcentration: literaturbasierte Regel — globale Marktstruktur (nicht Nordamerika-Zählung)
# cobalt: DRC 73% mine share, 2-3 major miners (USGS MCS 2026, Feb 2026)
# nmc/nca: China ≥95% PCAM share (IEA Global Critical Minerals Outlook 2025)
# copper_foil/aluminum_foil/separator/pvdf ergänzt 2026-08-11: chinesischer Marktanteil
# (66-83%, siehe IMPORT_ORIGIN_DISTRIBUTION oben) liegt über der Kobalt-Schwelle, die diese
# Einordnung ursprünglich begründet hat — Konsistenz mit der gleichen zugrundeliegenden
# Recherche, nicht separat neu bewertet.
HIGH_CONCENTRATION_MATERIALS = {"cobalt", "nmc", "nca", "copper_foil", "aluminum_foil", "separator", "pvdf"}

# Facility-level import_dependency overrides (cobalt)
# Basis: USGS NIR (79%) is a sector aggregate, not facility-level.
# The following facilities are known to source cobalt outside the DRC supply chain:
#   - Nth Cycle: secondary cobalt from battery recycling (no virgin DRC cobalt) — genuinely
#     not import-dependent (domestic recycling feedstock), import_dependency=False is correct.
#   - Sherritt International: Moa Bay (Cuba) + Ambatovy (Madagascar) JV — this facility IS
#     import-dependent (it does import cobalt/nickel, just not from DRC), so import_dependency
#     should be True, not False. Fixed 2026-08-03: the previous version set
#     import_dependency=False with import_origin_region="Caribbean / Africa (non-DRC)" — a
#     negation written as free text, which region_match()'s substring matching can't parse
#     ("non-DRC" literally contains "drc" as a substring, so a DRC-region event still matched
#     it whenever the import_dependency guard was bypassed) and which also meant Sherritt could
#     never match ANY real region-based event, including a genuine Cuba/Madagascar-specific one
#     it should be exposed to — overcorrected a false positive into a permanent false negative.
#     Naming the real, specific, affirmative source countries instead avoids both problems:
#     "Cuba / Madagascar" shares no substring with REGION_ALIASES["africa/drc"]'s
#     ["africa","drc","congo"] list, so it won't falsely match a DRC event, while still being
#     available to correctly match a real Cuba- or Madagascar-specific disruption.
#   - Boleo Copper Project (BGM): downstream processing of own Mexican mine output — genuinely
#     not import-dependent (captive upstream feedstock), import_dependency=False is correct.
# Documented in docs/data_processing.md §4.4
IMPORT_DEP_OVERRIDES: dict[str, dict] = {
    "Nth Cycle":              {"import_dependency": False, "import_origin_region": ""},
    "Sherritt International": {"import_dependency": True, "import_origin_region": "Cuba / Madagascar"},
    "Boleo Copper Project":   {"import_dependency": False, "import_origin_region": ""},
}

KEYWORD_RULES = [
    (["cobalt"],                           "cobalt"),
    (["nickel manganese cobalt", "nmc", "ncm"], "nmc"),
    (["nickel cobalt aluminum", "nca"],    "nca"),
    (["lithium iron phosphate", "lfp", "lifepo4"], "lfp"),
    (["lithium"],                          "lithium"),
    (["nickel"],                           "nickel"),
    (["manganese"],                        "manganese"),
    # "anode" removed (2026-08-06): checked every facility that relied on this bare word
    # instead of literally saying "graphite" — all 4 were false positives (Amprius/Nexeon
    # are silicon anodes, MSE Supplies is a generic supplies listing, Schlenk makes
    # copper/nickel foils) and zero were real graphite hits. "anode" alone doesn't imply
    # graphite now that silicon anodes exist in this dataset — see the new "silicon" rule
    # below for those.
    (["graphite"],                         "graphite"),
    (["electrolyte"],                      "electrolyte"),
    (["separator"],                        "separator"),
    (["lead acid", "lead-acid"],           "lead_acid"),
    # Added 2026-08-06 while auditing the 50 Midstream-BGM facilities with empty
    # material_keywords (docs/open_issues.md — material dependency chain work). Verified
    # against product text for zero false-positive collisions with existing rules
    # (checked separately: no other product field contains these substrings).
    (["silicon", "nano-silicon", "silane"], "silicon"),
    (["pvdf"],                              "pvdf"),
    (["carbon nanotube", "conductive carbon"], "cnt"),
]

# Product/Facility Type 中包含这些词 → Midstream-Cell
CELL_TYPE_KEYWORDS = [
    "cell", "pouch", "cylindrical", "prismatic", "lib manuf", "cell manuf",
    "cell assembly", "consumer batter",
]

# NMC/NCA/LFP sind Kathodenchemien (Verbundprodukte), keine Rohstoffe — enthalten aber
# die genannten Rohstoffe. Ohne diese Zuordnung würde ein Produkttext wie "NMC
# Cathode Active Material" nur material_keywords=["nmc"] ergeben, ohne "cobalt" —
# ein Kobalt-Ereignis würde diese Anlage dann über _material_match() nicht finden,
# obwohl NMC-Kathodenmaterial de facto Kobalt enthält (docs/architecture.md
# "Bekannte Grenzen des Seeding-Mechanismus" Punkt 4). Aluminium wird in diesem
# System nicht als Risikomaterial geführt, daher kein "aluminum" für NCA.
#
# lithium ergänzt bei allen drei (2026-08-10, docs/open_issues.md P29-Nachtrag):
# NMC (LiNiMnCoO2), NCA (LiNiCoAlO2) und LFP (LiFePO4) sind alle drei Lithium-
# Verbindungen — lithium fehlte hier bisher komplett, obwohl es (anders als
# Aluminium) zu den 6 verfolgten Rohstoffen gehört. Gefunden beim Auswerten,
# wie viele BGM-Anlagen Rohstoffe brauchen: 2 reine "lfp"-Anlagen matchten nicht
# auf material="lithium", obwohl LFP chemisch zwingend Lithium enthält — beim
# Nachprüfen stellte sich heraus, dass auch "nmc"/"nca"-Anlagen ohne separat
# genanntes "lithium" denselben Fehler hatten (nur zufällig durch andere
# Rohstoffe wie Kobalt/Nickel überdeckt, nie isoliert aufgefallen). "lfp" selbst
# war vorher gar nicht in dieser Tabelle — nur implizit über UPSTREAM_TO_BGM in
# build_graph.py als Graph-Kanten-Ziel bekannt, nicht als material_match-Signal.
CATHODE_CHEMISTRY_RAW_MATERIALS: dict[str, list[str]] = {
    "nmc": ["cobalt", "nickel", "manganese", "lithium"],
    "nca": ["cobalt", "nickel", "lithium"],
    "lfp": ["lithium"],
}

# Gleiches Prinzip wie oben, aber für verarbeitete Komponentenmaterialien statt
# Kathodenchemien: copper_foil ist ein Kupfer-Verarbeitungsprodukt (gewalzte/elektrolytisch
# abgeschiedene Kupferfolie als Stromkollektor), enthält also zwingend Kupfer als Rohstoff.
#
# Gefunden 2026-08-11 (docs/open_issues.md P30): _material_match() prüfte bisher per
# Teilstring statt exaktem Token-Match, wodurch material="copper" ALLE copper_foil-Anlagen
# traf (Zufallstreffer, weil "copper" ein Präfix von "copper_foil" ist) — UND dabei jede
# Regionsprüfung übersprang, weil keine dieser Anlagen je "copper"-Herkunftsdaten hatte.
# Die Teilstring-Prüfung wurde durch exaktes Token-Matching ersetzt (network_agent.py),
# was den Zufallstreffer korrekt beseitigt hat — aber copper_foil-Anlagen brauchen ja
# tatsächlich echtes Kupfer als Input, das war kein reiner Bug, sondern ein unvollständig
# modelliertes echtes Bedürfnis. Diese Tabelle macht die Abhängigkeit jetzt explizit,
# statt sie über einen Namenszufall laufen zu lassen.
#
# Kein Aluminium-Äquivalent für aluminum_foil: Aluminium wird in diesem System nicht als
# eigener Rohstoff geführt (gleiche Begründung wie bei NCA oben) — es gibt keine
# "aluminum" IMPORT_ORIGIN_DISTRIBUTION, also auch nichts, worauf aluminum_foil abbilden
# könnte.
#
# Hinweis zur Regionsschärfe: copper ist nicht in IMPORT_MATERIALS (siehe unten,
# USGS-Nettoimportabhängigkeit für Kupfer ist gering, anders als bei den 5 anderen
# Rohstoffen) — d.h. selbst mit dieser Ergänzung hat keine copper_foil-Anlage eine echte
# import_origin_region für "copper", ein Kupfer-Ereignis matcht also weiterhin nur über den
# materialbasierten Fallback (region wird ignoriert), nicht über einen echten Regionsabgleich
# — bewusst, gleiches "unbekannt ≠ ausgeschlossen"-Prinzip wie bei den P26/P28-Materialien.
COMPONENT_RAW_MATERIALS: dict[str, list[str]] = {
    "copper_foil": ["copper"],
}

# Manuelle Ergänzung für Hersteller (ursprünglich nur Midstream-Cell, seit 2026-08-06 auch
# Midstream-BGM — siehe unten), deren `product`/`product_type` nur generische Begriffe
# enthalten ("Other/Unknown", "Cell", "Additives", "Binders" o.ä.) ohne Chemie zu nennen —
# anders als bei den literaturbasierten Regeln (USGS/IEA, siehe oben) beruht dies auf
# öffentlich zugänglichen Firmen-/Produktinformationen, nicht auf einer standardisierten
# Statistikquelle, und ist daher im Sinn von Baris' "reale Daten / simulierte Annahmen /
# Modellvereinfachungen"-Dreiteilung als Modellvereinfachung mit manueller Recherche zu
# kennzeichnen (siehe docs/data_processing.md §4.6). Nur Firmen aufgenommen, deren
# Kernchemie öffentlich eindeutig dokumentiert ist; wo `brief_profile` keine Chemie nennt
# (z.B. Chemours "Binders", Parker LORD "Adhesives", DuPont/Black Diamond Structures
# "Additives", PPG "Other", The Mosaic Company/Nouryon Chemicals ohne jeden Batteriebezug),
# bewusst NICHT aufgenommen — Rätselraten wäre nicht vertretbar, gleiches Prinzip wie bei
# Stryten Energy unten.
MATERIAL_KEYWORD_OVERRIDES: dict[str, list[str]] = {
    "ABSL Power Solutions, Inc.": ["lithium"],           # EnerSys/ABSL Li-ion Zellen für Raumfahrt/Verteidigung
    "American Lithium Energy":    ["lithium"],           # Hochsicherheits-Li-Ion-Zellen (100C), Silizium-Anode
    "Nanotech Energy":             ["lithium"],           # Graphen-verstärkte Li-Ion-Zellen, nicht-brennbarer Elektrolyt
    "Nuvvon Inc":                   ["lithium", "nickel"],  # Festkörper-Polymerelektrolyt + Lithium-Metall-Anode + Hochnickel-Kathode
    "Samsung SDI America Inc.":    ["lithium", "nmc"],      # NMC-Dreistoff-Li-Ion-Zellen (EV/ESS)
    "Solid Power Inc.":             ["lithium", "nmc"],      # Sulfid-Festelektrolyt + NMC-Kathode
    # Ergänzt 2026-08-06 beim Audit der 50 leeren Midstream-BGM material_keywords
    # (docs/open_issues.md P-Serie, Material-Dependency-Diskussion). Quelle jeweils
    # brief_profile in facilities_clean.csv, nicht externe Recherche.
    "Kureha":                       ["pvdf"],       # product_type explizit "PVDF Binder", product-Feld nur "Cell materials"
    "Arkema":                       ["pvdf"],       # brief_profile: "Fluoropolymer... materials" für Elektroden
    "Daikin America":               ["pvdf"],       # brief_profile: Fluorchemie explizit für Li-Ion-Batterien
    "Advanced Carbon Products":     ["graphite"],   # product_type "Anode Battery Grade Materials" + "carbon-based materials"
    "Borman Specialty Materials":   ["manganese"],  # brief_profile: "electrolytic manganese dioxide"
    "Birla Carbon":                 ["cnt"],         # brief_profile: "carbon black additives"
    "Cabot Corp.":                   ["cnt"],         # gleiche Firma wie "Cabot Corporation" (andere Schreibweise
                                                        # in NAATBatt), gleiches Kerngeschäft: "Conductive additives,
                                                        # fumed metal oxides, and aerogel" statt "carbon nanotube"
                                                        # im product-Feld, daher von den KEYWORD_RULES nicht erfasst
    "Sakuu Corp":                    ["lithium"],     # brief_profile: "Cypress Li-Metal Cell technology"
    "Medtronic":                     ["lithium"],     # brief_profile: "specialized li-ion batteries"
    "Innophos":                      ["lfp"],         # brief_profile: Phosphat-Vorstufen explizit für LFP/LMFP
    "Sun Chemicals":                 ["lfp"],         # brief_profile: Eisenoxid explizit als LFP-Feedstock
    "Sparkz Inc":                     ["lfp", "lithium"],  # brief_profile: "FeCAM... LFP (Lithium Iron Phosphate)"
    "BASF":                           ["electrolyte"], # product_type bereits "Electrolyte (liquid)"
    "Huntsman Petrochemical LLC":    ["electrolyte"], # product_type bereits "Electrolyte (liquid)"
    "Addionics ":                     ["copper_foil"],           # product-Feld "Copper", Current-Collector-Hersteller
    "Armor Battery Films":            ["copper_foil", "aluminum_foil"],  # "aluminum and copper foils"
    "CBC America":                    ["aluminum_foil"],         # "Aluminum laminated film"
    "Chang Chun Petrochemical Co., Ltd.": ["copper_foil"],       # brief_profile: "copper foil"
    "Denkai America":                 ["copper_foil"],           # brief_profile: "electrodeposited copper foils"
    "Gränges":                         ["aluminum_foil"],         # brief_profile: "aluminum current collectors for LIBs"
    "Lotte Aluminum Materials USA LLC": ["aluminum_foil"],        # brief_profile: "current collectors for ... LIBs"
    "Redwood Materials":               ["copper_foil"],           # brief_profile: "extracts ... copper" (Recycling)
    "Schlenk Metallfolien GmbH & Co. KG": ["copper_foil"],        # product: "Rolled copper and nickel anode foils"
                                                                    # (gefunden beim "anode"-Keyword-Audit, siehe unten)
}

# Diese Midstream-BGM/-Cell-Anlagen liefern nachweislich mechanische/sicherheitstechnische
# oder elektronische Komponenten (Gehäuse, Dichtungen, Klebstoffe, BMS-Module,
# Thermomanagement, Leistungselektronik, Testgeräte) oder sind reine
# Vertriebs-/Projektentwickler-Firmen — keine elektrochemisch aktiven Materialien,
# anhand von product_type/product/brief_profile einzeln geprüft (docs/data_processing.md
# §4.6). Ohne diese Markierung würde build_graph.py sie via "if not c_kws" fälschlich als
# Treffer für JEDE BGM-Materialsuche werten (Kanten zu jedem geografisch nahen BGM-Knoten,
# unabhängig vom Material). Der Marker-Keyword "non_active_material" taucht in keiner
# KEYWORD_RULES/BGM_TO_CELL-Zielmenge auf und bewirkt daher über die bestehende
# `c_kws & cell_targets`-Logik automatisch "kein Treffer", ohne build_graph.py selbst
# ändern zu müssen.
NON_MATERIAL_COMPONENT_COMPANIES = {
    "3M", "ADA Technologies, Inc.", "ArlanXEO", "Avery Dennison Corp",
    "Intriplex", "Vertical Partners West LLC",
    # Ergänzt 2026-08-06 (BGM-Audit):
    "Dukosi, Inc.",                                            # Integrated Circuits, Firmware — Elektronik, kein Material
    "Hyundai MOBIS North America Electrified Powertrain, LLC", # Leistungselektronik (PE Systems, ICCUs)
    "Battery Technology, Inc. (BTI)",                           # Distributor, kein Hersteller
    "Aypa Power",                                                # Utility-Scale-Speicher-Projektentwickler, kein Materialhersteller
    "MSE Supplies LLC",                                          # Laborausrüstung/Testgeräte-Anbieter
}


def classify_midstream(product_type: str) -> str:
    """将 Midstream 设施按 Product/Facility Type 分为 BGM 或 Cell。"""
    t = str(product_type).lower()
    if any(kw in t for kw in CELL_TYPE_KEYWORDS):
        return "Midstream-Cell"
    return "Midstream-BGM"


def extract_keywords(product_text: str) -> list[str]:
    text = str(product_text).lower()
    found = []
    for patterns, keyword in KEYWORD_RULES:
        if any(p in text for p in patterns) and keyword not in found:
            found.append(keyword)

    # Kathodenchemien implizieren ihre Rohstoffe, auch wenn der Produkttext die
    # Rohstoffe nicht separat nennt (siehe CATHODE_CHEMISTRY_RAW_MATERIALS oben)
    for chemistry, raw_materials in CATHODE_CHEMISTRY_RAW_MATERIALS.items():
        if chemistry in found:
            for raw in raw_materials:
                if raw not in found:
                    found.append(raw)

    # Verarbeitete Komponentenmaterialien implizieren ebenso ihre Rohstoffe
    # (siehe COMPONENT_RAW_MATERIALS oben)
    for component, raw_materials in COMPONENT_RAW_MATERIALS.items():
        if component in found:
            for raw in raw_materials:
                if raw not in found:
                    found.append(raw)

    return found


def infer_import_origin(facility_id: str, keywords: list[str]) -> dict[str, str]:
    """
    Weighted, per-facility, PER-MATERIAL draw from IMPORT_ORIGIN_DISTRIBUTION — returns
    {material: origin_country} for every keyword that has a real distribution, not just
    the first one found. Seeded by f"{facility_id}:{kw}" so each material's draw is
    independently reproducible across re-runs.

    Fixed 2026-08-06: the previous version returned on the FIRST matching keyword and
    stopped — for a multi-material facility (e.g. an NMC cathode plant needing
    cobalt+nickel+manganese, material_keywords ordered ["nmc","cobalt","nickel","manganese"]
    by extract_keywords()), this meant every material after the first (nickel, manganese)
    silently never got its own origin computed at all, and _region_match() in
    network_agent.py had no way to tell them apart anyway since it only received one
    string. A manganese-specific regional event could therefore never reach an NMC
    facility, even though it genuinely depends on manganese — the facility's stored
    origin was always cobalt's draw. Real-world NMC production genuinely sources
    different metals from different countries simultaneously, so the data model needs a
    distinct origin per material, not one string per facility.
    """
    origins: dict[str, str] = {}
    for kw in keywords:
        dist = IMPORT_ORIGIN_DISTRIBUTION.get(kw)
        if not dist:
            continue
        rng = random.Random(f"{facility_id}:{kw}")
        r = rng.random()
        cumulative = 0.0
        for region, share in dist:
            cumulative += share
            if r < cumulative:
                origins[kw] = region
                break
        else:
            origins[kw] = dist[-1][0]  # float-rounding fallback
    return origins



def build_computed_fields(df: pd.DataFrame) -> pd.DataFrame:
    # material_keywords
    df["material_keywords"] = df["product"].apply(
        lambda x: extract_keywords(str(x))
    )

    # product_type gezielt nur auf "LIB" (Lithium-Ion Battery) prüfen, nicht blanket
    # mit product zusammenführen: ein Blanket-Merge löst fälschlich die "anode"→graphite
    # Regel für product_type="Anode ..." aus, auch bei Silizium-/LTO-Anodenmaterialien,
    # die de facto kein Graphit sind (2026-07-31, 16 Facilities betroffen, u.a. Group14
    # Technologies/Sila Nanotechnologies — beide Silizium-Anodenhersteller, kein Graphit).
    # "LIB" ist dagegen ein eindeutiges, unmehrdeutiges Signal für Lithium.
    df["material_keywords"] = df.apply(
        lambda r: r["material_keywords"] + (
            ["lithium"] if "lib" in str(r["product_type"]).lower() and "lithium" not in r["material_keywords"] else []
        ),
        axis=1,
    )

    # Manuelle Chemie-Ergänzung für Zellhersteller ohne Chemie-Angabe im Produkttext
    # (siehe MATERIAL_KEYWORD_OVERRIDES oben) — inkl. NMC/NCA-Rohstoff-Ableitung, damit
    # z.B. "nmc" bei Samsung SDI auch cobalt/nickel/manganese nach sich zieht.
    def _apply_material_overrides(row: pd.Series) -> list[str]:
        kws = list(row["material_keywords"])
        for kw in MATERIAL_KEYWORD_OVERRIDES.get(row["company"], []):
            if kw not in kws:
                kws.append(kw)
        for chemistry, raw_materials in CATHODE_CHEMISTRY_RAW_MATERIALS.items():
            if chemistry in kws:
                for raw in raw_materials:
                    if raw not in kws:
                        kws.append(raw)
        for component, raw_materials in COMPONENT_RAW_MATERIALS.items():
            if component in kws:
                for raw in raw_materials:
                    if raw not in kws:
                        kws.append(raw)
        return kws

    df["material_keywords"] = df.apply(_apply_material_overrides, axis=1)

    # Komponentenlieferanten markieren (siehe NON_MATERIAL_COMPONENT_COMPANIES oben)
    df["material_keywords"] = df.apply(
        lambda r: ["non_active_material"]
        if r["company"] in NON_MATERIAL_COMPONENT_COMPANIES and not r["material_keywords"]
        else r["material_keywords"],
        axis=1,
    )

    # capacity_source
    df["capacity_source"] = df["production_capacity_raw"].apply(
        lambda x: "naatbatt" if pd.notna(x) and str(x).strip() not in ("", "nan") else "unknown"
    )

    # supplier_concentration: literaturbasierte Regel — globale Marktstruktur
    # cobalt (USGS MCS 2026): DRC 73% + Indonesia 14% = 87%; 2-3 Bergbaukonzerne dominieren
    # nmc/nca (IEA GCMO 2025): China ≥95% PCAM-Anteil; <5 Nicht-China-Großlieferanten weltweit
    df["supplier_concentration"] = df["material_keywords"].apply(
        lambda kws: any(kw in HIGH_CONCENTRATION_MATERIALS for kw in kws)
    )

    # import_dependency: Geopolitical Risk — USGS Net Import Reliance (MCS 2026)
    # Upstream-Anlagen sind selbst Produktionsstandorte → kein Import → immer False
    # Nicht-Upstream mit Schlüsselmaterial → True (NIR: Co=79%, Graphit=100%, Mn=100%, Li>50%, Ni≈100% primary)
    df["import_dependency"] = df.apply(
        lambda r: r["supply_chain_segment"] != "Upstream"
                  and any(kw in IMPORT_MATERIALS for kw in r["material_keywords"]),
        axis=1,
    )
    # import_origin_region (2026-08-03 fix, restructured 2026-08-06): previously
    # infer_import_origin() ran unconditionally for every row, including Upstream — which
    # produced nonsensical values like Albemarle's Silver Peak, NV lithium mine (itself the
    # production site) being randomly assigned "Australia" as its "import origin". Now:
    # Upstream facilities ARE the origin, so their own state IS the accurate origin region
    # for every material they handle — use it instead of a random foreign draw.
    # Non-Upstream facilities with import_dependency=False (e.g. copper, not in
    # IMPORT_MATERIALS) get {} — we don't model where they actually source from, so leaving
    # it empty is more honest than guessing either a foreign or domestic origin for them.
    #
    # 2026-08-06: changed from a single string to a dict {material: origin_country} — see
    # infer_import_origin()'s docstring for why a single string per facility silently
    # dropped every material past the first for any multi-material facility (e.g. NMC
    # cathode needing cobalt+nickel+manganese from three different real countries).
    # `state` is already normalized to a full name by this point (see the top-level
    # STATE_ABBREV_TO_FULL normalization above, applied to the whole column) — no need to
    # re-expand it here.
    df["import_origin_region"] = df.apply(
        lambda r: (
            {kw: r["state"] for kw in r["material_keywords"]}
            if r["supply_chain_segment"] == "Upstream"
            else infer_import_origin(str(r["facility_id"]), r["material_keywords"])
                 if r["import_dependency"] else {}
        ),
        axis=1,
    )

    # Apply facility-specific overrides (documented exceptions to the USGS NIR rule).
    # import_origin_region overrides are now per-material dicts too — a company's override
    # applies to every import-critical material it's tagged with (e.g. Sherritt handles
    # both cobalt and nickel, both actually sourced from Cuba/Madagascar per this override,
    # not the DRC/Indonesia draw the generic distribution would otherwise assign).
    for company, overrides in IMPORT_DEP_OVERRIDES.items():
        mask = df["company"] == company
        for col, val in overrides.items():
            if col == "import_origin_region" and val:
                df.loc[mask, col] = df.loc[mask, "material_keywords"].apply(
                    lambda kws: {kw: val for kw in kws if kw in IMPORT_MATERIALS}
                )
            elif col == "import_origin_region":
                df.loc[mask, col] = [dict() for _ in range(mask.sum())]
            else:
                df.loc[mask, col] = val

    # lead_time_weeks = base_by_segment + material_adjustment + region_adjustment
    #                   + capacity_data_penalty (see MATERIAL_LEAD_TIME_ADJUSTMENT /
    #                   _region_lead_time_adjustment above for rationale). Multi-material
    #                   facilities (e.g. NMC cathode -> cobalt+nickel+manganese keywords)
    #                   take the MAX adjustment among their materials, not the sum — same
    #                   "any() dominates" pattern as supplier_concentration above, so a
    #                   facility handling 3 materials isn't penalized 3x for that alone.
    def _lead_time_weeks(row: pd.Series) -> int:
        base = LEAD_TIME[row["supply_chain_segment"]]
        material_adj = max(
            (MATERIAL_LEAD_TIME_ADJUSTMENT.get(kw, 0) for kw in row["material_keywords"]),
            default=0,
        )
        # region_adj: import_origin_region is now {material: origin_country} (2026-08-06) —
        # take the MAX adjustment across all of this facility's per-material origins, same
        # "worst case among its materials" pattern as material_adj above, not just whichever
        # material happened to be drawn/listed first.
        region_adj = max(
            (_region_lead_time_adjustment(o) for o in row["import_origin_region"].values()),
            default=0,
        )
        capacity_penalty = 2 if row["capacity_source"] == "unknown" else 0
        return base + material_adj + region_adj + capacity_penalty

    df["lead_time_weeks"] = df.apply(_lead_time_weeks, axis=1)

    # material_keywords → 逗号分隔字符串
    df["material_keywords"] = df["material_keywords"].apply(lambda x: ",".join(x))

    # import_origin_region dict → JSON 字符串（CSV 列只能存字符串）
    df["import_origin_region"] = df["import_origin_region"].apply(json.dumps)

    return df


def main():
    print(f"读取: {NAATBATT_FILE.name}")
    raw = pd.read_excel(NAATBATT_FILE, sheet_name="Append2")
    print(f"  总行数: {len(raw)}")

    # 过滤：Commercial + 北美
    df = raw[raw["Status"].str.strip().str.lower() == "commercial"].copy()
    df = df[df["Facility Country"].str.strip().isin(NORTH_AMERICA)].copy()
    print(f"  Commercial + 北美: {len(df)} 行")

    # 过滤：只保留 Upstream / Midstream / Downstream
    df = df[df["Supply Chain Segment"].isin({"Upstream", "Midstream", "Downstream"})].copy()
    print(f"  相关层级: {len(df)} 行")

    # 列重命名
    col_map = {
        "ID":                         "facility_id",
        "Company":                    "company",
        "Facility Name":              "facility_name",
        "Product/Facility Type":      "product_type",
        "Product":                    "product",
        "Status":                     "status",
        "Facility City":              "city",
        "Facility State or Province": "state",
        "Facility Country":           "country",
        "Latitude":                   "latitude",
        "Longitude":                  "longitude",
        "HQ Country":                 "hq_country",
        "Facility Workforce":         "workforce",
        "Production Capacity":        "production_capacity_raw",
        "Production Units":           "production_units",
        "Brief Company Profile":      "brief_profile",
    }
    df = df.rename(columns=col_map)

    # Midstream → BGM / Cell
    def assign_segment(row):
        seg = row["Supply Chain Segment"]
        if seg == "Midstream":
            return classify_midstream(row["product_type"])
        return seg

    df["supply_chain_segment"] = df.apply(assign_segment, axis=1)

    # 只保留需要的列
    keep_cols = list(col_map.values()) + ["supply_chain_segment"]
    keep_cols = [c for c in keep_cols if c in df.columns]
    df = df[keep_cols].copy()

    # state 缩写 → 全称 (2026-08-07): 之前只在派生 import_origin_region 时临时展开一次
    # （Upstream 分支），但 state 字段本身在别处也被当文本比对用——例如
    # network_agent.py::_entity_match_seeds() 用 mentioned_location（LLM 输出的全称，如
    # "Kansas"）去匹配 state 字段做地点消歧，缩写("KS")对全称("Kansas")永远匹配不上，
    # 是同一类 bug，只是还没被现有测试用例踩到（测试里 mentioned_location 恰好总是给了
    # city，掩盖了 state 匹配失败）。改到源头一次性解决，比在每个用到 state 的地方各自
    # 展开一遍更不容易漏。同时用 .strip() 顺手清掉像 "CA " 这种脏尾随空格。
    df["state"] = df["state"].apply(
        lambda s: STATE_ABBREV_TO_FULL.get(str(s).strip(), str(s).strip()) if pd.notna(s) else s
    )

    # 计算派生字段
    df = build_computed_fields(df)

    # Midstream-Cell 单位标准化：MWh/yr → GWh/yr (÷1000)
    cell_mwh = (df["supply_chain_segment"] == "Midstream-Cell") & (df["production_units"] == "MWh/yr")
    if cell_mwh.any():
        df.loc[cell_mwh, "production_capacity_raw"] = (
            pd.to_numeric(df.loc[cell_mwh, "production_capacity_raw"], errors="coerce") / 1000
        )
        df.loc[cell_mwh, "production_units"] = "GWh/yr"
        print(f"  Midstream-Cell MWh→GWh 转换: {cell_mwh.sum()} 条")

    # Midstream-Cell 产能不可比设施排除：
    # Schlenk (Cell components / MT/yr): 铝箔产商，非电芯，MT/yr 与 GWh/yr 不可比
    # Nuvvon (Ah): 无法转换，且为实验室量级
    cell_incompatible = (
        (df["supply_chain_segment"] == "Midstream-Cell") &
        (df["production_units"].isin(["MT/yr", "Ah"]))
    )
    if cell_incompatible.any():
        df.loc[cell_incompatible, "capacity_source"] = "unknown"
        print(f"  Midstream-Cell 单位不可比排除: {cell_incompatible.sum()} 条 "
              f"({list(df.loc[cell_incompatible, 'company'])})")

    # 去除缺坐标的行
    before = len(df)
    df = df.dropna(subset=["latitude", "longitude"])
    print(f"  经纬度过滤: {before} → {len(df)} 行")

    df.to_csv(OUTPUT_FILE, index=False)

    print(f"\n已保存: {OUTPUT_FILE}")
    print(f"总设施数: {len(df)}")
    print(f"\n按层级分布:")
    print(df["supply_chain_segment"].value_counts().to_string())
    print(f"\nsupplier_concentration=True: {df['supplier_concentration'].sum()}")
    print(f"import_dependency=True:  {df['import_dependency'].sum()}")
    print(f"capacity_source=naatbatt:   {(df['capacity_source']=='naatbatt').sum()}")
    print(f"capacity_source=unknown:    {(df['capacity_source']=='unknown').sum()}")

    # Midstream 拆分验证
    print(f"\nMidstream 拆分结果:")
    mid_bgm = df[df["supply_chain_segment"] == "Midstream-BGM"]
    mid_cell = df[df["supply_chain_segment"] == "Midstream-Cell"]
    print(f"  BGM:  {len(mid_bgm)}")
    print(f"  Cell: {len(mid_cell)}")
    print(f"\n  BGM product_type 样例:")
    for t in mid_bgm["product_type"].value_counts().head(5).index:
        print(f"    {t}")
    print(f"\n  Cell product_type 样例:")
    for t in mid_cell["product_type"].value_counts().head(5).index:
        print(f"    {t}")


if __name__ == "__main__":
    main()
