"""Atomic extraction diagnostics and exact-request pilot baseline reuse.

check makes real extraction requests only. reuse never calls the API.
"""
import argparse
import json
import math
import platform
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from ragstudy.api import APIClient, APIError, build_payload
from ragstudy.core import (VERIFY_PROMPT, VERIFY_SCHEMA, digest, file_sha,
                           read_jsonl, sentence_units, write_json)
from ragstudy.engine import (latest_predictions, load_config, make_units,
                             protocol_base, validate_dataset)


def check(input_path, output, config_path, max_requests=6, max_usd=0.5):
    cfg = load_config(config_path)
    rows = read_jsonl(input_path)
    validate_dataset(rows, cfg)
    if len(rows) > 10 or any(r["split"] != "train" for r in rows):
        raise ValueError("Extraction diagnostics require at most 10 train responses.")
    output = Path(output)
    signature = {**protocol_base(cfg), "dataset_sha256": file_sha(input_path),
                 "purpose": "extraction-only structural diagnostic; not detection evaluation"}
    fingerprint = digest(signature)
    manifest_path = output/"atomic_check_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["fingerprint"] != fingerprint:
            raise ValueError("Diagnostic settings changed. Choose a new output folder.")
    else:
        if output.exists() and any(output.iterdir()):
            raise ValueError("Choose an empty diagnostic output folder.")
        output.mkdir(parents=True, exist_ok=True)
        manifest = {**signature, "fingerprint": fingerprint,
                    "created_utc": datetime.now(timezone.utc).isoformat()}
    manifest["status"] = "partial"
    write_json(manifest_path, manifest)
    client = APIClient(cfg, output, max_requests, max_usd)
    report = {"purpose": signature["purpose"], "fingerprint": fingerprint,
              "semantic_quality": "REQUIRES HUMAN REVIEW; counts and exact quotes do not prove atomicity or fidelity",
              "rows": []}
    lines = ["# Atomic extraction diagnostic", "",
             "Real extraction only; no verification or detection F1. Human review is required.", ""]
    for i, row in enumerate(rows, 1):
        print(f"Extract {i}/{len(rows)}: {row['id']}", flush=True)
        calls = []
        units = make_units(row, "atomic", cfg, client, calls)
        sentences = sentence_units(row["response"])
        texts = [u["text"] for u in units]
        duplicates = sorted({t for t in texts if texts.count(t) > 1})
        item = {"id": row["id"], "response": row["response"], "sentences": sentences,
                "units": units, "unit_count": len(units),
                "alignment_fallback_units": sum("fallback" in u["alignment"] for u in units),
                "repeated_claim_texts": duplicates,
                "whole_sentence_claims": sum(u["text"] == sentences[u["sentence_id"]]["text"] for u in units),
                "estimated_usd": sum(c["estimated_usd"] for c in calls),
                "resolved_models": sorted({c["resolved_model"] for c in calls})}
        report["rows"].append(item)
        write_json(output/"atomic_check.json", report)
        lines += [f"## Response {row['id']}", "", row["response"], "",
                  f"Claims: {len(units)}; quote fallbacks: {item['alignment_fallback_units']}", ""]
        for n, u in enumerate(units, 1):
            lines += [f"{n}. {u['text']}",
                      f"   - Sentence {u['sentence_id']}; {u['alignment']}; quotes: " +
                      json.dumps(u.get("quotes", []), ensure_ascii=False)]
        lines += [""]
    (output/"atomic_check.md").write_text("\n".join(lines), encoding="utf-8")
    manifest.update(status="complete", completed_utc=datetime.now(timezone.utc).isoformat(),
                    estimated_usd_all_attempts=client.cost)
    write_json(manifest_path, manifest)
    print("Complete:", output/"atomic_check.json")
    print("Upload atomic_check.json for semantic review before the revised pilot.")
    return report


def _validate_cache(record, payload, model):
    if record.get("payload") != payload:
        raise ValueError("A cached verification request differs from the current request.")
    parsed = record.get("parsed")
    if (not isinstance(parsed, dict) or set(parsed) != {"status", "reason"}
            or parsed["status"] not in ("supported", "unsupported", "contradicted")
            or not isinstance(parsed["reason"], str)):
        raise ValueError("Invalid cached verdict.")
    raw = record["raw_response"]
    choice = raw["choices"][0]
    if (choice["finish_reason"] != "stop" or choice["message"].get("refusal")
            or json.loads(choice["message"]["content"]) != parsed
            or raw.get("model") != model or record.get("resolved_model") != model):
        raise ValueError("Cached raw output/model does not match its verdict or pinned snapshot.")
    for key in ("elapsed_ms", "estimated_usd"):
        if (type(record.get(key)) not in (int, float)
                or not math.isfinite(record[key]) or record[key] < 0):
            raise ValueError("Invalid historical cache timing/cost.")
    if type(record.get("attempts")) is not int or record["attempts"] < 1:
        raise ValueError("Invalid historical attempt count.")
    if not isinstance(record.get("usage"), dict):
        raise ValueError("Missing historical usage.")
    return parsed


def reuse(input_path, source, output, config_path):
    """Prepare a NEW pilot with matching response/sentence calls, never atomic calls."""
    cfg = load_config(config_path)
    rows = read_jsonl(input_path)
    validate_dataset(rows, cfg)
    if any(r["split"] != "train" for r in rows):
        raise ValueError("Baseline reuse is restricted to the train pilot, never final evaluation.")
    source, output = Path(source), Path(output)
    if source.resolve() == output.resolve() or (output.exists() and any(output.iterdir())):
        raise ValueError("Choose a NEW empty pilot output folder.")
    source_manifest = source/"run_manifest.json"
    old = json.loads(source_manifest.read_text(encoding="utf-8"))
    if (old["status"], old["phase"], old["backend"]) != ("complete", "pilot", "api"):
        raise ValueError("Source must be a complete API pilot.")
    if old.get("repeat") != 1:
        raise ValueError("This helper prepares repeat=1 pilots only.")
    base = protocol_base(cfg)
    keys = list(base) + ["dataset_sha256", "phase", "backend", "repeat"]
    if digest({k: old[k] for k in keys}) != old["fingerprint"]:
        raise ValueError("Source manifest fingerprint is invalid.")
    if old["dataset_sha256"] != file_sha(input_path) or old["input_ids"] != [r["id"] for r in rows]:
        raise ValueError("Source pilot and current pilot data must be identical.")
    for key in ("base_url", "model", "price_usd_per_million_input",
                "price_usd_per_million_cached_input", "price_usd_per_million_output"):
        if old["config"].get(key) != cfg.get(key):
            raise ValueError("Source endpoint/model/pricing differs: " + key)
    prior = latest_predictions(source/"predictions.jsonl")
    if any(prior.get((r["id"], v), {}).get("status") != "ok"
           for r in rows for v in ("response", "sentence", "atomic")):
        raise ValueError("Source predictions are incomplete.")
    copies = []
    for row in rows:
        for variant in ("response", "sentence"):
            units = ([{"text": row["response"], "spans": [[0, len(row["response"])]]}]
                     if variant == "response" else sentence_units(row["response"]))
            prediction = prior[(row["id"], variant)]
            if prediction.get("fingerprint") != old["fingerprint"] or len(prediction["units"]) != len(units):
                raise ValueError("Source prediction fingerprint/segmentation mismatch.")
            for i, unit in enumerate(units):
                user = {"question": row["question"], "evidence": row["evidence"],
                        "response_context": row["response"], "target_unit": unit["text"]}
                payload = build_payload(cfg, "verify", VERIFY_PROMPT, user, VERIFY_SCHEMA)
                scope = f"{row['id']}/{variant}/unit/{i}"
                cache_key = digest({"scope": scope, "payload": payload})
                path = source/"call_cache"/(cache_key + ".json")
                if not path.is_file():
                    raise ValueError("No exactly matching source cache for " + scope)
                record = json.loads(path.read_text(encoding="utf-8"))
                verdict = _validate_cache(record, payload, cfg["model"])
                historical = prediction["units"][i]
                if any(historical.get(k) != unit[k] for k in ("text", "spans")) or any(
                        historical.get(k) != verdict[k] for k in ("status", "reason")):
                    raise ValueError("Source prediction does not match cache: " + scope)
                copies.append((path, file_sha(path), record))
    signature = {**base, "dataset_sha256": file_sha(input_path),
                 "phase": "pilot", "backend": "api", "repeat": old["repeat"]}
    provenance = {"source_directory": str(source.resolve()), "source_fingerprint": old["fingerprint"],
                  "source_manifest_sha256": file_sha(source_manifest), "imported_calls": len(copies),
                  "imported_variants": ["response", "sentence"],
                  "atomic_calls_imported": 0, "predictions_imported": 0,
                  "historical_call_cost_usd": sum(r["estimated_usd"] for _, _, r in copies),
                  "historical_call_elapsed_ms": sum(r["elapsed_ms"] for _, _, r in copies),
                  "timing_note": "Imported baseline calls retain original call timings/usage/costs; current mapping overhead is measured. This is not a fresh three-group timing run.",
                  "cache_sha256": {p.name: sha for p, sha, _ in copies}}
    manifest = {**signature, "fingerprint": digest(signature), "status": "prepared",
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "input_file": str(Path(input_path).resolve()), "input_ids": [r["id"] for r in rows],
                "source_ids": sorted({r["source_id"] for r in rows}), "expected_responses": len(rows),
                "python": platform.python_version(), "platform": platform.platform(),
                "timing": provenance["timing_note"], "protocol_sha256": None,
                "cache_provenance": provenance}
    # Validate everything before creating the destination; remove partial writes on failure.
    output.mkdir(parents=True, exist_ok=True)
    try:
        cache = output/"call_cache"
        cache.mkdir()
        for path, expected_sha, _ in copies:
            target = cache/path.name
            shutil.copyfile(path, target)
            if file_sha(target) != expected_sha:
                raise ValueError("Source cache changed during copying.")
        write_json(output/"run_manifest.json", manifest)
    except BaseException:
        shutil.rmtree(output)
        raise
    print(f"Prepared {output}: imported {len(copies)} exactly matching response/sentence calls.")
    print("No API request was made. All atomic calls will run anew.")
    print("Pilot summaries retain historical baseline resource costs; calls.jsonl records new calls only.")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check", help="Real extraction only, at most 10 train responses")
    p.add_argument("--input", default="data/atomic_check3.jsonl")
    p.add_argument("--out", default="runs/atomic_check_v3")
    p.add_argument("--config", default="config.json")
    p.add_argument("--max-requests", type=int, default=6)
    p.add_argument("--max-usd", type=float, default=0.5)
    p = sub.add_parser("reuse", help="Prepare a fresh pilot with exact baseline caches; no API")
    p.add_argument("--input", default="data/pilot60.jsonl")
    p.add_argument("--source", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--config", default="config.json")
    args = parser.parse_args()
    if args.command == "check":
        check(args.input, args.out, args.config, args.max_requests, args.max_usd)
    else:
        reuse(args.input, args.source, args.out, args.config)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, APIError, OSError, KeyError, TypeError, IndexError) as error:
        print("ATOMIC_TOOLS_ERROR:", error, file=sys.stderr)
        sys.exit(2)
