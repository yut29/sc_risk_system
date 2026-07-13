# 项目状态 — SC Risk System (Masterprojekt)

> 最后更新：2026-05-24  
> 目标：**2026-07-31 完成系统设计、开发、测试、验证**（论文提交截止：**2026-08-31**）

---

## 当前阶段

**阶段 2 / 5 — 数据准备与知识图谱构建**

```
[x] 1. 项目规划 & Exposé                      (完成)
[~] 2. 数据准备 & 知识图谱构建                 (进行中)
[ ] 3. Agent 框架选型 & 各 Agent 实现
[ ] 4. Pipeline 集成 & Streamlit UI
[ ] 5. 场景评估 & 论文撰写
```

---

## 已完成

- [x] Exposé 撰写提交（`Expose_Masterprojekt_Yutong_Liu.docx`）
- [x] NAATBatt 数据清洗 → `data/facilities_clean.csv`
- [x] 知识图谱构建脚本（`data_prep/build_graph.py`, `data_prep/build_facilities.py`）
- [x] 知识图谱生成 → `data/knowledge_graph.json`

---

## 进行中

- [ ] 知识图谱节点/边结构验证（Unternehmen & Materialien）
- [ ] 模拟供应关系定义（Exposé §2.3：NAATBatt 无直接供应关系，需手动构建）
- [x] Agent 框架选型：**LangGraph** 确定（2026-05-11，见 [ADR-001](decisions/ADR-001-framework-selection.md)）

---

## 待办（按优先级）

### Agent 实现（`agents/`）
- [ ] Intake Agent — 新闻过滤 & 关键词提取
- [ ] Risk Assessment Agent — 风险分类 & 严重程度评估
- [ ] Network Agent — 图遍历 & 受影响企业识别（NetworkX）
- [ ] Data Retrieval Agent — NAATBatt 数据查询
- [ ] Synthesis Agent — 结构化风险报告生成
- [ ] Validation Agent — 来源验证 & Halluzination 检查（最多 2 次迭代）

### Pipeline & 集成（`pipeline/`）
- [ ] 两种触发模式：Trigger A（新闻自动）/ Trigger B（用户查询）
- [ ] NewsAPI 接入
- [ ] Agent 编排流程（含 Feedback Loop）

### UI（`ui/`）
- [ ] Streamlit 界面
- [ ] 风险报告展示 & 图可视化

### 评估
- [ ] 场景 1：刚果钴矿罢工
- [ ] 场景 2：锂出口限制
- [ ] 场景 3：关键物流港口中断

---

## 开放问题（来自 Baris 邮件 2026-05-15）

| # | 问题 | 严重程度 | 状态 |
|---|------|----------|------|
| 1 | **Risikomodell**：风险评分如何量化？% 数字怎么算出来？→ 需定义正式公式 | 🔴 高 | 🔄 草案中，见[下方](#risikomodell-草案) |
| 2 | **Ablaufdiagramm**：Agent 间关系与流程如何文档化？→ 需框架图/模型 | 🔴 高 | ✅ Swimlane-图（draw.io）完成，含两条 Feedback Loop + I/O 列，见 `multi_agent_pipeline_flow.drawio` |
| 3 | **Schnittstellenlogik**：哪些数据在哪里交换？→ 接口定义文档 | 🟡 中 | ✅ 完整接口定义见 [architecture.md](architecture.md#schnittstellenlogik--datenaustausch-zwischen-agenten)（含 failure_type 路由逻辑） |
| 4 | **Halluzination-Tests**：如何验证 Agent 输出正确性？→ 测试方案 | 🟡 中 | ✅ 见 [test_plan.md](test_plan.md) |

> 严重程度：🔴 高 / 🟡 中 / 🟢 低

### Risikomodell — aktueller Stand

详见 [docs/risk_model.md](risk_model.md)。核心公式：

```
RiskScore_i = Severity × TierWeight_i × Vulnerability_i × (1 − ResilienceDiscount_i)
```

| 维度组 | 维度 | 权重/来源 |
|--------|------|----------|
| 过滤器 | MaterialMatch, RegionMatch | 二值，不满足则排除 |
| Vulnerability | ImportDependency (0.30) + SingleSourceFlag (0.30) + CapacityShare (0.25) + LeadTime (0.15) | Network + Data Retrieval Agent |
| 网络位置 | Supply Chain Tier Weight（Upstream=1.0 … Downstream=0.3） | segment 字段直接映射 |
| 韧性折扣 | AltCapacityRatio → ResilienceDiscount（最多 -50%） | Data Retrieval Agent |

全局指标（报告标题，与 Top-3 排名独立）：
- `BetroffeneKapazität%` = 受影响设施产能 / 同材料总产能
- `AlternativeKapazität%` = 未受影响替代设施产能 / 总产能

---

## 里程碑

| 里程碑 | 目标日期 | 状态 |
|--------|----------|------|
| Exposé 提交 | 2026-04 | ✅ 完成 |
| 知识图谱完成 & 验证 | 2026-06-07 | 🔄 进行中 |
| Agent 框架选型完成 | 2026-05-11 | ✅ LangGraph |
| 所有 6 个 Agent 实现 | 2026-07-05 | ⏳ |
| Pipeline & UI 集成 | 2026-07-19 | ⏳ |
| 3 个场景评估完成 | 2026-07-31 | ⏳ |
| 论文撰写 | 2026-08 起 | ⏳ |
| 论文提交（官方截止） | 2026-08-31 | ⏳ |

补确定性函数单元测试（compute_scores、haversine_km、_bfs_descendants）
S1 / S2 / S3 场景测试（填结果表）
幻觉 + 稳定性测试
