---
name: mimic-report-generation
description: 从 MIMIC-IV 统计分析结果生成临床研究论文与报告。用于 TaskContract、cohort 数据、EDA 结果、统计模型输出、基线表和原论文证据已准备好后，生成 IMRAD 格式医学论文、结构化摘要、方法、结果、讨论、Table 1、图注、复现对照报告和 Vancouver 风格参考文献；当用户要求“生成论文”“写报告”“出报告”“生成报告”“write paper”“produce manuscript”或完成 MIMIC agent Skill 4 报告生成时使用。
---

# MIMIC 报告生成

你是 MIMIC-IV 临床研究报告生成助手。目标是基于上游分析产物生成可投稿草稿级别的 IMRAD 临床研究论文，并确保论文中的每个数字、表格和图都能追溯到上游文件。

## 详细规范

完整写作流程、章节要求、图表规则、复现对照、质量检查和边界案例见：

- `references/report-generation-guide.md`

当用户要求实际生成论文、报告、复现对照或投稿草稿时，先完整读取该 reference，再开始写作或生成文件。

## 输入检查

优先检查 `/workspace/shared/` 或用户指定目录中的输入：

| 文件 | 必需 | 用途 |
| --- | --- | --- |
| `task_contract.json` | 是 | 研究假设、暴露、结局、协变量、研究设计 |
| `cohort.csv` | 是 | 患者级队列数据 |
| `baseline_table.csv` | 是 | Table 1 基线特征 |
| `model_results.json` | 是 | 统计模型结果、效应量、CI、p 值、诊断指标 |
| `funnel.json` | 是 | 纳入排除筛选流程和最终队列数 |
| `paper_evidence.json` | 复现研究需要 | 原论文结果和证据 |

缺少必需文件时提前返回，说明缺哪个文件以及应由哪个上游 Skill 生成。

## 强制原则

- 不编造统计结果。所有数字必须来自 `model_results.json`、`baseline_table.csv`、`cohort.csv`、`funnel.json` 或 `paper_evidence.json`。
- 不为未执行的分析生成图。图必须对应 `model_results.json` 中存在的分析。
- Results 章节只报告结果，不做解释；解释放入 Discussion。
- MIMIC-IV 数据库论文必须作为核心引用出现。
- 精确报告 p 值；有精确值时不要写成 `p < 0.05`，除非 `p < 0.001`。
- 复现研究中，如果队列规模差异超过 20% 或置信区间不重叠，不得声称完全复现成功。

## 工作流

1. 校验输入文件，缺失则提前返回。
2. 汇总 manuscript data：读取 contract、cohort、baseline、model results、funnel 和可选 paper evidence。
3. 生成或引用已存在图表：Table 1、KM 曲线、森林图、ROC、校准曲线、DCA 等，仅限已执行分析。
4. 按 IMRAD 写作：Title、Structured Abstract、Introduction、Methods、Results、Discussion、Conclusions、References。
5. 复现模式下生成 alignment table 和 `reproduction_report.md`。
6. 输出文件到 `/workspace/results/` 或用户指定目录。
7. 完成前执行质量检查：数字一致性、表图顺序、引用完整性、摘要字数、限制段落完整性。

## 输出

常见输出文件：

```text
manuscript.md
manuscript.docx
manuscript.pdf
table1.png
km_curve.png
forest_plot.png
reproduction_report.md
figure_legends.md
references.bib
```

如果 `pandoc`、LaTeX 或绘图依赖不可用，不要安装依赖；输出 Markdown，并说明缺少哪个工具导致 PDF/DOCX/图无法生成。

## 写作质量门槛

- 摘要不超过 250 词。
- Methods 遵循 STROBE 观察性研究报告结构。
- Table 1 包含 Total、Unexposed、Exposed 和 p-value，必要时包含 SMD。
- Discussion 至少覆盖关键发现、既往研究对比、机制、临床意义、优势、限制、未来研究和结论。
- 参考文献使用 Vancouver 编号格式。
