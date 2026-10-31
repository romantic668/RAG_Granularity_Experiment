import html
import json
import random
import statistics
from collections import defaultdict
from pathlib import Path

from .core import (VARIANTS, binary_metrics, gold_spans, file_sha, percentile, read_jsonl,
                   span_counts, span_union, write_csv, write_json)
from .engine import latest_predictions

def score(rows, preds, variant, exclude_implicit=False):
    pairs, overlap, npred, ngold = [], 0, 0, 0
    ps = []
    for row in rows:
        p = preds[(row["id"],variant)]
        gold = gold_spans(row,exclude_implicit)
        pairs.append((bool(gold),p["predicted_hallucinated"]))
        a,b,c = span_counts(p["predicted_spans"],gold,len(row["response"]))
        overlap += a; npred += b; ngold += c
        ps.append(p)
    out = binary_metrics(pairs)
    out.update(n=len(rows), positive_responses=sum(g for g,p in pairs),
               span_precision=overlap/npred if npred else 0.0,
               span_recall=overlap/ngold if ngold else 0.0,
               span_f1=2*overlap/(npred+ngold) if npred+ngold else 0.0,
               overlapping_characters=overlap, predicted_characters=npred, gold_characters=ngold,
               mean_latency_ms=statistics.mean(p["total_latency_ms"] for p in ps),
               median_latency_ms=statistics.median(p["total_latency_ms"] for p in ps),
               mean_units=statistics.mean(p["verification_units"] for p in ps),
               mean_requests=statistics.mean(p["model_requests"] for p in ps),
               total_prompt_tokens=sum(p["prompt_tokens"] for p in ps),
               total_completion_tokens=sum(p["completion_tokens"] for p in ps),
               estimated_usd=sum(p["estimated_usd"] for p in ps),
               fallback_units=sum(p["alignment_fallback_units"] for p in ps),
               total_units=sum(p["verification_units"] for p in ps),
               cached_calls=sum(p["cached_calls"] for p in ps))
    return out

def bootstrap(rows, preds, n_bootstrap=1000, seed=20261015):
    if n_bootstrap < 1: return {}, {}
    groups = defaultdict(list)
    for row in rows: groups[row["source_id"]].append(row)
    ids = sorted(groups)
    if len(ids) < 2: return {}, {}
    rng = random.Random(seed)
    fs = {v:[] for v in VARIANTS}
    ds = {v:[] for v in VARIANTS if v != "response"}
    for _ in range(n_bootstrap):
        sample = [row for sid in rng.choices(ids,k=len(ids)) for row in groups[sid]]
        current = {}
        for v in VARIANTS:
            current[v] = binary_metrics((bool(gold_spans(r)),preds[(r["id"],v)]["predicted_hallucinated"]) for r in sample)["f1"]
            fs[v].append(current[v])
        for v in ds: ds[v].append(current[v]-current["response"])
    cis = {v:[percentile(a,.025),percentile(a,.975)] for v,a in fs.items()}
    deltas = {v:{"against":"response", "f1_difference_ci95":[percentile(a,.025),percentile(a,.975)]} for v,a in ds.items()}
    return cis,deltas

def evaluate(input_path, run_dir, allow_partial=False, n_bootstrap=1000):
    root = Path(run_dir)
    manifest = json.loads((root/"run_manifest.json").read_text(encoding="utf-8"))
    if file_sha(input_path) != manifest["dataset_sha256"]:
        raise ValueError("Evaluation dataset differs from the run manifest")
    rows = read_jsonl(input_path)
    preds = latest_predictions(root/"predictions.jsonl")
    allowed = {r["id"] for r in rows}
    for (rid,v),p in preds.items():
        if rid not in allowed or v not in VARIANTS: raise ValueError("Unexpected response/variant in predictions")
        if p["fingerprint"] != manifest["fingerprint"]: raise ValueError("Mixed experiment fingerprints")
        if p["status"] == "ok":
            if type(p["predicted_hallucinated"]) is not bool: raise ValueError("Prediction must be a JSON boolean")
            row = next(r for r in rows if r["id"] == rid)
            if p["source_id"] != row["source_id"]: raise ValueError("Prediction source mismatch")
            spans = span_union(p["predicted_spans"],len(row["response"]))
            if bool(spans) != p["predicted_hallucinated"]:
                raise ValueError("Response prediction and flagged source spans disagree")
    complete = [r for r in rows if all((r["id"],v) in preds and preds[(r["id"],v)]["status"] == "ok" for v in VARIANTS)]
    if len(complete) != len(rows) and not allow_partial:
        raise ValueError(f"Only {len(complete)}/{len(rows)} responses have all three successful variants. Resume inference first, or explicitly use --allow-partial.")
    if not complete: raise ValueError("No complete paired responses")
    results = {v:score(complete,preds,v) for v in VARIANTS}
    secondary = {v:score(complete,preds,v,True) for v in VARIANTS}
    cis,deltas = bootstrap(complete,preds,n_bootstrap)
    for v in VARIANTS:
        results[v]["f1_ci95"] = cis.get(v)
    category = "FINAL API INFERENCE" if manifest["phase"] == "final" and manifest["backend"] == "api" else "PILOT OR SMOKE API INFERENCE"
    if manifest["backend"] == "demo": category = "DEMO FIXTURE ONLY NOT RESEARCH RESULTS"
    if len(complete) != len(rows): category = "PARTIAL PAIRED SUBSET " + category
    summary = {"category":category, "phase":manifest["phase"],"backend":manifest["backend"],
               "requested_n":len(rows),"paired_complete_n":len(complete),
               "missing_or_failed_ids":[r["id"] for r in rows if r not in complete],
               "source_groups":len({r["source_id"] for r in complete}),
               "primary_policy":"all_annotated_spans", "primary":results,
               "sensitivity_excluding_implicit_true":secondary,
               "bootstrap":{"replicates":n_bootstrap,"seed":20261015,"unit":"source_id cluster","paired_f1_differences":deltas},
               "frozen_protocol_sha256":manifest.get("protocol_sha256"),
               "run_fingerprint":manifest["fingerprint"],"model":manifest["config"]["model"],
               "cache_provenance":manifest.get("cache_provenance"),
               "definitions":{"zero_division":"precision/recall/F1 set to 0 when denominator is zero",
                              "span_metric":"character micro F1 on unions; not official RAGTruth word-span scoring",
                              "latency":"client-observed, includes decomposition/network/retries/verification/mapping; cached calls use historical timing"}}
    write_json(root/"summary.json",summary)
    csvrows = []
    for v,s in results.items():
        entry = {"variant":v, "n":s["n"]}
        for k in ("precision","recall","f1","accuracy","span_precision","span_recall","span_f1"):
            entry[k+"_pct"] = round(s[k]*100,4)
        entry.update({k:s[k] for k in ("tp","fp","fn","tn","mean_latency_ms","median_latency_ms","mean_units","mean_requests","fallback_units","total_units","cached_calls","estimated_usd")})
        entry["f1_ci95_low_pct"] = round(s["f1_ci95"][0]*100,4) if s["f1_ci95"] else ""
        entry["f1_ci95_high_pct"] = round(s["f1_ci95"][1]*100,4) if s["f1_ci95"] else ""
        csvrows.append(entry)
    write_csv(root/"metrics.csv",list(csvrows[0]),csvrows)
    detail, errors, audits, response_audits = [], [], [], []
    audit_ids = {r["id"] for r in random.Random(20261015).sample(complete,min(20,len(complete)))}
    for row in complete:
        gold = gold_spans(row)
        for v in VARIANTS:
            pred = preds[(row["id"],v)]
            actual, guessed = bool(gold), pred["predicted_hallucinated"]
            kind = "TP" if actual and guessed else "FP" if guessed else "FN" if actual else "TN"
            a,b,c = span_counts(pred["predicted_spans"],gold,len(row["response"]))
            record = {"id":row["id"],"source_id":row["source_id"],"generator_model":row["generator_model"],
                      "variant":v,"gold_hallucinated":actual,"pred_hallucinated":guessed,"outcome":kind,
                      "gold_spans":gold,"predicted_spans":pred["predicted_spans"],"span_f1":2*a/(b+c) if b+c else "",
                      "total_latency_ms":pred["total_latency_ms"],"units":pred["verification_units"],
                      "requests":pred["model_requests"],"question":row["question"],
                      "evidence":row["evidence"],"response":row["response"]}
            detail.append(record)
            if kind in ("FP","FN"): errors.append({**record,"manual_cause":"","review_notes":""})
        if row["id"] in audit_ids:
            atomic = preds[(row["id"],"atomic")]
            for i,u in enumerate(atomic["units"]):
                audits.append({"id":row["id"],"unit_id":i,"original_response":row["response"],
                    "extracted_claim":u["text"],"mapped_spans":u["spans"],"mapped_text":[row["response"][a:b] for a,b in u["spans"]],
                    "alignment":u["alignment"],"meaning_preserved_Y_N":"","one_fact_Y_N":"","mapping_correct_Y_N":"","notes":""})
            response_audits.append({"id":row["id"],"original_response":row["response"],
                                   "all_extracted_claims":[u["text"] for u in atomic["units"]],
                                   "all_facts_covered_Y_N":"","missing_or_changed_facts":""})
    write_csv(root/"per_response.csv",list(detail[0]),detail)
    write_csv(root/"errors.csv",list(detail[0])+["manual_cause","review_notes"],errors)
    write_csv(root/"claim_audit.csv",list(audits[0]),audits)
    write_csv(root/"response_audit.csv",list(response_audits[0]),response_audits)
    grouped=[]
    for model in sorted({r["generator_model"] for r in complete}):
        subset=[r for r in complete if r["generator_model"] == model]
        for v in VARIANTS:
            s=score(subset,preds,v)
            grouped.append({"generator_model":model,"variant":v,"n":len(subset),"precision_pct":s["precision"]*100,
                            "recall_pct":s["recall"]*100,"f1_pct":s["f1"]*100,"accuracy_pct":s["accuracy"]*100})
    write_csv(root/"by_generator.csv",list(grouped[0]),grouped)
    lines=["# Verification Granularity Evaluation", "", f"**{category}**", "",
        f"Paired responses: {len(complete)} / {len(rows)}. Source groups: {summary['source_groups']}.",
        f"Verifier: `{summary['model']}`. All annotated spans are the primary gold standard.", "",
        "| Variant | Precision | Recall | Detection F1 | Accuracy | Character span F1 | Units per response | Requests per response | Mean ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for v,s in results.items():
        lines.append(f"| {v} | {s['precision']:.1%} | {s['recall']:.1%} | {s['f1']:.1%} | {s['accuracy']:.1%} | {s['span_f1']:.1%} | {s['mean_units']:.2f} | {s['mean_requests']:.2f} | {s['mean_latency_ms']:.1f} |")
    lines += ["", "## Interpretation and limits", "",
        "Primary detection metrics compare response-level predictions with human hallucination labels. They do not measure answer quality against a reference answer.",
        "Character span F1 compares the union of flagged source ranges with human ranges. A flagged response marks the whole response; a flagged sentence marks its whole sentence. Atomic claims use extracted quote ranges. This is a custom localization measure, not the official word-level RAGTruth benchmark score.",
        "Atomic quote alignment requires manual audit. Exact string matching does not prove semantic faithfulness, atomicity, or complete fact coverage. Sentence fallback units remain in the evaluation and are counted.",
        "The primary policy includes implicit_true annotations because the task is strict support by the provided evidence. summary.json also reports a predeclared sensitivity analysis excluding them.",
        "API latency includes network and service load. Use fresh output folders for repeated runs. Historical cache timings reconstructed during resumption should be disclosed.",
        "USD estimates use configuration prices, which can become stale. Actual billing is authoritative.", "",
        "## Paired source-cluster bootstrap", ""]
    if manifest.get("cache_provenance"):
        note = ("Pilot baseline reuse: response/sentence calls were imported only after exact request "
                "matching. Their original call timings, usage and costs are retained; atomic calls "
                "run anew. This is not a fresh three-group timing comparison. New-run calls.jsonl "
                "tracks newly made calls; the method table includes historical baseline costs.")
        lines[lines.index("## Paired source-cluster bootstrap"):lines.index("## Paired source-cluster bootstrap")] = [note, ""]
    for v,s in results.items():
        ci=s["f1_ci95"]
        lines.append(f"- {v}: F1 95% interval " + (f"{ci[0]:.1%} to {ci[1]:.1%}" if ci else "not computed"))
    for v,d in deltas.items():
        low,high=d["f1_difference_ci95"]
        observed=results[v]["f1"]-results["response"]["f1"]
        lines.append(f"- {v} minus response: {observed*100:.2f} percentage points; paired 95% interval {low*100:.2f} to {high*100:.2f} points.")
    lines += ["", "## Audit counts", ""]
    for v,s in results.items():
        lines.append(f"- {v}: {s['fallback_units']} fallback units / {s['total_units']} units; {s['cached_calls']} resumed calls.")
    lines += ["", "## Files", "", "metrics.csv is the slide table. errors.csv contains false positives and false negatives. claim_audit.csv and response_audit.csv are blank manual review forms. predictions.jsonl and call_cache retain actual outputs. Keep run_manifest.json and the frozen protocol with the report."]
    (root/"report.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    def highlighted(text,spans):
        pieces=[];pos=0
        for a,b in spans:
            pieces.extend([html.escape(text[pos:a]),"<mark>"+html.escape(text[a:b])+"</mark>"]);pos=b
        pieces.append(html.escape(text[pos:]))
        return "".join(pieces)
    cards=[]
    for row in complete:
        result_sections=[]
        for v in VARIANTS:
            p=preds[(row["id"],v)]
            original=row["response"]
            pieces=[]; pos=0
            for a,b in p["predicted_spans"]:
                pieces.extend([html.escape(original[pos:a]),"<mark>"+html.escape(original[a:b])+"</mark>"]);pos=b
            pieces.append(html.escape(original[pos:]))
            result_sections.append(f"<h4>{v}</h4><p>{''.join(pieces)}</p><ul>"+"".join(f"<li>{html.escape(u['text'])}: {u['status']} ({html.escape(u['reason'])})</li>" for u in p["units"])+"</ul>")
        cards.append(f"<details><summary>{html.escape(row['id'])} / {html.escape(row['question'])}</summary><h4>Evidence</h4><p class='original'>{html.escape(row['evidence'])}</p><h4>Human annotation</h4><p class='original'>{highlighted(row['response'],gold_spans(row))}</p>"+"".join(result_sections)+"</details>")
    headers=["Variant","Precision","Recall","Detection F1","Accuracy","Character span F1","Units / response","Requests / response","Mean ms"]
    htmlrows=[]
    for v,s in results.items():
        cells=[v,f"{s['precision']:.1%}",f"{s['recall']:.1%}",f"{s['f1']:.1%}",f"{s['accuracy']:.1%}",f"{s['span_f1']:.1%}",f"{s['mean_units']:.2f}",f"{s['mean_requests']:.2f}",f"{s['mean_latency_ms']:.1f}"]
        htmlrows.append("<tr>"+"".join("<td>"+html.escape(c)+"</td>" for c in cells)+"</tr>")
    htmltable="<div class='scroll'><table><thead><tr>"+"".join("<th>"+h+"</th>" for h in headers)+"</tr></thead><tbody>"+"".join(htmlrows)+"</tbody></table></div>"
    notes=[]
    for line in lines[lines.index("## Interpretation and limits"):]:
        if line.startswith("## "):
            notes.append("<h3>"+html.escape(line[3:])+"</h3>")
        elif line.startswith("- "):
            notes.append("<ul><li>"+html.escape(line[2:])+"</li></ul>")
        elif line:
            notes.append("<p>"+html.escape(line)+"</p>")
    page="<!doctype html><html lang='en'><meta charset='utf-8'><title>RAG evaluation</title><style>body{font:16px system-ui;max-width:1200px;margin:36px auto;padding:0 20px;line-height:1.5}pre,.original{white-space:pre-wrap}details{border-bottom:1px solid #ccc;padding:14px 0}summary{cursor:pointer}mark{background:#ffe29a}.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}td,th{border:1px solid #d9d9d9;padding:9px;text-align:right}th{background:#20354a;color:white}td:first-child,th:first-child{text-align:left}</style><h1>Verification granularity evaluation</h1><h2>"+html.escape(category)+f"</h2><p>Complete paired responses: {len(complete)} / {len(rows)}. Source groups: {summary['source_groups']}.</p>"+htmltable+"".join(notes)+"<h2>Response inspection</h2>"+"".join(cards)+"</html>"
    (root/"report.html").write_text(page,encoding="utf-8")
    print(f"Exported metrics.csv, summary.json, report.md, report.html and audit/error CSVs to {root}")
    return summary
