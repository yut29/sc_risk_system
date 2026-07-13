"""
从 NAATBatt 数据库提取并清洗设施数据，生成 facilities_clean.csv。
只保留 Commercial 设施，计算 material_keywords / supplier_concentration / capacity_source。

数据源: Append2 sheet (单表，包含所有层级)
v2 (2026-06-10): 适配 March 2026 版本，Midstream 按 Product/Facility Type 拆分为 BGM/Cell
"""

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

IMPORT_ORIGIN = {
    "cobalt":    "Africa (DRC)",
    "nickel":    "Asia / Pacific",
    "lithium":   "South America / Australia",
    "manganese": "Africa / Asia",
    "graphite":  "Asia (China)",
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
    return found


def infer_import_origin(keywords: list[str]) -> str:
    for kw in keywords:
        if kw in IMPORT_ORIGIN:
            return IMPORT_ORIGIN[kw]
    return ""



def build_computed_fields(df: pd.DataFrame) -> pd.DataFrame:
    # material_keywords
    df["material_keywords"] = df["product"].apply(
        lambda x: extract_keywords(str(x))
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
    df["import_origin_region"] = df["material_keywords"].apply(infer_import_origin)

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
