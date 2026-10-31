import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import atomic_tools
from experiment import demo_data
from ragstudy.api import APIClient, DemoClient, build_payload
from ragstudy.core import (EXTRACT_PROMPT, EXTRACT_SCHEMA, VERIFY_PROMPT, VERIFY_SCHEMA,
                           locate_quotes, read_jsonl, sentence_units, write_json, write_jsonl)
from ragstudy.engine import load_config, run, run_one
from ragstudy.evaluation import evaluate

ROOT = Path(__file__).resolve().parents[1]


class AtomicToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = load_config(ROOT/"config.json")
        self.cfg.update(http_transport="urllib", base_url="http://local-fixture.invalid/v1")
        self.config = self.root/"config.json"
        write_json(self.config, self.cfg)
        self.data = self.root/"data.jsonl"
        demo_data(self.data)
        rows = read_jsonl(self.data)
        for row in rows:
            row["labels_sentinel"] = "SECRET_GOLD_SENTINEL"
        write_jsonl(self.data, rows)
        self.payloads = []

        def opener(request, timeout):
            payload = json.loads(request.data)
            self.payloads.append(payload)
            user = json.loads(payload["messages"][1]["content"])
            stage = "extract" if "sentences" in user else "verify"
            result, _ = DemoClient().call(stage, "fixture", "", user, {})
            raw = {"model": self.cfg["model"], "choices": [{"finish_reason": "stop",
                   "message": {"content": json.dumps(result)}}],
                   "usage": {"prompt_tokens": 200, "completion_tokens": 30}}
            response = io.BytesIO(json.dumps(raw).encode())
            response.headers = {"x-request-id": "local-fixture"}
            return response

        self.mock = patch("ragstudy.api.urlopen", side_effect=opener)
        self.mock.start()
        self.env = patch.dict(os.environ, {"OPENAI_API_KEY": "local-fixture-only"})
        self.env.start()
        self.quiet = contextlib.redirect_stdout(io.StringIO())
        self.quiet.__enter__()

    def tearDown(self):
        self.quiet.__exit__(None, None, None)
        self.env.stop()
        self.mock.stop()
        self.tmp.cleanup()

    def source_pilot(self):
        old = {**self.cfg, "extract_reasoning_effort": "none"}
        path = self.root/"old_config.json"
        write_json(path, old)
        run(self.data, self.root/"source", path, phase="pilot", max_requests=200, max_usd=100)
        return self.root/"source"

    def test_stage_controls_preserve_verifier_request(self):
        old = {k: v for k, v in self.cfg.items() if k != "extract_reasoning_effort"}
        args = ("verify", VERIFY_PROMPT, {"target_unit": "test"}, VERIFY_SCHEMA)
        self.assertEqual(build_payload(old, *args), build_payload(self.cfg, *args))
        payload = build_payload(self.cfg, "extract", EXTRACT_PROMPT, {}, EXTRACT_SCHEMA)
        self.assertEqual(payload["reasoning_effort"], "low")
        props = EXTRACT_SCHEMA["properties"]["claims"]["items"]["properties"]
        self.assertIn("claim_text", props)
        self.assertNotIn("text", props)
        with self.assertRaises(ValueError):
            locate_quotes({"sentence_id": 0, "text": "legacy", "quotes": []}, sentence_units("A fact."))

    def test_claim_text_is_the_actual_verification_target(self):
        row = read_jsonl(self.data)[0]
        row["response"] = "The museum opened in 2001 and closes at 6 pm."
        claims = [
            {"sentence_id": 0, "claim_text": "The museum opened in 2001.",
             "quotes": [{"quote": "The museum opened in 2001", "occurrence": 0}]},
            {"sentence_id": 0, "claim_text": "The museum closes at 6 pm.",
             "quotes": [{"quote": "The museum", "occurrence": 0}, {"quote": "closes at 6 pm", "occurrence": 0}]}]
        client = APIClient(self.cfg, self.root/"target", max_requests=10, max_usd=10)
        real_call = client.call
        targets = []

        def call(stage, scope, prompt, user, schema):
            self.assertNotIn("SECRET_GOLD_SENTINEL", json.dumps(user))
            if stage == "extract":
                self.assertNotIn("evidence", user)
                _, record = DemoClient().call(stage, scope, prompt, user, schema)
                return {"claims": claims}, record
            targets.append(user["target_unit"])
            return real_call(stage, scope, prompt, user, schema)

        with patch.object(client, "call", side_effect=call):
            result = run_one(row, "atomic", self.cfg, client)
        self.assertEqual(targets, [c["claim_text"] for c in claims])
        self.assertEqual([u["text"] for u in result["units"]], targets)
        self.assertTrue(all(u["alignment"] == "quoted" for u in result["units"]))

    def test_check_only_extracts_without_gold_and_resumes(self):
        out = self.root/"check"
        report = atomic_tools.check(self.data, out, self.config, max_usd=100)
        self.assertEqual(len(report["rows"]), 4)
        self.assertEqual(len(self.payloads), 4)
        for payload in self.payloads:
            user = json.loads(payload["messages"][1]["content"])
            self.assertEqual(set(user), {"question", "response_context", "sentences"})
            self.assertNotIn("SECRET_GOLD_SENTINEL", json.dumps(payload))
        self.assertIn("REQUIRES HUMAN REVIEW", report["semantic_quality"])
        atomic_tools.check(self.data, out, self.config, max_usd=100)
        self.assertEqual(len(self.payloads), 4)
        self.assertFalse((out/"predictions.jsonl").exists())
        changed = {**self.cfg, "extract_reasoning_effort": "medium"}
        write_json(self.config, changed)
        with self.assertRaises(ValueError): atomic_tools.check(self.data, out, self.config)

    def test_reuse_baselines_only_and_preserves_historical_resources(self):
        source = self.source_pilot()
        before = len(self.payloads)
        target = self.root/"revised"
        manifest = atomic_tools.reuse(self.data, source, target, self.config)
        self.assertEqual(len(self.payloads), before)
        expected = sum(1 + len(sentence_units(r["response"])) for r in read_jsonl(self.data))
        self.assertEqual(manifest["cache_provenance"]["imported_calls"], expected)
        self.assertEqual(len(list((target/"call_cache").glob("*.json"))), expected)
        self.assertFalse((target/"predictions.jsonl").exists())
        run(self.data, target, self.config, phase="pilot", max_requests=200, max_usd=100)
        predictions = read_jsonl(target/"predictions.jsonl")
        self.assertEqual(sum(p["cached_calls"] for p in predictions), expected)
        self.assertTrue(all(p["cached_calls"] == 0 for p in predictions if p["variant"] == "atomic"))
        for p in predictions:
            if p["variant"] != "atomic":
                self.assertGreater(p["estimated_usd"], 0)
                self.assertGreater(p["total_latency_ms"], 0)
        summary = evaluate(self.data, target, n_bootstrap=0)
        self.assertEqual(summary["cache_provenance"]["imported_calls"], expected)
        self.assertIn("not a fresh three-group timing", (target/"report.html").read_text())
        # Only newly billed calls enter the new ledger, excluding imported calls.
        self.assertEqual(len(read_jsonl(target/"calls.jsonl")), len(self.payloads) - before)

    def test_reuse_rejects_changed_verifier_or_data_and_nonempty_destination(self):
        source = self.source_pilot()
        write_json(self.config, {**self.cfg, "reasoning_effort": "low"})
        target = self.root/"rejected"
        with self.assertRaises(ValueError): atomic_tools.reuse(self.data, source, target, self.config)
        self.assertFalse(target.exists())
        write_json(self.config, self.cfg)
        rows = read_jsonl(self.data)
        rows[0]["evidence"] += " changed"
        changed = self.root/"changed.jsonl"
        write_jsonl(changed, rows)
        with self.assertRaises(ValueError): atomic_tools.reuse(changed, source, target, self.config)
        target.mkdir()
        (target/"keep.txt").write_text("keep")
        with self.assertRaises(ValueError): atomic_tools.reuse(self.data, source, target, self.config)
        self.assertEqual((target/"keep.txt").read_text(), "keep")

    def test_reuse_rejects_inconsistent_raw_cache_before_writing(self):
        source = self.source_pilot()
        for path in (source/"call_cache").glob("*.json"):
            record = json.loads(path.read_text())
            if record["payload"]["response_format"]["json_schema"]["name"] == "grounded_verdict":
                record["parsed"]["reason"] = "corrupted"
                write_json(path, record)
        target = self.root/"rejected"
        with self.assertRaises(ValueError): atomic_tools.reuse(self.data, source, target, self.config)
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
