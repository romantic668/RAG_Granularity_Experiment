# Verification Granularity Evaluation

**FINAL API INFERENCE**

Paired responses: 300 / 300. Source groups: 138.
Verifier: `gpt-5.4-mini-2026-03-17`. All annotated spans are the primary gold standard.

| Variant | Precision | Recall | Detection F1 | Accuracy | Character span F1 | Units per response | Requests per response | Mean ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| response | 41.8% | 74.5% | 53.6% | 76.3% | 16.6% | 1.00 | 1.00 | 1339.3 |
| sentence | 34.4% | 94.5% | 50.5% | 66.0% | 29.3% | 6.48 | 6.48 | 58237.4 |
| atomic | 29.8% | 92.7% | 45.1% | 58.7% | 30.2% | 9.45 | 10.46 | 44464.6 |

## Interpretation and limits

Primary detection metrics compare response-level predictions with human hallucination labels. They do not measure answer quality against a reference answer.
Character span F1 compares the union of flagged source ranges with human ranges. A flagged response marks the whole response; a flagged sentence marks its whole sentence. Atomic claims use extracted quote ranges. This is a custom localization measure, not the official word-level RAGTruth benchmark score.
Atomic quote alignment requires manual audit. Exact string matching does not prove semantic faithfulness, atomicity, or complete fact coverage. Sentence fallback units remain in the evaluation and are counted.
The primary policy includes implicit_true annotations because the task is strict support by the provided evidence. summary.json also reports a predeclared sensitivity analysis excluding them.
API latency includes network and service load. Use fresh output folders for repeated runs. Historical cache timings reconstructed during resumption should be disclosed.
USD estimates use configuration prices, which can become stale. Actual billing is authoritative.

## Paired source-cluster bootstrap

- response: F1 95% interval 43.5% to 62.3%
- sentence: F1 95% interval 41.4% to 58.6%
- atomic: F1 95% interval 37.0% to 53.2%
- sentence minus response: -3.11 percentage points; paired 95% interval -9.93 to 4.40 points.
- atomic minus response: -8.46 percentage points; paired 95% interval -15.33 to -1.04 points.

## Audit counts

- response: 0 fallback units / 300 units; 0 resumed calls.
- sentence: 0 fallback units / 1944 units; 0 resumed calls.
- atomic: 108 fallback units / 2836 units; 0 resumed calls.

## Files

metrics.csv is the slide table. errors.csv contains false positives and false negatives. claim_audit.csv and response_audit.csv are blank manual review forms. predictions.jsonl and call_cache retain actual outputs. Keep run_manifest.json and the frozen protocol with the report.
