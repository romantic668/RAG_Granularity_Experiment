# Verification Status

Prepared on 2026-10-15 in America/Vancouver time.

## Completed

- Downloaded the two actual RAGTruth JSONL files from the pinned upstream commit and verified their SHA256 values.
- Prepared 60 QA/good train responses, 300 QA/good test responses, and a three-response smoke subset.
- Verified source_id separation between the pilot and final samples.
- Validated annotation offsets against original response substrings during preparation.
- Repeated preparation and confirmed byte-identical pilot, final and smoke data files.
- Passed 12 automated tests covering scoring, Unicode offsets, overlap merging, quote alignment, local HTTP API integration, gold-label separation, partial scoring, request caps, resumption, retries and frozen-protocol checks.
- Ran an offline four-response handwritten demo through decomposition, verification fixtures, aggregation, evaluation, CSV export and HTML report generation.
- Used a local mock HTTP server for API transport tests. These tests do not establish actual model accuracy.

## Not Completed

- No live OpenAI model was called in this delivery environment because OPENAI_API_KEY was not configured.
- No measured LLM pilot or final hallucination-detection F1 is supplied.
- No actual API cost or real API latency has been measured here.
- The Windows PowerShell instructions have not been executed on a Windows computer. Implementation and tests ran under Python 3.12 on Linux.

## What to Run Next

Follow README.md. Run the offline tests, set a valid API key locally, complete smoke3 and review its cost, then run and audit pilot60. Freeze the selected settings before final300. Keep generated outputs and distinguish smoke, pilot, demo and final results.
