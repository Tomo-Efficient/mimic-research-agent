# 依赖安装命令：pip install pandas scipy statsmodels lifelines pingouin scikit-learn
"""
文件模块分段（用# ====== 分割）：
1. 全局导入 & 包依赖
2. 全局配置常量 CONFIG
3. 自定义业务异常类 EXCEPTIONS
4. 通用工具1：分析子集构建（MIMIC宽表提取）
5. 通用工具2：变量类型自动识别
6. 通用工具3：全套前置统计校验函数
7. 通用工具4：方法选择规则引擎（无LLM硬编码）
8. 通用工具5：后置校验 + 统一结果标准化提取
9. 统计方法组1：基础检验（t/秩和/卡方/Fisher/相关）
10. 统计方法组2：回归分析（线性、Logistic）
11. 统计方法组3：生存分析（KM Logrank / CoxPH）
12. 统计方法组4：探索分析（描述统计、相关矩阵）
13. 顶层流水线入口 run_single_idea（唯一对外调用函数）
"""

# print("start")

# ===============================
# Import
# ===============================
import pandas as pd
import numpy as np
import pingouin as pg
from scipy.stats import chi2_contingency, ttest_ind, mannwhitneyu, pearsonr, spearmanr, fisher_exact
import statsmodels.api as sm
import statsmodels.stats.multitest as multi
import statsmodels.stats.outliers_influence as oi
from lifelines import KaplanMeierFitter, CoxPHFitter
from lifelines.statistics import logrank_test

# print("import complete")

# ===============================
# Config 全局常量配置
# ===============================
ALPHA = 0.05
MIN_CONT_SAMPLE = 30
MIN_CAT_PER_GROUP = 5
MAX_COV_MISS = 0.2
MAX_OUTCOME_EXP_MISS = 0.3
VIF_THRESHOLD = 10
CORR_METHOD_DEFAULT = "bh"

# print("config complete")

# ===============================
# Exceptions 自定义异常类
# ===============================

class DatasetBuildError(Exception):
    """构建分析子集失败：变量不存在/全部缺失"""
    pass

class SampleSizeNotEnoughError(Exception):
    """样本量低于预设最低阈值"""
    pass

class AssumptionViolationError(Exception):
    """统计前提检验不通过，需自动切换备选方法"""
    pass

class ModelConvergeError(Exception):
    """回归/生存模型拟合不收敛"""
    pass

class UnmatchedMethodError(Exception):
    """规则引擎无匹配预定义统计方法，触发LLM兜底流程"""
    pass

# print("exception complete")

# ===============================
# [utils]
# 1. dataset_builder 分析子集构建
# ===============================
def build_analysis_subset(
    full_wide_df: pd.DataFrame,
    exposure_vars: list[str],
    outcome_vars: list[str],
    cov_vars: list[str] = None
) -> tuple[pd.DataFrame, dict]:
    """
    MIMIC宽表提取单idea专属分析子集
    仅做列筛选、核心缺失行剔除、分类变量格式标准化
    深度清洗由Skill1完成，本函数不做插补/异常处理
    """
    cov_vars = cov_vars if cov_vars is not None else []
    all_used_cols = exposure_vars + outcome_vars + cov_vars
    exist_cols = [col for col in all_used_cols if col in full_wide_df.columns]
    missing_cols = list(set(all_used_cols) - set(exist_cols))
    if len(missing_cols) > 0:
        raise DatasetBuildError(f"输入变量不存在：{missing_cols}")
    subset_df = full_wide_df[exist_cols].copy()
    # 剔除暴露、结局全部缺失的样本
    key_cols = exposure_vars + outcome_vars
    subset_df = subset_df.dropna(subset=key_cols)
    total_valid = len(subset_df)
    if total_valid == 0:
        raise DatasetBuildError("暴露/结局变量全部缺失，无有效分析样本")
    # 缺失率统计
    miss_stats = {}
    for col in exist_cols:
        miss_rate = subset_df[col].isna().sum() / len(subset_df)
        miss_stats[col] = round(miss_rate, 4)
    # 校验核心变量缺失阈值
    for var in exposure_vars + outcome_vars:
        if miss_stats[var] > MAX_OUTCOME_EXP_MISS:
            raise DatasetBuildError(f"核心变量{var}缺失率{miss_stats[var]}，超过阈值{MAX_OUTCOME_EXP_MISS}")
    for var in cov_vars:
        if miss_stats[var] > MAX_COV_MISS:
            raise DatasetBuildError(f"协变量{var}缺失率{miss_stats[var]}，超过阈值{MAX_COV_MISS}")
    # 统一分类变量类型
    for col in subset_df.columns:
        if subset_df[col].nunique() <= 10 and subset_df[col].dtype in ("int64", "object"):
            subset_df[col] = subset_df[col].astype("category")
    stat_res = {"valid_sample_num": total_valid, "missing_rate": miss_stats}
    return subset_df, stat_res

# print("utils 1 complete")

# ===============================
# [utils]
# 2. var_identifier 变量类型识别
# ===============================
def identify_var_type(df: pd.DataFrame, var_name: str) -> str:
    """识别单变量：binary / multi_cat / continuous / time_event"""
    ser = df[var_name].dropna()
    unique_vals = ser.nunique()
    # 时间事件判定
    if any(keyword in var_name.lower() for keyword in ["time", "day", "hour", "followup"]):
        if pd.api.types.is_float_dtype(ser):
            return "time_event"
    # 二分类
    if unique_vals == 2:
        return "binary"
    # 多分类
    elif unique_vals >= 3 and unique_vals <= 12 and not pd.api.types.is_float_dtype(ser):
        return "multi_cat"
    # 连续变量
    else:
        return "continuous"

def batch_identify_vars(df: pd.DataFrame, var_list: list[str]) -> dict:
    """批量调用，识别一组变量，返回 {变量名: 类型}"""
    res = {}
    for v in var_list:
        res[v] = identify_var_type(df, v)
    return res

def validate_var_type_annotation(df: pd.DataFrame, annotation_dict: dict) -> tuple[bool, dict, list]:
    """
    复核LLM传入的变量类型标注
    输入：annotation_dict = {变量名: 类型标签}，类型可选：binary/multi_cat/continuous/time_event
    返回：(是否全部通过, 修正后的类型字典, 异常提示列表)
    """
    corrected = annotation_dict.copy()
    warnings = []
    all_pass = True
    
    for var, label in annotation_dict.items():
        if var not in df.columns:
            warnings.append(f"变量{var}不存在于数据集中")
            all_pass = False
            continue
        
        ser = df[var].dropna()
        unique_num = ser.nunique()
        is_numeric = pd.api.types.is_numeric_dtype(ser)
        
        # 数据侧客观判断
        data_label = "continuous"
        if unique_num == 2:
            data_label = "binary"
        elif 3 <= unique_num <= 12 and not is_numeric:
            data_label = "multi_cat"
        
        # 时间事件类型单独校验
        if label == "time_event":
            if not is_numeric or (ser < 0).any():
                warnings.append(f"变量{var}标注为time_event，但取值非全正数/非数值型")
                all_pass = False
                corrected[var] = data_label
        else:
            if label != data_label:
                warnings.append(f"变量{var}标注为{label}，数据特征匹配为{data_label}")
                all_pass = False
                corrected[var] = data_label
    
    return all_pass, corrected, warnings

# print("u 2 ok")

# ===============================
# [utils]
# 3. pre_checks 前置统计校验
# ===============================
def check_sample_size(df: pd.DataFrame, group_var=None) -> None:
    """校验总样本与分组最小样本量"""
    total = len(df)
    if total < MIN_CONT_SAMPLE:
        raise SampleSizeNotEnoughError(f"总样本{total} < 最低阈值{MIN_CONT_SAMPLE}")
    if group_var is not None:
        group_count = df[group_var].value_counts()
        for g, cnt in group_count.items():
            if cnt < MIN_CAT_PER_GROUP:
                raise SampleSizeNotEnoughError(f"分组{g}样本{cnt} < {MIN_CAT_PER_GROUP}")

def test_normality(df: pd.DataFrame, cont_var: str) -> float:
    """Shapiro-Wilk正态检验，返回p值"""
    data = df[cont_var].dropna()
    res = pg.normality(data)
    p_val = res["pval"].iloc[0]
    return p_val

def test_levene(df: pd.DataFrame, cont_var: str, group_var: str) -> float:
    """Levene方差齐性检验，返回p值"""
    res = pg.levene(data=df, dv=cont_var, group=group_var)
    return res["pval"].iloc[0]

def calc_vif(df, cov_list):
    """VIF多重共线性计算"""
    X = df[cov_list].dropna()
    vif_arr = oi.variance_inflation_factor(X.values)
    vif_dict = dict(zip(cov_list, vif_arr))
    return vif_dict

def check_chi2_freq(df, cat1, cat2):
    """卡方检验期望最小频数"""
    ct = pd.crosstab(df[cat1], df[cat2])
    _, _, exp_mat, _ = chi2_contingency(ct)
    min_exp = exp_mat.min()
    return min_exp

def check_survival_basic(df, time_var, event_var):
    """
    生存分析前置基础校验：时间非负、事件二分类、删失率合理
    校验不通过直接抛出异常
    """
    # 校验时间非负
    if (df[time_var].dropna() < 0).any():
        raise AssumptionViolationError("生存时间变量存在负值，数据异常")
    # 校验事件为0-1二分类
    event_vals = df[event_var].dropna().unique()
    if not set(event_vals).issubset({0, 1}):
        raise AssumptionViolationError(f"事件变量取值{event_vals}，非标准0-1二分类")
    # 校验删失率
    event_rate = df[event_var].dropna().mean()
    censoring_rate = 1 - event_rate
    if not (0.05 <= censoring_rate <= 0.8):
        raise AssumptionViolationError(f"删失率{censoring_rate:.1%}超出合理范围（5%~80%）")
    return {"event_rate": round(event_rate, 4), "censoring_rate": round(censoring_rate, 4)}

# print("u 3 ok")

# ===============================
# [utils]
# 4. method_selector 方法选择规则引擎
# ===============================
def match_stat_method(outcome_type: str, exp_type: str, has_cov: bool, is_survival: bool):
    """
    硬编码规则匹配主方法、备选方法
    返回 (主方法标识, 备选方法标识)
    """
    # 生存结局优先判断
    if is_survival:
        if has_cov:
            return "cox_ph", None
        else:
            return "km_logrank", None
    # 二分类结局
    if outcome_type == "binary":
        if has_cov:
            return "logistic_reg", None
        else:
            if exp_type in ("binary", "multi_cat"):
                return "chi2", "fisher"
            else:
                return "corr_spearman", None
    # 连续结局
    if outcome_type == "continuous":
        if has_cov:
            return "linear_reg", None
        else:
            if exp_type in ("binary", "multi_cat"):
                return "ttest", "wilcoxon"
            else:
                return "corr_pearson", "corr_spearman"
    # MVP暂不支持有序多分类，归入兜底
    return None, None

# print("u 4 ok")

# ===============================
# [utils]
# 5. post_process 后置校验 + 统一结果标准化提取
# ===============================
def multiple_correction(p_value_list: list[float], method=CORR_METHOD_DEFAULT) -> list[float]:
    """批量p值多重校正，默认BH法"""
    reject, adj_p, _, _ = multi.multipletests(p_value_list, alpha=ALPHA, method=method)
    return adj_p.tolist()

def numeric_validate(res_dict: dict):
    """通用数值合理性校验：p值、CI区间"""
    if "p_value" in res_dict:
        p = res_dict["p_value"]
        if not (0 <= p <= 1):
            raise ValueError(f"非法p值：{p}，必须0~1之间")
    if "ci_low" in res_dict and "ci_high" in res_dict:
        if res_dict["ci_low"] > res_dict["ci_high"]:
            raise ValueError("置信区间下限大于上限，数值异常")

def standardize_basic_test(output, method_name: str, sample_n: int):
    """基础检验(t/卡方/相关)统一格式化输出"""
    res = {"method": method_name, "sample_size": sample_n}
    if isinstance(output, pd.DataFrame):
        res["p_value"] = float(output["pval"].iloc[0])
        if "cohen_d" in output.columns:
            res["effect_size"] = float(output["cohen_d"].iloc[0])
        if "CI95%" in output.columns:
            res["ci_low"] = float(output["CI95%"].iloc[0][0])
            res["ci_high"] = float(output["CI95%"].iloc[0][1])
    elif hasattr(output, "pvalue"):
        res["p_value"] = float(output.pvalue)
        if hasattr(output, "statistic"):
            res["statistic"] = float(output.statistic)
    numeric_validate(res)
    return res

def standardize_reg(result_model, method_name: str, sample_n: int):
    """线性/Logistic回归统一格式化输出"""
    res = {"method": method_name, "sample_size": sample_n}
    params = result_model.params
    conf = result_model.conf_int()
    p_vals = result.pvalues
    res["coef_dict"] = {}
    for var in params.index:
        item = {
            "coef": float(params[var]),
            "ci_low": float(conf.loc[var, 0]),
            "ci_high": float(conf.loc[var, 1]),
            "p_value": float(p_vals[var])
        }
        res["coef_dict"][var] = item
    res["aic"] = float(result_model.aic)
    # 校验第一个变量数值
    first_key = list(res["coef_dict"].keys())[0]
    numeric_validate(res["coef_dict"][first_key])
    return res

def standardize_surv(fitter_obj, method_name: str, sample_n: int):
    """生存分析（KM/Cox）统一格式化输出"""
    res = {"method": method_name, "sample_size": sample_n}
    if hasattr(fitter_obj, "summary"):
        summ = fitter.summary
        res["hr"] = summ["exp(coef)"].to_dict()
        res["hr_ci_low"] = summ["lower 0.95"].to_dict()
        res["hr_ci_high"] = summ["upper 0.95"].to_dict()
        res["p_value"] = summ["p"].to_dict()
    # 校验首项p值
    first_p = list(res["p_value"].values())[0]
    numeric_validate({"p_value": first_p})
    return res

# print("u 5 ok")

# ===============================
# [stats_methods]
# 1. basic_tests
# ===============================
def run_ttest(df, exp_bin, cont_out):
    p_norm = test_norm(df, cont_out)
    if p_norm < ALPHA:
        raise AssumptionViolationError("不满足正态假设，自动切换Wilcoxon")
    p_levene = test_levene(df, cont_out, exp_bin)
    eq_var = p_levene >= ALPHA
    out = pg.ttest(data=df, dv=cont_out, group=exp_bin, equal_var=eq_var)
    return standardize_basic_test(out, "独立样本t检验", len(df))

def run_wilcoxon(df, exp_bin, cont_out):
    x1 = df[df[exp_bin]==0][cont_out].dropna()
    x2 = df[df[exp_bin]==1][cont_out].dropna()
    stat, p = mannwhitneyu(x1, x2)
    out = pd.DataFrame({"statistic":stat, "pval":p}, index=[0])
    return standardize_basic_test(out, "Wilcoxon秩和检验", len(df))

def run_chi2(df, cat_exp, cat_out):
    min_exp = check_chi2_freq(df, cat_exp, cat_out)
    if min_exp < MIN_CAT_PER_GROUP:
        raise AssumptionViolationError("单元格期望频数不足，切换Fisher精确检验")
    ct = pd.crosstab(df[cat_exp], df[cat_out])
    stat, p, _, _ = chi2_contingency(ct)
    out = pd.DataFrame({"statistic":stat, "pval":p}, index=[0])
    return standardize_basic_test(out, "卡方检验", len(df))

def run_fisher(df, cat_exp, cat_out):
    ct = pd.crosstab(df[cat_exp], df[cat_out])
    odds, p = fisher_exact(ct)
    out = pd.DataFrame({"statistic":odds, "pval":p}, index=[0])
    return standardize_basic_test(out, "Fisher精确检验", len(df))

def run_corr_pearson(df, x, y):
    out = pg.corr(df[x], df[y], method="pearson")
    return standardize_basic_test(out, "Pearson相关分析", len(df))

def run_corr_spearman(df, x, y):
    out = pg.corr(df[x], df[y], method="spearman")
    return standardize_basic_test(out, "Spearman相关分析", len(df))

# print("s 1 ok")

# ===============================
# [stats_methods]
# 2. regression
# ===============================
def run_logistic(df, outcome_bin, exp_var, cov_list=None):
    cov_list = cov_list if cov_list is not None else []
    x_cols = [exp_var] + cov_list
    sub_df = df[x_cols + [outcome_bin]].dropna()
    X = sm.add_constant(sub_df[x_cols])
    y = sub_df[outcome_bin]
    model = sm.Logit(y, X)
    result = model.fit(disp=0)
    if not result.converged:
        raise ModelConvergeError("Logistic回归模型无法收敛")
    vif_dict = calc_vif(X.drop("const", axis=1))
    std_res = standardize_reg(result, "多因素Logistic回归", len(sub_df))
    std_res["vif_info"] = vif_dict
    return std_res

def run_linear(df, outcome_cont, exp_var, cov_list=None):
    cov_list = cov_list if cov_list is not None else []
    x_cols = [exp_var] + cov_list
    sub_df = df[x_cols + [outcome_cont]].dropna()
    X = sm.add_constant(sub_df[x_cols])
    y = sub_df[outcome_cont]
    model = sm.OLS(y, X)
    result = model.fit()
    vif_dict = calc_vif(X.drop("const", axis=1))
    std_res = standardize_reg(result, "多因素线性回归", len(sub_df))
    std_res["vif_info"] = vif_dict
    return std_res

# print("s 2 ok")

# ===============================
# [stats_methods]
# 3. survival
# ===============================
def run_km_logrank(df, time_var, event_var, group_var):
    kmf = KaplanMeierFitter()
    km_res = {}
    for g in df[group_var].unique():
        sub = df[df[group_var]==g]
        kmf.fit(sub[time_var], event_observed=sub[event_var], label=str(g))
        km_res[str(g)] = kmf.summary.to_dict()
    lr_res = logrank_test(
        durations_A=df[df[group_var]==0][time_var],
        event_observed_A=df[df[group_var]==0][event_var],
        durations_B=df[df[group_var]==1][time_var],
        event_observed_B=df[df[group_var]==1][event_var]
    )
    raw_out = {"logrank_p": lr_res.p_value, "km_summary": km_res}
    return standardize_surv(raw_out, "KM生存曲线+Logrank检验", len(df))

def run_cox(df, time_var, event_var, exp_var, cov_list=None):
    cov_list = cov_list if cov_list is not None else []
    cols = [time_var, event_var, exp_var] + cov_list
    sub_df = df[cols].dropna()
    cph = CoxPHFitter(sub_df)
    fit_res = cph.fit(duration_col=time_var, event_col=event_var, show_progress=False)
    # 比例风险假设后置校验
    ph_check = cph.check_assumptions(p_value_threshold=ALPHA, show_plots=False)
    std_res = standardize_surv(fit_res, "Cox比例风险回归", len(sub_df))
    std_res["ph_assumption_check"] = ph_check
    return std_res

# print("s 3 ok")

# ===============================
# [stats_methods]
# 4. explore
# ===============================
def run_descriptive_stats(df, var_list):
    """批量描述统计（基线表）"""
    desc_result = {}
    total_n = len(df)
    for var in var_list:
        ser = df[var].dropna()
        unique_num = ser.nunique()
        if unique_num <= 10:
            freq = ser.value_counts()
            pct = (ser.value_counts(normalize=True)*100).round(2)
            desc_result[var] = {
                "type": "categorical",
                "freq": freq.to_dict(),
                "percent": pct.to_dict(),
                "valid_n": len(ser)
            }
        else:
            desc_result[var] = {
                "type": "continuous",
                "mean": round(ser.mean(),3),
                "median": round(ser.median(),3),
                "std": round(ser.std(),3),
                "q1": round(ser.quantile(0.25),3),
                "q3": round(ser.quantile(0.75),3),
                "valid_n": len(ser)
            }
    return {
        "method": "批量描述性统计",
        "total_sample": total_n,
        "detail": desc_result
    }

def run_corr_matrix(df, var_list, method="pearson"):
    """全变量相关矩阵+批量p值"""
    sub_df = df[var_list].dropna()
    corr_mat = sub_df.corr(method).round(4)
    p_dict = {}
    for c1 in var_list:
        p_dict[c1] = {}
        for c2 in var_list:
            if c1 == c2:
                p_dict[c1][c2] = 1.0
                continue
            if method == "pearson":
                _, p = pearsonr(sub_df[c1], sub_df[c2])
            else:
                _, p = spearmanr(sub_df[c1])
            p_dict[c1][c2] = round(p,4)
    return {
        "method": f"{method}相关矩阵",
        "correlation_table": corr_mat.to_dict(),
        "p_value_table": p_dict,
        "valid_sample": len(sub_df)
    }

# print("s 4 ok")

# ===============================
# pipeline for single idea
# ===============================
def run_single_idea(
    full_wide_df: pd.DataFrame,
    exposure: list[str],
    outcome: list[str],
    covariates: list[str] = None,
    var_type_annotation: dict = None,
    need_correction: bool = False
) -> tuple[dict, dict]:
    """
    Skill3对外统一调用入口
    入参：
        full_wide_df: Skill1输出MIMIC患者级清洗宽表
        exposure: 暴露变量列表（单变量为主）
        outcome: 结局变量列表；生存分析为[时间变量, 事件变量]
        covariates: 协变量，无则传[]
        var_type_annotation: LLM预标注的变量类型字典（可选）
        need_correction: 是否开启批量多重比较校正
    返回：
        stat_result: 标准化统计结果字典
        run_log: 全流程执行日志（用于上层展示/调试）
    """
    covariates = covariates if covariates is not None else []
    run_log = {"step_records": [], "sample_info": {}, "method_info": {}}
    exp_var = exposure[0]
    out_var = outcome[0]

    # 步骤1：构建分析子集
    run_log["step_records"].append("步骤1：提取idea专属分析子集")
    sub_df, miss_stat = build_analysis_subset(full_wide_df, exposure, outcome, covariates)
    run_log["sample_info"] = miss_stat

    # 步骤2：变量类型识别与复核（优先外部标注，无标注则自动识别）
    run_log["step_records"].append("步骤2：变量类型识别与复核")
    all_vars = exposure + outcome + covariates
    if var_type_annotation is not None:
        pass_flag, var_type_map, warn_list = validate_var_type_annotation(sub_df, var_type_annotation)
        run_log["var_type_warnings"] = warn_list
        if not pass_flag:
            run_log["step_records"].append("外部变量标注复核不通过，已按数据特征修正")
    else:
        var_type_map = batch_identify_vars(sub_df, all_vars)
    out_type = var_type_map[out_var]
    exp_type = var_type_map[exp_var]
    has_cov = len(covariates) > 0
    is_survival = True if out_type == "time_event" else False

    # 步骤3：规则引擎匹配主/备选方法
    run_log["step_records"].append("步骤3：规则引擎匹配统计方法")
    main_m, alt_m = match_stat_method(out_type, exp_type, has_cov, is_survival)
    if main_m is None:
        raise UnmatchedMethodError(f"无匹配预定义方法，结局{out_type}，暴露{exp_type}")
    run_log["method_info"]["main_method"] = main_m
    run_log["method_info"]["alt_method"] = alt_m

    # 步骤4：前置基础样本校验
    run_log["step_records"].append("步骤4：执行前置样本量校验")
    group_check_var = exp_var if exp_type in ["binary", "multi_cat"] else None
    check_sample_size(sub_df, group_var=group_check_var)

    # 生存分析专属前置校验
    if is_survival:
        run_log["step_records"].append("执行生存分析基础前置校验")
        event_var = outcome[1] if len(outcome) > 1 else exp_var
        surv_check_res = check_survival_basic(sub_df, out_var, event_var)
        run_log["survival_check"] = surv_check_res

    # 步骤5：前置假设校验 & 自动切换备选方法
    switch_method = None
    if main_m in ["ttest", "linear_reg"]:
        pn = test_normality(sub_df, out_var)
        pl = test_levene(sub_df, out_var, exp_var)
        if pn < ALPHA or pl < ALPHA:
            switch_method = alt_m
    if main_m == "chi2":
        mf = check_chi2_freq(sub_df, exp_var, out_var)
        if mf < MIN_CAT_PER_GROUP:
            switch_method = "fisher"
    if switch_method is not None:
        run_log["step_records"].append(f"前置校验不通过，切换方法 {main_m} → {switch_method}")
        main_m = switch_method

    # 步骤6：分发执行对应统计函数
    run_log["step_records"].append("步骤5：执行预定义统计方法")
    stat_result = {}
    if main_m == "ttest":
        stat_result = run_ttest(sub_df, exp_var, out_var)
    elif main_m == "wilcoxon":
        stat_result = run_wilcoxon(sub_df, exp_var, out_var)
    elif main_m == "chi2":
        stat_result = run_chi2(sub_df, exp_var, out_var)
    elif main_m == "fisher":
        stat_result = run_fisher(sub_df, exp_var, out_var)
    elif main_m == "corr_pearson":
        stat_result = run_corr_pearson(sub_df, exp_var, out_var)
    elif main_m == "corr_spearman":
        stat_result = run_corr_spearman(sub_df, exp_var, out_var)
    elif main_m == "logistic_reg":
        stat_result = run_logistic(sub_df, out_var, exp_var, covariates)
    elif main_m == "linear_reg":
        stat_result = run_linear(sub_df, out_var, exp_var, covariates)
    elif main_m == "km_logrank":
        event_var = outcome[1] if len(outcome) > 1 else exp_var
        stat_result = run_km_logrank(sub_df, out_var, event_var, exp_var)
    elif main_m == "cox_ph":
        event_var = outcome[1] if len(outcome) > 1 else exp_var
        stat_result = run_cox(sub_df, time_var=out_var, event_var=event_var, exp_var=exp_var, cov_list=covariates)
    elif main_m == "desc_stats":
        stat_result = run_descriptive_stats(sub_df, all_vars)
    elif main_m == "corr_matrix":
        stat_result = run_corr_matrix(sub_df, all_vars)

    # 步骤7：通用数值后置校验
    run_log["step_records"].append("步骤6：数值合理性后置校验")
    try:
        numeric_validate(stat_result)
    except ValueError as warn_msg:
        run_log["warning"] = f"数值校验警告：{warn_msg}"

    # 步骤8：批量多重校正（可选）
    if need_correction and "p_value" in stat_result:
        run_log["step_records"].append("步骤7：执行多重比较校正")
        raw_p = [stat_result["p_value"]]
        adj_p_list = multiple_correction(raw_p)
        stat_result["adjusted_p_value"] = adj_p_list[0]

    return stat_result, run_log
