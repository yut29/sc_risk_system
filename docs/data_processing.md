# 数据处理说明 — SC Risk System

> 更新：2026-07-05  
> 数据来源：NAATBatt / NREL North American Lithium-Ion Battery Supply Chain Database (March 2026)

---

## 1. 数据来源

| 项目 | 内容 |
|------|------|
| 名称 | NAATBatt North American Li-Ion Battery Supply Chain Database |
| 发布方 | NREL（美国国家可再生能源实验室）+ NAATBatt International |
| 版本 | 2026年3月31日（本项目使用） |
| 获取方式 | 免费注册下载（Excel格式） |
| 覆盖范围 | 北美（美国、加拿大、墨西哥）电池供应链设施目录 |
| 原始行数 | 1280 条设施记录 |
| 重要说明 | **数据库不含真实供应商关系**，仅为设施目录；供应关系为模拟生成 |

---

## 2. Datenklassifikation — Drei Datentypen

> Hinweis Baris Albayrak (E-Mail 2026-06-30): „Die finale Version sollte noch stärker zwischen **realen Daten, simulierten Annahmen und Modellvereinfachungen** unterscheiden."

本项目所有数据字段按以下三类归类，在论文中明确区分各类的来源与局限性：

| 类别 | 德语术语 | 含义 | 涉及字段 |
|------|---------|------|---------|
| **① 实际数据** | *Reale Daten* | 直接来自 NAATBatt 数据库，未经推断或修改 | 设施基本信息（公司名、地址、层级、产品类型等）、`production_capacity_raw`（有原始值的设施）、`production_units`、`latitude/longitude` |
| **② 模拟假设** | *Simulierte Annahmen* | 真实数据缺失，用可追溯方法近似替代——存在数据基础，但需方法说明 | 图谱边（按材料关键词 + 地理距离规则生成，替代不存在的合同关系数据；NAATBatt 不含真实供应关系）|
| **③ 模型简化** | *Modellvereinfachungen* | 主动设计选择：有意识地用简化规则代替精确数据，方法本身即假设 | `lead_time_weeks`（按层级固定值，非实测）；`import_dependency`（按材料+层级的文献阈值规则，非供应商级别实测）；`supplier_concentration`（按文献阈值的二元分类）；`import_origin_region`（按材料查表，非设施级别核查）；`material_keywords`（规则关键词提取）|

**三类的关键区别：**
- *模拟假设* = 数据理论上存在（如 Upstream 确有产能），只是我们拿不到，因此用统计方法填补；或关系理论上存在（如 BGM→Cell 供应关系），只是没有公开合同数据，因此按地理距离模拟。
- *模型简化* = 不是数据缺失问题，而是主动决定"不使用精确数据"或"精确数据不可得时用规则替代"——这是建模选择，需在论文局限性章节中明确说明。

**实际数据占比：**

| 字段 | 类型 | 覆盖率 |
|------|------|--------|
| 设施基本信息 | ① 实际数据 | 100%（386条） |
| `production_capacity_raw` | ① 实际数据 / 缺失 | 37% naatbatt + 63% unknown |
| 图谱边 | ② 模拟假设 | 100% simulated（关键词匹配 + 地理距离，NAATBatt 无真实供应关系数据） |
| 风险指标字段 | ③ 模型简化 | 100%（规则覆盖全部386条） |

---

## 3. 处理流程总览

```
naatbatt-database-31march2026.xlsx
            │
            ▼  data_prep/build_facilities.py
    facilities_clean.csv  (386 条设施)
            │
            ▼  data_prep/build_graph.py
    knowledge_graph.json  (386 节点, 1537 条边)
```
---

## 4. 第一步：设施清洗（build_facilities.py）

### 4.1 过滤规则

原始 1280 条记录经三层过滤，保留 386 条：

| 过滤条件 | 保留 | 排除 |
|----------|------|------|
| `Status == "Commercial"` | 856 | 424（Planned / Under Construction / Cancelled 等）|
| 地理范围：US / CA / MX | 388 | 468（超出北美范围）|
| 供应链层级：Upstream / Midstream / Downstream | 386 | 2（缺经纬度，删除）|

排除"Other - Research / Equipment / Service"等非生产性层级，因其与风险传播逻辑无关。

### 4.2 Midstream 层级拆分

原始数据仅有 `Midstream` 一个标签（397条），本项目需区分材料加工（BGM）与电芯制造（Cell）两个层级，以支持图中的分层传播逻辑。

拆分依据 `Product/Facility Type` 字段关键词：

| 判断为 Midstream-Cell 的关键词 | 示例 |
|-------------------------------|------|
| cell, pouch, cylindrical, prismatic, LIB manuf, cell assembly | "Cylindrical cells", "Prismatic pouch cells", "Cell Manufacturing" |

不含上述关键词的 Midstream 记录归入 Midstream-BGM（电池级材料）。

### 4.3 派生字段计算

原始数据不含以下字段，均为本项目按规则计算/推断生成：

| 字段 | 类型 | 计算逻辑 |
|------|------|---------|
| `material_keywords` | 规则提取 | 从 `Product` 字段提取关键词（cobalt / lithium / nmc / graphite 等） |
| `capacity_source` | 数据质量标记 | 原始产能值存在 → `naatbatt`；缺失 → `unknown` |
| `supplier_concentration` | 文献阈值规则 | 全球市场结构风险：材料属高集中度类别 → `True`（见 3.4 节） |
| `import_dependency` | 文献阈值规则 | 地缘政治风险：Segment ≠ Upstream 且材料属北美净进口依赖类别 → `True`（见 3.4 节） |
| `import_origin_region` | 规则推断 | 按材料类型推断主要来源地（如 cobalt → "Africa (DRC)"） |
| `lead_time_weeks` | 规则固定值 | 按层级：Upstream=12, BGM=8, Cell=6, Downstream=4 |

### 4.4 风险指标文献依据

`supplier_concentration` 和 `import_dependency` 均基于公开权威文献，以下为具体赋值依据：

#### ImportDep — USGS Net Import Reliance (MCS 2026)

衡量：北美是否净进口依赖该材料（地缘政治风险维度）。  
**Upstream 设施本身是原材料产地，不从海外进口 → 一律 `False`。**  
非 Upstream 设施中，以下材料 `True`：

| 材料 | USGS NIR (2025e) | 主要进口来源 |
|------|-----------------|-------------|
| cobalt | 79% | Norway 26%, Finland 16%, Canada 14%, Japan 14% |
| graphite | 100% | China 46%, Canada 13%, Mozambique 13%, Mexico 12% |
| manganese | 100% | Gabon 23%, South Africa 21%, Malaysia 11%, Australia 10% |
| lithium | >50% | Chile 54%, Argentina 43% |
| nickel | ≈100%（primary，不含废料）| Canada 44%, Norway 11%, Australia 8%, Brazil 7% |
| nmc / nca | — | 同 cobalt/nickel/lithium 原料 |

来源：U.S. Geological Survey, *Mineral Commodity Summaries 2026*, February 2026  
[cobalt](https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-cobalt.pdf) · [graphite](https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-graphite.pdf) · [nickel](https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-nickel.pdf) · [lithium](https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-lithium.pdf) · [manganese](https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-manganese.pdf)

#### SupplierConcentration — USGS MCS 2026 + IEA GCMO 2025

衡量：全球商业供应商数量是否高度集中（市场结构风险维度）。  
采用基于文献阈值的规则，仅 **cobalt** 和 **nmc/nca** 赋值 `True`：

| 材料 | SupplierConcentration | 文献依据 |
|------|----------------------|---------|
| cobalt | **True** | DRC 73% + Indonesia 14% = 87% 全球矿产量；CMOC（Kisanfu ≈全球25%）、Glencore（KCC + Mutanda）主导；市场高度集中 — USGS MCS 2026 |
| nmc / nca | **True** | 中国占全球 PCAM（前驱体阴极材料）份额 **≥95%**；非中国主要供应商 <5 家（L&F、POSCO FM、Umicore、BASF、Sumitomo）— IEA Global Critical Minerals Outlook 2025 |
| graphite | False | 中国 82% 矿产量，但中国内部供应商众多（数百家矿企）；另有 Tanzania、Mozambique、Brazil、Russia、India 等 15+ 生产国 — USGS MCS 2026 |
| lithium | False | 全球 10+ 主要矿企（Albemarle、SQM、Pilbara、Ganfeng、Tianqi 等）；IEA: 前3国份额 <70%（持续分散） |
| nickel | False | Indonesia 67%，但多公司；全球产能过剩自 2022（surplus 182k MT in 2024）— USGS MCS 2026 |
| manganese | False | South Africa 38%、Gabon 25%、Ghana 10%、Australia 8%；多国多公司 — USGS MCS 2026 |

来源：  
- U.S. Geological Survey, *Mineral Commodity Summaries 2026*, February 2026  
- IEA, *Global Critical Minerals Outlook 2025*, 2025 ([executive summary](https://www.iea.org/reports/global-critical-minerals-outlook-2025/executive-summary)); commentary: *With new export controls on critical minerals, supply concentration risks become reality* ([link](https://www.iea.org/commentaries/with-new-export-controls-on-critical-minerals-supply-concentration-risks-become-reality))

---

## 5. 第二步：知识图谱构建（build_graph.py）

### 5.1 节点

每条 `facilities_clean.csv` 记录对应一个图节点，携带所有清洗后字段。

### 5.2 模拟边生成逻辑

NAATBatt 不含真实供应关系，全部边为模拟生成，分三层：

**层一：Upstream → Midstream-BGM**（421条）

基于原材料关键词匹配：若 Upstream 节点的材料（如 `cobalt`）在 BGM 节点的材料中有对应下游用途（如 `nmc`, `nca`），则连边。

```
cobalt  → cobalt, nmc, nca
lithium → lithium, nmc, nca, lfp, electrolyte
nickel  → nickel, nmc, nca
graphite→ graphite
```

**层二：Midstream-BGM → Midstream-Cell**（684条）

关键词匹配 + 地理距离排序，每个 BGM 节点连接关键词匹配的最近 **K=6** 个 Cell 节点。

**层三：Midstream-Cell → Downstream**（432条）

纯地理距离：每个 Cell 节点连接最近 **5个**（K_NEAR）+ 随机远距离 **3个**（K_FAR，模拟跨区域供应）Downstream 节点。随机采样使用 facility_id 作种子，保证结果可复现。


---

## 6. 前后对比

### 6.1 设施数量（旧版 Sep 2025 → 新版 Mar 2026）

| 层级 | 旧版 | 新版 | 变化 |
|------|------|------|------|
| Upstream | 26 | 29 | +3 |
| Midstream-BGM | 42 | 114 | +72 |
| Midstream-Cell | 44 | 54 | +10 |
| Downstream | 173 | 189 | +16 |
| **合计** | **285** | **386** | **+101** |

Midstream-BGM 增幅最大（+72），主要来自新增的电解液、分离膜、阳极材料等细分设施。

### 6.2 知识图谱连边密度

| 指标 | 旧版 | 新版 |
|------|------|------|
| Cell→Down 连通率 | **81%** (6134/7612) | **4%** (432/10206) |
| 总边数 | 7451 | 1537（全部模拟边）|
| 任一 cobalt 事件可达 Downstream | 166/173 (96%) | 116/189 (61%) |
| 任一 lithium 事件可达 Downstream | 166/173 (96%) | 150/189 (79%) |

旧版采用"同国家全连接"逻辑，导致 Cell→Downstream 近全连通，任何上游事件均传播至几乎所有下游节点，图结构失去区分度。新版改为地理距离 + 度数上限，图结构对 Network Agent 的传播分析有实际贡献。

### 6.3 产能数据质量

| 来源 | 数量 | 占比 | 说明 |
|------|------|------|------|
| NAATBatt 原始值（`capacity_source=naatbatt`） | 142 | 37% | 直接使用 |
| 无产能记录（`capacity_source=unknown`） | 244 | 63% | 产能值为 NaN（空值，非零） |

`unknown` 设施的产能为 NaN，不参与 CapacityShare 和 AltCapacityRatio 计算（`capacity_known=False`），风险得分中该维度置 0——这是保守估计而非"产能为零"假设，在论文局限性章节中说明。

**为何不插补缺失产能：** Upstream 有 3 条设施（South Star Battery Metals Corp / graphite、South32 / manganese、Titan Lithium / lithium）无原始产能记录。曾考虑用同层级中位数（10,500 MT）填补，但各材料产能量级差异较大（锰矿与锂矿不具可比性），且 3 条仅占总设施的 0.8%，对聚合指标影响可忽略。最终选择保持 `unknown`，以 `capacity_known=False` 机制保守处理，概念更清晰且易于答辩。

### 6.4 材料覆盖（facilities_clean.csv 中含各材料的设施数）

| 材料 | 设施数 |
|------|--------|
| cobalt | 39 |
| lithium | 39 |
| nickel | 36 |
| graphite | 23 |
| manganese | 19 |

### 6.5 地理分布

| 国家 | 设施数 |
|------|--------|
| US | 328 (85%) |
| Canada | 49 (13%) |
| Mexico | 9 (2%) |

---

## 7. 输出文件

| 文件 | 描述 |
|------|------|
| `data/facilities_clean.csv` | 386条清洗后设施记录，含所有派生字段 |
| `data/knowledge_graph.json` | 386节点 / 1537条边的有向图（JSON格式），含 metadata |

`knowledge_graph.json` 的 metadata 字段记录了构建参数，可追溯：

```json
{
  "total_nodes": 386,
  "total_edges": 1537,
  "created_at": "2026-07-01",
  "edge_params": { "K_NEAR": 5, "K_FAR": 3, "K_BGM_CELL": 6 }
}
```

---

## 8. 局限性说明（论文用）

1. **无真实供应关系**：NAATBatt 仅为设施目录，边均为基于关键词和地理距离的模拟，不代表真实合同关系。
2. **产能数据缺失率高**（63%）：无法精确计算受影响产能比例，全局指标（BetroffeneKapazität%）为保守下界估计。
3. **文献阈值规则局限**：`supplier_concentration` / `import_dependency` 基于文献阈值规则（USGS MCS 2026 / IEA GCMO 2025），非公司层面实测，未考虑实际合同关系；分类阈值（如"≥95%"）存在一定主观性。
4. **北美范围限制**：数据库仅覆盖北美设施，无法反映亚洲（尤其中国）供应链对北美市场的间接影响。
