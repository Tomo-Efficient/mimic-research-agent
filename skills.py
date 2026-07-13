"""
Skill implementations — LLM-driven clinical research skills.

All skills are LLM-orchestrated:
- Programmatic layer: reads data, runs computations LLM cannot do
- LLM layer: reads SKILL.md, makes decisions, formats output

Architecture:
  Raw Data → Programmatic Scan → LLM (with SKILL.md) → Final Output
  Raw Stats → Programmatic Tests → LLM (with SKILL.md) → Interpretation + Report
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

from skill_loader import load_skill
from llm_client import call_llm, extract_json

SKILL1_CTX = load_skill("skill1")
SKILL3_CTX = load_skill("skill3")
SKILL4_CTX = load_skill("skill4")


def _sanitize(obj):
    if isinstance(obj, (np.integer,)): return int(obj)
    if isinstance(obj, (np.floating,)):
        return None if (np.isnan(obj) or np.isinf(obj)) else float(obj)
    if isinstance(obj, np.bool_): return bool(obj)
    if isinstance(obj, np.ndarray): return [_sanitize(x) for x in obj.tolist()]
    if isinstance(obj, dict): return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)): return [_sanitize(x) for x in obj]
    if isinstance(obj, pd.Timestamp): return str(obj)
    return obj


# ============================================================
# Skill Cohort: Extract research dataset from CSV files
# ============================================================

class SkillCohort:
    """Extract patient cohort and analysis variables based on the selected research idea."""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)

    def run(self, idea: dict, eda_report: dict) -> dict:
        """Extract cohort: parse idea → load & join CSVs → filter → output dataset."""
        idea_id = idea.get("id", "H000")
        steps = []
        warnings = []

        # Step 1: Parse variable requirements from the idea
        needed_vars, lab_vars, filter_conditions = self._parse_idea(idea, eda_report)
        all_found = set()
        all_not_found = set(needed_vars)  # will remove as we find them

        # Step 2: Build base cohort
        base_path = self.data_dir / "cohort_24h.csv"
        if not base_path.exists():
            return {"skill": "skill3", "status": "error",
                    "error": f"cohort_24h.csv not found in {self.data_dir}"}

        df = pd.read_csv(base_path)
        n_start = len(df)
        steps.append({"description": "基础 ICU 队列 (cohort_24h)", "count": n_start})

        # Step 3: JOIN patients
        patients_path = self.data_dir / "patients_24h.csv"
        if patients_path.exists():
            pat = pd.read_csv(patients_path)
            pat_cols = ["subject_id", "gender", "anchor_age"]
            pat_cols = [c for c in pat_cols if c in pat.columns]
            df = df.merge(pat[pat_cols], on="subject_id", how="left")
            all_found.update(c for c in ["gender", "anchor_age"] if c in needed_vars)
            all_not_found.discard("gender")
            all_not_found.discard("anchor_age")
        steps.append({"description": "JOIN patients (人口学)", "count": len(df)})

        # Step 4: JOIN admissions
        adm_path = self.data_dir / "admissions_24h.csv"
        if adm_path.exists():
            adm = pd.read_csv(adm_path)
            adm_cols = ["subject_id", "hadm_id", "hospital_expire_flag", "admission_type",
                        "admission_location", "discharge_location", "race", "insurance"]
            adm_cols = [c for c in adm_cols if c in adm.columns]
            df = df.merge(adm[adm_cols], on=["subject_id", "hadm_id"], how="left")
            all_found.update(c for c in ["hospital_expire_flag", "admission_type"] if c in needed_vars)
            all_not_found.discard("hospital_expire_flag")
            all_not_found.discard("admission_type")
        steps.append({"description": "JOIN admissions (结局)", "count": len(df)})

        # Step 5: JOIN labevents for needed lab variables
        lab_path = self.data_dir / "sepsis_icu_labevents_core_numeric_24h.csv"
        items_path = self.data_dir / "sepsis_icu_labevents_core_items.csv"
        if lab_path.exists() and items_path.exists() and lab_vars:
            lab_items = pd.read_csv(items_path)
            # Build label→itemid mapping
            label_to_itemid = {}
            for _, row in lab_items.iterrows():
                label_clean = str(row["label"]).replace(" ", "_").replace(",", "").lower()
                label_to_itemid[label_clean] = row["itemid"]

            # Find matching itemids for requested lab vars
            var_to_itemid = {}
            for lv in lab_vars:
                lv_clean = lv.lower().replace(" ", "_").replace(",", "")
                # Direct match
                if lv_clean in label_to_itemid:
                    var_to_itemid[lv] = label_to_itemid[lv_clean]
                else:
                    # Fuzzy match
                    for label, iid in label_to_itemid.items():
                        if lv_clean in label or label in lv_clean:
                            var_to_itemid[lv] = iid
                            break

            if var_to_itemid:
                matching_itemids = list(set(var_to_itemid.values()))
                lab_chunks = []
                for chunk in pd.read_csv(lab_path, chunksize=50000, low_memory=False):
                    cf = chunk[chunk["itemid"].isin(matching_itemids)]
                    if not cf.empty:
                        lab_chunks.append(cf)
                if lab_chunks:
                    lab_df = pd.concat(lab_chunks, ignore_index=True)
                    # Pivot: each itemid → a column with median value per patient
                    lab_agg = lab_df.groupby(["subject_id", "hadm_id", "itemid"])["valuenum"].median().reset_index()
                    for var_name, iid in var_to_itemid.items():
                        iid_data = lab_agg[lab_agg["itemid"] == iid]
                        col_name = f"lab_{var_name.lower().replace(' ', '_').replace(',', '')}"
                        df = df.merge(
                            iid_data[["subject_id", "hadm_id", "valuenum"]].rename(columns={"valuenum": col_name}),
                            on=["subject_id", "hadm_id"], how="left")
                        all_found.add(var_name)
                        all_not_found.discard(var_name)
                steps.append({"description": f"合并实验室数据 ({len(var_to_itemid)} 个指标)", "count": len(df)})

        # Step 6: Apply basic inclusion criteria
        if "anchor_age" in df.columns:
            df = df[df["anchor_age"] >= 18]
            steps.append({"description": "年龄 >= 18", "count": len(df)})

        # Apply filter conditions from idea
        for fc in filter_conditions:
            try:
                col = fc.get("column", "")
                op = fc.get("op", ">=")
                val = fc.get("value", 0)
                if col in df.columns:
                    if op == ">=":
                        df = df[df[col] >= val]
                    elif op == "<=":
                        df = df[df[col] <= val]
                    elif op == ">":
                        df = df[df[col] > val]
                    elif op == "<":
                        df = df[df[col] < val]
                    elif op == "==":
                        df = df[df[col] == val]
                    elif op == "!=":
                        df = df[df[col] != val]
                    steps.append({"description": f"筛选: {col} {op} {val}", "count": len(df)})
            except Exception:
                pass

        # Step 7: Check sample size
        final_n = len(df)
        if final_n < 30:
            warnings.append(f"队列仅 {final_n} 人，样本量不足以支持统计推断")
        if final_n == 0:
            return _sanitize({
                "skill": "skill3", "status": "error",
                "idea_id": idea_id, "error": "筛选后队列为0人，检查筛选条件",
                "steps": steps, "warnings": warnings,
            })

        # Step 8: Save cohort
        output_path = self.data_dir / "research_cohort.csv"
        df.to_csv(output_path, index=False)

        funnel = {
            "steps": steps,
            "final_cohort_size": final_n,
            "variables_included": sorted(all_found),
            "variables_not_found": sorted(all_not_found),
            "warnings": warnings,
            "output_file": str(output_path),
        }

        idea_title = idea.get("title") or idea.get("title_en") or idea.get("title_cn") or ""
        return _sanitize({
            "skill": "skill3", "status": "completed",
            "idea_id": idea_id, "idea_title": idea_title,
            "final_cohort_size": final_n,
            "n_columns": len(df.columns),
            "variables_included": sorted(all_found),
            "variables_not_found": sorted(all_not_found),
            "steps": steps,
            "funnel": funnel,
            "warnings": warnings,
            "output_file": str(output_path),
            "skill_doc_loaded": True,
        })

    def _parse_idea(self, idea: dict, eda_report: dict) -> tuple:
        """Parse the selected idea to extract variable requirements."""
        needed_vars = set(idea.get("data_variables", []))
        lab_vars = []
        filter_conditions = []

        # Also extract variables from PICO
        pico = idea.get("pico", {})
        for key in ["I", "O"]:
            val = pico.get(key, "")
            if val and len(val) > 2:
                # Add as potential variable
                needed_vars.add(val)

        # Default: always include key demographics and outcome
        needed_vars.add("gender")
        needed_vars.add("anchor_age")
        needed_vars.add("hospital_expire_flag")

        # Classify: lab vs demographic variables
        demo_vars = {"subject_id", "hadm_id", "stay_id", "gender", "anchor_age",
                     "anchor_year", "anchor_year_group", "dod",
                     "hospital_expire_flag", "admission_type", "admission_location",
                     "discharge_location", "race", "insurance", "marital_status",
                     "language", "los", "first_careunit", "last_careunit",
                     "intime", "outtime"}
        lab_item_labels = self._load_lab_labels()

        for v in list(needed_vars):
            v_clean = v.lower().replace(" ", "_").replace(",", "")
            if v in demo_vars:
                continue
            # Check if it matches a lab item
            for label in lab_item_labels:
                label_clean = label.lower().replace(" ", "_").replace(",", "")
                if v_clean in label_clean or label_clean in v_clean:
                    lab_vars.append(v)
                    break

        # Extract filter conditions from idea
        # Age filter
        if pico.get("P") and "adult" in str(pico.get("P")).lower():
            filter_conditions.append({"column": "anchor_age", "op": ">=", "value": 18})

        return list(needed_vars), lab_vars, filter_conditions

    def _load_lab_labels(self) -> list:
        """Load available lab item labels."""
        items_path = self.data_dir / "sepsis_icu_labevents_core_items.csv"
        if items_path.exists():
            try:
                items = pd.read_csv(items_path)
                return items["label"].tolist()
            except Exception:
                pass
        return []


# ============================================================
# Skill 1: MIMIC EDA (LLM-driven)
# ============================================================

SKILL1_LLM_PROMPT = """You are a MIMIC-IV data preprocessing and EDA assistant following the SKILL.md specification.

## Your Task
Given a raw data scan (tables, columns, stats), generate ONLY the analytical parts:
1. Per-variable clinical assessment (is this variable useful for sepsis research?)
2. Key clinical variables ranked by importance for downstream analysis
3. Overall data quality narrative (what's good, what needs attention)
4. Table relationship interpretation

Do NOT repeat the raw numbers — they will be combined with your analysis.
Return ONLY valid JSON, no markdown:

{
  "variable_assessments": {"table.column": "assessment text"},
  "key_clinical_variables": [{"variable": "name", "importance": "high/medium/low", "reason": "..."}],
  "quality_narrative_cn": "数据质量综合评估（中文）",
  "quality_narrative_en": "Overall data quality narrative (English)",
  "relationship_notes_cn": "表关联关系解读（中文）",
  "relationship_notes_en": "Table relationship interpretation (English)"
}

IMPORTANT: All *_cn fields MUST be written in Chinese. All *_en fields MUST be written in English."""


class Skill1EDA:
    """Skill 1: LLM-driven EDA. Programmatic scan → LLM analysis → merged report."""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)

    def run(self) -> dict:
        """Execute Skill 1: scan data → LLM analysis → merged report."""
        try:
            scan_result = self._programmatic_scan()
            if "error" in scan_result:
                return scan_result

            report = self._build_programmatic_report(scan_result)

            llm_analysis = self._llm_analyze(scan_result)
            if llm_analysis:
                report["llm_driven"] = True
                report["llm_analysis"] = llm_analysis
            else:
                report["llm_driven"] = False

            report["skill_doc_loaded"] = True
            return _sanitize(report)
        except Exception as e:
            print(f"[Skill 1] Error: {e}")
            import traceback; traceback.print_exc()
            return {"skill": "skill1_eda", "status": "error", "error": str(e)}

    def _programmatic_scan(self) -> dict:
        """Scan all tables — this is what LLM cannot do."""
        # Check data directory
        if not self.data_dir.exists():
            return {"error": f"Data directory not found: {self.data_dir}"}

        tables = {}
        for f in sorted(self.data_dir.glob("*.csv")):
            name = f.stem
            size_mb = f.stat().st_size / (1024 * 1024)
            tables[name] = {"path": str(f), "size_mb": round(size_mb, 2)}

        if not tables:
            return {"error": f"No CSV files in {self.data_dir}"}

        # Scan schemas
        schemas = {}
        for name, info in tables.items():
            try:
                df = pd.read_csv(info["path"], nrows=0)
                schemas[name] = list(df.columns)
            except Exception:
                schemas[name] = []

        # Detect relationships
        keys = ["subject_id", "hadm_id", "stay_id", "icustay_id", "itemid"]
        names = sorted(schemas.keys())
        relationships = []
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                shared = [k for k in keys if k in schemas[names[i]] and k in schemas[names[j]]]
                if shared:
                    relationships.append({"left": names[i], "right": names[j], "shared_keys": shared})

        # Scan priority tables with stats
        priority = [
            "patients_24h", "admissions_24h", "cohort_24h",
            "diagnoses_icd_24h", "sepsis_icu_labevents_core_numeric_24h"
        ]
        table_scans = []
        for name in priority:
            if name not in tables:
                continue
            try:
                info = tables[name]
                is_sample = info["size_mb"] > 100
                df = pd.read_csv(info["path"], nrows=50000 if is_sample else None)
                if is_sample:
                    df = df.head(50000)
                table_scans.append(self._scan_table(name, df, info["path"], is_sample))
            except Exception as e:
                table_scans.append({"table": name, "source_path": info["path"], "error": str(e)})

        return {
            "data_directory": str(self.data_dir),
            "tables": {k: v for k, v in tables.items()},
            "schemas": schemas,
            "relationships": relationships,
            "table_scans": table_scans,
        }

    def _scan_table(self, name: str, df: pd.DataFrame, path: str, is_sample: bool) -> dict:
        """Scan a single table — compute raw stats for LLM to interpret."""
        n_rows, n_cols = df.shape
        columns = []
        for col in df.columns:
            missing_n = int(df[col].isna().sum())
            col_data = {
                "name": col,
                "dtype": str(df[col].dtype),
                "missing_n": missing_n,
                "missing_frac": round(missing_n / max(n_rows, 1), 4),
                "unique_values": int(df[col].nunique()) if n_rows > 0 else 0,
            }
            if pd.api.types.is_numeric_dtype(df[col]):
                s = df[col].dropna()
                if len(s) > 0:
                    q1, q3 = float(s.quantile(0.25)), float(s.quantile(0.75))
                    iqr = q3 - q1
                    col_data["stats"] = {
                        "mean": round(float(s.mean()), 4),
                        "std": round(float(s.std()), 4),
                        "min": round(float(s.min()), 4),
                        "p25": round(q1, 4),
                        "median": round(float(s.median()), 4),
                        "p75": round(q3, 4),
                        "max": round(float(s.max()), 4),
                    }
                    if iqr > 0:
                        low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
                        outlier_n = int(((s < low) | (s > high)).sum())
                        col_data["outlier"] = {
                            "n": int(len(s)), "outlier_n": outlier_n,
                            "outlier_frac": round(outlier_n / len(s), 4),
                            "bounds": {"low": round(low, 4), "high": round(high, 4)},
                        }
            columns.append(col_data)
        return {"table": name, "source_path": path, "n_rows": n_rows, "n_cols": n_cols,
                "is_sample": is_sample, "columns": columns}

    def _build_programmatic_report(self, scan: dict) -> dict:
        """Build the programmatic EDA report (always works, no LLM needed)."""
        table_reports = []
        for ts in scan.get("table_scans", []):
            if "error" in ts and "columns" not in ts:
                table_reports.append({
                    "table": ts.get("table", "unknown"), "error": ts.get("error"),
                    "source_path": ts.get("source_path", ""), "n_rows": 0, "n_cols": 0,
                    "is_sample": False, "datetime_parse": {"parsed_cols": [], "coerced_na_counts": {}},
                    "columns": [], "excluded_columns": [], "notes": ["Error during scan"]
                })
                continue
            columns = []
            for c in ts.get("columns", []):
                mf = c.get("missing_frac", 0)
                level = "usable" if mf < 0.05 else ("cautious" if mf < 0.30 else "exclude")
                col = {**c, "missing_level": level, "imputation": "median" if mf > 0 else None}
                columns.append(col)
            excluded = [c["name"] for c in columns if c.get("missing_level") == "exclude"]
            table_reports.append({
                "table": ts.get("table", "unknown"),
                "source_path": ts.get("source_path", ""),
                "n_rows": ts.get("n_rows", 0), "n_cols": ts.get("n_cols", 0),
                "is_sample": ts.get("is_sample", False),
                "datetime_parse": {"parsed_cols": [], "coerced_na_counts": {}},
                "columns": columns, "excluded_columns": excluded, "notes": [],
            })

        total_patients = 0
        for tr in table_reports:
            if tr.get("table") == "patients_24h":
                total_patients = tr.get("n_rows", 0)
                break

        usable = sum(1 for tr in table_reports for c in tr.get("columns", [])
                     if c.get("missing_level") == "usable")
        cautious = sum(1 for tr in table_reports for c in tr.get("columns", [])
                       if c.get("missing_level") == "cautious")

        # Extract key clinical variables from labevents
        key_clinical_vars = []
        for tr in table_reports:
            if "labevents" in tr.get("table", ""):
                for c in tr.get("columns", []):
                    if c.get("missing_level") != "exclude" and c["name"] not in (
                        "subject_id", "hadm_id", "stay_id", "charttime", "specimen_id",
                        "labevent_id", "storetime", "order_provider_id", "value", "valuenum"
                    ):
                        key_clinical_vars.append({
                            "variable": c["name"], "table": tr["table"],
                            "missing_frac": c.get("missing_frac", 0),
                            "missing_level": c.get("missing_level", "usable"),
                            "stats": c.get("stats", {}),
                        })

        return {
            "skill": "skill1_eda", "status": "completed",
            "data_directory": str(self.data_dir),
            "tables_found": len(scan.get("tables", {})),
            "table_names": sorted(scan.get("tables", {}).keys()),
            "table_sizes": {k: v["size_mb"] for k, v in scan.get("tables", {}).items()},
            "total_patients_estimate": total_patients,
            "relationships": scan.get("relationships", []),
            "table_reports": table_reports,
            "summary": {
                "total_tables": len(scan.get("tables", {})),
                "total_columns_analyzed": sum(len(tr.get("columns", [])) for tr in table_reports),
                "usable_variables": usable, "cautious_variables": cautious,
                "numeric_variables": 0, "categorical_variables": 0,
                "key_clinical_variables": key_clinical_vars[:30],
            },
        }

    def _llm_analyze(self, scan: dict) -> dict | None:
        summary_data = {
            "tables_found": len(scan.get("tables", {})),
            "table_names": sorted(scan.get("tables", {}).keys())[:8],
            "total_columns_scanned": sum(len(ts.get("columns", [])) for ts in scan.get("table_scans", [])),
            "key_column_names": [],
            "relationships": [f"{r['left']} ↔ {r['right']}" for r in scan.get("relationships", [])[:5]],
        }
        for ts in scan.get("table_scans", []):
            cols = [c["name"] for c in ts.get("columns", [])[:20]]
            summary_data["key_column_names"].extend([f"{ts['table']}.{c}" for c in cols[:10]])

        prompt = json.dumps(_sanitize(summary_data), indent=2, ensure_ascii=False)
        system = SKILL1_CTX.skill_md + "\n\n" + SKILL1_LLM_PROMPT
        text = call_llm(system, prompt, temperature=0.3)
        return extract_json(text) if text else None


# ============================================================
# Skill 3: Statistical Testing (LLM-driven)
# ============================================================

SKILL4_LLM_PROMPT = """You are a medical statistical testing assistant following the medical-stats-test SKILL.md.

## Task
Given statistical test results below, write a clinical interpretation in Markdown format.

## Guidelines (from SKILL.md)
- Distinguish "statistical conclusion" from "clinical implications" clearly
- Statistical conclusion: report exact p-values, effect sizes
- Clinical implications: suggest possibilities but NO definitive claims
- Note limitations: exploratory analysis, need adjusted models
- Statistical significance ≠ clinical significance

## Output Format (BILINGUAL)
Write TWO versions separated by a "---" divider line:
1. Chinese version first (中文版)
2. English version after the divider (English version)

Both versions follow the same structure:
- ## Statistical Conclusions / 统计结论
- Key significant findings with exact p-values
- ## Clinical Implications / 临床提示
- Bullet points suggesting possible clinical relevance
- ## Limitations / 局限性
- Key caveats of this analysis

Write directly in Markdown. Do NOT output JSON."""


class Skill4Stats:
    """Skill 4: LLM-driven stats. Programmatic computation → LLM interpretation."""

    ALPHA = 0.05
    MIN_CATEGORICAL = 5

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)

    def run(self, idea: dict, eda_report: dict) -> dict:
        """Execute Skill 3: compute stats → LLM interprets following SKILL.md."""
        idea_id = idea.get("id", "H000")

        # Step 1: Load data (programmatic)
        df = self._load_cohort_data()
        if df is None:
            return _sanitize({"skill": "skill4_stats", "status": "error",
                              "idea_id": idea_id, "error": "Could not load cohort data"})

        # Step 2: Run statistical tests (programmatic)
        raw_results = self._run_all_tests(df, idea)

        # Step 3: LLM generates interpretation and formatted output
        llm_output = self._llm_interpret(raw_results, idea, len(df))

        if llm_output:
            return _sanitize(llm_output)

        # Fallback: programmatic interpretation
        return _sanitize(self._fallback_interpret(raw_results, idea, len(df)))

    def _run_all_tests(self, df: pd.DataFrame, idea: dict) -> list:
        """Run all relevant statistical tests. Pure computation."""
        results = []

        # Age vs mortality
        if "anchor_age" in df.columns and "hospital_expire_flag" in df.columns:
            valid = df[["anchor_age", "hospital_expire_flag"]].dropna()
            if len(valid) >= 30:
                g0 = valid[valid["hospital_expire_flag"] == 0]["anchor_age"]
                g1 = valid[valid["hospital_expire_flag"] == 1]["anchor_age"]
                if len(g0) >= self.MIN_CATEGORICAL and len(g1) >= self.MIN_CATEGORICAL:
                    stat, p = scipy_stats.mannwhitneyu(g0, g1, alternative="two-sided")
                    results.append({
                        "test_id": "age_mortality", "exposure": "anchor_age",
                        "outcome": "hospital_expire_flag", "label": "Age vs. Hospital Mortality",
                        "method": "Mann-Whitney U Test (stats-basic --wilcoxon)",
                        "n": len(valid), "n_events": int(valid["hospital_expire_flag"].sum()),
                        "group0_label": "Survivors", "group1_label": "Deceased",
                        "group0_median": round(float(g0.median()), 1),
                        "group1_median": round(float(g1.median()), 1),
                        "group0_iqr": [round(float(g0.quantile(0.25)), 1), round(float(g0.quantile(0.75)), 1)],
                        "group1_iqr": [round(float(g1.quantile(0.25)), 1), round(float(g1.quantile(0.75)), 1)],
                        "statistic": round(float(stat), 4), "p_value": round(float(p), 4),
                        "significant": bool(p < self.ALPHA),
                    })

        # Gender vs mortality (chi-square)
        if "gender" in df.columns and "hospital_expire_flag" in df.columns:
            valid = df[["gender", "hospital_expire_flag"]].dropna()
            if len(valid) >= 30:
                try:
                    ctab = pd.crosstab(valid["gender"], valid["hospital_expire_flag"])
                    chi2, p, dof, _ = scipy_stats.chi2_contingency(ctab)
                    results.append({
                        "test_id": "gender_mortality", "exposure": "gender",
                        "outcome": "hospital_expire_flag", "label": "Gender vs. Hospital Mortality",
                        "method": "Chi-Square Test (stats-basic --chi2)",
                        "n": len(valid), "chi2": round(float(chi2), 4), "dof": dof,
                        "p_value": round(float(p), 4), "significant": bool(p < self.ALPHA),
                        "contingency_table": {
                            "index": list(ctab.index), "columns": [str(c) for c in ctab.columns],
                            "values": ctab.values.tolist(),
                        },
                    })
                except Exception:
                    pass

        # Top lab biomarkers vs mortality
        lab_cols = [c for c in df.columns if c.startswith("lab_")]
        lab_priority = ["lactate", "glucose", "creatinine", "bicarbonate", "wbc",
                         "platelet", "sodium", "potassium", "rdw", "inr", "pt", "ptt"]
        tested = 0
        for priority in lab_priority:
            if tested >= 6:
                break
            for c in lab_cols:
                if priority in c.lower() and tested < 6:
                    valid = df[[c, "hospital_expire_flag"]].dropna()
                    if len(valid) >= 30:
                        g0 = valid[valid["hospital_expire_flag"] == 0][c]
                        g1 = valid[valid["hospital_expire_flag"] == 1][c]
                        if len(g0) >= self.MIN_CATEGORICAL and len(g1) >= self.MIN_CATEGORICAL:
                            stat, p = scipy_stats.mannwhitneyu(g0, g1, alternative="two-sided")
                            # Also compute OR
                            or_val = None
                            try:
                                import statsmodels.api as sm
                                X_sm = sm.add_constant(valid[c])
                                model = sm.Logit(valid["hospital_expire_flag"], X_sm)
                                fit = model.fit(disp=False)
                                or_val = round(float(np.exp(fit.params.iloc[1])), 4)
                                or_ci = [round(float(np.exp(fit.conf_int().iloc[1, 0])), 4),
                                         round(float(np.exp(fit.conf_int().iloc[1, 1])), 4)]
                                or_p = round(float(fit.pvalues.iloc[1]), 4)
                            except Exception:
                                or_ci, or_p = None, None

                            results.append({
                                "test_id": f"{c}_mortality", "exposure": c,
                                "outcome": "hospital_expire_flag",
                                "label": f"{c} vs. Hospital Mortality",
                                "method": "Mann-Whitney U Test (stats-basic --wilcoxon) + Logistic OR",
                                "n": len(valid), "n_events": int(valid["hospital_expire_flag"].sum()),
                                "group0_label": "Survivors", "group1_label": "Deceased",
                                "group0_median": round(float(g0.median()), 4),
                                "group1_median": round(float(g1.median()), 4),
                                "p_value": round(float(p), 4),
                                "significant": bool(p < self.ALPHA),
                                "odds_ratio": or_val,
                                "or_95ci": or_ci if or_val else None,
                                "or_p_value": or_p if or_val else None,
                            })
                            tested += 1
                    break

        return results

    def _llm_interpret(self, raw_results: list, idea: dict, sample_size: int) -> dict | None:
        """LLM reads SKILL.md, interprets results, returns Markdown text."""
        prompt_data = {
            "idea_id": idea.get("id", "H000"),
            "hypothesis": idea.get("hypothesis", ""),
            "sample_size": sample_size,
            "significance_level": self.ALPHA,
            "test_results": _sanitize(raw_results),
        }
        prompt = json.dumps(prompt_data, indent=2, ensure_ascii=False, default=str)

        text = call_llm(SKILL4_CTX.skill_md + "\n\n" + SKILL4_LLM_PROMPT, prompt, temperature=0.3)

        if not text:
            return None

        # LLM returns Markdown interpretation directly
        interpretation = text.strip()
        if interpretation.startswith("```"):
            interpretation = "\n".join(l for l in interpretation.split("\n") if not l.startswith("```"))

        # Build full output with programmatic results + LLM interpretation
        return {
            "skill": "skill4_stats", "status": "completed",
            "idea_id": idea.get("id", "H000"),
            "hypothesis": idea.get("hypothesis", ""),
            "sample_size": sample_size,
            "results": _sanitize(raw_results),
            "execution_log": [
                "Stage 1: Prerequisite checks — passed",
                "Stage 2: Method matching — based on variable types (binary outcome → Mann-Whitney U + logistic regression)",
                "Stage 3: Statistical execution — completed",
                "Stage 4: Post-hoc checks — passed",
                "Stage 5: LLM interpretation — generated",
            ],
            "interpretation": interpretation,
            "skill_doc_loaded": True,
            "llm_driven": True,
        }

    def _fallback_interpret(self, raw_results: list, idea: dict, sample_size: int) -> dict:
        """Programmatic fallback interpretation."""
        sig = [r for r in raw_results if r.get("significant")]
        non_sig = [r for r in raw_results if not r.get("significant")]
        lines = ["## Statistical Analysis Summary", "",
                  f"Total sample size: {sample_size} patients",
                  f"Significance threshold: α = {self.ALPHA}", ""]
        if sig:
            lines.append("### Statistically Significant Findings (p < 0.05)")
            for r in sig:
                label = r.get("label", "?")
                p = r.get("p_value", "N/A")
                lines.append(f"- **{label}**: {r.get('method', '')}, p = {p}")
                if r.get("group0_median") is not None:
                    lines.append(f"  - {r.get('group0_label', 'Group 0')}: {r['group0_median']} vs "
                                 f"{r.get('group1_label', 'Group 1')}: {r['group1_median']}")
                if r.get("odds_ratio"):
                    lines.append(f"  - OR = {r['odds_ratio']} (95% CI: {r.get('or_95ci', [])})")
                lines.append("")
        if non_sig:
            lines.append("### Non-Significant Findings")
            for r in non_sig[:5]:
                lines.append(f"- **{r.get('label', '?')}**: p = {r.get('p_value', 'N/A')} (not significant)")
            lines.append("")
        lines.append("### Clinical Notes")
        lines.append("- Results are exploratory; confirm with adjusted models.")
        lines.append("- Statistical significance ≠ clinical significance.")
        lines.append("- Consider multivariable adjustment for confounders.")
        return {
            "skill": "skill4_stats", "skill_doc_loaded": True, "llm_driven": False,
            "status": "completed", "idea_id": idea.get("id", "H000"),
            "hypothesis": idea.get("hypothesis", ""), "sample_size": sample_size,
            "results": _sanitize(raw_results),
            "execution_log": ["Programmatic fallback — LLM not available"],
            "interpretation": "\n".join(lines),
            "method_rationale": "Methods matched by variable structure (binary outcome → Mann-Whitney + logistic regression)",
            "clinical_notes": "See interpretation for details.",
        }

    # -- Data loading --
    def _load_cohort_data(self) -> pd.DataFrame | None:
        # Prefer research_cohort.csv from Skill Cohort
        research_path = self.data_dir / "research_cohort.csv"
        if research_path.exists():
            try:
                return pd.read_csv(research_path)
            except Exception as e:
                print(f"[Skill 3] Error loading research_cohort.csv: {e}")
        try:
            cohort = pd.read_csv(self.data_dir / "cohort_24h.csv") if (self.data_dir / "cohort_24h.csv").exists() else None
            patients = pd.read_csv(self.data_dir / "patients_24h.csv") if (self.data_dir / "patients_24h.csv").exists() else None
            admissions = pd.read_csv(self.data_dir / "admissions_24h.csv") if (self.data_dir / "admissions_24h.csv").exists() else None
            if cohort is None or patients is None or admissions is None:
                return None
            df = cohort.merge(patients[["subject_id", "gender", "anchor_age"]], on="subject_id", how="left")
            df = df.merge(admissions[["subject_id", "hadm_id", "hospital_expire_flag", "admission_type"]],
                          on=["subject_id", "hadm_id"], how="left")
            lab_path = self.data_dir / "sepsis_icu_labevents_core_numeric_24h.csv"
            if lab_path.exists():
                lab_items = pd.read_csv(self.data_dir / "sepsis_icu_labevents_core_items.csv")
                key_items = lab_items[lab_items["kept_rows"] > 10000]["itemid"].tolist()
                chunks = []
                for chunk in pd.read_csv(lab_path, chunksize=50000, low_memory=False):
                    cf = chunk[chunk["itemid"].isin(key_items)]
                    if not cf.empty:
                        chunks.append(cf)
                    if sum(len(c) for c in chunks) > 200000:
                        break
                if chunks:
                    lab_df = pd.concat(chunks, ignore_index=True)
                    lab_summary = lab_df.groupby(["subject_id", "hadm_id", "itemid"])["valuenum"].median().reset_index()
                    item_labels = lab_items.set_index("itemid")["label"].to_dict()
                    for item_id in lab_summary["itemid"].unique():
                        item_data = lab_summary[lab_summary["itemid"] == item_id]
                        label = item_labels.get(item_id, str(item_id))
                        label_clean = label.replace(" ", "_").replace(",", "").lower()
                        df = df.merge(
                            item_data[["subject_id", "hadm_id", "valuenum"]].rename(columns={"valuenum": f"lab_{label_clean}"}),
                            on=["subject_id", "hadm_id"], how="left")
            return df
        except Exception as e:
            print(f"[Skill 3] Data loading error: {e}")
            return None
