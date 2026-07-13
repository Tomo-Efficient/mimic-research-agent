"""
Skill 4: LLM Chat-based Statistical Testing.
The LLM reads SKILL.md + idea + data context, suggests methods,
generates Python code, and interprets results.
"""
import json
import io
import os
import sys
import traceback
from pathlib import Path
import pandas as pd
import numpy as np
from scipy import stats as scipy_stats

from skill_loader import load_skill
from llm_client import get_api_key

SKILL4_CTX = load_skill("skill4")
DATA_DIR = Path(os.environ.get("DATA_DIR", str(Path(__file__).parent / "data")))


def _get_llm():
    key = get_api_key()
    if not key:
        return None
    from openai import OpenAI
    return OpenAI(api_key=key, base_url="https://api.deepseek.com")


def _build_data_context() -> str:
    """Build a summary of available data for the LLM."""
    ctx = ["## Available Data (MIMIC-IV Sepsis ICU 24h)\n"]
    try:
        cohort = pd.read_csv(DATA_DIR / "cohort_24h.csv")
        ctx.append(f"### cohort_24h.csv: {len(cohort)} patients, columns: {list(cohort.columns)}")

        patients = pd.read_csv(DATA_DIR / "patients_24h.csv")
        ctx.append(f"### patients_24h.csv: {len(patients)} rows, columns: {list(patients.columns)}")

        admissions = pd.read_csv(DATA_DIR / "admissions_24h.csv")
        ctx.append(f"### admissions_24h.csv: {len(admissions)} rows, columns: {list(admissions.columns)}")

        lab = pd.read_csv(DATA_DIR / "sepsis_icu_labevents_core_items.csv")
        lab_items = lab[lab["kept_rows"] > 10000]["itemid"].tolist()
        lab_labels = lab.set_index("itemid")["label"].to_dict()
        ctx.append(f"### Available lab biomarkers ({len(lab_items)} items):")
        for item_id in lab_items[:40]:
            label = lab_labels.get(item_id, str(item_id))
            ctx.append(f"  - itemid={item_id}: {label}")

        # Build merged data for column listing
        df = _build_cohort_df()
        if df is not None:
            cols_info = []
            for c in df.columns:
                dtyp = str(df[c].dtype)
                n_null = int(df[c].isna().sum())
                n_unique = int(df[c].nunique())
                cols_info.append(f"  - {c}: dtype={dtyp}, missing={n_null}/{len(df)}, unique={n_unique}")
            ctx.append(f"\n### Merged analysis dataset ({len(df)} rows, {len(df.columns)} columns):")
            ctx.extend(cols_info[:60])
    except Exception as e:
        ctx.append(f"\nError reading data: {e}")
    return "\n".join(ctx)


def _build_cohort_df() -> pd.DataFrame | None:
    """Build the merged cohort dataframe (same logic as Skill4Stats)."""
    try:
        cohort = pd.read_csv(DATA_DIR / "cohort_24h.csv") if (DATA_DIR / "cohort_24h.csv").exists() else None
        patients = pd.read_csv(DATA_DIR / "patients_24h.csv") if (DATA_DIR / "patients_24h.csv").exists() else None
        admissions = pd.read_csv(DATA_DIR / "admissions_24h.csv") if (DATA_DIR / "admissions_24h.csv").exists() else None
        if cohort is None or patients is None or admissions is None:
            return None
        df = cohort.merge(patients[["subject_id", "gender", "anchor_age"]], on="subject_id", how="left")
        df = df.merge(admissions[["subject_id", "hadm_id", "hospital_expire_flag", "admission_type"]],
                      on=["subject_id", "hadm_id"], how="left")
        lab_path = DATA_DIR / "sepsis_icu_labevents_core_numeric_24h.csv"
        if lab_path.exists():
            lab_items = pd.read_csv(DATA_DIR / "sepsis_icu_labevents_core_items.csv")
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
        print(f"[Skill4Chat] Data loading error: {e}")
        return None


# ============================================================
# Chat Handlers
# ============================================================

def suggest_methods(idea: dict, user_msg: str = "", history: list = None) -> dict:
    """LLM suggests statistical methods based on the idea and data."""
    client = _get_llm()
    if not client:
        return {"error": "LLM not available"}

    data_ctx = _build_data_context()
    idea_ctx = json.dumps(idea, ensure_ascii=False, indent=2, default=str)

    system = SKILL4_CTX.full_context + """

## YOUR TASK NOW
You are talking to a clinical researcher. Based on the SKILL.md above, the selected research idea,
and the available data, suggest the MOST APPROPRIATE statistical methods.

Follow the Skill 3 workflow:
1. Check data availability for the idea's variables
2. Classify variable types (binary/continuous/multi_cat)
3. Match statistical methods using the method matching rules in SKILL.md
4. Present your recommendation clearly

If the user suggests different methods, evaluate them against SKILL.md rules and respond honestly.
If the user's suggestion is valid, agree and adjust. If not, explain why and suggest alternatives.

Output format (Markdown, NOT JSON):
## 推荐统计方法
Explain why you chose each method, referencing the SKILL.md rules.

## 分析计划
Brief step-by-step plan.

## 变量确认
List which columns from the dataset will be used.
"""

    messages = [{"role": "system", "content": system}]

    if history:
        for h in history[-10:]:
            role = "assistant" if h.get("role") == "assistant" else "user"
            messages.append({"role": role, "content": h.get("content", "")[:1000]})

    if user_msg:
        messages.append({"role": "user", "content": user_msg})
    else:
        messages.append({"role": "user", "content": f"""## Selected Research Idea
{idea_ctx}

## Available Data
{data_ctx}

Please suggest the best statistical methods for this idea."""})

    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=messages,
        temperature=0.3, max_tokens=4096,
    )
    return {"message": resp.choices[0].message.content, "role": "assistant"}


def generate_and_execute(idea: dict, chat_history: list) -> dict:
    """LLM generates Python code → backend executes → returns results."""
    client = _get_llm()
    if not client:
        return {"error": "LLM not available"}

    data_ctx = _build_data_context()
    idea_ctx = json.dumps(idea, ensure_ascii=False, indent=2, default=str)

    # Build conversation context
    conv = "\n".join([f"{'User' if m['role']=='user' else 'Assistant'}: {m['content'][:500]}" for m in chat_history[-6:]])

    system = """You are a medical statistician following the SKILL.md specification.
Generate executable Python code to run the agreed-upon statistical tests.

## CRITICAL RULES
1. The code will be executed via exec() in a sandbox with pandas, numpy, scipy.stats, and statsmodels available.
2. Data is loaded as `df` (a pandas DataFrame). All columns listed in the data context exist in df.
3. Use: `import pandas as pd; import numpy as np; from scipy import stats; import statsmodels.api as sm`
4. Handle missing values with dropna().
5. Print ALL results clearly with labels.
6. Store results in a list called `__results__` (list of dicts), each with keys: test_id, label, method, n, p_value, significant, effect_size (optional), and any other relevant metrics.
7. Also store the sample size as `__sample_size__` (int).
8. NEVER access files, network, or system. Only use the provided df.

Output format:
First explain briefly what you're doing, then provide the code in a ```python block.
The message and code MUST be separated clearly. The code block will be extracted and executed.
"""

    user = f"""## Selected Research Idea
{idea_ctx}

## Available Data Columns (in df)
{data_ctx}

## Conversation History (for context on agreed methods)
{conv}

Please generate the Python code to execute the agreed statistical tests."""

    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.1, max_tokens=4096,
    )
    llm_output = resp.choices[0].message.content

    # Extract code block
    code = _extract_code(llm_output)
    if not code:
        return {"error": "No code block found in LLM response", "llm_output": llm_output}

    # Execute code
    exec_result = _execute_code(code)
    return {
        "message": llm_output.replace(f"```python\n{code}\n```", "[Code executed — see results below]"),
        "code": code,
        "exec_result": exec_result,
        "role": "assistant",
    }


def interpret_results(idea: dict, exec_result: dict, chat_history: list) -> dict:
    """LLM interprets the statistical results in bilingual format."""
    client = _get_llm()
    if not client:
        return {"error": "LLM not available"}

    results_str = json.dumps(exec_result.get("results", []), ensure_ascii=False, indent=2)
    sample_size = exec_result.get("sample_size", "N/A")

    system = SKILL4_CTX.full_context + """

## YOUR TASK NOW
Interpret the statistical results following the SKILL.md output format.
Provide a bilingual (Chinese + English) clinical interpretation.

Output format (Markdown, separated by ---):
## 统计结论
(Significant findings with exact p-values in Chinese)

## Statistical Conclusions
(Same in English)

## 临床提示
(Clinical implications in Chinese — suggestive only, no definitive claims)

## Clinical Implications
(Same in English)

## 局限性
(Limitations in Chinese)

## Limitations
(Same in English)
"""

    user = f"""## Research Idea
{json.dumps(idea, ensure_ascii=False, indent=2, default=str)}

## Sample Size: {sample_size}

## Statistical Results
{results_str}

Please interpret these results."""

    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.3, max_tokens=4096,
    )
    return {"message": resp.choices[0].message.content, "role": "assistant"}


# ============================================================
# Helpers
# ============================================================

def _extract_code(text: str) -> str | None:
    """Extract Python code from markdown code blocks."""
    import re
    # Match ```python ... ``` blocks
    m = re.search(r'```python\s*\n(.*?)\n```', text, re.DOTALL)
    if m:
        return m.group(1)
    # Match ``` ... ``` without language spec
    m = re.search(r'```\s*\n(.*?)\n```', text, re.DOTALL)
    if m:
        return m.group(1)
    return None


def _execute_code(code: str, max_retries: int = 5) -> dict:
    """Execute LLM-generated Python code in a sandbox."""
    namespace = {
        "pd": pd, "np": np, "stats": scipy_stats,
        "__results__": [], "__sample_size__": 0,
    }

    # Build the dataframe
    df = _build_cohort_df()
    if df is None:
        return {"error": "Could not load data", "stdout": "", "results": [], "sample_size": 0}
    namespace["df"] = df

    old_stdout = sys.stdout
    captured = io.StringIO()
    sys.stdout = captured

    result = {"error": None, "stdout": "", "results": [], "sample_size": 0, "retries": 0}

    for attempt in range(max_retries):
        try:
            captured.truncate(0)
            captured.seek(0)
            exec(code, namespace)
            result["stdout"] = captured.getvalue()
            result["results"] = list(namespace.get("__results__", []))
            result["sample_size"] = int(namespace.get("__sample_size__", len(df)))
            result["retries"] = attempt
            result["error"] = None
            break
        except Exception as e:
            result["error"] = f"Attempt {attempt+1}: {str(e)}\n{traceback.format_exc()}"
            result["retries"] = attempt + 1
            if attempt == max_retries - 1:
                result["stdout"] = captured.getvalue()
                result["results"] = list(namespace.get("__results__", []))
                result["sample_size"] = int(namespace.get("__sample_size__", len(df)))

    sys.stdout = old_stdout

    # Sanitize results for JSON
    def _san(v):
        if isinstance(v, (np.integer,)): return int(v)
        if isinstance(v, (np.floating,)):
            return None if (np.isnan(v) or np.isinf(v)) else float(v)
        if isinstance(v, np.bool_): return bool(v)
        if isinstance(v, np.ndarray): return v.tolist()
        return v

    result["results"] = [{k: _san(v) for k, v in r.items()} for r in result["results"]]

    return result


# ============================================================
# Streaming version — SSE for real-time chat
# ============================================================

def suggest_methods_stream(idea: dict, user_msg: str = "", history: list = None):
    """Stream LLM method suggestions as SSE events."""
    client = _get_llm()
    if not client:
        yield f"data: {json.dumps({'error': 'LLM not available'})}\n\n"
        return

    data_ctx = _build_data_context()
    idea_ctx = json.dumps(idea, ensure_ascii=False, indent=2, default=str)

    system = SKILL4_CTX.full_context + """
## YOUR TASK NOW
You are talking to a clinical researcher. Based on the SKILL.md, the selected idea, and available data, suggest the BEST statistical methods. Follow Skill 3 workflow: check variables → classify types → match methods → recommend. Output clean Markdown. End with [READY] on its own line."""

    messages = [{"role": "system", "content": system}]
    if history:
        for h in history[-8:]:
            role = "assistant" if h.get("role") == "assistant" else "user"
            messages.append({"role": role, "content": h.get("content", "")[:800]})

    if user_msg:
        messages.append({"role": "user", "content": user_msg})
    else:
        messages.append({"role": "user", "content": f"## Selected Idea\n{idea_ctx}\n\n## Available Data\n{data_ctx}\n\nSuggest methods."})

    try:
        stream = client.chat.completions.create(
            model="deepseek-chat", messages=messages, temperature=0.3, max_tokens=4096, stream=True)
        full = ""
        for chunk in stream:
            if chunk.choices[0].delta.content:
                token = chunk.choices[0].delta.content
                full += token
                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'done': True, 'full': full}, ensure_ascii=False)}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n"
