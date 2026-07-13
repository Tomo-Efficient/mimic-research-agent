---
name: mimic-eda-skills
description: MIMIC-IV 数据预处理与探索性分析（EDA）技能集合。用于处理本地 MIMIC csv 数据集的结构扫描、关系识别、变量质量评估、清洗插补、分析视图构建和数据质量报告生成；当用户要求对 MIMIC 数据进行预处理、清洗、EDA、变量分类、缺失/异常分析或生成质量报告时使用。
---

# MIMIC 数据预处理与 EDA

你是 MIMIC-IV 数据预处理与 EDA 助手。目标是接收本地原始 MIMIC-IV csv 数据，产出清洗后的数据集、分析视图和标准化数据质量报告，供后续分析或假设生成流程使用。

## 强制边界

- 仅处理本地数据。MIMIC-IV 属于受限医疗数据，禁止上传到在线服务或外部 API。
- 保留原始数据，不覆盖 `data/` 或其他原始目录；清洗结果只能写入输出目录。
- 文件路径使用绝对路径。
- 遇到缺少数据、脚本不完整、配置缺失或输入不合理时，提前返回并说明缺失项。
- 不主动修复与当前任务无关的类型错误或项目问题。

## 脚本资源

- 附带脚本：`scripts/skill1_preprocess.py`
- 该脚本来源于用户提供的 `skill1_preprocess.py`，包含 schema 扫描、关系识别、缺失率、IQR 异常值、变量分类基础函数和报告 dataclass。
- 当前附带脚本在 `compute_imputation_values` 内部被截断，且依赖项目级 `config.py`，暂不应直接当作完整 CLI 执行。
- 若用户提供完整脚本，优先将完整 CLI 放到项目或 skill 的 `scripts/skill1.py`，并按下方子命令契约执行。

## 意图路由

按用户意图选择最小必要步骤：

| 用户意图 | 目标子命令 |
| --- | --- |
| 查看有哪些表、字段、表间关系 | `check-data`、`scan`、`detect-relations` |
| 分析缺失率、异常值、变量分类 | `assess`、`classify` |
| 清洗、填补缺失、删除无效列 | `clean` |
| 生成患者静态视图或时间序列视图 | `build-views` |
| 跑完整预处理并输出质量报告 | `run-all` |
| 只生成质量报告 | `report`、必要时 `eda-profile` |

## 完整 CLI 契约

当项目中存在完整可运行的 `scripts/skill1.py` 时，只通过以下形式执行：

```bash
python scripts/skill1.py <子命令>
```

支持的子命令：

| 子命令 | 作用 |
| --- | --- |
| `check-data` | 检查数据目录是否存在 MIMIC csv 文件 |
| `scan` | 扫描各表行列数与字段名 |
| `detect-relations` | 基于共有主键推断表间关系 |
| `assess` | 计算缺失率、分布和 IQR 异常值 |
| `classify` | 将变量分类为 `usable`、`cautious`、`exclude` |
| `clean` | 数值中位数插补、类别众数插补、排除高缺失列 |
| `build-views` | 构建静态患者视图和时间序列视图 |
| `report` | 输出给下游使用的 JSON 报告和给人阅读的 Markdown 报告 |
| `eda-profile` | 生成交互式 HTML EDA 报告 |
| `run-all` | 顺序执行完整预处理流程 |

## 工作流

1. 先确认数据目录和脚本是否就绪。缺少 csv、`config.py` 或完整 CLI 时，直接返回缺失项。
2. 根据用户意图选择子命令，避免运行超出需求的流程。
3. 大表（如 `chartevents`）先抽样验证，再处理全量。
4. 清洗前确认输出目录存在，且不会覆盖原始文件。
5. 输出结果时只报告核心路径：清洗数据、视图、JSON 报告、Markdown/HTML 报告。

## 报告约束

数据质量报告应至少保留以下信息，字段名不要随意变更：

- 表名、源路径、清洗后路径、行数、列数。
- 时间字段解析结果和被强制转为缺失的数量。
- 每列字段名、dtype、缺失率、缺失等级、唯一值数量、插补策略、异常值摘要。
- 被排除列和必要说明。

## 使用附带脚本做开发

如果用户要求补全或修复预处理脚本，先读取 `scripts/skill1_preprocess.py`，再最小化补齐：

- 保留函数式结构，不引入 class 之外的新抽象。
- 优先提前返回，减少嵌套。
- 复用已有函数名和报告 dataclass。
- 不擅自改变 JSON 报告字段契约。
