"""
从 NAATBatt 数据库提取并清洗设施数据，生成 facilities_clean.csv。
只保留 Commercial 设施，计算 material_keywords / supplier_concentration / capacity_source。

数据源: Append2 sheet (单表，包含所有层级)
v2 (2026-06-10): 适配 March 2026 版本，Midstream 按 Product/Facility Type 拆分为 BGM/Cell
"""

import random
import re
import pandas as pd
from pathlib import Path

NAATBATT_FILE = Path(__file__).parent.parent / "data" / "naatbatt-database-31march2026.xlsx"
OUTPUT_FILE   = Path(__file__).parent.parent / "data" / "facilities_clean.csv"

NORTH_AMERICA = {"US", "USA", "CA", "Canada", "MX", "Mexico"}

LEAD_TIME = {
    "Upstream":       12,
    "Midstream-BGM":  8,
    "Midstream-Cell": 6,
    "Downstream":     4,
}

IMPORT_MATERIALS = {"cobalt", "nickel", "lithium", "manganese", "graphite"}

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
        ("Africa (DRC)",            0.73),  # USGS MCS 2026: DRC 73% of world mine production
        ("Asia (Indonesia)",        0.14),  # USGS MCS 2026: Indonesia 14%
        ("Other / Diversified",     0.13),
    ],
    "nickel": [
        ("Asia (Indonesia)",              0.67),  # USGS MCS 2026: Indonesia ~67%
        ("Asia / Pacific (Philippines)",  0.18),
        ("Other / Diversified",           0.15),
    ],
    "lithium": [
        ("South America (Chile/Argentina)", 0.45),
        ("Australia",                        0.35),
        ("Other / Diversified",              0.20),
    ],
    "manganese": [
        ("Africa (South Africa, Gabon)",  0.55),
        ("Asia / Pacific (Australia)",    0.25),
        ("Other / Diversified",           0.20),
    ],
    "graphite": [
        ("Asia (China)",                                0.77),  # USGS MCS 2026: China ~77%
        ("Africa (Mozambique, Madagascar, Tanzania)",   0.15),
        ("Other / Diversified",                          0.08),
    ],
}

# SupplierConcentration: literaturbasierte Regel — globale Marktstruktur (nicht Nordamerika-Zählung)
# cobalt: DRC 73% mine share, 2-3 major miners (USGS MCS 2026, Feb 2026)
# nmc/nca: China ≥95% PCAM share (IEA Global Critical Minerals Outlook 2025)
HIGH_CONCENTRATION_MATERIALS = {"cobalt", "nmc", "nca"}

# Facility-level import_dependency overrides (cobalt)
# Basis: USGS NIR (79%) is a sector aggregate, not facility-level.
# The following facilities are known to source cobalt outside the DRC supply chain:
#   - Nth Cycle: secondary cobalt from battery recycling (no virgin DRC cobalt)
#   - Sherritt International: Moa Bay (Cuba) + Ambatovy (Madagascar) JV; no DRC exposure
#   - Boleo Copper Project (BGM): downstream processing of own Mexican mine output
# Documented in docs/data_processing.md §4.4
IMPORT_DEP_OVERRIDES: dict[str, dict] = {
    "Nth Cycle":              {"import_dependency": False, "import_origin_region": ""},
    "Sherritt International": {"import_dependency": False, "import_origin_region": "Caribbean / Africa (non-DRC)"},
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
    (["graphite", "anode"],                "graphite"),
    (["electrolyte"],                      "electrolyte"),
    (["separator"],                        "separator"),
    (["lead acid", "lead-acid"],           "lead_acid"),
]

# Product/Facility Type 中包含这些词 → Midstream-Cell
CELL_TYPE_KEYWORDS = [
    "cell", "pouch", "cylindrical", "prismatic", "lib manuf", "cell manuf",
    "cell assembly", "consumer batter",
]

# NMC/NCA sind Kathodenchemien (Verbundprodukte), keine Rohstoffe — enthalten aber
# die genannten Rohstoffe. Ohne diese Zuordnung würde ein Produkttext wie "NMC
# Cathode Active Material" nur material_keywords=["nmc"] ergeben, ohne "cobalt" —
# ein Kobalt-Ereignis würde diese Anlage dann über _material_match() nicht finden,
# obwohl NMC-Kathodenmaterial de facto Kobalt enthält (docs/architecture.md
# "Bekannte Grenzen des Seeding-Mechanismus" Punkt 4). Aluminium wird in diesem
# System nicht als Risikomaterial geführt, daher kein "aluminum" für NCA.
CATHODE_CHEMISTRY_RAW_MATERIALS: dict[str, list[str]] = {
    "nmc": ["cobalt", "nickel", "manganese"],
    "nca": ["cobalt", "nickel"],
}

# Manuelle Ergänzung für Midstream-Cell-Hersteller, deren `product`/`product_type`
# nur generische Begriffe enthalten ("Other/Unknown", "Cell", "Solid-state battery" o.ä.)
# ohne Chemie zu nennen — anders als bei den literaturbasierten Regeln (USGS/IEA, siehe
# oben) beruht dies auf öffentlich zugänglichen Firmen-/Produktinformationen, nicht auf
# einer standardisierten Statistikquelle, und ist daher im Sinn von Baris' "reale Daten /
# simulierte Annahmen / Modellvereinfachungen"-Dreiteilung als Modellvereinfachung mit
# manueller Recherche zu kennzeichnen (siehe docs/data_processing.md §4.6). Nur Firmen
# aufgenommen, deren Kernchemie öffentlich eindeutig dokumentiert ist; Stryten Energy
# (führt sowohl Blei-Säure- als auch Lithium-Produktlinien) bewusst NICHT aufgenommen,
# da für genau diese Anlage (Ottawa Transportation Manufacturing Plant) keine
# Chemie-spezifische Quelle gefunden wurde — Rätselraten hier wäre nicht vertretbar.
MATERIAL_KEYWORD_OVERRIDES: dict[str, list[str]] = {
    "ABSL Power Solutions, Inc.": ["lithium"],           # EnerSys/ABSL Li-ion Zellen für Raumfahrt/Verteidigung
    "American Lithium Energy":    ["lithium"],           # Hochsicherheits-Li-Ion-Zellen (100C), Silizium-Anode
    "Nanotech Energy":             ["lithium"],           # Graphen-verstärkte Li-Ion-Zellen, nicht-brennbarer Elektrolyt
    "Nuvvon Inc":                   ["lithium", "nickel"],  # Festkörper-Polymerelektrolyt + Lithium-Metall-Anode + Hochnickel-Kathode
    "Samsung SDI America Inc.":    ["lithium", "nmc"],      # NMC-Dreistoff-Li-Ion-Zellen (EV/ESS)
    "Solid Power Inc.":             ["lithium", "nmc"],      # Sulfid-Festelektrolyt + NMC-Kathode
}

# Diese Midstream-Cell-Anlagen liefern nachweislich mechanische/sicherheitstechnische
# Komponenten (Gehäuse, Dichtungen, Klebstoffe, BMS-Module, Thermomanagement), keine
# elektrochemisch aktiven Materialien — anhand von product_type/product einzeln geprüft
# (docs/data_processing.md §4.6). Ohne diese Markierung würde build_graph.py sie via
# "if not c_kws" fälschlich als Treffer für JEDE BGM-Materialsuche werten (Kanten zu
# jedem geografisch nahen BGM-Knoten, unabhängig vom Material). Der Marker-Keyword
# "non_active_material" taucht in keiner KEYWORD_RULES/BGM_TO_CELL-Zielmenge auf und
# bewirkt daher über die bestehende `c_kws & cell_targets`-Logik automatisch "kein
# Treffer", ohne build_graph.py selbst ändern zu müssen.
NON_MATERIAL_COMPONENT_COMPANIES = {
    "3M", "ADA Technologies, Inc.", "ArlanXEO", "Avery Dennison Corp",
    "Intriplex", "Vertical Partners West LLC",
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

    return found


def infer_import_origin(facility_id: str, keywords: list[str]) -> str:
    """
    Weighted, per-facility draw from IMPORT_ORIGIN_DISTRIBUTION — not every facility
    handling a material gets the single dominant source country; seeded by facility_id
    so the assignment is reproducible across re-runs of this script.
    """
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
                return region
        return dist[-1][0]  # float-rounding fallback
    return ""



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
    df["import_origin_region"] = df.apply(
        lambda r: infer_import_origin(str(r["facility_id"]), r["material_keywords"]), axis=1
    )

    # Apply facility-specific overrides (documented exceptions to the USGS NIR rule)
    for company, overrides in IMPORT_DEP_OVERRIDES.items():
        mask = df["company"] == company
        for col, val in overrides.items():
            df.loc[mask, col] = val

    # lead_time_weeks
    df["lead_time_weeks"] = df["supply_chain_segment"].map(LEAD_TIME)

    # material_keywords → 逗号分隔字符串
    df["material_keywords"] = df["material_keywords"].apply(lambda x: ",".join(x))

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
