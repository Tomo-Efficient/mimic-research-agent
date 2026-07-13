# MIMIC Research Agent — 技能管线说明

基于 SKILL1234 四个 Skill 文档（`mimic-eda-skills` / `medical-literature-retrieval-workflow` / `medical-stats-test` / `mimic-report-generation`）搭建的临床研究 Agent，完整走通「数据→选题→统计→论文」四步管线。

---

## Skill 1：MIMIC 数据预处理与 EDA

**定位**：数据预处理与探索性分析助手

**输入**：本地 MIMIC-IV 原始 CSV 数据目录

**过程**（按意图路由，选择最小必要步骤）：

| 用户意图 | 对应子命令 |
|---------|-----------|
| 查看有哪些表、字段、表间关系 | `check-data` → `scan` → `detect-relations` |
| 分析缺失率、异常值、变量分类 | `assess` → `classify` |
| 清洗、填补、删无效列 | `clean` |
| 生成静态视图或时序视图 | `build-views` |
| 跑完整预处理 + 出报告 | `run-all` |

工作流：确认数据目录 → 按意图选子命令 → 大表先抽样 → 确认输出目录不覆盖原始文件 → 只报告核心路径

**输出**：清洗后数据集、分析视图、标准化数据质量报告（JSON + Markdown）

**报告约束**：表名、源路径、清洗后路径、行列数；时间字段解析结果及被强制转缺失的数量；每列字段名、dtype、缺失率、缺失等级、唯一值数、插补策略、异常值摘要；被排除列及必要说明

**脚本资源**：`scripts/skill1_preprocess.py`（当前版本 `compute_imputation_values` 被截断，依赖 `config.py`，不可直接 CLI 执行）

---

## Skill 2：医学文献检索与研究选题

**定位**：从 EDA 报告到候选研究 idea 的完整选题工作流

**输入**：EDA 报告、变量描述、相关性分析、回归结果、组间差异、临床队列探索报告；或医学主题、疾病、人群、暴露变量、结局变量、检测方法

**过程**（强制 9 步流程）：

1. **读取 EDA 报告**：识别数据来源、研究对象、变量字典、统计方法、已发现的显著/非显著关联及限制
2. **提取研究要素**（参考 `eda-pico-extraction-rules.md`）：人群、疾病、暴露变量、结局变量、分组变量、检测方法、显著变量（含 p 值/效应量/CI）、样本量和事件数
3. **生成 PICO/PECO + PubMed 检索式**：1 个主框架 + 2-4 个备选。同概念用 OR，跨概念用 AND，新兴变量加 `[Title/Abstract]`，MeSH 不确定时标注
4. **执行三类 PubMed 检索**（按当前日期动态计算时间窗）：
   - 近 3 年原始研究
   - 近 5 年综述
   - 不限时间精确组合
5. **文献结构化摘要**：对每条真实可验证文献保留 Title、Authors、Year、Journal、PMID、DOI、Evidence Status，并结构化摘要（对象、样本量、暴露、结局、方法、主要发现、局限性）
6. **构建 Evidence Gap Matrix**（参考 `evidence-gap-idea-scoring.md`）：EDA 发现 + PICO + 三类证据 + 人群/暴露/结局匹配度 + Gap 类型（人群 gap / 暴露 gap / 结局 gap / 方法 gap / 组合 gap / 验证 gap / 证据过密）+ Idea 转化潜力
7. **生成 5-10 个候选 idea**：每个包含标题、研究问题、PICO、假设、可用变量、建议统计方法、需补充数据、目标论文类型
8. **六维度评分**（每项 1-5 分）：创新性、数据可验证性、临床意义、统计可行性、发表潜力、风险
9. **输出 Top 10**：按总分降序，同分优先（数据可直接验证 > 事件数足够 > 文献有基础但直接证据少 > 临床问题清晰）

**硬规则**：不编造论文/DOI/PMID；不能查到的写"本轮检索未发现直接证据"；不把相关性写成因果；不把预印本当同行评议；不把 idea 写成已验证结论

**输出**（参考 `retrieval-output-template.md`）：EDA 要素摘要表、PICO/PECO 表、三类 PubMed Query、文献结构化摘要表、Evidence Gap Matrix、Idea 评分表、Top 推荐及下一步分析建议

---

## Skill 3：医学统计检验

**定位**：AI 统计师——检验执行助手。根据输入的研究 idea + 预处理数据集，自动完成统计方法匹配、全流程校验、检验执行与标准化输出

**输入**：
- 来自 Skill 1：清洗后数据集、数据字典、数据质量报告
- 来自 Skill 2/用户：结构化候选 idea 列表（含 idea ID、来源、创新类型、暴露/结局/协变量、医学合理性初筛结论）
- 可选配置：显著性水平 α（默认 0.05）、多重校正开关、校正方法

**过程**（每个 idea 串行执行 5 个阶段）：

| 阶段 | 内容 |
|------|------|
| **前置校验** | 变量存在性、有效样本量（连续≥30/组，分类≥5/组）、缺失率（暴露/结局≤30%，协变量≤20%）、医学合理性（按 idea 来源分级）、统计前提（正态性/方差齐性/共线性 VIF≤10/比例风险假设） |
| **方法匹配** | 第一层：按研究目的路由（stats-basic / stats-regression / stats-survival / stats-explore / stats-reproduce）。第二层：按变量结构细分。前提不满足自动切换备选方法并记录原因 |
| **统计执行** | 调用预验证统计代码模板，通过 `python scripts/stats_cli.py <子命令>` 执行。禁止 LLM 自由生成代码。异常走模板内置分支。兜底规则：模板库未覆盖的场景需先提示"无预验证模板" |
| **后置校验** | 通用：数值合理性、结果一致性。分类型：回归拟合度/强影响点、生存分析 PH 假设终检、探索分析轮廓系数≥0.5。批量校正：≥3 个分析项 → Benjamini-Hochberg |
| **结果输出** | 提取核心指标 → 格式化输出 → 基于预设模板生成自然语言解读（严格区分"统计结论"与"临床提示"）→ 全流程日志 |

**方法集约束**：结论创新型 idea 仅常规方法；方法创新型可加进阶方法

**子技能**：
- `stats-reproduce`：复现类检验
- `stats-survival`：Kaplan-Meier / Cox / 竞争风险
- `stats-regression`：线性/Logistic/多因素/诊断/RCS/PSM
- `stats-basic`：t 检验/ANOVA/Wilcoxon/卡方/Fisher/相关性
- `stats-explore`：描述统计/相关性矩阵/K-means/PCA

**输出**：结构化统计结果总表（Markdown）、单 idea 自然语言解读草稿、完整执行日志、失败清单及原因和调整建议

---

## Skill 4：报告生成

**定位**：基于上游分析产物生成可投稿草稿级别的 IMRAD 临床研究论文

**输入**（必需文件，缺失即停止）：

| 文件 | 用途 |
|------|------|
| `task_contract.json` | 研究假设、暴露、结局、协变量、研究设计 |
| `cohort.csv` | 患者级队列数据 |
| `baseline_table.csv` | Table 1 基线特征 |
| `model_results.json` | 统计模型结果、效应量、CI、p 值 |
| `funnel.json` | 纳入排除筛选流程和最终队列数 |
| `paper_evidence.json`（复现时） | 原论文结果和证据 |

**过程**（7 步，参考 `report-generation-guide.md` 28KB 详细规范）：

1. **校验输入**：缺失则提前返回，说明缺哪个文件、应由哪个上游 Skill 生成
2. **汇总 manuscript data**：读取 contract、cohort、baseline、model results、funnel
3. **生成图表**：Table 1、KM 曲线、森林图、ROC、校准曲线、DCA 等，仅限已执行的分析
4. **按 IMRAD 写作**：Title → Structured Abstract → Introduction → Methods → Results → Discussion → Conclusions → References
5. **复现模式**：生成 alignment table + `reproduction_report.md`
6. **输出到 `/workspace/results/`**
7. **质量检查**：数字一致性、图表顺序、引用完整性、摘要字数、限制段落完整性

**写作质量门槛**：
- 摘要 ≤ 250 词
- Methods 遵循 STROBE
- Table 1 含 Total/Unexposed/Exposed 和 p-value/SMD
- Discussion 覆盖：关键发现、既往研究对比、机制、临床意义、优势、限制、未来研究、结论
- 参考文献 Vancouver 编号格式
- 精确报告 p 值，不写 `p < 0.05`（除非 `p < 0.001`）

**输出**：`manuscript.md` / `.docx` / `.pdf`、`table1.png`、`km_curve.png`、`forest_plot.png`、`reproduction_report.md`、`references.bib`

---

## 管线总览

```
原始 MIMIC CSV
    │
    ▼
Skill 1 (EDA) ──── 数据扫描、质量评估、清洗、视图构建
    │                产出: 清洗后数据 + 质量报告
    ▼
Skill 2 (选题) ─── EDA报告 → PICO → PubMed检索 → Evidence Gap → Idea评分
    │                产出: 10个候选idea（论文复现模式则产出5篇真实论文）
    ▼
Skill 3 (统计) ─── Idea + 数据 → 方法匹配 → 前提校验 → CLI执行 → 解读
    │                产出: 统计结果表 + 执行日志 + 解读草稿
    ▼
Skill 4 (报告) ─── 全部上游产物 → IMRAD论文（或复现对比报告）
                    产出: 可投稿草稿 manuscript.md + 图表 + references.bib
```
