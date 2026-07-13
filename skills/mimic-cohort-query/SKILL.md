---
name: mimic-cohort-query
description: |
  从 MIMIC-IV CSV 数据文件中提取研究队列。根据上游选定的研究 idea (来自 Skill2) 的变量需求、
  PICO 纳排标准，从本地 CSV 文件中 JOIN 相关表、筛选患者、提取变量，生成研究用分析数据集
  (research_cohort.csv) 和筛选漏斗文档 (funnel.json)。
  在 idea 选定后、统计检验 (Skill3) 前执行。
---

# MIMIC 队列提取 (CSV 版)

你是 MIMIC-IV 队列提取助手。目标是根据选定的研究 idea，从本地 CSV 数据文件中提取对应的患者队列和分析变量集。

## 强制边界

- 仅处理本地 CSV 文件。MIMIC-IV 属于受限医疗数据，禁止上传到在线服务。
- 不修改原始数据文件。队列输出写入独立目录或临时文件。
- 队列提取不应对原始 CSV 做 in-place 替换。
- 所有步骤的行数必须来自实际计算，不编造数字。

## 输入

| 输入 | 来源 | 说明 |
|------|------|------|
| 选定研究 idea | Skill2 (selected_idea) | 含 data_variables、PICO、hypothesis、纳排条件 |
| EDA 报告 | Skill1 (eda_report) | 数据表结构、变量列表、缺失情况 |
| CSV 数据文件 | data_dir | 11 张 MIMIC-IV CSV 表 |

## 输出

| 文件 | 说明 |
|------|------|
| `research_cohort.csv` | 患者级分析数据集，含所有 study variables |
| `funnel.json` | 筛选漏斗：每步描述 + 患者数 |

## 数据文件关系

核心表关联（基于 MIMIC-IV 主键）：
- `subject_id` → patients_24h, admissions_24h
- `subject_id + hadm_id` → cohort_24h, diagnoses_icd_24h
- `subject_id + hadm_id + stay_id` → labevents, chartevents, inputevents, outputevents, procedureevents

实验室指标 labels → sepsis_icu_labevents_core_items.csv (itemid ↔ label 映射)

## 执行流程

### 第 1 步：解析 idea 的变量需求

从 selected_idea 中提取：
- `data_variables`: 用户/LLM 指定的变量名列表
- `pico.P` (Population): 人群过滤条件（如年龄 ≥ 18）
- `pico.I` (Exposure): 暴露变量
- `pico.O` (Outcome): 结局变量
- `hypothesis`: 研究假设（含隐含变量需求）

变量源的智能匹配：
1. 在 EDA 报告的 table_reports 中搜索列名
2. 模糊匹配：如 "lactate" → labevents 中的 "Lactate" label
3. 无法匹配的变量标注为 `not_found`，在输出中报告
4. 默认包含: subject_id, hadm_id, stay_id, gender, anchor_age, hospital_expire_flag

### 第 2 步：构建分析数据集

从 cohort_24h.csv 开始（作为患者基础表），逐步 LEFT JOIN：

```
Step 1: 加载 cohort_24h.csv（基础人群）
Step 2: JOIN patients_24h.csv（性别、年龄）
Step 3: JOIN admissions_24h.csv（住院结局）
Step 4: 根据 idea 需要的 lab items → JOIN labevents
Step 5: 根据需要 JOIN 其他表（diagnoses, chartevents, etc.）
Step 6: 应用排除条件（缺失率过滤）
```

### 第 3 步：应用纳排条件

基于 idea 的 PICO 和 hypothesis，应用筛选：

- 年龄条件: `anchor_age >= 18`
- 实验室值范围: 根据临床常识过滤异常值（如乳酸 < 0 排除）
- 缺失处理: 核心变量缺失 → 标记并计数

### 第 4 步：验证队列规模

- 记录每一步后的患者数
- 与 idea 中预期的样本量对比
- 如果最终队列 < 30 人，标记为 `insufficient_sample`

### 第 5 步：生成筛选漏斗

输出 `funnel.json`：

```json
{
  "steps": [
    {"description": "基础 ICU 队列 (cohort_24h)", "count": 17241},
    {"description": "JOIN patients (人口学)", "count": 17241},
    {"description": "JOIN admissions (结局)", "count": 14189},
    {"description": "合并实验室数据后", "count": 12000},
    {"description": "排除核心变量缺失", "count": 11500}
  ],
  "final_cohort_size": 11500,
  "variables_included": ["subject_id", "anchor_age", "gender", "hospital_expire_flag", "lab_lactate", ...],
  "variables_not_found": ["some_variable"],
  "warnings": []
}
```

## 常用变量映射

| 研究概念 | CSV 文件 | 列名/itemid 映射方式 |
|----------|---------|---------------------|
| 年龄 | patients_24h | anchor_age |
| 性别 | patients_24h | gender |
| 住院死亡 | admissions_24h | hospital_expire_flag |
| 入院类型 | admissions_24h | admission_type |
| ICU 时长 | cohort_24h | los |
| 实验室指标 | sepsis_icu_labevents_core_numeric_24h | 通过 labevents_core_items 的 label 查 itemid |
| 生命体征 | sepsis_icu_chartevents_core_numeric_24h | 通过 itemid 查询 |
| ICD 诊断 | diagnoses_icd_24h | icd_code + icd_version |
| 入量 | inputevents_24h | itemid + amount |
| 出量 | outputevents_24h | itemid + value |

## 异常处理

| 失败类型 | 处理逻辑 | 输出建议 |
|---------|---------|---------|
| 所需变量在所有表中未找到 | 标注 not_found，继续处理其他变量 | 检查变量名拼写或 itemid 映射 |
| 队列规模为 0 | 检查筛选条件 | 放宽过滤条件或报告数据不足 |
| 队列 < 30 | 标记 insufficient_sample，仍输出队列 | 样本量不足以支持统计推断 |
| 核心变量缺失率 > 50% | 标记 warning，排除该变量 | 该变量不适合纳入分析 |
| CSV 文件不存在 | 报告缺失文件，终止 | 确认数据目录路径 |
