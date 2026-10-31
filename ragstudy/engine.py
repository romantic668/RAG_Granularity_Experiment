import json
import platform
import random
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from .api import APIClient, APIError, BudgetStop, DemoClient
from .core import (VARIANTS, VERIFY_PROMPT, VERIFY_SCHEMA, EXTRACT_PROMPT, EXTRACT_SCHEMA,
                   append_jsonl, code_sha, digest, file_sha, locate_quotes, read_jsonl,
                   sentence_units, span_union, write_json)

def load_config(path):
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    if cfg["primary_label_policy"] != "all_annotated_spans":
        raise ValueError("Primary label policy must stay all_annotated_spans; a sensitivity analysis is exported separately.")
    if type(cfg["max_claims"]) is not int or cfg["max_claims"] < 1:
        raise ValueError("max_claims must be positive")
    if not cfg.get("model") or not cfg.get("base_url"):
        raise ValueError("Missing model/base_url")
    return cfg

def validate_dataset(rows, cfg):
    if not rows: raise ValueError("Empty dataset")
    ids = set()
    for row in rows:
        if row["id"] in ids: raise ValueError("Duplicate response IDs")
        ids.add(row["id"])
        for field in ("question", "evidence", "response"):
            if not isinstance(row[field], str): raise ValueError(f"Non-string {field}")
        if not row["response"].strip(): raise ValueError("Empty response")
        if len(row["evidence"]) > cfg["max_evidence_chars"] or len(row["response"]) > cfg["max_response_chars"]:
            raise ValueError(f"Input {row['id']} exceeds configured limits. No text was truncated.")

def protocol_base(cfg):
    return {"config": cfg, "config_sha256": digest(cfg), "code_sha256": code_sha(),
            "verification_prompt_sha256": digest(VERIFY_PROMPT),
            "extraction_prompt_sha256": digest(EXTRACT_PROMPT),
            "aggregation": "any unsupported or contradicted unit makes response positive",
            "localization": "union of flagged units' mapped source-character ranges; micro F1",
            "primary_labels": "all RAGTruth annotations including implicit_true",
            "secondary_labels": "sensitivity excluding implicit_true"}

def latest_predictions(path):
    rows = read_jsonl(path) if Path(path).exists() else []
    result = {}
    for row in rows:
        result[(row["id"], row["variant"])] = row
    return result

def freeze(config_path, pilot_dir, final_data, output, audit_notes=None):
    cfg = load_config(config_path)
    manifest = json.loads((Path(pilot_dir) / "run_manifest.json").read_text(encoding="utf-8"))
    base = protocol_base(cfg)
    if manifest["backend"] != "api" or manifest["phase"] != "pilot" or manifest["status"] != "complete":
        raise ValueError("Freeze needs a complete API pilot, not a demo/smoke/partial run")
    for k in ("config_sha256", "code_sha256"):
        if manifest[k] != base[k]: raise ValueError(f"Pilot {k} differs. Re-run the pilot with the intended configuration.")
    final = read_jsonl(final_data)
    validate_dataset(final, cfg)
    if any(x["split"] != "test" or x["dataset_kind"] != "RAGTruth_QA" for x in final):
        raise ValueError("Formal evaluation must use held-out RAGTruth test responses")
    if set(manifest["source_ids"]) & {x["source_id"] for x in final}:
        raise ValueError("Pilot/final source overlap")
    base.update(created_utc=datetime.now(timezone.utc).isoformat(),
                pilot_manifest_sha256=file_sha(Path(pilot_dir)/"run_manifest.json"),
                pilot_source_ids=manifest["source_ids"], final_sha256=file_sha(final_data),
                final_n=len(final), final_ids=[x["id"] for x in final],
                audit_notes_sha256=file_sha(audit_notes) if audit_notes else None)
    if Path(output).exists():
        raise ValueError("Protocol file exists. Use a new filename for a deliberate revision.")
    write_json(output, base)
    print(f"Frozen protocol: {output}")

def make_units(row, variant, cfg, client, calls):
    text = row["response"]
    if variant == "response":
        return [{"text": text, "spans": [[0,len(text)]], "alignment":"exact", "sentence_id":None}]
    sentences = sentence_units(text)
    if variant == "sentence": return sentences
    # Whitelist prevents human annotations/evidence from entering extraction.
    user = {"question": row["question"], "response_context": text,
            "sentences": [{"sentence_id":s["sentence_id"],"text":s["text"]} for s in sentences]}
    result, record = client.call("extract", f"{row['id']}/{variant}/extract", EXTRACT_PROMPT, user, EXTRACT_SCHEMA)
    calls.append(record)
    claims = result.get("claims")
    if not isinstance(claims, list): raise ValueError("Extractor did not return a claims list")
    if len(claims) > cfg["max_claims"]:
        raise ValueError(f"Extractor returned {len(claims)} claims, over max_claims; no claims were truncated")
    if not claims:
        # Never reward a broken/empty extraction with an automatic clean response.
        return [{**s,"alignment":"empty_extraction_sentence_fallback"} for s in sentences]
    units = []
    seen = set()
    for claim in claims:
        unit = locate_quotes(claim, sentences)
        key = digest({"text":unit["text"],"spans":unit["spans"]})
        if key not in seen:
            seen.add(key); units.append(unit)
    return units

def run_one(row, variant, cfg, client):
    start = time.perf_counter()
    calls = []
    units = make_units(row, variant, cfg, client, calls)
    decomposition_ms = (time.perf_counter()-start)*1000
    if variant == "atomic":
        decomposition_ms += sum(r["elapsed_ms"]-r["current_ms"] for r in calls if r["from_cache"])
    for i, unit in enumerate(units):
        print(f"  {row['id']} {variant}: verify {i+1}/{len(units)}", flush=True)
        # Only these whitelisted text fields enter the verifier. No gold labels/offsets.
        user = {"question":row["question"], "evidence":row["evidence"],
                "response_context":row["response"], "target_unit":unit["text"]}
        result, record = client.call("verify", f"{row['id']}/{variant}/unit/{i}",
                                     VERIFY_PROMPT, user, VERIFY_SCHEMA)
        calls.append(record)
        if result.get("status") not in ("supported","unsupported","contradicted") or not isinstance(result.get("reason"),str):
            raise ValueError("Invalid verifier status/reason; no clean label was substituted")
        unit.update(result)
    flagged = [s for u in units if u["status"] != "supported" for s in u["spans"]]
    elapsed = (time.perf_counter()-start)*1000
    # Resumption reuses historical call timings instead of timing fast cache lookups.
    elapsed += sum(r["elapsed_ms"]-r["current_ms"] for r in calls if r["from_cache"])
    return {"id":row["id"], "source_id":row["source_id"], "variant":variant, "status":"ok",
            "predicted_hallucinated": any(u["status"] != "supported" for u in units),
            "predicted_spans":span_union(flagged,len(row["response"])), "units":units,
            "decomposition_ms":max(0,decomposition_ms), "total_latency_ms":max(0,elapsed),
            "verification_units":len(units), "model_requests":sum(r["attempts"] for r in calls),
            "prompt_tokens":sum(r["usage"].get("prompt_tokens",0) for r in calls),
            "completion_tokens":sum(r["usage"].get("completion_tokens",0) for r in calls),
            "estimated_usd":sum(r["estimated_usd"] for r in calls),
            "cached_calls":sum(r["from_cache"] for r in calls),
            "alignment_fallback_units":sum("fallback" in u["alignment"] for u in units),
            "resolved_models":sorted({r["resolved_model"] for r in calls if r.get("resolved_model")})}

def run(input_path, output, config_path, phase="smoke", backend="api", lock=None,
        max_requests=200, max_usd=2.0, repeat=1):
    cfg = load_config(config_path)
    rows = read_jsonl(input_path)
    validate_dataset(rows,cfg)
    if phase in ("smoke","pilot") and backend == "api" and any(r["split"] != "train" for r in rows):
        raise ValueError("Smoke/pilot must use train responses, preserving test responses for final evaluation")
    base = protocol_base(cfg)
    if phase == "final":
        if backend != "api" or not lock: raise ValueError("Final evaluation requires API inference and --lock protocol.json")
        frozen = json.loads(Path(lock).read_text(encoding="utf-8"))
        for k in ("config_sha256","code_sha256"):
            if base[k] != frozen[k]: raise ValueError(f"Frozen {k} mismatch. Do not change settings during final testing.")
        if file_sha(input_path) != frozen["final_sha256"]:
            raise ValueError("Final sample differs from frozen protocol")
        if any(r["split"] != "test" for r in rows): raise ValueError("Final input must use official test split")
        if set(frozen["pilot_source_ids"]) & {r["source_id"] for r in rows}:
            raise ValueError("Pilot/final source overlap")
    output = Path(output)
    output.mkdir(parents=True,exist_ok=True)
    manifest_path = output / "run_manifest.json"
    signature = {**base,"dataset_sha256":file_sha(input_path),"phase":phase,"backend":backend,"repeat":repeat}
    fingerprint = digest(signature)
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["fingerprint"] != fingerprint:
            raise ValueError("This output folder belongs to another configuration/sample/code version. Choose a new folder.")
    else:
        if any(output.iterdir()): raise ValueError("Output directory is nonempty and has no valid run manifest")
        manifest = {**signature,"fingerprint":fingerprint,"created_utc":datetime.now(timezone.utc).isoformat(),
                    "input_file":str(Path(input_path).resolve()),"input_ids":[r["id"] for r in rows],
                    "source_ids":sorted({r["source_id"] for r in rows}),"expected_responses":len(rows),
                    "python":platform.python_version(),"platform":platform.platform(),
                    "timing":"sequential client-observed latency including decomposition, network, retries, verification, mapping",
                    "protocol_sha256":file_sha(lock) if lock else None}
    manifest["status"] = "running"
    write_json(manifest_path,manifest)
    try:
        client = DemoClient() if backend == "demo" else APIClient(cfg,output,max_requests,max_usd)
    except APIError:
        manifest["status"]="partial"
        write_json(manifest_path,manifest)
        raise
    predictions = output / "predictions.jsonl"
    prior = latest_predictions(predictions)
    rng = random.Random(cfg["seed"]+repeat)
    ordered_rows = list(rows); rng.shuffle(ordered_rows)
    errors = 0
    try:
        for n,row in enumerate(ordered_rows,1):
            variants = list(VARIANTS); rng.shuffle(variants)
            for variant in variants:
                key = (row["id"],variant)
                if key in prior and prior[key]["status"] == "ok":
                    print(f"Resume: {n}/{len(rows)} {row['id']} {variant} already complete",flush=True)
                    continue
                print(f"Response {n}/{len(rows)} {row['id']}, variant={variant}",flush=True)
                try:
                    result = run_one(row,variant,cfg,client)
                except BudgetStop:
                    raise
                except (APIError,ValueError,KeyError,TypeError) as e:
                    errors += 1
                    result = {"id":row["id"],"source_id":row["source_id"],"variant":variant,
                              "status":"error","error":str(e)}
                    print("ERROR:",str(e),flush=True)
                    # Authentication/model/config failures should not issue hundreds of identical calls.
                    append_jsonl(predictions,{**result,"fingerprint":fingerprint})
                    raise APIError("Stopped on an invalid/failed prediction. Resolve it and resume the same output folder.") from e
                result["fingerprint"] = fingerprint
                append_jsonl(predictions,result)
                prior[key] = result
        manifest["status"] = "complete"
    except (APIError,KeyboardInterrupt):
        manifest["status"] = "partial"
        raise
    finally:
        manifest["updated_utc"] = datetime.now(timezone.utc).isoformat()
        manifest["successful_pairs"] = sum(r["status"] == "ok" for r in latest_predictions(predictions).values())
        manifest["estimated_usd_all_attempts"] = getattr(client,"cost",0)
        write_json(manifest_path,manifest)
    print(f"Complete: {output}. Run evaluate to export the result tables.",flush=True)
    return manifest
