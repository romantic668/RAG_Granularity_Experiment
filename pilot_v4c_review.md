# Pilot v4c Review and Audit Notes

Review date: 2026-10-16 (America/Vancouver)
Reviewer: Codex / ChatGPT, AI-assisted quantitative verification and qualitative audit.

Status: Run integrity and metric computation verified. Qualitative review completed with documented limitations. This document is not a new human gold annotation set or a certification of perfect atomic decomposition. The supplied CSV audit forms remain unmodified; no human sign-off is recorded here.

Decision: The v4c pilot can end. The implemented extraction-and-verification pipeline may be frozen and evaluated on the held-out final300 data, with the limitations below retained. Further changes to code, model, prompts, data selection or scoring would require a new pilot and protocol. Do not describe these train-pilot numbers as formal test results.

## 1. Provenance and integrity

- Source bundle: cursor_review_pilot_v4c.zip.
- User run directory: runs/pilot_v4c_r1.
- Dataset: 60 RAGTruth QA train responses; 60 source groups; 17 gold-positive responses.
- Complete successful results: 180 / 180 (60 for each of response, sentence and atomic).
- Data SHA256: 86be532244cbdf1a90761aa6b1b137228a674f34fc106443d77786f7ab561722.
- Windows source-code SHA256: 3a7e0bd3c56ede865a547bf38dac6e17997cccaee3924ccf0cedfbb2b10ec413.
- Run fingerprint: 70d6922a2f6f4732e86cc495fcb18ec86d3a67add0aad2c10ef1e79615de1488.
- Fixed model: gpt-5.4-mini-2026-03-17.
- Extraction effort: low. Verification effort: none in all three treatments.
- Code and configuration match the previously reviewed v4c diagnostic version.
- Verified all 1,222 cached requests against the current prompts, schemas, question/evidence/target fields, scoped cache keys, raw model outputs and recorded predictions. All resolved-model records identify the fixed snapshot.
- Confirmed 501 baseline calls were copied byte-for-byte from the earlier complete pilot_v2. No old atomic calls were imported.
- Confirmed 721 new successful atomic calls: 60 extraction calls and 661 verification calls. Their logged estimated costs sum to USD 0.79080525.
- Independently recomputed response confusion matrices, detection scores, character-overlap localization scores, mean units, mean recorded latency, cache counts and method costs from the dataset and predictions. They agree with summary.json.
- Extraction payloads contain question, response context and numbered sentences; verification payloads contain question, evidence, response context and target text. No gold annotation fields are sent.

The final300 file supplied with the original project has disjoint source IDs. The freeze command must still validate the actual final file on the user's computer and bind its hash before inference.

## 2. Pilot results

Hallucinated response is the positive class. The primary gold policy includes all annotated spans, including implicit_true annotations.

| Method | TP | FP | FN | TN | Precision | Recall | Detection F1 | Accuracy | Character localization F1 | Mean recorded seconds | Mean units | Method cost USD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Response | 14 | 11 | 3 | 32 | 56.00% | 82.35% | 66.67% | 76.67% | 28.68% | 1.306 | 1.00 | 0.062531 |
| Sentence | 15 | 14 | 2 | 29 | 51.72% | 88.24% | 65.22% | 73.33% | 49.03% | 8.200 | 7.35 | 0.367959 |
| Atomic pipeline | 16 | 17 | 1 | 26 | 48.48% | 94.12% | 64.00% | 70.00% | 49.83% | 17.039 | 11.02 | 0.790805 |

The atomic pipeline detected 16 of 17 positive responses, versus 14 for response-level checking. It also produced 17 false positives, versus 11. The observed F1 difference is -2.67 percentage points, while character localization F1 is 21.15 points higher than response-level and only 0.79 points higher than sentence-level.

The 1,000-replicate paired source-cluster bootstrap interval for atomic-minus-response detection F1 is -14.49 to +10.37 percentage points. It includes zero. These data do not establish a detection-F1 advantage or a statistically clear disadvantage. They also do not establish equivalence. Sampling intervals do not measure model nondeterminism.

Character localization F1 uses unions of original-character ranges. It is a custom localization metric, not the official RAGTruth word-level benchmark score. Higher localization F1 does not by itself establish more faithful extraction.

## 3. Timing, requests and cost

Response and sentence calls are historical baseline calls. Their call timings, token usage and costs are retained, with currently measured mapping overhead. This pilot is not a fresh three-group timing comparison. All groups must be run afresh in the final evaluation.

- New calls billed in this pilot: 721 atomic calls, estimated USD 0.79080525.
- Imported historical baseline costs: USD 0.43048950.
- Sum of method resource costs, including those historical baseline calls: USD 1.22129475. This is not the newly billed total for this run.
- The configured USD threshold is a soft threshold checked before a request and can overshoot by one call. Provider billing is authoritative.
- Recorded latency includes extraction where applicable, verification, mapping, network/service delay, retries if any, curl process startup and temporary-file I/O. It is not GPU compute time.

A simple fivefold extrapolation to 300 responses across all three methods is approximately USD 6.11 and 133 sequential minutes. This is only a planning estimate: claim counts, response lengths, provider caching, service load and retries can differ. Recompute actual costs and timing after final inference.

## 4. Qualitative audit scope

Reviewed the 20 responses exported in response_audit.csv and their 237 atomic units, using the original response text and extracted targets. Also inspected all 22 fallback units and selected evidence/verdict error cases. This was an AI-assisted qualitative review, not an independently double-annotated human accuracy study. No numeric semantic-fidelity or coverage accuracy is claimed.

Reviewed IDs: 13958, 13833, 16280, 15813, 14174, 15798, 12079, 12312, 17258, 13551, 16825, 12568, 13766, 17211, 16286, 13880, 14579, 14336, 12834, 14927.

### Mapping fallbacks

- Full pilot: 22 / 661 units, or 3.33%.
- 21 are invalid/mismatched source-quote mappings, retained with a complete-source-sentence localization fallback.
- One is an empty-extraction sentence fallback for response 12428, whose text is only an inability-to-answer statement. An empty factual-claim list is plausible for that response; it is not evidence of an omitted factual claim there.
- The 20-response audit subset contains 10 fallback units among 237 units.
- Some quotes improperly include a heading from another numbered sentence (for example 14174 and 14571); others splice separated wording or change source text. The deterministic mapper exposes these failures instead of inventing offsets.
- Existing sentence segmentation can split abbreviations such as “Dept.” or merge a temperature ending in “F.” with the next sentence. This affects the operational meaning of the sentence condition and the extractor's sentence IDs. It is a disclosed splitter limitation.

### Extraction limitations observed

- 13958: “set up like hospitals with shared rooms” remains one compound target; other multi-attribute statements and lists also remain bundled. Decomposition is not uniformly atomic.
- 15798 and 16825: some later weather claims omit the place/date in the rewritten target, retaining a need for response context.
- 13880: the main quantities and named vitamins/minerals are separated, but the supplement alternatives are partly separated from their qualifying conditions. This is a potential scope/faithfulness issue; not every rewritten claim can be assumed semantically equivalent simply because its quotes match.
- 16286: the extractor retains counts and list order but omits the substantive attributed quotations in the listed verses. Coverage is incomplete for the full response.
- 13766: both a short instruction and the same instruction with its purpose are returned, creating overlapping checks even without exact duplicate texts.
- Procedural answers sometimes lose prerequisites or sequence context when rewritten as short steps. Attribution and framing also require qualitative review.

No response, gold label, raw model output or prediction was changed as a result of this audit.

## 5. Error analysis examples

- 12079 (gold negative, atomic false positive): the evidence explicitly includes peeling/grating and rinsing/drying as steps, but the verifier rejects some extracted short steps because they do not repeat the complete preparation sequence. This illustrates interaction between lost procedural context and a verifier that requires excessive completeness.
- 17211 (gold negative, atomic false positive): evidence says to measure both the wall opening and the oven box; the verifier rejects the separate instruction to measure the wall opening. It also rejects a trimming step after decomposition. Supported substeps are not always accepted as standalone units.
- 15813 (gold negative, atomic false positive): the verifier treats “key responsibilities” as unsupported even where the evidence describes required knowledge. This illustrates sensitivity to semantic paraphrase and framing.
- 12025 (gold positive, the sole atomic false negative): the annotation marks a location change from “base of the skull on the neck” to “base of the neck.” Extraction retains the changed location, but the verifier accepts it. This is a missed subtle conflict rather than a dropped claim.
- 12143: one supported verdict cites response_context as its reason, although the evidence distinguishes extracting Sn0wBreeze from the target's extracting firmware. This shows that the verifier can misuse response context despite the prompt's source-only instruction. The benchmark label is negative, so this example is not counted as an additional official false negative.

These are diagnostic interpretations, not a replacement for the RAGTruth gold labels. Annotation gaps or support-policy ambiguity are possible; no gold labels are edited. With the fixed any-positive aggregation rule, one false alarm among many units makes the entire response positive, helping explain the precision/recall trade-off.

## 6. Fixed scope for the formal evaluation

Evaluate the implemented atomic extraction pipeline, including its disclosed under-decomposition, incomplete coverage, alignment fallbacks and verifier errors. Do not describe it as oracle-quality atomic facts or claim that only ideal linguistic granularity changes.

The model, verification prompt/schema, evidence, aggregation and score definitions remain controlled. Atomic extraction is an additional method step and its resource use is included. All three final conditions must use fresh requests under the same frozen protocol. Keep the primary gold policy and supplementary implicit_true sensitivity analysis distinct.

## 7. Next steps on the user's computer

Read this review and retain it as the audit record. Place this file, pilot_v4c_review.md, beside experiment.py. The following commands freeze the actual local code/configuration and held-out dataset, then perform formal inference:

```powershell
py -3 experiment.py freeze --pilot-dir runs/pilot_v4c_r1 --final-data data/final300.jsonl --out protocol_v4c.json --audit-notes pilot_v4c_review.md
py -3 experiment.py run --input data/final300.jsonl --out runs/final_v4c_r1 --phase final --lock protocol_v4c.json --max-requests 20000 --max-usd 10 --repeat 1
py -3 experiment.py evaluate --input data/final300.jsonl --run-dir runs/final_v4c_r1 --bootstrap 1000
```

The USD 10 setting is a soft threshold, not a guaranteed spending cap. If a protocol or output folder already exists for a different version, use a fresh name consistently. An interrupted final run resumes with the identical command and output folder; do not change the frozen code or configuration during resumption. Freeze checks bind the actual final file and source separation. No formal inference has been performed in this review environment.

After final inference, package source/configuration, this audit record, protocol_v4c.json, the actual final input and all final run outputs for verification. Final reporting should use the held-out results, identify reconstructed timings if any final interruption required resumption, and retain the documented extraction and verifier limitations.
