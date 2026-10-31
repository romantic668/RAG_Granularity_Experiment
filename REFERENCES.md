# References

1. Niu et al. 2024. RAGTruth: A Hallucination Corpus for Developing Trustworthy Retrieval-Augmented Language Models. ACL 2024. https://aclanthology.org/2024.acl-long.585/
2. Hu et al. 2024. Knowledge-Centric Hallucination Detection. EMNLP 2024. RefChecker framework. https://aclanthology.org/2024.emnlp-main.395/
3. Tang, Laban and Durrett. 2024. MiniCheck: Efficient Fact-Checking of LLMs on Grounding Documents. EMNLP 2024. https://aclanthology.org/2024.emnlp-main.499/
4. Ru et al. 2024. RAGChecker: A Fine-grained Framework for Diagnosing Retrieval-Augmented Generation. NeurIPS 2024 Datasets and Benchmarks. https://arxiv.org/abs/2408.08067

## Data and Implementations

- RAGTruth official repository: https://github.com/ParticleMedia/RAGTruth
- Pinned dataset commit: c103204b9ce28d6bbad859304bf30de72b8ed8fe
- Raw source_info.jsonl SHA256: 0dffc26ea9f3c1c3d7c7e8336b56ef1646e3cec876edffcca3c9c624d12d578b
- Raw response.jsonl SHA256: e4c2e4ac24fff676d8984cc61c35d791612fadc58015335d97dd632375e18073
- RefChecker repository: https://github.com/amazon-science/RefChecker
- MiniCheck repository: https://github.com/Liyan06/MiniCheck
- RAGChecker repository: https://github.com/amazon-science/RAGChecker

## API Documentation

- Model ID and snapshot: https://developers.openai.com/api/docs/models/gpt-5.4-mini
- Structured outputs: https://developers.openai.com/api/docs/guides/structured-outputs
- API reference: https://developers.openai.com/api/reference/resources/chat
- Checked on 2026-10-15. This implementation uses a fixed model snapshot and no server-side tools.

Dataset examples in data/ are derived from the official RAGTruth corpus. Its license is included under licenses/RAGTruth_LICENSE.txt. The study code is an independent implementation, not the official implementation of the four cited papers.
