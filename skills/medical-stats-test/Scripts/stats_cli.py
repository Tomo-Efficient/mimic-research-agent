#!/usr/bin/env python3
"""
医学统计检验 CLI。

依赖：pandas、scipy、statsmodels；生存分析额外依赖 lifelines。
用法示例：
python scripts/stats_cli.py stats-basic --chi2 --x gender --y death --data data.csv
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ALPHA = 0.05
MIN_CONT_SAMPLE = 30
MIN_CAT_PER_GROUP = 5
CORR_METHOD_MAP = {"bh": "fdr_bh", "bonferroni": "bonferroni", "sidak": "sidak"}


def load_table(path: str):
    import pandas as pd

    file_path = Path(path)
    if not file_path.exists():
        raise ValueError(f"数据文件不存在：{path}")
    readers = {".csv": pd.read_csv, ".tsv": lambda p: pd.read_csv(p, sep="\t"), ".xlsx": pd.read_excel, ".xls": pd.read_excel}
    reader = readers.get(file_path.suffix.lower())
    if not reader:
        raise ValueError("仅支持 csv、tsv、xlsx、xls 数据文件")
    return reader(file_path)


def parse_vars(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def ensure_columns(df, cols: list[str]) -> None:
    missing = [col for col in cols if col not in df.columns]
    if missing:
        raise ValueError(f"输入变量不存在：{missing}")


def clean_xy(df, x: str, y: str):
    ensure_columns(df, [x, y])
    sub_df = df[[x, y]].dropna()
    if len(sub_df) < MIN_CAT_PER_GROUP:
        raise ValueError(f"有效样本量过低：{len(sub_df)}")
    return sub_df


def grouped_values(df, group_col: str, value_col: str):
    groups = [g for _, g in df.groupby(group_col, observed=True)[value_col]]
    if len(groups) != 2:
        raise ValueError("该检验仅支持两组比较")
    if min(len(g) for g in groups) < MIN_CAT_PER_GROUP:
        raise ValueError("至少一组样本量低于阈值")
    return [g.astype(float).to_numpy() for g in groups]


def safe_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def write_output(payload: dict[str, Any], out: str | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if not out:
        print(text)
        return
    Path(out).write_text(text, encoding="utf-8")


def base_payload(args, method: str, result: dict[str, Any], warnings: list[str] | None = None) -> dict[str, Any]:
    return {
        "status": "success",
        "idea_id": getattr(args, "idea_id", None),
        "method": method,
        "alpha": ALPHA,
        "result": result,
        "warnings": warnings or [],
    }


def run_t_test(args):
    from scipy.stats import levene, shapiro, ttest_ind

    df = clean_xy(load_table(args.data), args.x, args.y)
    groups = grouped_values(df, args.x, args.y)
    warnings = []
    normal_p = [safe_float(shapiro(g).pvalue) for g in groups if len(g) >= 3]
    if normal_p and min(normal_p) < ALPHA:
        warnings.append("正态性检验未通过，建议改用 --wilcoxon")
    levene_p = safe_float(levene(*groups).pvalue)
    stat, p_value = ttest_ind(*groups, equal_var=bool(levene_p and levene_p >= ALPHA), nan_policy="omit")
    return base_payload(args, "独立样本t检验", {
        "sample_size": len(df),
        "group_sizes": [len(g) for g in groups],
        "statistic": safe_float(stat),
        "raw_p": safe_float(p_value),
        "normality_p": normal_p,
        "levene_p": levene_p,
    }, warnings)


def run_wilcoxon(args):
    from scipy.stats import mannwhitneyu

    df = clean_xy(load_table(args.data), args.x, args.y)
    groups = grouped_values(df, args.x, args.y)
    stat, p_value = mannwhitneyu(*groups, alternative="two-sided")
    return base_payload(args, "Wilcoxon秩和检验", {
        "sample_size": len(df),
        "group_sizes": [len(g) for g in groups],
        "statistic": safe_float(stat),
        "raw_p": safe_float(p_value),
    })


def run_chi2(args):
    import pandas as pd
    from scipy.stats import chi2_contingency

    df = clean_xy(load_table(args.data), args.x, args.y)
    table = pd.crosstab(df[args.x], df[args.y])
    stat, p_value, dof, expected = chi2_contingency(table)
    min_expected = float(expected.min())
    warnings = ["期望频数低于阈值，建议改用 --fisher"] if min_expected < MIN_CAT_PER_GROUP else []
    return base_payload(args, "卡方检验", {
        "sample_size": len(df),
        "statistic": safe_float(stat),
        "raw_p": safe_float(p_value),
        "dof": int(dof),
        "min_expected_freq": min_expected,
        "contingency_table": table.to_dict(),
    }, warnings)


def run_fisher(args):
    import pandas as pd
    from scipy.stats import fisher_exact

    df = clean_xy(load_table(args.data), args.x, args.y)
    table = pd.crosstab(df[args.x], df[args.y])
    if table.shape != (2, 2):
        raise ValueError("Fisher精确检验仅支持2x2列联表")
    odds_ratio, p_value = fisher_exact(table)
    return base_payload(args, "Fisher精确检验", {
        "sample_size": len(df),
        "odds_ratio": safe_float(odds_ratio),
        "raw_p": safe_float(p_value),
        "contingency_table": table.to_dict(),
    })


def run_corr(args, method: str):
    from scipy.stats import pearsonr, spearmanr

    df = clean_xy(load_table(args.data), args.x, args.y)
    x = df[args.x].astype(float)
    y = df[args.y].astype(float)
    stat, p_value = (pearsonr(x, y) if method == "pearson" else spearmanr(x, y))
    return base_payload(args, f"{method.title()}相关分析", {
        "sample_size": len(df),
        "correlation": safe_float(stat),
        "raw_p": safe_float(p_value),
    })


def run_basic(args):
    runners = {
        "t_test": run_t_test,
        "wilcoxon": run_wilcoxon,
        "chi2": run_chi2,
        "fisher": run_fisher,
        "corr_pearson": lambda a: run_corr(a, "pearson"),
        "corr_spearman": lambda a: run_corr(a, "spearman"),
    }
    method = next((name for name in runners if getattr(args, name)), None)
    if not method:
        raise ValueError("必须指定一种基础检验方法")
    return runners[method](args)


def model_matrix(df, x: str, covariates: list[str]):
    import pandas as pd
    import statsmodels.api as sm

    cols = [x] + covariates
    ensure_columns(df, cols)
    matrix = pd.get_dummies(df[cols], drop_first=True, dtype=float)
    if matrix.empty:
        raise ValueError("模型自变量为空")
    return sm.add_constant(matrix, has_constant="add")


def vif_info(x_matrix):
    from statsmodels.stats.outliers_influence import variance_inflation_factor

    cols = [col for col in x_matrix.columns if col != "const"]
    return {col: safe_float(variance_inflation_factor(x_matrix[cols].values, i)) for i, col in enumerate(cols)}


def run_regression(args):
    import statsmodels.api as sm

    covariates = parse_vars(args.covariates)
    df = load_table(args.data)
    ensure_columns(df, [args.x, args.y] + covariates)
    sub_df = df[[args.x, args.y] + covariates].dropna()
    if len(sub_df) < MIN_CONT_SAMPLE:
        raise ValueError(f"有效样本量低于阈值：{len(sub_df)}")
    x_matrix = model_matrix(sub_df, args.x, covariates)
    y = sub_df[args.y].astype(float)
    fit = (sm.Logit(y, x_matrix).fit(disp=0) if args.logistic else sm.OLS(y, x_matrix).fit())
    params = {
        key: {
            "coef": safe_float(fit.params[key]),
            "ci_low": safe_float(fit.conf_int().loc[key, 0]),
            "ci_high": safe_float(fit.conf_int().loc[key, 1]),
            "raw_p": safe_float(fit.pvalues[key]),
        }
        for key in fit.params.index
    }
    return base_payload(args, "多因素Logistic回归" if args.logistic else "多因素线性回归", {
        "sample_size": len(sub_df),
        "aic": safe_float(getattr(fit, "aic", None)),
        "params": params,
        "vif_info": vif_info(x_matrix),
    })


def run_survival(args):
    covariates = parse_vars(args.covariates)
    df = load_table(args.data)
    ensure_columns(df, [args.x, args.time, args.event] + covariates)
    sub_df = df[[args.x, args.time, args.event] + covariates].dropna()
    if (sub_df[args.time].astype(float) < 0).any():
        raise ValueError("生存时间变量存在负值")
    if not set(sub_df[args.event].dropna().unique()).issubset({0, 1}):
        raise ValueError("事件变量必须为0/1二分类")
    return run_cox(args, sub_df, covariates) if args.cox else run_km(args, sub_df)


def run_km(args, df):
    from lifelines import KaplanMeierFitter
    from lifelines.statistics import logrank_test

    groups = list(df[args.x].dropna().unique())
    if len(groups) != 2:
        raise ValueError("KM+Logrank仅支持两组比较")
    kmf = KaplanMeierFitter()
    summaries = {}
    for group in groups:
        part = df[df[args.x] == group]
        kmf.fit(part[args.time], event_observed=part[args.event], label=str(group))
        summaries[str(group)] = {"median_survival_time": safe_float(kmf.median_survival_time_)}
    a = df[df[args.x] == groups[0]]
    b = df[df[args.x] == groups[1]]
    lr = logrank_test(a[args.time], b[args.time], event_observed_A=a[args.event], event_observed_B=b[args.event])
    return base_payload(args, "KM生存曲线+Logrank检验", {
        "sample_size": len(df),
        "groups": [str(g) for g in groups],
        "median_surv": summaries,
        "raw_p": safe_float(lr.p_value),
    })


def run_cox(args, df, covariates: list[str]):
    import pandas as pd
    from lifelines import CoxPHFitter

    x_df = pd.get_dummies(df[[args.x] + covariates], drop_first=True, dtype=float)
    cox_df = pd.concat([df[[args.time, args.event]].astype(float), x_df], axis=1)
    fitter = CoxPHFitter()
    fitter.fit(cox_df, duration_col=args.time, event_col=args.event, show_progress=False)
    summary = fitter.summary
    return base_payload(args, "Cox比例风险回归", {
        "sample_size": len(cox_df),
        "hr": {k: safe_float(v) for k, v in summary["exp(coef)"].items()},
        "hr_ci_low": {k: safe_float(v) for k, v in summary["exp(coef) lower 95%"].items()},
        "hr_ci_high": {k: safe_float(v) for k, v in summary["exp(coef) upper 95%"].items()},
        "raw_p": {k: safe_float(v) for k, v in summary["p"].items()},
    })


def describe_series(series):
    if series.nunique(dropna=True) <= 12:
        freq = series.value_counts(dropna=False)
        pct = series.value_counts(normalize=True, dropna=False).mul(100).round(2)
        return {"type": "categorical", "freq": freq.to_dict(), "percent": pct.to_dict(), "valid_n": int(series.notna().sum())}
    return {
        "type": "continuous",
        "mean": safe_float(series.mean()),
        "median": safe_float(series.median()),
        "std": safe_float(series.std()),
        "q1": safe_float(series.quantile(0.25)),
        "q3": safe_float(series.quantile(0.75)),
        "valid_n": int(series.notna().sum()),
    }


def run_explore(args):
    df = load_table(args.data)
    vars_ = parse_vars(args.vars) or list(df.columns)
    ensure_columns(df, vars_)
    if args.desc:
        return base_payload(args, "批量描述性统计", {"total_sample": len(df), "detail": {v: describe_series(df[v]) for v in vars_}})
    corr = df[vars_].dropna().corr(method=args.method).round(4)
    return base_payload(args, f"{args.method}相关矩阵", {"valid_sample": len(df[vars_].dropna()), "correlation_table": corr.to_dict()})


def collect_p_values(value: Any) -> list[float]:
    if isinstance(value, dict):
        values = [item for key, item in value.items() if key in {"raw_p", "p_value"}]
        nested = [item for item in value.values() if isinstance(item, (dict, list))]
        return [p for item in values + nested for p in collect_p_values(item)]
    if isinstance(value, list):
        return [p for item in value for p in collect_p_values(item)]
    p = safe_float(value)
    return [p] if p is not None and 0 <= p <= 1 else []


def run_correct(args):
    from statsmodels.stats.multitest import multipletests

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    p_values = collect_p_values(payload)
    if not p_values:
        raise ValueError("未找到可校正的p值")
    method = CORR_METHOD_MAP.get(args.method.lower())
    if not method:
        raise ValueError("多重校正方法仅支持 BH、Bonferroni、Sidak")
    _, adj_p, _, _ = multipletests(p_values, alpha=ALPHA, method=method)
    return {"status": "success", "method": args.method, "raw_p": p_values, "adj_p": [safe_float(v) for v in adj_p]}


def run_interpret(args):
    payload = json.loads(Path(args.res).read_text(encoding="utf-8"))
    method = payload.get("method", "统计分析")
    result = payload.get("result", payload)
    raw_p = result.get("raw_p")
    conclusion = "结果达到统计学显著" if isinstance(raw_p, (int, float)) and raw_p < ALPHA else "未观察到明确统计学显著"
    text = "\n".join([
        f"# {method} 解读草稿",
        "",
        f"## 客观统计结论",
        f"- {conclusion}；具体结果以结构化JSON为准。",
        "",
        "## 仅供参考的临床提示",
        "- 该结果不能直接推出医学因果关系，需结合研究设计、混杂控制和数据质量报告复核。",
    ])
    Path(args.out).write_text(text, encoding="utf-8")
    return {"status": "success", "output": args.out}


def add_common(parser):
    parser.add_argument("--data", required=True)
    parser.add_argument("--idea-id")
    parser.add_argument("--out")


def build_parser():
    parser = argparse.ArgumentParser(description="医学统计检验自动化 CLI")
    sub = parser.add_subparsers(dest="command")

    basic = sub.add_parser("stats-basic")
    add_common(basic)
    basic.add_argument("--x", required=True)
    basic.add_argument("--y", required=True)
    for flag in ["t-test", "wilcoxon", "chi2", "fisher", "corr-pearson", "corr-spearman"]:
        basic.add_argument(f"--{flag}", action="store_true", dest=flag.replace("-", "_"))

    reg = sub.add_parser("stats-regression")
    add_common(reg)
    reg.add_argument("--x", required=True)
    reg.add_argument("--y", required=True)
    reg.add_argument("--covariates")
    reg.add_argument("--linear", action="store_true")
    reg.add_argument("--logistic", action="store_true")

    surv = sub.add_parser("stats-survival")
    add_common(surv)
    surv.add_argument("--x", required=True)
    surv.add_argument("--time", required=True)
    surv.add_argument("--event", required=True)
    surv.add_argument("--covariates")
    surv.add_argument("--km", action="store_true")
    surv.add_argument("--logrank", action="store_true")
    surv.add_argument("--cox", action="store_true")

    explore = sub.add_parser("stats-explore")
    add_common(explore)
    explore.add_argument("--vars")
    explore.add_argument("--desc", action="store_true")
    explore.add_argument("--corr-matrix", action="store_true")
    explore.add_argument("--method", choices=["pearson", "spearman"], default="pearson")

    correct = sub.add_parser("stats-correct")
    correct.add_argument("--input", required=True)
    correct.add_argument("--method", default="BH")
    correct.add_argument("--out")

    interpret = sub.add_parser("stats-interpret")
    interpret.add_argument("--res", required=True)
    interpret.add_argument("--out", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    runners = {
        "stats-basic": run_basic,
        "stats-regression": run_regression,
        "stats-survival": run_survival,
        "stats-explore": run_explore,
        "stats-correct": run_correct,
        "stats-interpret": run_interpret,
    }
    try:
        payload = runners[args.command](args)
        write_output(payload, getattr(args, "out", None))
        return 0
    except Exception as exc:
        write_output({"status": "failed", "error": str(exc)}, getattr(args, "out", None))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
