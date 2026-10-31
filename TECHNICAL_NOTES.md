# Experimental Design and Technical Notes

## Scope

The study evaluates a hallucination detector, using fixed RAGTruth QA evidence and already-generated responses. It does not evaluate a retriever, regenerate RAGTruth answers, fine-tune a model, or compute RAGChecker answer-quality F1. RefChecker already studied granularity. This project is a controlled evaluation on a fixed sample with detection, localization and cost measurements, not a claim that atomic checking is novel.

The implementation is inspired by the reviewed work. It does not reproduce RefChecker's triplet implementation or claim to reproduce published MiniCheck/RAGChecker benchmark numbers. The default verifier is a general LLM via API, not MiniCheck. A sentence-trained checker is deliberately not the default for the whole-response treatment.

## Independent Variable and Controls

The independent variable is the verification unit: complete response, original sentence, or extracted atomic assertion. The same verifier snapshot, evidence, question, verification system prompt, output schema and aggregation rule apply to all three treatments. Full response text is available as untrusted context for pronoun resolution in every treatment. It is explicitly not evidence.

Units are verified sequentially, one target per API request. The code evaluates every unit, even after an earlier unit is unsupported, to obtain complete localization outputs. Variant order and response order are seeded and counterbalanced by shuffling within each response. The atomic treatment additionally requires extraction; its time, tokens and requests are included in its total cost.

## Data

Pinned upstream commit: `c103204b9ce28d6bbad859304bf30de72b8ed8fe`.

Download SHA256 values are defined in `ragstudy/data.py`. Preparation verifies both raw files, joins by source_id, keeps QA and quality=good, validates every label's exact source substring and boundaries, checks unique response IDs, and checks that official train/test source IDs are disjoint.

Uniform response sampling without replacement uses seed 20261015. It does not use label values. Pilot uses train, final uses test. The three treatments process the identical selected IDs. More than one generator response may share a source within a split, so bootstrap confidence intervals resample source_id clusters, not independent response rows.

Do not remove a sample because it is difficult or because one method gets it wrong. Invalid API output stops the run instead of creating a clean label. Partial diagnostic scoring requires an explicit flag and scores only the paired intersection. A partial report is visibly labeled and reports its excluded IDs.

## Gold Labels and Judgments

The primary target is strict support by supplied evidence. All annotated RAGTruth spans, including implicit_true=true spans, are retained. The latter can be true in the world but unsupported by the supplied evidence. The primary response label is positive if at least one annotated span exists.

The verifier returns supported, unsupported or contradicted. A response is predicted positive if any unit is unsupported or contradicted. These two labels are merged only for binary hallucination scoring. No confidence threshold is fitted; the decision rule is fixed before testing.

An additional, predeclared sensitivity analysis excludes implicit_true annotations from gold labels without rerunning the model. It is supplementary, with a changed gold definition. It must not silently replace the primary result. Human annotations may be incomplete; inspect false positives and false negatives, but do not edit test labels selectively after observing performance.

Only whitelisted text fields enter model payloads. Gold labels, annotation comments and gold offsets never enter extraction or verification. The extractor additionally does not receive evidence, reducing the chance that it corrects an unsupported assertion during decomposition. The automated HTTP tests verify this separation.

## Segmentation and Extraction

`sentence_units` is a deterministic conservative English punctuation/newline splitter with common abbreviation handling. It preserves exact Python Unicode character offsets. Sentence boundaries can be imperfect, especially abbreviations at sentence endings and unusual list formats. Audit segmentation in the pilot. Do not treat it as a full linguistic parser.

The extractor generates self-contained natural-language atomic assertions, not knowledge triplets. It returns exact quotes and a source sentence ID. Exact quote occurrences are mapped deterministically back to the untouched response. Meaning, negation, attribution and completeness require a human pilot audit. Mechanical offset validity alone does not establish extraction quality.

Missing/mismatched quotes fall back to the complete source sentence and are counted. An empty claim list falls back to sentence units, rather than automatically assigning a clean response. Invalid sentence IDs, excess claims, malformed output or an API failure stop the run. No evidence, response or claim list is silently truncated. These fallbacks make the evaluated atomic condition an extraction pipeline with disclosed fallback behavior, not a perfect oracle decomposition.

## Metrics

Binary metrics use hallucinated response as positive:

- Precision = TP / (TP + FP).
- Recall = TP / (TP + FN).
- F1 = 2TP / (2TP + FP + FN).
- Accuracy = (TP + TN) / N.

Zero-denominator precision, recall and F1 use zero, and the convention is included in summary.json. Always report the number of positive responses and the sample size with the scores.

Localization uses the union of predicted original-character intervals and the union of human intervals, per response, then sums intersection, prediction and gold character counts across the sample. P = intersection/predicted characters, R = intersection/gold characters, F1 = 2*intersection/(predicted+gold characters). Overlapping ranges are merged to avoid double counting. Both-empty responses add zero counts rather than a free per-response perfect score.

A positive whole-response unit marks the entire response. A positive sentence unit marks its full original sentence. A positive atomic claim marks all of its mapped quote ranges, including shared subject/predicate text. This measures granularity-dependent source-range localization and mapping quality. It is not evidence that the verifier emitted a precise minimal error span, and it is not the official RAGTruth word-level metric.

The report includes primary response metrics, character micro localization metrics, means/medians of latency, mean units and requests, extraction fallback counts, estimated USD cost, generator-stratified detection results, and source-cluster bootstrap 95% intervals. Paired F1 differences use the same resampled source clusters for both methods, with response-level checking as the comparison baseline. These intervals describe sampling uncertainty for this sample, not model nondeterminism.

## Timing and Cost

Total per-response latency starts before decomposition and includes extraction, verification, mapping, network time, retries and retry backoff. Inference is sequential. Data download, dataset preparation, model-independent scoring and one-time setup are outside this timer. API service load and prompt caching may affect latency and billing even under identical client settings.

Each successful model call is saved in a run-local cache keyed by its scope and complete request payload. A scope contains response ID, variant and unit position, so identical text across different treatments does not share the client's call cache. This preserves a separate call for each treatment. Interrupted calls may be resumed from prior successes. The saved historical call duration is used for a reconstructed processing-time total, and cache reuse counts are reported. For the cleanest latency comparison, use fresh output directories and disclose any interrupted/reconstructed runs.

Network request counts include extraction and retries. Token/cost records include successful billed outputs even if parsing later failed. Provider-side charges not accompanied by usage information cannot be known from this code. The USD threshold is a soft check before the next request, not a guaranteed spending ceiling. Set actual account budgets in the API platform as appropriate to your account.

Configuration includes a price-check date. Update prices before the pilot if required. The actual account bill takes precedence over local estimates. Inference snapshots can still produce stochastic outputs. For a stronger latency/reliability study, run repeated complete experiments with fixed data/configuration and new run directories and report all repetitions.

## Protocol and Reproducibility

Freeze requires a completed API pilot, official held-out test rows, disjoint pilot/final sources, and unchanged configuration/code. The protocol binds final file hash, selected response IDs, model configuration, prompts and source code. The manifest records OS/Python details and resolved model IDs. Output directories reject another configuration or dataset, preventing accidental result mixing.

Keep data/manifest.json, protocol.json, config.json, source code, run_manifest.json, predictions.jsonl, calls.jsonl, call_cache, summary.json, CSV reports and human audit notes together. API keys are not stored in any of these artifacts.

## Interpretation

Do not conclude that more fine-grained verification is always better. Atomic extraction can drop a fact, change meaning, create overlapping checks, or add cost. Localization improvements can partly follow from the smaller reporting unit. Report the trade-off and error types supported by actual outputs. API client-observed latency does not isolate GPU compute time. The study covers QA/good records only and may not generalize to Summary or Data2txt tasks. A public benchmark may have appeared in a verifier's training data; source-only prompts do not prove absence of training contamination.
