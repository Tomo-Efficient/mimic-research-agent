# skill1_preprocess.py
import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype

import config as C


# =========================
# Utilities
# =========================
def ensure_dirs():
    C.INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    C.VIEWS_DIR.mkdir(parents=True, exist_ok=True)
    C.REPORT_DIR.mkdir(parents=True, exist_ok=True)


def list_raw_tables(raw_dir: Path) -> Dict[str, Path]:
    files = sorted(raw_dir.glob("*.csv"))
    tables = {f.stem: f for f in files}
    for name, path in tables.items():
        print(f"[发现] {name}: {path}")
    return tables


def read_schema(csv_path: Path) -> List[str]:
    df0 = pd.read_csv(csv_path, nrows=0, **C.CSV_READ_KWARGS)
    return list(df0.columns)


def scan_all_schemas(table_paths: Dict[str, Path]) -> Dict[str, List[str]]:
    schemas = {}
    for name, path in table_paths.items():
        cols = read_schema(path)
        schemas[name] = cols
        print(f"[扫描schema] 表 {name}: {len(cols)} 列")
    return schemas


def detect_relationships_from_schema(schemas: Dict[str, List[str]]) -> List[dict]:
    """
    仅根据“共享主键字段名”推断可能的表间关系（启发式）。
    """
    keys = ["subject_id", "hadm_id", "stay_id", "icustay_id"]
    relations = []
    names = list(schemas.keys())
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            shared = [k for k in keys if (k in schemas[a] and k in schemas[b])]
            if shared:
                relations.append({"left": a, "right": b, "shared_keys": shared})
    return relations


def parse_datetimes_inplace(df: pd.DataFrame, table_name: str) -> dict:
    info = {"parsed_cols": [], "coerced_na_counts": {}}
    dt_cols = C.DATETIME_COLS.get(table_name, [])
    for col in dt_cols:
        if col in df.columns:
            before_na = int(df[col].isna().sum())
            df[col] = pd.to_datetime(df[col], errors="coerce")
            after_na = int(df[col].isna().sum())
            info["parsed_cols"].append(col)
            info["coerced_na_counts"][col] = max(0, after_na - before_na)
    return info


def safe_mode(series: pd.Series):
    s = series.dropna()
    if s.empty:
        return None
    m = s.mode(dropna=True)
    if m.empty:
        return None
    return m.iloc[0]


def infer_categorical_columns(df: pd.DataFrame) -> List[str]:
    cat_cols = []
    for c in df.columns:
        if df[c].dtype == "object" or pd.api.types.is_string_dtype(df[c]):
            cat_cols.append(c)
    return cat_cols


def infer_numeric_columns(df: pd.DataFrame) -> List[str]:
    return [c for c in df.columns if is_numeric_dtype(df[c])]


def compute_missingness(df: pd.DataFrame) -> pd.Series:
    return df.isna().mean().sort_values(ascending=False)


def missing_level(frac: float) -> str:
    if frac < C.MISSING_USABLE:
        return "usable"
    if frac < C.MISSING_CAUTIOUS:
        return "cautious"
    return "exclude"


def iqr_outlier_summary(series: pd.Series) -> dict:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return {"n": 0, "outlier_n": 0, "outlier_frac": None}
    q1, q3 = np.nanpercentile(s, [25, 75])
    iqr = q3 - q1
    if iqr == 0 or np.isnan(iqr):
        return {"n": int(s.shape[0]), "outlier_n": 0, "outlier_frac": 0.0}
    low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    out = ((s < low) | (s > high)).sum()
    return {
        "n": int(s.shape[0]),
        "outlier_n": int(out),
        "outlier_frac": float(out / s.shape[0]),
        "bounds": {"low": float(low), "high": float(high)},
    }


# =========================
# Reporting dataclasses
# =========================
@dataclass
class ColumnReport:
    name: str
    dtype: str
    missing_frac: float
    missing_level: str
    unique: Optional[int] = None
    imputation: Optional[str] = None
    outlier: Optional[dict] = None


@dataclass
class TableReport:
    table: str
    source_path: str
    cleaned_path: str
    n_rows: int
    n_cols: int
    datetime_parse: dict
    columns: List[ColumnReport]
    excluded_columns: List[str]
    notes: List[str]


# =========================
# Imputation values (for chunked cleaning)
# =========================
def compute_imputation_values(df: pd.DataFrame) -> Dict[str, Any]:
    """
    从 df（通常是全量或抽样）计算插补值，供 chunk 清洗时使用。
    """
    impute = {}

    num_cols = infer_numeric_columns(df)
    for c in num_cols:
        med = pd.to_numeric(df[c], errors="coerce").median()
        impute[c] = None if pd.isna(med) else float(med)

    cat_cols = infer_categorical_columns(df)
    for c in cat_cols:
        if C.USE_MODE_FOR_LOW_CARDINALITY_CATEGORICAL:
            nunique = df