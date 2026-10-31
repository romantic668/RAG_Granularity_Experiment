# Atomic Extraction Fixes and Next Steps

> **Historical development note (v3b to v4b).** It records how the atomic extraction prompt was revised during the pilot stage. The later v4c revision, the pilot and the formal 300-response run are documented in `README.md`, `pilot_v4c_review.md` and `protocol_v4c.json`. The commands at the end are kept for the record and refer to the old v3 workflow.

Problem identified: in the raw API extraction output, `text` sometimes repeated the complete compound sentence while only the quoted fragments were separate. The program then really did verify that compound sentence. The CSV export was not turning atomic facts back into whole sentences.

Fix: the output now uses an explicit `claim_text` field. The prompt provides fictional examples of splitting weather attributes and a person's events, stresses that each claim contains exactly one assertion, and requires negation, scope, conditions and uncertainty to be preserved. Extraction has its own `low` reasoning effort, while verification stays at `none` for every group and the fixed model is unchanged. The rule is not a simple split on "and". Quote matching still does not prove semantic correctness; manual audit is required.

## v3b: additions after the first small check

The 3 real extraction results uploaded by the user were reviewed: 4, 10 and 5 facts, 19 in total. There were no duplicate texts or quote fallbacks, and all quote positions and ranges were valid. The review still found:

- A care facility described as "like a hospital and with shared rooms" was merged into a single fact.
- In "may have ... and offer ...", the second predicate lost "may", turning a possibility into a definite statement.
- The location and date of the follow-up weather sentences were not completed in the fact text, and the wind-speed quote took only the number, without the usable relation word "winds".

v3b adds fictional workshop/studio examples that demonstrate attribute splitting and the scope of modal words, and requires context to be completed and relation words to be quoted. These are train-stage process fixes; test performance was not used. The real effect still needs a second round of checks.

## v4b: local manual prompt edit (on top of v3b, edited directly in `ragstudy/core.py`)

Do not run `apply_http_fix.py` again: it contains the v3b patch, `core.py` already has local modifications, and the installer will refuse to overwrite it.

New rules:

- Coordinated verbs are split into separate facts.
- Purpose, manner, instrument and duration phrases stay with the action they belong to.
- A shared modifier stays on every coordinated item.
- Framing phrases such as "the main X are:" are kept in every fact and must not be weakened.
- A fact must not start with an unresolved it/they/these.
- Every quotation must be one contiguous, unmodified substring of the original sentence (no `...`, no square brackets, no change of first-letter case).

The new examples use app features, a kettle and a vent, which are unrelated to the samples tested.

Verification (extraction diagnostics only, no verifier, about 0.29 USD):

- On `data/atomic_check_heldout8.jsonl`, which was not used for tuning the prompt (8 samples; v3b twice, v4b twice), fallbacks, fact counts and modal-word preservation were roughly level. There was **no reliable improvement** and no clear regression; differences are within random fluctuation.
- On `hard8` (the samples where the problems were found, already used for tuning, so they only show whether the problem disappeared): "take out and let rest" left unsplit 2/3 to 0/3; "good" kept on the coordinated items 1/3 to 3/3; the `main` framing was partly preserved, up from 0/3.
- Still present: unresolved "These ...", non-"main" framings (such as "advantages") dropped, an occasional missed fact (the renewal term of 14185), an occasional purpose clause split off, and an occasional quote case mismatch that causes a `sentence_fallback`. Extraction is stochastic: the same prompt gives different results across runs.
- Conclusion: further prompt changes would yield little and risk overfitting to these samples. In the pilot stage, quantify these problems with `claim_audit.csv` and state them as limitations; for the formal experiment, repeat the atomic group (`--repeat`) and report the variability.

## Execution at that time

Download the latest `apply_atomic_fix.py`, overwrite the previous installer of the same name, place it in the same directory as `experiment.py`, and run in PowerShell:

```powershell
py -3 apply_atomic_fix.py
py -3 atomic_tools.py check --out runs/atomic_check_v3b --max-requests 6 --max-usd 0.5
```

Installation makes no paid API calls. The last command normally calls the extraction model 3 times; retries count toward the cap of 6, and 0.5 USD is a soft threshold. The verifier was not run, so there are no detection scores.

Afterwards, upload `runs/atomic_check_v3b/atomic_check.json`. It contains the original text, the split facts and the quotes, so single-fact status, meaning preservation, completeness and source mapping can be checked. Do not judge a pass by the number of facts alone. Samples 15798, 16825 and 13958 come from structural problems in the old train pilot and are used for diagnosis, not for estimating detection performance.

## Pilot after the check passes

```powershell
py -3 atomic_tools.py reuse --source runs/pilot_v2 --out runs/pilot_v3
py -3 experiment.py run --input data/pilot60.jsonl --out runs/pilot_v3 --phase pilot --max-requests 6000 --max-usd 5
py -3 experiment.py evaluate --input data/pilot60.jsonl --run-dir runs/pilot_v3 --bootstrap 1000
py -3 experiment.py estimate --run-dir runs/pilot_v3 --n 300
```

If the old directory has a different name, change `--source`. `reuse` makes no API calls; it only copies into the new directory the response/sentence cache whose data, complete request, model and raw result all match. The 501 calls in the uploaded pilot_v2 can be reused; the old atomic calls are not imported at all. Historical latency and per-method cost are kept, the report discloses the reuse source, and the new directory's budget counts only new requests. After `run` has started, resuming re-executes only `run`, not `reuse`. A frozen protocol from an older version cannot be reused.

After pilot_v3 completes, audit `claim_audit.csv`, `response_audit.csv` and the error cases as in the original manual, and record the actual conclusions in `templates/pilot_audit_notes.md`. Passing the small diagnostic does not mean all 60 extractions are correct.

## Formal 300 responses after the audit

```powershell
py -3 experiment.py freeze --pilot-dir runs/pilot_v3 --final-data data/final300.jsonl --out protocol_v3.json --audit-notes templates/pilot_audit_notes.md
py -3 experiment.py run --input data/final300.jsonl --out runs/final_v3_r1 --phase final --lock protocol_v3.json --max-requests 20000 --max-usd 20 --repeat 1
py -3 experiment.py evaluate --input data/final300.jsonl --run-dir runs/final_v3_r1 --bootstrap 1000
```

The 5 and 20 USD values are example soft thresholds; set the budget from the revised pilot's estimate and the actual bill. The formal experiment calls all three groups anew and does not use the old pilot cache. Do not adjust the extraction prompt or select favorable samples based on test scores.

## Local verification scope

30 project tests passed, covering `claim_text` actually reaching the verifier, stage parameters, no label leakage, extraction-only diagnostics, complete-request cache matching, error rejection, retention of historical resources, resuming, transport and freezing. 4 installation checks also passed, covering installation backup, repeated installation, rollback, protection of custom modifications, and the v3 to v3b upgrade. All 501 baseline calls in the user-uploaded files were verified to match. No real OpenAI model was called in this environment, so the actual semantic quality of the revised version still awaits review of the small diagnostic.
