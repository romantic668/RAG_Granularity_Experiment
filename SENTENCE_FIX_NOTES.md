# Sentence segmentation correction

List markers such as `1.` stay with their following sentence. Source-only
citations such as `(Passage 1)` and `[1]` stay with the preceding sentence.
All unit texts remain exact slices of the original response with valid character
offsets. Citation-only responses are retained. Headings and other text blocks are
retained, so sentence unit counts need not be lower than atomic claim counts.

In smoke response 13833, sentence units decrease from 12 to 6. The previous
count treated list numbers and citations as independent verification units.
The response/evidence, verifier, prompts, aggregation and gold labels do not change.
Both sentence verification and atomic extraction receive the corrected splitting.

This changes the source fingerprint. Existing smoke outputs remain historical
diagnostic results. Start a fresh pilot directory; do not mix old/new runs or
reuse an older frozen protocol. Update cost/time estimates from the new pilot.

24 local unit/integration checks passed, including actual curl against a mock
HTTP server. Live model extraction quality still requires the pilot audit.
