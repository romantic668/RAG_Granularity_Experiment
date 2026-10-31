import json
import random
from collections import Counter
from pathlib import Path
from urllib.request import Request, urlopen

from .core import file_sha, gold_spans, read_jsonl, write_json, write_jsonl

COMMIT = "c103204b9ce28d6bbad859304bf30de72b8ed8fe"
HASHES = {
    "source_info.jsonl": "0dffc26ea9f3c1c3d7c7e8336b56ef1646e3cec876edffcca3c9c624d12d578b",
    "response.jsonl": "e4c2e4ac24fff676d8984cc61c35d791612fadc58015335d97dd632375e18073"
}

def download(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, expected in HASHES.items():
        path = directory / name
        if path.exists() and file_sha(path) == expected:
            print(f"Verified existing {path}", flush=True)
            continue
        url = f"https://raw.githubusercontent.com/ParticleMedia/RAGTruth/{COMMIT}/dataset/{name}"
        print(f"Downloading {name} from pinned RAGTruth commit", flush=True)
        request = Request(url, headers={"User-Agent": "RAG-Granularity-Study/1.0"})
        temp = path.with_suffix(".download")
        try:
            with urlopen(request, timeout=120) as response, temp.open("wb") as output:
                while True:
                    block = response.read(1024*1024)
                    if not block: break
                    output.write(block)
            if file_sha(temp) != expected:
                raise ValueError(f"SHA256 mismatch for {name}; file was not installed")
            temp.replace(path)
        finally:
            if temp.exists(): temp.unlink()
    write_json(directory / "download_manifest.json", {"commit": COMMIT, "sha256": HASHES})

def prepare(raw_dir, out_dir, pilot_n=60, final_n=300, seed=20261015, force=False):
    raw_dir, out_dir = Path(raw_dir), Path(out_dir)
    if pilot_n < 3 or final_n < 1:
        raise ValueError("Use pilot_n >= 3 and final_n >= 1")
    outputs = [out_dir / x for x in (f"pilot{pilot_n}.jsonl", f"final{final_n}.jsonl", "smoke3.jsonl", "manifest.json")]
    if not force and any(x.exists() for x in outputs):
        raise ValueError("Prepared files exist. Keep them fixed, or use --force to deliberately rebuild.")
    for name, expected in HASHES.items():
        if not (raw_dir / name).exists() or file_sha(raw_dir / name) != expected:
            raise ValueError(f"Missing or modified {name}. Run the download command first.")
    source_rows = read_jsonl(raw_dir / "source_info.jsonl")
    sources = {str(x["source_id"]): x for x in source_rows}
    if len(sources) != len(source_rows):
        raise ValueError("Duplicate source IDs")
    pools = {"train": [], "test": []}
    all_ids, excluded = set(), Counter()
    for r in read_jsonl(raw_dir / "response.jsonl"):
        rid, sid = str(r["id"]), str(r["source_id"])
        if rid in all_ids: raise ValueError(f"Duplicate response ID {rid}")
        all_ids.add(rid)
        source = sources[sid]
        if source["task_type"] != "QA":
            excluded["not_QA"] += 1; continue
        if r["quality"] != "good":
            excluded[r["quality"]] += 1; continue
        if r["split"] not in pools:
            raise ValueError(f"Unknown dataset split {r['split']}")
        for label in r["labels"]:
            if r["response"][label["start"]:label["end"]] != label["text"]:
                raise ValueError(f"Annotation offset mismatch in {rid}")
        row = {"id": rid, "source_id": sid, "task_type": "QA", "split": r["split"],
               "dataset_kind": "RAGTruth_QA", "generator_model": r["model"],
               "question": source["source_info"]["question"],
               "evidence": source["source_info"]["passages"], "response": r["response"],
               "labels": r["labels"]}
        gold_spans(row)  # Validates all bounds, without changing the source response.
        pools[r["split"]].append(row)
    if {r["source_id"] for r in pools["train"]} & {r["source_id"] for r in pools["test"]}:
        raise ValueError("Official train/test source IDs overlap")
    if len(pools["train"]) < pilot_n or len(pools["test"]) < final_n:
        raise ValueError("Requested sample exceeds the eligible pool")
    # Sampling never uses labels. Retain the natural response-level class distribution.
    rng = random.Random(seed)
    pilot = rng.sample(pools["train"], pilot_n)
    final = rng.sample(pools["test"], final_n)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(outputs[0], pilot)
    write_jsonl(outputs[1], final)
    write_jsonl(outputs[2], pilot[:3])
    manifest = {"dataset": "RAGTruth", "task": "QA", "quality": "good", "commit": COMMIT,
                "raw_sha256": HASHES, "seed": seed, "sampling": "uniform responses without replacement; labels not used",
                "primary_labels": "all annotated spans including implicit_true; strict source faithfulness",
                "source_overlap": 0, "excluded": dict(excluded), "samples": {}}
    for name, rows, path in [("pilot",pilot,outputs[0]),("final",final,outputs[1]),("smoke",pilot[:3],outputs[2])]:
        manifest["samples"][name] = {"path": path.name, "sha256": file_sha(path), "n": len(rows),
            "official_split": rows[0]["split"], "source_count": len({x["source_id"] for x in rows}),
            "generator_counts": dict(Counter(x["generator_model"] for x in rows)),
            "annotated_hallucination_responses": sum(bool(x["labels"]) for x in rows),
            "ids": [x["id"] for x in rows]}
    write_json(outputs[3], manifest)
    print(json.dumps({"prepared": str(out_dir), "pilot_n": pilot_n, "final_n": final_n,
                      "source_overlap": 0}, ensure_ascii=False))
    return manifest
