#!/usr/bin/env python3
"""Run `python experiment.py --help`. Python 3.10+, no pip dependencies."""
import argparse
import json
import os
import platform
import statistics
import sys
from pathlib import Path

from ragstudy.api import APIError, BudgetStop
from ragstudy.core import VARIANTS, read_jsonl, sentence_units, write_jsonl
from ragstudy.data import download, prepare
from ragstudy.engine import freeze, load_config, run
from ragstudy.evaluation import evaluate

ROOT = Path(__file__).resolve().parent

def demo_data(path):
    def label(text, needle):
        start=text.index(needle)
        return {"start":start,"end":start+len(needle),"text":needle,
                "label_type":"DEMO_ONLY","implicit_true":False,"meta":"Handwritten teaching example"}
    examples = [
        ("demo01", "When does Subway open on Monday and what seating is offered?",
         "Subway opens at 9 a.m. on Monday. It offers outdoor seating.",
         "Subway opens at 8 a.m. on Monday. It offers outdoor seating.", ["8 a.m."]),
        ("demo02", "How should beets be baked?", "Bake beets at 350°F for 45 to 60 minutes.",
         "Bake beets at 350°F for 45 to 60 minutes.", []),
        ("demo03", "What parking and Wi-Fi are available at Subway?", "Subway has a parking lot. Wi-Fi is not available. Parking charges are not specified.",
         "Subway offers free parking and Wi-Fi.", ["free", "Wi-Fi"]),
        ("demo04", "When does Subway open on Monday?", "Subway opens at 9 a.m. on Monday.",
         "Subway opens at 9 a.m. on Monday.", [])
    ]
    rows=[{"id":rid,"source_id":rid,"task_type":"QA","split":"train","dataset_kind":"DEMO_FIXTURE",
           "generator_model":"HANDWRITTEN_DEMO","question":q,"evidence":e,"response":r,
           "labels":[label(r,n) for n in needles]} for rid,q,e,r,needles in examples]
    write_jsonl(path,rows)

def parser():
    p=argparse.ArgumentParser(description="Controlled RAGTruth verification-granularity experiment")
    sub=p.add_subparsers(dest="command",required=True)
    d=sub.add_parser("doctor",help="Check Python/config/key presence without an API call")
    d.add_argument("--config",default="config.json")
    d=sub.add_parser("download",help="Download and verify pinned official RAGTruth files")
    d.add_argument("--raw-dir",default="raw_data")
    d=sub.add_parser("prepare",help="Build fixed QA/good pilot and final samples")
    d.add_argument("--raw-dir",default="raw_data");d.add_argument("--out",default="data")
    d.add_argument("--pilot-n",type=int,default=60);d.add_argument("--final-n",type=int,default=300)
    d.add_argument("--seed",type=int,default=20261015);d.add_argument("--force",action="store_true")
    d=sub.add_parser("demo",help="Offline handwritten fixture; NOT a real experiment")
    d.add_argument("--out",default="runs/demo");d.add_argument("--config",default="config.json")
    d=sub.add_parser("plan",help="Show sample statistics and request cap before spending")
    d.add_argument("--input",required=True);d.add_argument("--config",default="config.json")
    d=sub.add_parser("run",help="Run/resume actual API inference for all three granularities")
    d.add_argument("--input",required=True);d.add_argument("--out",required=True)
    d.add_argument("--config",default="config.json");d.add_argument("--phase",choices=["smoke","pilot","final"],default="smoke")
    d.add_argument("--lock");d.add_argument("--max-requests",type=int,default=200)
    d.add_argument("--max-usd",type=float,default=2.0,help="Soft total run estimate threshold, not a hard billing limit")
    d.add_argument("--repeat",type=int,default=1)
    d=sub.add_parser("evaluate",help="Export metrics, human-readable reports and audit forms")
    d.add_argument("--input",required=True);d.add_argument("--run-dir",required=True)
    d.add_argument("--allow-partial",action="store_true")
    d.add_argument("--bootstrap",type=int,default=1000)
    d=sub.add_parser("estimate",help="Extrapolate observed smoke/pilot expense and runtime")
    d.add_argument("--run-dir",required=True);d.add_argument("--n",type=int,required=True)
    d=sub.add_parser("freeze",help="Lock config/code/final sample after a successful API pilot")
    d.add_argument("--config",default="config.json");d.add_argument("--pilot-dir",required=True)
    d.add_argument("--final-data",default="data/final300.jsonl");d.add_argument("--out",default="protocol.json")
    d.add_argument("--audit-notes")
    return p

def main(argv=None):
    args=parser().parse_args(argv)
    if args.command == "doctor":
        cfg=load_config(args.config)
        print(json.dumps({"python":platform.python_version(),"python_ok":sys.version_info>=(3,10),
                          "model":cfg["model"],"api_key_present":bool(os.getenv("OPENAI_API_KEY")),
                          "network_called":False,"dependencies":"Python standard library only"},indent=2))
    elif args.command == "download": download(args.raw_dir)
    elif args.command == "prepare": prepare(args.raw_dir,args.out,args.pilot_n,args.final_n,args.seed,args.force)
    elif args.command == "demo":
        folder=Path(args.out)
        folder.mkdir(parents=True,exist_ok=True)
        # Keep input outside the run directory to preserve the run integrity check.
        input_path=folder.parent/(folder.name+"_input.jsonl")
        demo_data(input_path)
        run(input_path,folder,args.config,phase="demo",backend="demo")
        evaluate(input_path,folder,n_bootstrap=100)
        print("DEMO ONLY. No LLM was called. Do not put these numbers in your experiment results.")
    elif args.command == "plan":
        cfg=load_config(args.config);rows=read_jsonl(args.input)
        sentences=sum(len(sentence_units(r["response"])) for r in rows)
        upper=len(rows)*2+sentences+len(rows)*cfg["max_claims"]
        print(json.dumps({"responses":len(rows),"response_units":len(rows),"sentence_units":sentences,
                          "atomic_units":"unknown until extraction; no silent truncation",
                          "maximum_first_attempt_requests_if_within_claim_cap":upper,
                          "retry_multiplier_upper_bound":cfg["max_retries"]+1,
                          "api_calls_made":0,"next_step":"Run smoke3, evaluate, then estimate using observed costs."},indent=2))
    elif args.command == "run":
        if args.max_requests<1 or args.max_usd<=0 or args.repeat<1:
            raise ValueError("Request cap, USD threshold and repeat must be positive")
        run(args.input,args.out,args.config,args.phase,"api",args.lock,args.max_requests,args.max_usd,args.repeat)
    elif args.command == "evaluate":
        if args.bootstrap<0: raise ValueError("bootstrap must be nonnegative")
        evaluate(args.input,args.run_dir,args.allow_partial,args.bootstrap)
    elif args.command == "freeze":
        freeze(args.config,args.pilot_dir,args.final_data,args.out,args.audit_notes)
    elif args.command == "estimate":
        s=json.loads((Path(args.run_dir)/"summary.json").read_text(encoding="utf-8"))
        if s["backend"] != "api": raise ValueError("Demo expenses cannot estimate a real experiment")
        if args.n<1: raise ValueError("n must be positive")
        n=s["paired_complete_n"]
        observed=sum(v["estimated_usd"] for v in s["primary"].values())
        seconds=sum(v["mean_latency_ms"] for v in s["primary"].values())/1000
        print(json.dumps({"observed_responses":n,"target_responses":args.n,
                          "estimated_usd_for_all_three_variants":round(observed/n*args.n,4),
                          "estimated_sequential_minutes":round(seconds*args.n/60,2),
                          "warning":"Extrapolation only; claim counts, retries, load and billing may differ."},indent=2))

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted. Re-run the identical command to resume.",file=sys.stderr)
        sys.exit(130)
    except (ValueError,APIError,OSError,KeyError) as e:
        print(f"ERROR: {e}",file=sys.stderr)
        sys.exit(2)
