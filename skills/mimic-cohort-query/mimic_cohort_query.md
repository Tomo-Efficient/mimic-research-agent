---
name: mimic_cohort_query
description: Build and execute MIMIC-IV SQL queries for patient cohort extraction based on a TaskContract.
---

# MIMIC Cohort Query

## Purpose

Translate the cohort definition from `task_contract.json` into MIMIC-IV SQL queries, execute them safely, and produce a cohort CSV with screening funnel documentation.

## Prerequisites

- `task_contract.json` exists in `/workspace/shared/`
- MIMIC-IV database is accessible (env vars set)
- psql client is installed

## Workflow

### Step 1: Verify Database Connection

```bash
psql -c "SELECT COUNT(*) FROM mimiciv_hosp.admissions;" 2>&1
```

If this fails, report connection error and stop.

### Step 2: Build Cohort SQL

Read `/workspace/shared/task_contract.json` and `/workspace/shared/paper_evidence.json`.

Map inclusion/exclusion criteria to MIMIC-IV tables. Common patterns:

**Adult ICU patients:**
```sql
WITH base AS (
  SELECT a.subject_id, a.hadm_id, i.stay_id
  FROM mimiciv_hosp.admissions a
  JOIN mimiciv_icu.icustays i ON a.hadm_id = i.hadm_id
  JOIN mimiciv_hosp.patients p ON a.subject_id = p.subject_id
  WHERE p.anchor_age >= 18
)
```

**Diagnosis filtering:**
```sql
SELECT DISTINCT subject_id, hadm_id
FROM mimiciv_hosp.diagnoses_icd
WHERE icd_code ~ '^(99591|99592|A419|A40[0-9])'
```

**First ICU stay only:**
```sql
SELECT DISTINCT ON (subject_id) subject_id, hadm_id, stay_id
FROM ...
ORDER BY subject_id, admittime
```

### Step 3: SQL Safety Check

Before executing ANY SQL file, run:
```bash
grep -iE '(DROP|DELETE|UPDATE|INSERT|ALTER|CREATE|TRUNCATE|GRANT)' /workspace/shared/cohort_query.sql
```

If this returns ANY matches, STOP and fix the SQL. Only SELECT is allowed.

### Step 4: Execute Query

```bash
psql -c "SET statement_timeout = '300000';"
psql --csv -f /workspace/shared/cohort_query.sql > /workspace/shared/cohort.csv
echo "Cohort row count:"
wc -l /workspace/shared/cohort.csv
```

### Step 5: Validate Cohort Size

Compare extracted cohort size with paper-reported sample size:

```bash
head -1 /workspace/shared/cohort.csv
tail -n +2 /workspace/shared/cohort.csv | wc -l
```

### Step 6: Document Screening Funnel

Create `/workspace/shared/funnel.json`:

```json
{
  "steps": [
    {"description": "Total ICU admissions", "count": 73181},
    {"description": "Adult patients (age >= 18)", "count": 63521},
    {"description": "First ICU stay only", "count": 53215},
    {"description": "Sepsis diagnosis", "count": 10234},
    {"description": "After exclusion criteria", "count": 8521}
  ],
  "final_cohort_size": 8521,
  "paper_reported_size": 8712,
  "size_difference_percent": 2.2,
  "within_tolerance": true
}
```

## SQL Patterns Reference

### Common MIMIC-IV Concepts

| Concept | Table | Example |
|---------|-------|---------|
| Demographics | `mimiciv_hosp.patients` | `anchor_age, gender` |
| Admissions | `mimiciv_hosp.admissions` | `admittime, dischtime, admission_type` |
| ICU stays | `mimiciv_icu.icustays` | `intime, outtime, first_careunit` |
| Diagnoses | `mimiciv_hosp.diagnoses_icd` | `icd_code, icd_version` |
| Procedures | `mimiciv_hosp.procedures_icd` | `icd_code, icd_version` |
| Medications | `mimiciv_hosp.prescriptions` | `drug, gsn` |
| Vitals | `mimiciv_icu.chartevents` | `itemid, valuenum` |
| Labs | `mimiciv_hosp.labevents` | `itemid, valuenum` |
| Derived scores | `mimiciv_derived.*` | SOFA, SAPS, etc. |

### Charlson Comorbidity Index

Common ICD code mappings:
- Myocardial infarction: ICD-9 410-410.9, ICD-10 I21-I22
- Congestive heart failure: ICD-9 428-428.9, ICD-10 I50
- COPD: ICD-9 490-496, ICD-10 J44
- Diabetes: ICD-9 250-250.9, ICD-10 E10-E14
- Renal disease: ICD-9 582-583, ICD-10 N18
- Cancer: ICD-9 140-172, ICD-10 C00-C26

## Guardrails

- Only SELECT statements. Verify with grep before every execution.
- Use CTEs for multi-step logic. Avoid temp tables.
- Document each screening step with row counts.
- Flag cohort size discrepancies >20% explicitly.
- If a required MIMIC table is missing or inaccessible, report it clearly rather than guessing.
- If the paper references concepts not available in standard MIMIC-IV tables, note them as `requires_derived_table`.
