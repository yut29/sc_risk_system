"""
基于 facilities_clean.csv 构建供应链知识图谱，输出 knowledge_graph.json。

边生成逻辑：
  Upstream → Midstream-BGM  : 共享原材料关键词（cobalt→nmc/nca, lithium→lfp/electrolyte, ...）
  Midstream-BGM → Midstream-Cell : 关键词匹配 + 地理距离排序，每节点最多 K_BGM_CELL 条边
  Midstream-Cell → Downstream : 地理距离最近 K_NEAR 条 + 随机远距离 K_FAR 条（模拟跨区域供应）

改动说明（v2, 2026-06-10）：
  旧版 Cell→Downstream 按国家全连接，导致 81% 连通率（6134/7612条边），
  任何 Upstream 事件均可传播到 166/173 个 Downstream 节点，图结构失去区分度。
  新版每个 Cell 节点最多连接 K_NEAR+K_FAR=8 个 Downstream，连通率从 81% 降至约 13%。
"""

import json
import math
import random
from typing import Optional
import pandas as pd
import networkx as nx
from pathlib import Path
from datetime import date

FACILITIES_FILE = Path(__file__).parent.parent / "data" / "facilities_clean.csv"
OUTPUT_FILE     = Path(__file__).parent.parent / "data" / "knowledge_graph.json"

# ── 连边参数 ──────────────────────────────────────────────────────────────────
K_NEAR      = 5   # Cell→Down: 地理最近的 N 个 Downstream 节点
K_FAR       = 3   # Cell→Down: 额外随机抽取 N 个（模拟跨区域长链供应）
K_BGM_CELL  = 6   # BGM→Cell: 关键词匹配后按距离取最近 N 个

NORTH_AMERICA_COUNTRIES = {"US", "CA", "Canada", "MX"}

# ── 关键词映射表 ──────────────────────────────────────────────────────────────
UPSTREAM_TO_BGM: dict[str, list[str]] = {
    "cobalt":    ["cobalt", "nmc", "nca"],
    "lithium":   ["lithium", "nmc", "nca", "lfp", "electrolyte"],
    "nickel":    ["nickel", "nmc", "nca"],
    "manganese": ["manganese", "nmc"],
    "graphite":  ["graphite"],
}

BGM_TO_CELL: dict[str, list[str]] = {
    "nmc":         ["nmc"],
    "nca":         ["nca"],
    "lfp":         ["lfp"],
    "cobalt":      ["nmc", "nca"],
    "lithium":     ["nmc", "nca", "lfp"],
    "graphite":    ["nmc", "nca", "lfp", "graphite"],
    "electrolyte": ["nmc", "nca", "lfp"],
    "separator":   ["nmc", "nca", "lfp"],
}



# ── 工具函数 ──────────────────────────────────────────────────────────────────

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:  # type: ignore[return]
    """大圆距离（千米）。"""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _to_float(val) -> Optional[float]:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def parse_keywords(kw_str: str) -> set[str]:
    if pd.isna(kw_str) or str(kw_str).strip() == "":
        return set()
    return {k.strip() for k in str(kw_str).split(",") if k.strip()}


def _geo_sort(
    src_lat: Optional[float],
    src_lon: Optional[float],
    candidates: list,
) -> list:
    """按距离排序 candidates，缺坐标的节点排到末尾。"""
    result = []
    for row in candidates:
        lat = _to_float(row.get("latitude"))
        lon = _to_float(row.get("longitude"))
        if src_lat is not None and src_lon is not None and lat is not None and lon is not None:
            dist = haversine_km(src_lat, src_lon, lat, lon)
        else:
            dist = float("inf")
        result.append((dist, row))
    return sorted(result, key=lambda x: x[0])


# ── 图构建 ────────────────────────────────────────────────────────────────────

def build_graph(df: pd.DataFrame):
    G = nx.DiGraph()

    # 添加节点
    for _, row in df.iterrows():
        G.add_node(
            str(row["facility_id"]),
            company=str(row.get("company", "")),
            facility_name=str(row.get("facility_name", "")),
            segment=str(row.get("supply_chain_segment", "")),
            product_type=str(row.get("product_type", "")),
            material_keywords=str(row.get("material_keywords", "")),
            country=str(row.get("country", "")),
            state=str(row.get("state", "")),
            city=str(row.get("city", "")),
            latitude=_to_float(row.get("latitude")),
            longitude=_to_float(row.get("longitude")),
            production_capacity_raw=str(row.get("production_capacity_raw", "")),
            production_units=str(row.get("production_units", "")),
            capacity_source=str(row.get("capacity_source", "")),
            supplier_concentration=bool(row.get("supplier_concentration", False)),
            import_dependency=bool(row.get("import_dependency", False)),
            import_origin_region=str(row.get("import_origin_region", "")),
            lead_time_weeks=int(row.get("lead_time_weeks", 0)),
        )

    up   = df[df["supply_chain_segment"] == "Upstream"]
    bgm  = df[df["supply_chain_segment"] == "Midstream-BGM"]
    cell = df[df["supply_chain_segment"] == "Midstream-Cell"]
    down = df[df["supply_chain_segment"] == "Downstream"]

    edge_count = {"up_bgm": 0, "bgm_cell": 0, "cell_down": 0}

    # ── Upstream → Midstream-BGM（关键词匹配，逻辑不变）────────────────────
    for _, u_row in up.iterrows():
        u_kws = parse_keywords(u_row["material_keywords"])
        bgm_targets: set[str] = set()
        for uk in u_kws:
            bgm_targets.update(UPSTREAM_TO_BGM.get(uk, []))

        for _, b_row in bgm.iterrows():
            b_kws = parse_keywords(b_row["material_keywords"])
            if b_kws & bgm_targets:
                shared = list(u_kws & set(UPSTREAM_TO_BGM.keys()))
                G.add_edge(
                    str(u_row["facility_id"]),
                    str(b_row["facility_id"]),
                    relationship="supplies_material",
                    material=shared[0] if shared else "unknown",
                )
                edge_count["up_bgm"] += 1

    # ── Midstream-BGM → Midstream-Cell（关键词匹配 + 地理最近 K_BGM_CELL）──
    for _, b_row in bgm.iterrows():
        b_kws = parse_keywords(b_row["material_keywords"])
        cell_targets: set[str] = set()
        for bk in b_kws:
            cell_targets.update(BGM_TO_CELL.get(bk, []))

        # 关键词匹配的候选 Cell
        matched: list[pd.Series] = []
        for _, c_row in cell.iterrows():
            c_kws = parse_keywords(c_row["material_keywords"])
            if not c_kws or (c_kws & cell_targets):
                matched.append(c_row)

        # 按距离排序，取最近 K_BGM_CELL 个
        b_lat = _to_float(b_row.get("latitude"))
        b_lon = _to_float(b_row.get("longitude"))
        sorted_cell = _geo_sort(b_lat, b_lon, matched)
        selected = sorted_cell[:K_BGM_CELL]

        for _, c_row in selected:
            b_kws_list = list(b_kws & set(BGM_TO_CELL.keys()))
            G.add_edge(
                str(b_row["facility_id"]),
                str(c_row["facility_id"]),
                relationship="supplies_material",
                material=b_kws_list[0] if b_kws_list else "processed_material",
            )
            edge_count["bgm_cell"] += 1

    # ── Midstream-Cell → Downstream（地理最近 K_NEAR + 随机远距离 K_FAR）──
    na_down = down[down["country"].str.strip().isin(NORTH_AMERICA_COUNTRIES)]

    for _, c_row in cell.iterrows():
        c_lat = _to_float(c_row.get("latitude"))
        c_lon = _to_float(c_row.get("longitude"))

        sorted_down = _geo_sort(c_lat, c_lon, [r for _, r in na_down.iterrows()])

        # K_NEAR 个地理最近节点
        near = sorted_down[:K_NEAR]

        # K_FAR 个随机（用 facility_id 作种子保证可复现）
        rest = sorted_down[K_NEAR:]
        rng = random.Random(int(c_row["facility_id"]))
        far = rng.sample(rest, min(K_FAR, len(rest)))

        for _, d_row in near + far:
            G.add_edge(
                str(c_row["facility_id"]),
                str(d_row["facility_id"]),
                relationship="supplies_cells",
                material="battery_cells",
            )
            edge_count["cell_down"] += 1

    return G, edge_count


def graph_to_json(G: nx.DiGraph) -> dict:
    nodes = [{"id": nid, **attrs} for nid, attrs in G.nodes(data=True)]
    edges = [{"source": s, "target": t, **attrs} for s, t, attrs in G.edges(data=True)]

    return {
        "metadata": {
            "total_nodes": G.number_of_nodes(),
            "total_edges": G.number_of_edges(),
            "created_at": str(date.today()),
            "edge_params": {"K_NEAR": K_NEAR, "K_FAR": K_FAR, "K_BGM_CELL": K_BGM_CELL},
            "edge_source": "All edges simulated: keyword matching + geographic proximity. NAATBatt contains no real supplier links.",
            "segments": ["Upstream", "Midstream-BGM", "Midstream-Cell", "Downstream"],
        },
        "nodes": nodes,
        "edges": edges,
    }


def main():
    df = pd.read_csv(FACILITIES_FILE)
    print(f"读取设施: {len(df)} 条")

    G, edge_count = build_graph(df)

    total = G.number_of_edges()
    cell_down_max = (
        sum(1 for n in G.nodes if G.nodes[n].get("segment") == "Midstream-Cell")
        * sum(1 for n in G.nodes if G.nodes[n].get("segment") == "Downstream")
    )
    print(f"\n图统计:")
    print(f"  节点数:              {G.number_of_nodes()}")
    print(f"  总边数:              {total}")
    print(f"  Upstream→BGM:        {edge_count['up_bgm']}")
    print(f"  BGM→Cell:            {edge_count['bgm_cell']}")
    print(f"  Cell→Downstream:     {edge_count['cell_down']}  "
          f"(连通率 {edge_count['cell_down']/cell_down_max:.0%}, 旧版 81%)")

    # 场景可达性验证
    print("\n场景可达性 (BFS):")
    from collections import deque, defaultdict
    adj = defaultdict(list)
    for s, t in G.edges():
        adj[s].append(t)
    nodes_data = dict(G.nodes(data=True))
    total_down = sum(1 for n, d in nodes_data.items() if d.get("segment") == "Downstream")

    def bfs_down(start_ids: list[str]) -> int:
        visited = set(start_ids)
        q = deque(start_ids)
        reached = set()
        while q:
            cur = q.popleft()
            for nxt in adj[cur]:
                if nodes_data[nxt].get("segment") == "Downstream":
                    reached.add(nxt)
                if nxt not in visited:
                    visited.add(nxt)
                    q.append(nxt)
        return len(reached)

    for mat in ["cobalt", "lithium", "graphite"]:
        up_ids = [n for n, d in nodes_data.items()
                  if d.get("segment") == "Upstream" and mat in d.get("material_keywords", "")]
        n_down = bfs_down(up_ids)
        print(f"  {mat}: {len(up_ids)} upstream → {n_down}/{total_down} Downstream 可达")

    data = graph_to_json(G)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n已保存: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
