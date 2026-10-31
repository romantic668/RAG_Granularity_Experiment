import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from experiment import demo_data
from ragstudy.api import APIClient, APIError, BudgetStop, DemoClient
from ragstudy.core import (VERIFY_PROMPT, VERIFY_SCHEMA, binary_metrics, gold_spans,
                           locate_quotes, read_jsonl, sentence_units, span_counts,
                           span_union, write_json, write_jsonl)
from ragstudy.engine import freeze, load_config, run
from ragstudy.evaluation import evaluate

ROOT=Path(__file__).resolve().parents[1]

class CoreTests(unittest.TestCase):
    def test_confusion_metrics(self):
        s=binary_metrics([(1,1)]*3+[(0,1)]*2+[(1,0)]+[(0,0)]*4)
        self.assertAlmostEqual(s["precision"],.6)
        self.assertAlmostEqual(s["recall"],.75)
        self.assertAlmostEqual(s["f1"],2/3)
        self.assertAlmostEqual(s["accuracy"],.7)

    def test_union_no_double_counting(self):
        self.assertEqual(span_union([[0,5],[3,8],[8,9]],10),[[0,9]])
        self.assertEqual(span_counts([[0,5],[3,8]],[[4,7]],10),(3,8,3))
        with self.assertRaises(ValueError): span_union([[-1,2]],10)
        with self.assertRaises(ValueError): span_union([[0,11]],10)
        with self.assertRaises(ValueError): span_union([[False,3]],10)

    def test_offsets_unicode_and_implicit_policy(self):
        text="猫 opens at 8 a.m."
        lo=text.index("8 a.m.")
        row={"response":text,"labels":[{"start":lo,"end":lo+6,"implicit_true":True}]}
        self.assertEqual(gold_spans(row),[[lo,lo+6]])
        self.assertEqual(gold_spans(row,True),[])

    def test_sentences_abbreviations_and_offsets(self):
        text="Dr. Smith arrives at 9 a.m. on Monday.\nThe value is 3.5. Is it correct? Yes!"
        units=sentence_units(text)
        self.assertEqual(len(units),4)
        for u in units:
            a,b=u["spans"][0]
            self.assertEqual(text[a:b],u["text"])

    def test_quotes_and_exposed_fallback(self):
        units=sentence_units("Subway offers free parking and Wi-Fi.")
        claim={"sentence_id":0,"claim_text":"Subway offers Wi-Fi.","quotes":[{"quote":"Subway offers","occurrence":0},{"quote":"Wi-Fi","occurrence":0}]}
        out=locate_quotes(claim,units)
        self.assertEqual(out["alignment"],"quoted")
        self.assertEqual(len(out["spans"]),2)
        claim["quotes"]=[{"quote":"not in text","occurrence":0}]
        self.assertEqual(locate_quotes(claim,units)["alignment"],"sentence_fallback")
        claim["sentence_id"]=5
        with self.assertRaises(ValueError): locate_quotes(claim,units)

    def test_list_numbers_and_citations_stay_with_source_sentence(self):
        text="How to copy:\n\n1. Connect the reader. (Passage 1)\n2. Copy the files. (Passage 2)\n\nDone. [1][2]"
        units=sentence_units(text)
        self.assertEqual([u["text"] for u in units], [
            "How to copy:", "1. Connect the reader. (Passage 1)",
            "2. Copy the files. (Passage 2)", "Done. [1][2]"])
        for i,u in enumerate(units):
            self.assertEqual(u["sentence_id"],i)
            lo,hi=u["spans"][0]
            self.assertEqual(text[lo:hi],u["text"])

    def test_wrapped_number_marker_and_citation_only_line(self):
        text="1.\nConnect the reader.\n(Passage 1)\n2.\nCopy files."
        units=sentence_units(text)
        self.assertEqual([u["text"] for u in units],[
            "1.\nConnect the reader.\n(Passage 1)","2.\nCopy files."])
        self.assertEqual(sentence_units("42.")[0]["text"],"42.")
        self.assertEqual(len(sentence_units("2026. This is a separate sentence.")),2)

class APIPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.requests=[]
        self.fail_next=False
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                payload=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append(payload)
                if owner.fail_next:
                    owner.fail_next=False
                    self.send_response(429);self.end_headers();return
                user=json.loads(payload["messages"][1]["content"])
                stage="extract" if "sentences" in user else "verify"
                result,_=DemoClient().call(stage,"fixture",payload["messages"][0]["content"],user,{})
                out={"model":"LOCAL_TEST_FIXTURE","choices":[{"finish_reason":"stop","message":{"content":json.dumps(result)}}],
                     "usage":{"prompt_tokens":200,"completion_tokens":30,"prompt_tokens_details":{"cached_tokens":0}}}
                body=json.dumps(out).encode()
                self.send_response(200);self.send_header("Content-Type","application/json")
                self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)
        self.server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.cfg=load_config(ROOT/"config.json")
        self.cfg["http_transport"]="urllib"
        self.cfg["base_url"]=f"http://127.0.0.1:{self.server.server_port}/v1"
        self.cfg_path=self.root/"config.json";write_json(self.cfg_path,self.cfg)
        self.data=self.root/"data.jsonl";demo_data(self.data)
        rows=read_jsonl(self.data)
        for r in rows:
            for l in r["labels"]: l["meta"]="SECRET_GOLD_SENTINEL"
        write_jsonl(self.data,rows)
        self.env=patch.dict(os.environ,{"OPENAI_API_KEY":"local-unit-test-only"});self.env.start()
        self.quiet=contextlib.redirect_stdout(io.StringIO());self.quiet.__enter__()

    def tearDown(self):
        self.quiet.__exit__(None,None,None)
        self.env.stop();self.server.shutdown();self.server.server_close();self.thread.join()
        self.tmp.cleanup()

    def _run(self,folder="run",phase="smoke",cap=200):
        return run(self.data,self.root/folder,self.cfg_path,phase=phase,max_requests=cap,max_usd=100)

    def test_full_http_pipeline_no_gold_leakage_and_resume(self):
        self._run()
        summary=evaluate(self.data,self.root/"run",n_bootstrap=30)
        self.assertEqual(summary["paired_complete_n"],4)
        self.assertTrue((self.root/"run"/"report.html").exists())
        self.assertGreater(len(self.requests),10)
        for payload in self.requests:
            self.assertNotIn("SECRET_GOLD_SENTINEL",json.dumps(payload))
            self.assertFalse(payload["store"])
            user=json.loads(payload["messages"][1]["content"])
            self.assertNotIn("labels",user)
            self.assertNotIn("gold_spans",user)
            if "target_unit" in user:
                self.assertEqual(set(user),{"question","evidence","response_context","target_unit"})
                self.assertEqual(payload["messages"][0]["content"],VERIFY_PROMPT)
        before=len(self.requests)
        self._run()
        self.assertEqual(len(self.requests),before)
        self.assertEqual(len(read_jsonl(self.root/"run"/"predictions.jsonl")),12)

    def test_budget_stop_and_call_level_resume(self):
        with self.assertRaises(BudgetStop): self._run(cap=2)
        manifest=json.loads((self.root/"run"/"run_manifest.json").read_text())
        self.assertEqual(manifest["status"],"partial")
        with self.assertRaises(ValueError): evaluate(self.data,self.root/"run",n_bootstrap=0)
        self._run(cap=200)
        summary=evaluate(self.data,self.root/"run",n_bootstrap=0)
        self.assertEqual(summary["paired_complete_n"],4)
        self.assertGreaterEqual(sum(s["cached_calls"] for s in summary["primary"].values()),1)

    def test_partial_paired_intersection(self):
        self._run()
        p=self.root/"run"/"predictions.jsonl"
        records=read_jsonl(p)
        write_jsonl(p,records[:-1])
        with self.assertRaises(ValueError): evaluate(self.data,self.root/"run",n_bootstrap=0)
        s=evaluate(self.data,self.root/"run",allow_partial=True,n_bootstrap=0)
        self.assertEqual(s["paired_complete_n"],3)
        self.assertTrue(s["category"].startswith("PARTIAL"))
        self.assertEqual({x["n"] for x in s["primary"].values()},{3})

    def test_protocol_blocks_changed_settings_and_source_overlap(self):
        self._run(phase="pilot")
        final=read_jsonl(self.data)
        for r in final:
            r.update(id="heldout_"+r["id"],source_id="heldout_"+r["source_id"],split="test",dataset_kind="RAGTruth_QA")
        finalpath=self.root/"final.jsonl";write_jsonl(finalpath,final)
        lock=self.root/"protocol.json"
        freeze(self.cfg_path,self.root/"run",finalpath,lock)
        changed=dict(self.cfg);changed["reasoning_effort"]="low"
        changedpath=self.root/"changed.json";write_json(changedpath,changed)
        with self.assertRaises(ValueError): run(finalpath,self.root/"final",changedpath,phase="final",lock=lock)
        run(finalpath,self.root/"final",self.cfg_path,phase="final",lock=lock,max_requests=200,max_usd=100)
        self.assertEqual(evaluate(finalpath,self.root/"final",n_bootstrap=0)["category"],"FINAL API INFERENCE")
        final[0]["source_id"]=read_jsonl(self.data)[0]["source_id"]
        write_jsonl(finalpath,final)
        with self.assertRaises(ValueError): freeze(self.cfg_path,self.root/"run",finalpath,self.root/"badlock.json")

    def test_changed_run_data_rejected(self):
        self._run()
        rows=read_jsonl(self.data);rows[0]["evidence"]+=" Changed."
        write_jsonl(self.data,rows)
        with self.assertRaises(ValueError): self._run()
        with self.assertRaises(ValueError): evaluate(self.data,self.root/"run",n_bootstrap=0)

    def test_retry_recorded_and_cache_not_rebilled(self):
        folder=self.root/"single";folder.mkdir()
        c=APIClient(self.cfg,folder,10,100)
        self.fail_next=True
        with patch("ragstudy.api.time.sleep"):
            result,record=c.call("verify","single",VERIFY_PROMPT,{"target_unit":"A correct statement"},VERIFY_SCHEMA)
        self.assertEqual(record["attempts"],2)
        cost=c.cost;before=len(self.requests)
        _,cached=c.call("verify","single",VERIFY_PROMPT,{"target_unit":"A correct statement"},VERIFY_SCHEMA)
        self.assertTrue(cached["from_cache"])
        self.assertEqual(c.cost,cost)
        self.assertEqual(len(self.requests),before)

    def test_missing_key_does_not_complete_run(self):
        with patch.dict(os.environ,{"OPENAI_API_KEY":""}):
            with self.assertRaises(APIError): self._run()
        m=json.loads((self.root/"run"/"run_manifest.json").read_text())
        self.assertEqual(m["status"],"partial")

if __name__=="__main__": unittest.main()
