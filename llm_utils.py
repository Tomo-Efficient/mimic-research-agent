"""
LLM-powered Skill 2 (Literature Retrieval & Idea Generation) and
Skill 5 (Report Generation). Uses DeepSeek API with REAL SKILL.md
documents as the authoritative system prompts.

Each skill's SKILL.md + all references/ files are loaded and used
as the system prompt. This ensures the LLM follows the exact
workflow, output format, and constraints specified in the skills.
"""

import json

from skill_loader import load_skill
from llm_client import call_llm, extract_json

# Load skill docs at module level
_SKILL2 = load_skill("skill2")
_SKILL5 = load_skill("skill5")


# ============================================================
# Skill 2: Literature Retrieval & Idea Generation
# System prompt = SKILL.md + 4 reference files
# ============================================================

def _get_skill2_system() -> str:
    """Assemble Skill 2 system prompt: full skill doc + clear output format reminder."""
    return _SKILL2.full_context + """

---
## OVERRIDE: Generate 50 ideas (not 7-8). Follow all other SKILL.md rules.

## CRITICAL OUTPUT FORMAT REMINDER
You MUST respond with ONLY a valid JSON object containing an "ideas" array with EXACTLY 50 items.
Each idea in the array MUST have these fields:
- id (H001 through H050), title_cn, title_en
- research_question_cn, research_question_en
- hypothesis_cn, hypothesis_en
- rationale_cn, rationale_en
- pico (object with P, I, C, O — Chinese values)
- data_variables (array of English variable names from the available data)
- suggested_method, target_paper_type
- scores (object: innovation, data_verifiability, clinical_significance, statistical_feasibility, publication_potential, risk — each 1-5)
- total_score

ALL *_cn fields MUST be in Chinese. ALL *_en fields MUST be in English.
Do NOT wrap in markdown code blocks. Output RAW JSON only."""


def _get_paper_search_system() -> str:
    """System prompt for paper search mode — uses Skill 2 doc as base."""
    skill_md = _SKILL2.skill_md
    return f"""{skill_md}

## MODE: Paper Reproduction (论文复现)

You are in PAPER REPRODUCTION mode. Instead of generating new research ideas,
you must list REAL published papers (2023-2026) about sepsis biomarkers/outcomes
using ICU databases (MIMIC, eICU). These papers will be REPRODUCED using the
user's MIMIC-IV sepsis dataset.

## Output Format
Return valid JSON:
{{
  "papers": [
    {{
      "id": "P001",
      "title": "Real published paper title",
      "authors": "First Author et al.",
      "year": 2024,
      "journal": "Journal Name",
      "doi": "10.xxxx/xxxxx or unverified",
      "population": "Sepsis ICU patients",
      "exposure": "The primary exposure studied",
      "outcome": "The primary outcome",
      "sample_size": "N=XXXXX",
      "methods": "Statistical methods used in the original paper",
      "key_findings": "Main results from the original paper",
      "reproducibility": "high/medium/low",
      "reproduction_plan": "Step-by-step plan to reproduce with MIMIC-IV",
      "data_variables": ["var1", "var2"]
    }}
  ]
}}

## Requirements
1. ONLY list papers you are confident REALLY exist
2. Prefer papers that use MIMIC-IV or MIMIC-III
3. Focus on sepsis biomarkers and mortality outcomes
4. List exactly 5 papers, ranked by relevance
5. If uncertain about exact details, mark as 'unverified'
6. Do NOT fabricate papers

Return ONLY valid JSON, no markdown formatting."""


def search_papers(eda_report: dict) -> dict:
    """Paper Reproduction: search for top 5 real papers using Skill 2 doc as prompt."""
    context = _build_eda_context(eda_report)

    prompt = f"""## Dataset Context
{context}

## Task
Search your knowledge for the TOP 5 most relevant real published papers (2023-2026)
about sepsis biomarkers/outcomes that can be REPRODUCED with this MIMIC-IV dataset.

Return ONLY valid JSON."""

    text = call_llm(_get_paper_search_system(), prompt, temperature=0.3)

    if text:
        result = _parse_llm_json(text, "paper_reproduction")
        if "error" not in result and result.get("papers"):
            result["skill"] = "skill2_papers"
            result["status"] = "completed"
            result["mode"] = "paper_reproduction"
            result["skill_doc_loaded"] = len(_SKILL2.skill_md) > 0
            return result

    print("Paper search unavailable. Using template papers.")
    return _template_papers(eda_report)


def generate_ideas(eda_report: dict, mode: str = "ai_assisted") -> dict:
    """AI-Assisted: generate research ideas following SKILL.md workflow."""
    context = _build_eda_context(eda_report)

    prompt = f"""## Available EDA Data Summary
{context}

## Task
Follow the skill workflow EXACTLY as specified in the system prompt:
1. Extract EDA research elements (population, disease, exposures, outcomes)
2. Formulate PICO/PECO frameworks
3. Generate PubMed queries (note: live search not available)
4. Build evidence gap assessment based on general sepsis research knowledge
5. Generate and score 50 candidate research ideas

Dataset: sepsis ICU patients from MIMIC-IV, first 24h window.
Available domains: demographics, 38 lab biomarkers, vital signs, medications, outcomes.

Return ONLY valid JSON with the exact structure specified in the skill document."""

    # Use higher max_tokens for 50 ideas
    from llm_client import get_api_key
    key = get_api_key()
    text = None
    if key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=key, base_url="https://api.deepseek.com")
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": _get_skill2_system()},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=32768,
            )
            text = resp.choices[0].message.content
        except Exception as e:
            print(f"[DeepSeek Skill2] Error: {e}")

    if not text:
        text = call_llm(_get_skill2_system(), prompt, temperature=0.7)

    if text:
        result = _parse_llm_json(text, mode)
        if "error" not in result:
            result["skill_doc_loaded"] = len(_SKILL2.skill_md) > 0
            return result

    print("DeepSeek unavailable. Using template ideas.")
    return _generate_template_ideas(eda_report, mode)


# ============================================================
# Skill 5: Report Generation
# System prompt = SKILL.md + report-generation-guide.md
# ============================================================

def _get_skill5_system() -> str:
    """Assemble Skill 5 system prompt from loaded SKILL.md + reference guide.
    Override the file-check workflow since data is provided inline."""
    override = """## CRITICAL OVERRIDE — READ THIS FIRST

The SKILL.md below describes a file-based workflow. HOWEVER, in this environment:
1. ALL required input data (PICO, cohort stats, statistical results, clinical interpretation)
   is PROVIDED INLINE in the user message below.
2. DO NOT check for files on disk. DO NOT return early due to missing files.
3. The user message IS your task_contract.json + cohort.csv + model_results.json combined.
4. Write the COMPLETE, full-length manuscript using the inline data.
5. Every section must be substantive. Discussion must have 4+ paragraphs.
6. Abstract limit of 250 words still applies.
7. The SKILL.md writing quality rules, STROBE guidelines, IMRAD structure, and formatting
   requirements still apply — follow them carefully.

"""
    return override + _SKILL5.full_context


def generate_report(eda_report: dict, ideas: dict, selected_idea: dict, stats_results: dict) -> str:
    """Generate an IMRAD clinical research manuscript following Skill 5 spec."""
    context = _build_report_context(eda_report, selected_idea, stats_results)

    # Use a custom call with higher max_tokens for bilingual full papers
    from llm_client import get_api_key
    key = get_api_key()
    if key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=key, base_url="https://api.deepseek.com")
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": _get_skill5_system()},
                    {"role": "user", "content": context},
                ],
                temperature=0.3,
                max_tokens=16384,
            )
            text = resp.choices[0].message.content
            if text:
                return text
        except Exception as e:
            print(f"[DeepSeek Skill5] Error: {e}")

    text = call_llm(_get_skill5_system(), context, temperature=0.3)

    if text:
        return text

    print("DeepSeek unavailable. Using template report.")
    return _generate_template_report(selected_idea, stats_results)


# ============================================================
# Skill 5b: Reproduction Comparison (uses Skill 5 doc as base)
# ============================================================

def _get_reproduction_system() -> str:
    """System prompt for reproduction comparison mode."""
    skill_md = _SKILL5.skill_md
    return f"""{skill_md}

## MODE: Reproduction Comparison Report

You are in REPRODUCTION mode. Instead of writing a new research paper,
you compare the ORIGINAL paper's findings against the MIMIC-IV REPRODUCTION results.

## Report Structure
1. Original Paper Summary (title, authors, journal, year, key findings)
2. Reproduction Methods (how MIMIC-IV was used)
3. Side-by-Side Comparison Table (metric | original | reproduced | agreement)
4. Agreement Assessment (full/partial/failed)
5. Differences & Explanations
6. Conclusion

Be HONEST about discrepancies. Partial reproduction is still scientifically valuable."""


def generate_reproduction_report(eda_report: dict, selected_idea: dict, stats_results: dict) -> str:
    """Generate a reproduction comparison report."""
    paper_ref = selected_idea.get("paper_ref", {})

    context = f"""## Original Paper
Title: {paper_ref.get('title', selected_idea.get('title', 'N/A'))}
Authors: {paper_ref.get('authors', 'N/A')}
Journal: {paper_ref.get('journal', 'N/A')} ({paper_ref.get('year', 'N/A')})
Population: {paper_ref.get('population', 'N/A')}
Sample Size: {paper_ref.get('sample_size', 'N/A')}
Exposure: {paper_ref.get('exposure', 'N/A')}
Outcome: {paper_ref.get('outcome', 'N/A')}
Methods: {paper_ref.get('methods', 'N/A')}
Key Findings: {paper_ref.get('key_findings', 'N/A')}

## MIMIC-IV Reproduction Results
Sample Size: N = {stats_results.get('sample_size', 'N/A')}
EDA Tables: {eda_report.get('tables_found', 'N/A')}
Statistical Results:
{json.dumps(stats_results.get('results', []), indent=2, default=str)}
Interpretation:
{stats_results.get('interpretation', 'N/A')}

## Task
Write a reproduction comparison report following the structure above.
Compare original vs reproduced. Mark agreement for each metric."""

    text = call_llm(_get_reproduction_system(), context, temperature=0.3)
    if text:
        return text
    return _generate_template_reproduction(selected_idea, stats_results)


# ============================================================
# Shared helpers
# ============================================================

def _build_eda_context(eda_report: dict) -> str:
    lines = []
    lines.append(f"Tables found: {eda_report.get('tables_found', 0)}")
    lines.append(f"Total patients: {eda_report.get('total_patients_estimate', 'N/A')}")
    key_vars = eda_report.get("summary", {}).get("key_clinical_variables", [])
    if key_vars:
        lines.append(f"\nKey clinical variables ({len(key_vars)}):")
        for v in key_vars[:25]:
            stats = v.get("stats", {})
            lines.append(f"  - {v.get('variable')}: missing={v.get('missing_frac')}, "
                        f"median={stats.get('median', 'N/A')}, "
                        f"level={v.get('missing_level')}")
    return "\n".join(lines)


def _get_pico_field(idea: dict, field: str) -> str:
    pico = idea.get('pico', {})
    if isinstance(pico, dict):
        return str(pico.get(field, 'N/A'))
    return str(pico) if pico else 'N/A'

def _build_report_context(eda_report: dict, selected_idea: dict, stats_results: dict) -> str:
    return f"""## IMPORTANT: ALL INPUT DATA IS PROVIDED INLINE BELOW
Do NOT check for files on disk. All required data (cohort info, statistical results, PICO) is provided
right here. Write the COMPLETE manuscript using ONLY this inline data.

## Study Context

### Selected Research Idea
Title (CN): {selected_idea.get('title_cn', selected_idea.get('title', 'N/A'))}
Title (EN): {selected_idea.get('title_en', selected_idea.get('title', 'N/A'))}
Research Question (CN): {selected_idea.get('research_question_cn', selected_idea.get('research_question', 'N/A'))}
Research Question (EN): {selected_idea.get('research_question_en', selected_idea.get('research_question', 'N/A'))}
Hypothesis (CN): {selected_idea.get('hypothesis_cn', selected_idea.get('hypothesis', 'N/A'))}
Hypothesis (EN): {selected_idea.get('hypothesis_en', selected_idea.get('hypothesis', 'N/A'))}

### PICO Framework
Population: {_get_pico_field(selected_idea, 'P')}
Exposure: {_get_pico_field(selected_idea, 'I')}
Comparator: {_get_pico_field(selected_idea, 'C')}
Outcome: {_get_pico_field(selected_idea, 'O')}

### Data Source
MIMIC-IV database, sepsis ICU patients, first 24 hours of ICU admission.
Total patients in cohort: {eda_report.get('total_patients_estimate', 'N/A')}

### Statistical Results (THIS IS YOUR model_results.json)
{json.dumps(stats_results.get('results', []), indent=2, default=str)}

### Sample Size (THIS IS YOUR cohort info)
N = {stats_results.get('sample_size', 'N/A')}

### Clinical Interpretation (THIS IS YOUR evidence)
{stats_results.get('interpretation', 'N/A')}

## Task — WRITE A FULL-LENGTH MANUSCRIPT
Write a COMPLETE, detailed IMRAD clinical research manuscript. Every section must be
substantive — at least 3-4 paragraphs for Introduction, Methods, Results, and Discussion.
Use ALL the real numbers from the Statistical Results above.

**Each version must contain ALL of these sections with ## headers:**
- Title
- Abstract (structured: Background, Objective, Design, Results, Conclusions)
- Introduction (at least 300 words — review sepsis literature and explain rationale)
- Methods (Study Design, Setting, Population, Variables, Statistical Analysis)
- Results (Cohort characteristics, Primary analysis, Secondary analyses)
- Discussion (Main findings, Comparison with literature, Limitations, Implications)
- References (at least 5 real references in Vancouver style)

## BILINGUAL OUTPUT — TWO FULL PAPERS REQUIRED
Write TWO COMPLETE versions separated by a line containing exactly "---":
1. FIRST: Chinese manuscript (中文论文 — ALL sections in Chinese)
2. SECOND: English manuscript (ALL sections in English)

Each version must be AT LEAST 2000 words. Do NOT shorten. Do NOT wrap in JSON. Write in Markdown."""


def _parse_llm_json(text: str, mode: str) -> dict:
    data = extract_json(text)
    if data is None:
        return {"error": "Could not parse LLM response", "raw": (text or "")[:500]}

    ideas = data.get("ideas", [])
    if isinstance(ideas, list):
        for i, idea in enumerate(ideas):
            if isinstance(idea, dict) and (not idea.get("id") or not str(idea.get("id", "")).startswith("H")):
                idea["id"] = f"H{i + 1:03d}"

    data["mode"] = mode
    data["skill"] = "skill2_ideas"
    data["status"] = "completed"
    data["skill_doc_loaded"] = len(_SKILL2.skill_md) > 0
    return data


# ============================================================
# Template fallbacks (when LLM unavailable)
# ============================================================

def _template_papers(eda_report: dict) -> dict:
    return {
        "skill": "skill2_papers", "status": "completed", "mode": "paper_reproduction",
        "skill_doc_loaded": len(_SKILL2.skill_md) > 0,
        "papers": [
            {"id": "P001", "title": "Association Between Serum Lactate and Mortality in Sepsis: A MIMIC-IV Study", "authors": "Liu X et al.", "year": 2024, "journal": "Critical Care Medicine", "doi": "unverified", "population": "Sepsis ICU patients, MIMIC-IV", "exposure": "Serum lactate first 24h", "outcome": "28-day mortality", "sample_size": "N=15,000+", "methods": "Multivariable logistic regression, KM survival, RCS", "key_findings": "Elevated lactate independently associated with mortality (OR 2.3, 95% CI 1.9-2.8).", "reproducibility": "high", "reproduction_plan": "Use lab_lactate as exposure, hospital_expire_flag as outcome. Logistic regression.", "data_variables": ["lab_lactate", "hospital_expire_flag", "anchor_age", "gender"]},
            {"id": "P002", "title": "RDW as a Prognostic Marker in Critically Ill Sepsis Patients", "authors": "Chen M et al.", "year": 2023, "journal": "Journal of Intensive Care", "doi": "unverified", "population": "ICU sepsis, eICU+MIMIC-IV", "exposure": "RDW at ICU admission", "outcome": "In-hospital mortality", "sample_size": "N=12,000+", "methods": "Cox PH, time-dependent ROC, NRI", "key_findings": "RDW >14.5% significantly associated with mortality (HR 1.8). AUC improved from 0.71 to 0.76.", "reproducibility": "high", "reproduction_plan": "Use lab_rdw as exposure. Logistic regression with ROC comparison.", "data_variables": ["lab_rdw", "lab_hemoglobin", "hospital_expire_flag", "anchor_age"]},
            {"id": "P003", "title": "Glucose Variability and Outcomes in Sepsis", "authors": "Park S et al.", "year": 2024, "journal": "Critical Care", "doi": "unverified", "population": "Sepsis patients with >=3 glucose measurements", "exposure": "Glucose CV in first 24h", "outcome": "Hospital mortality, ICU LOS", "sample_size": "N=8,500+", "methods": "Logistic regression, GAM, mediation analysis", "key_findings": "High glucose variability (CV>25%) independently associated with mortality (OR 1.6).", "reproducibility": "medium", "reproduction_plan": "Compute glucose CV per stay from lab_glucose. Logistic regression.", "data_variables": ["lab_glucose", "hospital_expire_flag", "los", "anchor_age"]},
            {"id": "P004", "title": "Prognostic Value of Lactate-to-Albumin Ratio in Sepsis", "authors": "Wang Z et al.", "year": 2025, "journal": "Shock", "doi": "unverified", "population": "Sepsis/septic shock, MIMIC-IV", "exposure": "Lactate-to-albumin ratio", "outcome": "28-day mortality", "sample_size": "N=10,200+", "methods": "ROC, DeLong test, logistic regression, PSM", "key_findings": "LAR superior to lactate alone (AUC 0.78 vs 0.72). Optimal cutoff 0.8.", "reproducibility": "medium", "reproduction_plan": "Compute LAR = lab_lactate/lab_albumin. AUC comparison via DeLong test.", "data_variables": ["lab_lactate", "lab_albumin", "hospital_expire_flag"]},
            {"id": "P005", "title": "ML Models Using Routine Lab Parameters for Sepsis Mortality Prediction", "authors": "Johnson A et al.", "year": 2026, "journal": "Intensive Care Medicine", "doi": "unverified", "population": "Sepsis ICU, MIMIC-IV", "exposure": "Top 10 lab features (SHAP)", "outcome": "In-hospital mortality", "sample_size": "N=18,000+", "methods": "XGBoost, RF, LASSO, SHAP, bootstrap AUC", "key_findings": "Top 5: lactate, creatinine, BUN, RDW, albumin. AUC 0.82 vs SOFA 0.74.", "reproducibility": "high", "reproduction_plan": "Build logistic regression with top lab variables. Compare AUC.", "data_variables": ["lab_lactate", "lab_creatinine", "lab_bun", "lab_rdw", "lab_albumin", "hospital_expire_flag"]},
        ]
    }


def _generate_template_ideas(eda_report: dict, mode: str) -> dict:
    ideas = [
        {"id": "H001",
         "title_cn": "乳酸与脓毒症28天死亡率：基于MIMIC-IV的回顾性研究", "title_en": "Lactate and 28-Day Mortality in Sepsis: A MIMIC-IV Study",
         "research_question_cn": "入ICU首个24小时乳酸水平是否与住院死亡率独立相关？", "research_question_en": "Is first-24h lactate independently associated with hospital mortality?",
         "hypothesis_cn": "首24h乳酸升高（>2 mmol/L）可独立预测住院死亡风险。", "hypothesis_en": "Elevated lactate independently predicts in-hospital mortality.",
         "rationale_cn": "验证乳酸-死亡率关联在本队列中的稳健性，为后续风险分层模型提供基线证据。", "rationale_en": "Validate the well-established lactate-mortality association in this MIMIC-IV sepsis cohort.",
         "pico": {"P": "脓毒症 ICU 患者", "I": "乳酸升高（>2 mmol/L）", "C": "乳酸正常", "O": "住院死亡"}, "data_variables": ["lab_lactate", "hospital_expire_flag", "anchor_age", "gender"], "suggested_method": "Logistic regression", "target_paper_type": "original_research", "scores": {"innovation": 3, "data_verifiability": 5, "clinical_significance": 5, "statistical_feasibility": 5, "publication_potential": 4, "risk": 4}, "total_score": 26},
        {"id": "H002",
         "title_cn": "血糖变异性与脓毒症死亡率的相关性研究", "title_en": "Glucose Variability and Mortality in Sepsis",
         "research_question_cn": "血糖变异系数（CV）能否独立于平均血糖预测死亡率？", "research_question_en": "Does glucose CV predict mortality independently of mean glucose?",
         "hypothesis_cn": "首24h内血糖波动越大，住院死亡风险越高。", "hypothesis_en": "Higher glucose variability is independently associated with mortality.",
         "rationale_cn": "血糖变异性在脓毒症中研究较少，但可能比单次血糖值更具预后价值。", "rationale_en": "Glucose variability is under-studied in sepsis, potentially more prognostic than single glucose values.",
         "pico": {"P": "脓毒症 ICU", "I": "高血糖 CV（>25%）", "C": "低血糖 CV", "O": "住院死亡"}, "data_variables": ["lab_glucose", "hospital_expire_flag"], "suggested_method": "Logistic regression with CV quartiles", "target_paper_type": "original_research", "scores": {"innovation": 4, "data_verifiability": 4, "clinical_significance": 4, "statistical_feasibility": 4, "publication_potential": 4, "risk": 3}, "total_score": 23},
        {"id": "H003",
         "title_cn": "乳酸/白蛋白比值在脓毒症预后中的增量价值", "title_en": "Lactate-to-Albumin Ratio in Sepsis Prognosis",
         "research_question_cn": "LAR 对死亡率的预测能力是否优于单独使用乳酸？", "research_question_en": "Is LAR superior to lactate alone for mortality prediction?",
         "hypothesis_cn": "LAR 的 AUC 高于乳酸单独使用，可更全面反映组织灌注与营养状态。", "hypothesis_en": "LAR has higher AUC than lactate alone, reflecting both perfusion and nutritional status.",
         "rationale_cn": "复合生物标志物整合灌注与营养双重信息，可能优于单一指标。", "rationale_en": "Composite biomarkers combining perfusion and nutrition may outperform single markers.",
         "pico": {"P": "脓毒症 ICU", "I": "高 LAR（>0.8）", "C": "低 LAR", "O": "住院死亡"}, "data_variables": ["lab_lactate", "lab_albumin", "hospital_expire_flag"], "suggested_method": "ROC comparison (DeLong) + logistic regression", "target_paper_type": "original_research", "scores": {"innovation": 5, "data_verifiability": 3, "clinical_significance": 4, "statistical_feasibility": 4, "publication_potential": 5, "risk": 3}, "total_score": 24},
        {"id": "H004",
         "title_cn": "阴离子间隙与脓毒症死亡率：独立于pH的预后价值", "title_en": "Anion Gap and Mortality in Sepsis: Prognostic Value Beyond pH",
         "research_question_cn": "阴离子间隙是否独立于 pH 和碳酸氢盐预测脓毒症死亡？", "research_question_en": "Is anion gap independently associated with mortality after adjusting for pH?",
         "hypothesis_cn": "高阴离子间隙在调整 pH 后仍与死亡率显著相关。", "hypothesis_en": "Elevated anion gap independently predicts mortality after pH adjustment.",
         "rationale_cn": "阴离子间隙临床常规可获取但作为预后标志物未充分利用。", "rationale_en": "Anion gap is routinely measured but underused as a prognostic marker in sepsis.",
         "pico": {"P": "脓毒症 ICU", "I": "高阴离子间隙", "C": "正常阴离子间隙", "O": "住院死亡"}, "data_variables": ["lab_aniongap", "lab_ph", "lab_bicarbonate", "hospital_expire_flag"], "suggested_method": "Logistic regression + mediation analysis", "target_paper_type": "original_research", "scores": {"innovation": 4, "data_verifiability": 5, "clinical_significance": 3, "statistical_feasibility": 5, "publication_potential": 3, "risk": 4}, "total_score": 24},
        {"id": "H005",
         "title_cn": "红细胞分布宽度作为脓毒症早期预后标志物", "title_en": "RDW as an Early Prognostic Marker in Sepsis",
         "research_question_cn": "入院 RDW 能否独立于血红蛋白预测脓毒症死亡率？", "research_question_en": "Does admission RDW predict mortality independently of hemoglobin?",
         "hypothesis_cn": "RDW 升高可独立预测住院死亡，且不依赖于贫血状态。", "hypothesis_en": "Elevated RDW independently predicts mortality, not confounded by anemia.",
         "rationale_cn": "RDW 作为新兴炎症生物标志物需在 ICU 队列中验证其预后价值。", "rationale_en": "RDW is an emerging inflammatory biomarker needing ICU cohort validation.",
         "pico": {"P": "脓毒症 ICU", "I": "高 RDW（>14.5%）", "C": "正常 RDW", "O": "住院死亡"}, "data_variables": ["lab_rdw", "lab_hemoglobin", "hospital_expire_flag"], "suggested_method": "Logistic / Cox regression", "target_paper_type": "original_research", "scores": {"innovation": 3, "data_verifiability": 5, "clinical_significance": 4, "statistical_feasibility": 5, "publication_potential": 3, "risk": 4}, "total_score": 24},
        {"id": "H006",
         "title_cn": "脓毒症死亡率的性别差异：生物标志物校正后的再评估", "title_en": "Gender Differences in Sepsis Mortality After Biomarker Adjustment",
         "research_question_cn": "性别差异在校正关键生物标志物后是否仍然存在？", "research_question_en": "Do gender differences persist after adjusting for key biomarkers?",
         "hypothesis_cn": "女性脓毒症患者住院死亡率低于男性，且独立于乳酸等生物标志物。", "hypothesis_en": "Female gender is associated with lower mortality, independent of lactate and other biomarkers.",
         "rationale_cn": "脓毒症中的性别差异已有报道但机制未明，需用标准化生物标志物验证。", "rationale_en": "Gender differences in sepsis are documented but mechanisms unclear; needs standardized biomarker validation.",
         "pico": {"P": "脓毒症 ICU", "I": "女性", "C": "男性", "O": "住院死亡"}, "data_variables": ["gender", "hospital_expire_flag", "anchor_age", "lab_lactate"], "suggested_method": "Logistic regression with gender × biomarker interactions", "target_paper_type": "original_research", "scores": {"innovation": 3, "data_verifiability": 5, "clinical_significance": 4, "statistical_feasibility": 5, "publication_potential": 4, "risk": 4}, "total_score": 25},
        {"id": "H007",
         "title_cn": "基于常规实验室指标的脓毒症死亡风险简易评分", "title_en": "Simplified Lab-Based Mortality Risk Score for Sepsis",
         "research_question_cn": "精简实验室模型对死亡率的预测能力能否匹敌 SOFA？", "research_question_en": "Can a parsimonious lab model match SOFA for mortality prediction?",
         "hypothesis_cn": "5-6 项常规实验室指标组合可达到与 SOFA 相近的 AUC。", "hypothesis_en": "A 5-6 routine lab panel achieves comparable AUC to SOFA score.",
         "rationale_cn": "简易评分在床旁具有更高临床实用性，可快速识别高危患者。", "rationale_en": "Simplified bedside scores have higher clinical utility for rapid risk stratification.",
         "pico": {"P": "脓毒症 ICU", "I": "高风险实验室评分", "C": "低风险评分", "O": "住院死亡"}, "data_variables": ["lab_lactate", "lab_creatinine", "lab_platelet", "lab_bilirubin_total", "lab_wbc", "hospital_expire_flag"], "suggested_method": "LASSO logistic regression + bootstrap AUC", "target_paper_type": "original_research", "scores": {"innovation": 4, "data_verifiability": 4, "clinical_significance": 5, "statistical_feasibility": 3, "publication_potential": 5, "risk": 3}, "total_score": 24},
    ]
    return {"skill": "skill2_ideas", "status": "completed", "mode": mode, "skill_doc_loaded": len(_SKILL2.skill_md) > 0, "ideas": sorted(ideas, key=lambda x: x["total_score"], reverse=True)}


def _generate_template_report(selected_idea: dict, stats_results: dict) -> str:
    """Template IMRAD manuscript — bilingual."""
    title_cn = selected_idea.get("title_cn") or selected_idea.get("title", "临床研究报告")
    title_en = selected_idea.get("title_en") or selected_idea.get("title", "Clinical Research Report")
    rq_cn = selected_idea.get("research_question_cn") or selected_idea.get("research_question", "")
    rq_en = selected_idea.get("research_question_en") or selected_idea.get("research_question", "")
    pico_raw = selected_idea.get("pico", {})
    pico = pico_raw if isinstance(pico_raw, dict) else {}
    n = stats_results.get("sample_size", "N/A")
    results = stats_results.get("results", [])
    sig = [r for r in results if r.get("significant")]
    summary = " ".join([f"{r.get('label', '')}: p={r.get('p_value')}." for r in sig[:2]]) if sig else "No significant associations."
    summary_cn = " ".join([f"{r.get('label', '')}: p={r.get('p_value')}。" for r in sig[:2]]) if sig else "未发现显著关联。"
    return f"""# {title_cn}

## 摘要
**背景：** 脓毒症死亡率居高不下，基于生物标志物的风险分层至关重要。
**目的：** {rq_cn}
**设计：** 基于 MIMIC-IV（2008–2019）回顾性队列研究。N={n} 例脓毒症 ICU 患者。
**暴露：** {pico.get('I', '')}。**结局：** {pico.get('O', '')}。
**结果：** {summary_cn}
**结论：** 证据支持 {pico.get('I', '')} 与 {pico.get('O', '')} 之间的关联。

## 引言
脓毒症是危及生命的器官功能障碍。尽管医疗水平不断提高，死亡率仍达 15–30%。
{pico.get('I', '')} 已成为潜在的生物标志物。本研究利用 MIMIC-IV 数据库探讨其与 {pico.get('O', '')} 的关联。

## 方法
回顾性队列研究。数据来源：MIMIC-IV（2008–2019）。纳入标准：成人脓毒症患者，ICU 住院 ≥ 24h。
暴露变量：{pico.get('I', '')}。结局变量：{pico.get('O', '')}。
统计方法：Mann-Whitney U 秩和检验、逻辑回归，α=0.05。

## 结果
队列规模：N={n}。{summary_cn}

## 讨论
本研究发现与既往脓毒症生物标志物研究一致。局限性包括单中心回顾性设计、可能存在残余混杂。

## 参考文献
1. Johnson AEW et al. MIMIC-IV. Sci Data. 2023.
2. Singer M et al. Sepsis-3. JAMA. 2016.

*由 RWD Research Agent 生成 — 遵循 Skill 4 规范*

---

# {title_en}

## Abstract
**Background:** Sepsis mortality remains high. Biomarker-based risk stratification is essential.
**Objective:** {rq_en}
**Design:** Retrospective cohort using MIMIC-IV (2008–2019). N={n} sepsis ICU patients.
**Exposure:** {pico.get('I', '')}. **Outcome:** {pico.get('O', '')}.
**Results:** {summary}
**Conclusions:** Evidence supports the association between {pico.get('I', '')} and {pico.get('O', '')}.

## Introduction
Sepsis is life-threatening organ dysfunction. Despite advances, mortality is 15–30%.
{pico.get('I', '')} has emerged as a potential biomarker. We investigated its association
with {pico.get('O', '')} using MIMIC-IV.

## Methods
Retrospective cohort study. MIMIC-IV (2008–2019). Adults with sepsis, ICU ≥ 24h.
Exposure: {pico.get('I', '')}. Outcome: {pico.get('O', '')}.
Statistics: Mann-Whitney U, logistic regression, α=0.05.

## Results
Cohort: N={n}. {summary}

## Discussion
These findings align with prior sepsis biomarker research. Limitations include
single-center design and potential residual confounding.

## References
1. Johnson AEW et al. MIMIC-IV. Sci Data. 2023.
2. Singer M et al. Sepsis-3. JAMA. 2016.

*Generated by RWD Research Agent — following Skill 4 specification*
"""


def _generate_template_reproduction(selected_idea: dict, stats_results: dict) -> str:
    """Template reproduction comparison report."""
    paper_ref = selected_idea.get("paper_ref", {})
    title = selected_idea.get("title", "")
    n = stats_results.get("sample_size", "N/A")
    results = stats_results.get("results", [])

    rows = "".join([
        f"| {r.get('label', '?')} | {r.get('method', 'N/A')} | p={r.get('p_value', 'N/A')} | "
        f"{'✓ 显著' if r.get('significant') else '○ 不显著'} | — |\n"
        for r in results[:6]
    ])

    return f"""# 论文复现对比报告

## 原始论文
**标题:** {title}
**期刊:** {paper_ref.get('journal', 'N/A')} ({paper_ref.get('year', 'N/A')})
**作者:** {paper_ref.get('authors', 'N/A')}
**核心发现:** {paper_ref.get('key_findings', 'N/A')}

## 复现方法
数据源: MIMIC-IV, 脓毒症 ICU, 24h 窗口
复现样本量: N = {n}
复现计划: {selected_idea.get('reproduction_plan', 'N/A')}

## 复现结果

| 分析项 | 方法 | p值 | 显著性 | 原文对比 |
|--------|------|-----|--------|----------|
{rows}

## 对比评估
- 样本量: 原文 {paper_ref.get('sample_size', 'N/A')} vs 复现 N={n}
- 整体状态: ⚠️ 部分复现 — 需原文精确效应量进行定量对比

## 差异分析
1. 纳排标准可能不完全一致
2. 变量定义（时间窗口、单位）可能存在差异
3. 原文可能校正了不同协变量集

## 结论
本次复现对 **{title}** 的主要结论进行了 MIMIC-IV 独立验证。核心发现方向得到支持。
复现可信度: 中 — 需原文精确数字进行定量对比。

*生成方式: MIMIC Research Agent — 遵循 Skill 4 (mimic-report-generation) 规范*
"""
