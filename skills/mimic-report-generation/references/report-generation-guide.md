---
name: mimic-report-generation
description: Generates a complete clinical research paper in IMRAD format from MIMIC-IV statistical analysis results. Takes TaskContract, cohort data, EDA results, statistical test outputs, and original paper evidence as inputs. Produces a publication-ready manuscript with structured abstract, introduction, methods, results (including Table 1 and Kaplan-Meier curves), discussion, and Vancouver-style references. Designed for the MIMIC agent architecture as Skill 4 (报告生成).
allowed-tools: Read Write Edit Bash
license: MIT license
metadata:
  version: "1.0"
  skill-author: "HKAI-SCI"
  mimir_agent_stage: "Skill 4"
  upstream_skills: ["Skill 1 (EDA)", "Skill 3 (统计检验)"]
quality:
  grade: A
  score: 109
  date: 2026-06-29
---

# MIMIC Report Generation (Skill 4)

## Overview

This skill generates a complete clinical research paper from MIMIC-IV database analysis results. It is the final stage (Skill 4) of the MIMIC agent pipeline, consuming outputs from Skill 1 (data preprocessing & EDA) and Skill 3 (statistical hypothesis testing) to produce a publication-ready manuscript in IMRAD format.

The skill is purpose-built for clinical epidemiology papers using the MIMIC-IV database. It enforces STROBE reporting guidelines for observational studies, Vancouver citation style, and produces manuscripts suitable for submission to critical care and clinical informatics journals.

**Core Principle: Every number, table, and figure in the manuscript must be traceable to an upstream artifact.** The manuscript is a faithful representation of computed results — never fabricate, embellish, or round numbers beyond what the statistical output provides.

## When to Use This Skill

This skill should be used when:
- Skill 3 (statistical testing) has completed and produced `model_results.json`
- The user requests generation of a clinical research paper from MIMIC-IV analysis
- A reproduction study needs a formal manuscript comparing reproduced results against the original paper
- A novel hypothesis discovered during EDA needs to be written up as a research paper
- The user says "generate report", "write paper", "生成论文", "写报告", "出报告", "写文章", "生成报告", "produce manuscript", or "finalize paper"

## Prerequisites

Before executing this skill, verify the following artifacts exist in `/workspace/shared/`:

| Artifact | Source | Required | Content |
|----------|--------|----------|---------|
| `task_contract.json` | Skill 1/2 | Yes | Research hypothesis, exposure, outcome, covariates, study design |
| `cohort.csv` | Skill 2 | Yes | Patient-level extracted cohort data |
| `paper_evidence.json` | Skill 1 | For reproduction | Original paper's reported results for comparison |
| `baseline_table.csv` | Skill 3 | Yes | TableOne-style baseline characteristics |
| `model_results.json` | Skill 3 | Yes | Statistical model outputs (OR/HR, CI, p-values, model diagnostics) |
| `funnel.json` | Skill 2 | Yes | Screening funnel with inclusion/exclusion counts |

**Validation command:**
```bash
paper-repro validate-inputs --stage report
```

If any required artifact is missing, halt and report which artifact is needed from which upstream skill.

## Workflow

### Stage 1: Assemble Manuscript Data

Collect and validate all inputs into a single structured manuscript data object.

**Step 1.1: Load all artifacts**

```python
import json, pandas as pd

# Load structured inputs
with open('/workspace/shared/task_contract.json') as f:
    contract = json.load(f)

with open('/workspace/shared/model_results.json') as f:
    model_results = json.load(f)

with open('/workspace/shared/funnel.json') as f:
    funnel = json.load(f)

# Load tabular inputs
cohort = pd.read_csv('/workspace/shared/cohort.csv')
baseline = pd.read_csv('/workspace/shared/baseline_table.csv')

# Load paper evidence if available (reproduction mode)
try:
    with open('/workspace/shared/paper_evidence.json') as f:
        paper_evidence = json.load(f)
    reproduction_mode = True
except FileNotFoundError:
    paper_evidence = None
    reproduction_mode = False
```

**Step 1.2: Extract manuscript parameters**

From `task_contract.json`, extract:
- `study_design`: cohort / case-control / cross-sectional
- `exposure`: variable name, definition, categorization
- `outcome`: variable name, definition, time-to-event or binary
- `population`: inclusion/exclusion criteria summary
- `covariates`: list of adjustment variables
- `subgroup_variables`: variables for subgroup analysis (if any)
- `sensitivity_analyses`: planned sensitivity analyses (if any)

**Step 1.3: Extract statistical results**

From `model_results.json`, extract:
- Primary model: effect estimate, 95% CI, p-value
- Adjusted model: effect estimate, 95% CI, p-value
- Subgroup results: forest plot data (if available)
- Model diagnostics: C-statistic/AUC, calibration metrics, DCA results (if available)
- Survival results: log-rank p-value, KM curve data (if time-to-event)

**Step 1.4: Compute manuscript-level summary statistics**

```python
# Cohort summary
n_total = len(cohort)
n_exposed = (cohort[contract['exposure']['variable']] == 1).sum()
n_unexposed = n_total - n_exposed
n_events = cohort[contract['outcome']['variable']].sum()
event_rate = n_events / n_total * 100

# Follow-up summary (if time-to-event)
if 'time_col' in contract['outcome']:
    median_followup = cohort[contract['outcome']['time_col']].median()
    iqr_followup = (
        cohort[contract['outcome']['time_col']].quantile(0.25),
        cohort[contract['outcome']['time_col']].quantile(0.75)
    )
```

### Stage 2: Generate Figures

**Step 2.1: Kaplan-Meier Curve**

If the outcome is time-to-event, generate the KM curve using the pre-built stats_tools:

```python
from repro_agent.stats_tools.km_survival import run_km_analysis
from repro_agent.stats_tools.plot_utils import set_journal_style, save_figure

set_journal_style()

km_result = run_km_analysis(
    df=cohort,
    time_col=contract['outcome']['time_col'],
    event_col=contract['outcome']['variable'],
    group_col=contract['exposure']['variable'],
    group_labels=['Control', 'Exposed'],
    output_dir='/workspace/results/'
)
```

**KM curve requirements:**
- Risk table below x-axis showing number at risk at each time point
- Log-rank p-value displayed on plot
- Group labels with event counts: "Exposed (n=X, events=Y)"
- Y-axis: cumulative survival probability (0 to 1)
- X-axis: time in appropriate units (days, months, years)
- Colors: colorblind-friendly palette
- Resolution: 300 DPI minimum

**Step 2.2: Forest Plot (if subgroup analysis exists)**

```python
from repro_agent.stats_tools.subgroup_analysis import run_subgroup_analysis

if 'subgroup_results' in model_results:
    run_subgroup_analysis(
        df=cohort,
        time_col=contract['outcome']['time_col'],
        event_col=contract['outcome']['variable'],
        exposure_col=contract['exposure']['variable'],
        subgroup_vars=contract['subgroup_variables'],
        output_dir='/workspace/results/'
    )
```

**Step 2.3: Additional Figures (conditional)**

Generate only if the corresponding analysis was performed:

| Analysis Performed | Figure to Generate | Tool |
|-------------------|-------------------|------|
| Logistic regression | ROC curve | `stats_tools.roc_analysis.run_roc_analysis()` |
| Prediction model | Calibration curve | `stats_tools.calibration_curve.plot_calibration()` |
| Prediction model | Decision curve | `stats_tools.dca_analysis.run_dca_analysis()` |
| Cox model | Nomogram | `stats_tools.nomogram.create_nomogram()` |
| Competing risks | CIF curves | `stats_tools.competing_risk.run_competing_risk()` |
| Non-linear effects | RCS dose-response | `stats_tools.rcs_analysis.run_rcs_analysis()` |

**Never generate a figure for an analysis that was not performed.** Each figure must correspond to a section in `model_results.json`.

### Stage 3: Write Manuscript Sections

Write each section in flowing prose. Use the two-stage approach: outline key points first, then expand to full paragraphs.

**Step 3.1: Title**

Construct title following clinical epidemiology conventions:

```
Pattern 1 (exposure-outcome): "Association Between [Exposure] and [Outcome] in [Population]: A [Design] Study Using the MIMIC-IV Database"
Pattern 2 (reproduction): "[Original Paper Title]: A Reproduction Study Using MIMIC-IV"
Pattern 3 (novel finding): "[Key Finding]: [Exposure] and [Outcome] in Critically Ill Patients"
```

**Step 3.2: Structured Abstract**

Follow the 250-word structured abstract format required by most medical journals:

- **Background**: 2-3 sentences on clinical context and knowledge gap
- **Objective**: 1 sentence stating the research question
- **Design, Setting, and Participants**: Study design, data source (MIMIC-IV), inclusion period, final cohort size
- **Exposure**: Definition and categorization of the exposure variable
- **Main Outcomes and Measures**: Primary and secondary outcome definitions
- **Results**: Key numbers — cohort size, event count, primary effect estimate with 95% CI and p-value, key secondary findings
- **Conclusions and Relevance**: 2-3 sentences on clinical interpretation and implications

**Step 3.3: Introduction**

Structure (3-4 paragraphs, ~500 words):

1. **Paragraph 1 — Clinical Background**: Establish the clinical importance of the outcome. Cite key epidemiological data (prevalence, mortality, burden).
2. **Paragraph 2 — The Exposure**: Introduce the exposure variable, its biological/clinical rationale, and prior evidence linking it to the outcome.
3. **Paragraph 3 — Knowledge Gap**: Identify what is unknown or controversial. Why existing evidence is insufficient (small samples, conflicting results, different populations).
4. **Paragraph 4 — Study Objective**: State the research question/hypothesis clearly. "We therefore aimed to investigate the association between [exposure] and [outcome] in [population] using the MIMIC-IV database."

**Step 3.4: Methods**

Structure following STROBE guidelines:

**Study Design and Data Source**
- Retrospective cohort study using MIMIC-IV (version 2.2 or as appropriate)
- Beth Israel Deaconess Medical Center, 2008-2019
- Describe database characteristics briefly

**Study Population**
- List all inclusion criteria with rationale
- List all exclusion criteria with rationale
- Reference the screening funnel (`funnel.json`) for exact counts
- State that the MIMIC-IV database is de-identified, IRB-approved

**Exposure**
- Precise definition of the exposure variable
- How it was measured/extracted (lab item IDs, chart event item IDs)
- Categorization method (tertiles, quartiles, clinical cutoffs, continuous)
- Timing of measurement (e.g., "first 24 hours of ICU admission")

**Outcome**
- Primary outcome definition
- Secondary outcome definitions (if any)
- For time-to-event: origin (time zero), scale (days), censoring rules
- ICD code lists for diagnosis-based outcomes

**Covariates**
- List all covariates included in adjusted models
- Justification for each (clinical relevance, prior literature)
- How continuous variables were modeled (linear, spline, categorized)

**Statistical Analysis**
- Descriptive statistics: continuous (mean±SD or median[IQR]), categorical (n,%)
- Comparison method: t-test/Mann-Whitney, chi-square/Fisher's exact
- Primary model: logistic regression / Cox proportional hazards
- Model building strategy: pre-specified vs. stepwise
- Proportional hazards assumption testing (if Cox)
- Subgroup analyses: variables, interaction testing
- Sensitivity analyses: description of each
- Missing data handling: complete case / multiple imputation / indicator method
- Software: Python version, key packages (lifelines, statsmodels, scikit-learn)
- Significance level: two-sided α = 0.05 unless otherwise specified

**Step 3.5: Results**

Structure following the "primary → secondary → sensitivity" hierarchy:

**Cohort Characteristics**
- Start with the screening funnel: "A total of N patients were screened; after applying exclusion criteria, N patients were included in the final cohort."
- Reference Table 1
- Highlight key differences between exposure groups
- Report median follow-up time for time-to-event outcomes

**Primary Analysis**
- State the primary effect estimate with 95% CI and p-value
- Report both unadjusted and adjusted results
- Reference the KM curve figure if applicable
- Report absolute event rates by exposure group

**Subgroup Analyses**
- Describe consistency or heterogeneity of effect across subgroups
- Report interaction p-values
- Reference forest plot figure

**Sensitivity Analyses**
- Report results of each sensitivity analysis
- Note whether results are consistent with primary analysis

**Secondary Outcomes** (if applicable)
- Report secondary outcome results in order of pre-specified importance

**Writing rules for Results:**
- Report exact p-values (not "p < 0.05") unless p < 0.001
- Always pair effect estimates with 95% CIs
- Use consistent decimal places (2 for OR/HR, 3 for p-values)
- Never interpret results in the Results section (that belongs in Discussion)

**Step 3.6: Discussion**

Structure (6-8 paragraphs, ~1000 words):

1. **Summary of Key Findings**: 1 paragraph restating the primary result in plain language
2. **Comparison with Prior Literature**: 2-3 paragraphs comparing results to published studies. Cite specific papers with their effect estimates. Explain similarities and differences.
3. **Biological/Clinical Plausibility**: 1 paragraph on potential mechanisms linking exposure to outcome
4. **Clinical Implications**: 1 paragraph on how findings might influence clinical practice or risk stratification
5. **Strengths**: 1 paragraph on methodological strengths (large sample, granular ICU data, robust adjustment, sensitivity analyses)
6. **Limitations**: 1 paragraph honestly addressing:
   - Residual confounding (observational design)
   - Single-center data (external validity)
   - Measurement error in exposure/outcome definitions
   - Missing data
   - Unmeasured confounders
   - Generalizability beyond ICU population
7. **Future Research**: 1 paragraph suggesting next steps
8. **Conclusions**: 1-2 sentences, no overstatement

**Step 3.7: References**

Generate Vancouver-style (numbered) references. For each citation in the text:

```python
references = [
    {
        "id": 1,
        "authors": "Johnson AEW, Pollard TJ, Shen L, et al.",
        "title": "MIMIC-IV, a freely accessible electronic health record dataset.",
        "journal": "Sci Data",
        "year": 2023,
        "volume": "10",
        "issue": "1",
        "pages": "1",
        "doi": "10.1038/s41597-022-01899-x"
    },
    # ... additional references
]
```

**Required citations in every MIMIC paper:**
1. MIMIC-IV dataset paper (Johnson et al., 2023)
2. MIMIC-III paper (Johnson et al., 2016) — cite when referencing MIMIC's history
3. Key clinical references for the outcome (3-5 papers)
4. Key references for the exposure (3-5 papers)
5. Methodological references (STROBE statement, specific statistical methods)

**Reference format:**
```
[1] Johnson AEW, Pollard TJ, Shen L, et al. MIMIC-IV, a freely accessible electronic health record dataset. Sci Data. 2023;10(1):1. doi:10.1038/s41597-022-01899-x
```

### Stage 4: Assemble and Format

**Step 4.1: Table 1 Formatting**

Format the baseline table for publication:

```python
from repro_agent.stats_tools.baseline_table import create_table1

table1 = create_table1(
    df=cohort,
    group_col=contract['exposure']['variable'],
    continuous_vars=contract['continuous_covariates'],
    categorical_vars=contract['categorical_covariates'],
    output_format='latex'  # or 'markdown' for draft
)
```

**Table 1 requirements:**
- Columns: Variable name | Total (N=XXX) | Unexposed (N=XXX) | Exposed (N=XXX) | p-value
- Continuous variables: mean ± SD or median [IQR] based on distribution
- Categorical variables: n (%)
- SMD (standardized mean difference) column optional but recommended
- Footnote: list of abbreviations, test used for p-values

**Step 4.2: Figure Legends**

Write self-contained figure legends:

```
Figure 1. Kaplan-Meier survival curves for [outcome] stratified by [exposure] groups.
The shaded regions represent 95% confidence intervals. The number of patients at risk
at each time point is shown below the x-axis. The p-value was calculated using the
log-rank test.
```

**Step 4.3: Manuscript Assembly**

Assemble sections in order:
1. Title page (title, authors, affiliations, corresponding author, word count)
2. Abstract (structured, ≤250 words)
3. Introduction
4. Methods
5. Results
6. Discussion
7. Conclusions
8. Acknowledgments
9. Author Contributions
10. Conflicts of Interest
11. Funding
12. References
13. Tables (Table 1, additional tables)
14. Figure Legends
15. Figures (embedded or appended)

**Step 4.4: Output Formats**

Generate the manuscript in multiple formats:

```bash
# Markdown (primary, editable)
cp manuscript.md /workspace/results/manuscript.md

# PDF (if LaTeX available)
pandoc manuscript.md -o /workspace/results/manuscript.pdf --pdf-engine=xelatex

# DOCX (for submission systems)
pandoc manuscript.md -o /workspace/results/manuscript.docx
```

### Stage 5: Reproduction Comparison (if applicable)

If `paper_evidence.json` exists, generate a reproduction alignment section:

**Step 5.1: Compare cohort sizes**

```python
original_n = paper_evidence['cohort']['n']
reproduced_n = len(cohort)
delta_pct = abs(reproduced_n - original_n) / original_n * 100
```

**Step 5.2: Compare effect estimates**

```python
original_hr = paper_evidence['primary_result']['effect_estimate']
reproduced_hr = model_results['primary_model']['effect_estimate']
# Check if reproduced CI overlaps with original point estimate
```

**Step 5.3: Generate alignment table**

| Metric | Original Paper | Reproduction | Agreement |
|--------|---------------|--------------|-----------|
| Cohort size | N=X | N=Y | Δ = Z% |
| Primary HR/OR | X.XX (95% CI: A-B) | Y.YY (95% CI: C-D) | ✓/✗ |
| Event rate | X% | Y% | Δ = Z pp |

**Step 5.4: Document deviations**

For any metric where reproduction differs from the original by >20% or where CIs do not overlap:
- Categorize the deviation: cohort / variable definition / model specification / data version
- Document in the reproduction report
- Discuss in the manuscript's limitations section

### Stage 6: Quality Assurance

Before finalizing, run these checks:

**Content checks:**
- [ ] Every number in the manuscript matches `model_results.json`
- [ ] Table 1 values match `baseline_table.csv`
- [ ] Cohort size matches `funnel.json` final count
- [ ] All cited references have complete Vancouver formatting
- [ ] Abstract word count ≤ 250
- [ ] No interpretation in Results section
- [ ] Limitations section includes at least 5 specific limitations

**Formatting checks:**
- [ ] IMRAD structure intact
- [ ] Figures referenced in order (Figure 1, Figure 2, ...)
- [ ] Tables referenced in order (Table 1, Table 2, ...)
- [ ] All abbreviations defined at first use
- [ ] P-values reported consistently (exact values, not inequalities)

**Reproducibility checks (if reproduction mode):**
- [ ] Original paper's primary result cited and compared
- [ ] Deviations documented with explanations
- [ ] Reproduction status clearly stated (full/partial/failed)

## Integration with MIMIC Agent Pipeline

```
Skill 1 (EDA) ──► Skill 2 (Cohort) ──► Skill 3 (Stats) ──► Skill 4 (Report)
     │                  │                    │                    │
     ▼                  ▼                    ▼                    ▼
eda_results.json   cohort.csv          model_results.json   manuscript.md
paper_evidence.json funnel.json        baseline_table.csv   figures/*.png
                   task_contract.json                       reproduction_report.md
```

**Input contract:** Skill 4 reads from `/workspace/shared/` and writes to `/workspace/results/`.

**Error handling:**
- If `model_results.json` is missing → "Statistical results not found. Run Skill 3 first."
- If `cohort.csv` is missing → "Cohort data not found. Run Skill 2 first."
- If `task_contract.json` is missing → "Task contract not found. Run Skill 1 first."
- If a figure generation fails → Log the error, skip that figure, note in manuscript

## Anti-Patterns

<NEVER>
- Never fabricate or invent statistical results not present in `model_results.json` — every number must be traceable to computed output (consequence: scientific fraud, irreproducible manuscript)
- Never write bullet points in the final manuscript — all text must be flowing prose in complete paragraphs (consequence: manuscript rejected by journals for poor formatting)
- Never interpret results in the Results section — interpretation belongs exclusively in Discussion (consequence: reviewers flag as methodological error)
- Never omit the MIMIC-IV dataset citation — Johnson et al., 2023 must be reference [1] in every paper (consequence: failure to credit data source, potential license violation)
- Never generate figures for analyses that were not performed — each figure must correspond to a section in model_results.json (consequence: misleading readers about what was actually analyzed)
- Never report "p < 0.05" when exact p-values are available — report exact values (e.g., p = 0.032) unless p < 0.001 (consequence: loss of information, discouraged by AMA and ICMJE guidelines)
- Never claim reproduction success if the cohort size differs by >20% or CIs do not overlap — clearly label as partial reproduction (consequence: misrepresentation of reproducibility)
- Never use first-person plural ("we") excessively in Methods — prefer passive voice for methodological descriptions (consequence: perceived as informal by some journals, though this is style-dependent)
</NEVER>

## Examples

### Example 1: Reproduction Study Report

**Input scenario:** User has completed reproduction of a paper on "Triglyceride-Glucose Index and Sepsis Mortality" and wants a manuscript.

**Expected output structure:**
```
Title: Association Between Triglyceride-Glucose Index and 28-Day Mortality
       in Sepsis Patients: A Reproduction Study Using MIMIC-IV

Abstract: [Structured, 248 words]
  Background: The triglyceride-glucose (TyG) index has been proposed...
  Objective: To reproduce the reported association between TyG index...
  Design: Retrospective cohort study using MIMIC-IV (2008-2019).
  Results: Among X,XXX sepsis patients, ...
  Conclusions: This reproduction [confirmed / partially confirmed / did not confirm]...

Introduction: [4 paragraphs]
  - Sepsis burden and mortality
  - TyG index as metabolic marker
  - Original study findings and rationale for reproduction
  - Study objective

Methods: [Full STROBE-compliant]
  - MIMIC-IV database description
  - Inclusion: sepsis (ICD codes), ICU admission, age ≥18
  - Exclusion: ICU stay <24h, missing TyG components
  - Exposure: TyG index quartiles
  - Outcome: 28-day all-cause mortality
  - Covariates: age, sex, SOFA, Charlson, lactate, etc.
  - Statistics: Cox proportional hazards, KM curves, subgroup analyses

Results:
  - Cohort: N=X,XXX, events=Y,YYY (Z.Z%)
  - Table 1: baseline characteristics by TyG quartile
  - Figure 1: KM curves (log-rank p = 0.XXX)
  - Primary: HR X.XX (95% CI X.XX-X.XX, p = 0.XXX) for Q4 vs Q1
  - Subgroup: forest plot showing consistent association
  - Reproduction alignment: cohort size Δ = X%, HR Δ = Y%

Discussion: [8 paragraphs]
  - Summary, comparison with original, mechanisms, implications,
    strengths, limitations, future research, conclusions

References: [25-35 references, Vancouver style]
```

### Example 2: Novel Hypothesis Paper

**Input scenario:** User discovered a novel association during EDA and wants to write it up.

**Key difference from reproduction:** No `paper_evidence.json`, no reproduction comparison section. Introduction must build the case from first principles rather than referencing a specific prior study to reproduce.

**Expected modifications:**
- Introduction: broader literature review establishing biological plausibility
- Discussion: emphasis on novelty, no reproduction alignment table
- Title: Pattern 3 (key finding focused)

## Edge Cases

### Missing Data in Baseline Table
If some covariates have >10% missing data, add a footnote to Table 1 indicating the proportion missing. If multiple imputation was used, report both pre- and post-imputation distributions.

### Non-Significant Primary Result
A null result is still publishable. The manuscript should:
- Emphasize precision of the estimate (narrow CIs)
- Discuss clinical relevance of the confidence interval bounds
- Avoid language implying "no association" — use "no statistically significant association was observed"
- Strengthen the limitations discussion

### Very Large Cohort (>50,000)
For very large cohorts:
- SMD may be more appropriate than p-values for baseline comparisons
- Consider reporting both statistical and clinical significance
- KM curves may need time-truncation for readability

### Multiple Testing
If multiple outcomes or exposures were tested:
- Report both unadjusted and adjusted (e.g., Bonferroni) p-values
- Clearly designate the primary analysis vs. exploratory analyses
- Use hierarchical testing where appropriate

### Competing Risks
If competing risks analysis was performed:
- Report both cause-specific HR and subdistribution HR (Fine-Gray)
- Include CIF curves alongside or instead of KM curves
- Explain the competing event in Methods

## Output Files

After successful execution, the following files are produced:

```
/workspace/results/
  manuscript.md              # Full manuscript in Markdown
  manuscript.pdf             # PDF rendering (if LaTeX available)
  manuscript.docx            # DOCX for journal submission
  table1.png                 # Formatted Table 1
  km_curve.png               # Kaplan-Meier survival curve
  forest_plot.png            # Subgroup forest plot (if applicable)
  roc_curve.png              # ROC curve (if applicable)
  calibration_curve.png      # Calibration curve (if applicable)
  reproduction_report.md     # Reproduction alignment report (if applicable)
  figure_legends.md          # All figure legends
  references.bib             # Bibliography in BibTeX format
```

## Troubleshooting

### pandoc Not Installed
If `pandoc` is not available for PDF/DOCX generation:
```bash
apt-get update && apt-get install -y pandoc
```
Fallback: Output Markdown only and inform the user that PDF/DOCX requires pandoc.

### LaTeX Not Installed (for PDF)
If `xelatex` is not available:
```bash
apt-get install -y texlive-xetex texlive-latex-recommended
```
Fallback: Skip PDF generation, output Markdown and DOCX only.

### Figure Generation Fails
If `run_km_analysis()` or other stats_tools functions fail:
1. Check that `cohort.csv` has the expected columns
2. Verify that `task_contract.json` variable names match column names
3. Log the specific error and skip that figure
4. Add a note in the manuscript: "[Figure X could not be generated: error details]"

### model_results.json Has Unexpected Structure
If the JSON schema doesn't match expected keys:
1. Print the available keys: `print(model_results.keys())`
2. Map available results to manuscript sections as best as possible
3. Flag missing expected sections in the reproduction report
4. Never fabricate missing results — use "Not available" placeholders

### Cohort Size Mismatch
If `funnel.json` final count doesn't match `len(cohort)`:
1. Use `len(cohort)` as the authoritative count (actual data)
2. Note the discrepancy in the reproduction report
3. Flag as a potential Skill 2 (cohort extraction) issue

### Non-ASCII Characters in Manuscript
If patient data or variable names contain non-ASCII characters:
1. Ensure all text output uses UTF-8 encoding
2. For LaTeX: escape special characters (&, %, $, #, _, {, }, ~, ^, \)
3. For Vancouver references: use ASCII transliterations of author names

## References

This skill references the following guidelines and standards:

- **STROBE Statement**: von Elm E, Altman DG, Egger M, et al. The Strengthening the Reporting of Observational Studies in Epidemiology (STROBE) statement: guidelines for reporting observational studies. *Lancet*. 2007;370(9596):1453-1457.
- **ICMJE Recommendations**: International Committee of Medical Journal Editors. Recommendations for the Conduct, Reporting, Editing, and Publication of Scholarly Work in Medical Journals. 2024.
- **MIMIC-IV Citation**: Johnson AEW, Pollard TJ, Shen L, et al. MIMIC-IV, a freely accessible electronic health record dataset. *Sci Data*. 2023;10(1):1.
- **Vancouver Style**: Patrias K. Citing Medicine: The NLM Style Guide for Authors, Editors, and Publishers. 2nd ed. National Library of Medicine; 2007.
