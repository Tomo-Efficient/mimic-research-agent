# 输出模板

## A. EDA 要素摘要

| 要素 | 提取结果 | 来源/备注 |
|---|---|---|
| 人群 |  |  |
| 疾病 |  |  |
| 暴露变量 |  |  |
| 结局变量 |  |  |
| 分组变量 |  |  |
| 检测方法 |  |  |
| 显著相关变量 |  |  |
| 样本量和事件数 |  |  |
| 主要限制 |  |  |

## B. PICO/PECO 表

| 框架 | Population | Intervention/Exposure | Comparator | Outcome | 用途 |
|---|---|---|---|---|---|
| 主 PICO/PECO |  |  |  |  |  |
| 备选 1 |  |  |  |  |  |
| 备选 2 |  |  |  |  |  |

## C. PubMed Query

### C1. 近 3 年原始研究

```text
(
  (P terms)
  AND
  (I/E terms)
  AND
  (O terms)
)
AND
("YYYY/MM/DD"[Date - Publication] : "YYYY/MM/DD"[Date - Publication])
AND
(
  clinical study[Publication Type]
  OR observational study[Publication Type]
  OR cohort[Title/Abstract]
  OR case-control[Title/Abstract]
  OR cross-sectional[Title/Abstract]
  OR randomized[Title/Abstract]
)
NOT
(
  review[Publication Type]
  OR systematic review[Publication Type]
  OR meta-analysis[Publication Type]
)
```

### C2. 近 5 年综述

```text
(
  (P terms)
  AND
  (I/E terms)
  AND
  (O terms)
)
AND
("YYYY/MM/DD"[Date - Publication] : "YYYY/MM/DD"[Date - Publication])
AND
(
  review[Publication Type]
  OR systematic review[Publication Type]
  OR meta-analysis[Publication Type]
)
```

### C3. 不限时间精确组合检索

```text
(
  (P exact terms)
  AND
  (I/E exact terms)
  AND
  (O exact terms)
)
```

补充说明：
- `YYYY/MM/DD` 必须按当前日期动态计算。
- MeSH 未验证项必须列出。
- 如果未联网检索，明确写“仅生成 query，未返回真实文献记录”。

## D. 文献结构化摘要表

| Title | Authors | Year | Journal | PMID | DOI | Evidence Status | 研究对象 | 样本量 | 暴露因素 | 结局 | 方法 | 主要发现 | 局限性 |
|---|---|---:|---|---|---|---|---|---|---|---|---|---|---|
|  |  |  |  |  | DOI not verified |  |  | not reported |  |  |  |  |  |

## E. Evidence Gap Matrix

| EDA finding | PICO/PECO | 近 3 年原始研究 | 近 5 年综述 | 不限时间精确证据 | 人群匹配 | 暴露/方法匹配 | 结局匹配 | Gap 类型 | Idea 潜力 |
|---|---|---|---|---|---|---|---|---|---|
|  |  | 有/少/未检出 | 有/少/未检出 | 有/少/未检出 | 高/中/低 | 高/中/低 | 高/中/低 |  | 高/中/低 |

注意：`未检出` 只能表示本轮检索未发现直接证据，不能写成“确定无人研究”。

## F. 候选 Idea 评分表

| 排名 | Idea 标题 | PICO/PECO | 假设 | 可用数据变量 | 建议统计方法 | 创新性 | 数据可验证性 | 临床意义 | 统计可行性 | 发表潜力 | 风险 | 总分 |
|---:|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 |  |  |  |  |  |  |  |  |  |  |  |  |

## G. 最推荐的 10 个 Idea

按总分降序输出：

1. 标题：
   推荐理由：
   下一步分析：

如果不足 10 个，输出全部候选，并说明是因为数据、事件数、变量含义或文献证据限制。

## H. 下一步

给出最小可执行分析计划：
- 需要确认的变量定义。
- 需要补充的混杂因素。
- 推荐主模型。
- 推荐敏感性分析。
- 是否需要外部验证。
