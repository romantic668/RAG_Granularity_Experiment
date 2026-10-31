import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .core import append_jsonl, canonical, digest, read_jsonl, write_json
from .http_transport import curl_urlopen, error_detail, safe_text

class APIError(RuntimeError):
    pass

class BudgetStop(APIError):
    pass

def token_cost(usage, cfg):
    n = usage.get("prompt_tokens", 0)
    cached = usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
    out = usage.get("completion_tokens", 0)
    return ((n-cached)*cfg["price_usd_per_million_input"]
            + cached*cfg["price_usd_per_million_cached_input"]
            + out*cfg["price_usd_per_million_output"]) / 1_000_000

def build_payload(cfg, stage, prompt, user, schema):
    """Shared request construction also permits exact-payload cache reuse checks."""
    payload = {"model": cfg["model"], "store": False,
               "messages": [{"role":"system", "content":prompt}, {"role":"user", "content":canonical(user)}],
               "max_completion_tokens": cfg["extract_max_completion_tokens" if stage == "extract" else "verify_max_completion_tokens"],
               "response_format": {"type":"json_schema", "json_schema": {
                   "name": "atomic_extraction" if stage == "extract" else "grounded_verdict",
                   "strict": True, "schema": schema}}}
    effort = cfg.get("extract_reasoning_effort", cfg.get("reasoning_effort")) if stage == "extract" else cfg.get("reasoning_effort")
    if effort is not None:
        payload["reasoning_effort"] = effort
    if cfg.get("temperature") is not None:
        payload["temperature"] = cfg["temperature"]
    return payload

class APIClient:
    def __init__(self, cfg, run_dir, max_requests=200, max_usd=2.0):
        self.cfg, self.run_dir = cfg, Path(run_dir)
        self.cache = self.run_dir / "call_cache"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.log = self.run_dir / "calls.jsonl"
        self.requests = 0
        self.max_requests, self.max_usd = max_requests, max_usd
        history = read_jsonl(self.log) if self.log.exists() else []
        self.cost = sum(e.get("estimated_usd",0) for e in history)
        self.history = {}
        for event in history:
            self._remember(event)
        self.key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not self.key:
            raise APIError("OPENAI_API_KEY is not set. Set it locally; never put it in config.json or send it in chat.")
        if cfg.get("http_transport", "urllib") not in ("urllib", "curl"):
            raise APIError("http_transport must be urllib or curl")

    def _remember(self, event):
        state = self.history.setdefault(event["scope"], {"attempts":0,"elapsed_ms":0,"cost":0,
            "usage":{"prompt_tokens":0,"completion_tokens":0,"prompt_tokens_details":{"cached_tokens":0}}})
        state["attempts"] += int(event.get("kind") != "backoff")
        state["elapsed_ms"] += event.get("duration_ms",0)
        state["cost"] += event.get("estimated_usd",0)
        usage=event.get("usage",{})
        state["usage"]["prompt_tokens"]+=usage.get("prompt_tokens",0)
        state["usage"]["completion_tokens"]+=usage.get("completion_tokens",0)
        state["usage"]["prompt_tokens_details"]["cached_tokens"]+=usage.get("prompt_tokens_details",{}).get("cached_tokens",0)

    def _event(self, event):
        append_jsonl(self.log,event)
        self._remember(event)

    def call(self, stage, scope, prompt, user, schema):
        start = time.perf_counter()
        payload = build_payload(self.cfg, stage, prompt, user, schema)
        key = digest({"scope": scope, "payload": payload})
        path = self.cache / (key + ".json")
        if path.exists():
            record = json.loads(path.read_text(encoding="utf-8"))
            return record["parsed"], {**record, "from_cache": True,
                "current_ms": (time.perf_counter()-start)*1000}
        prior = dict(self.history.get(scope,{"attempts":0,"elapsed_ms":0,"cost":0}))
        attempts = 0
        while True:
            if self.requests >= self.max_requests:
                raise BudgetStop("Request cap reached. Resume the same command with a suitable cap.")
            if self.max_usd is not None and self.cost >= self.max_usd:
                raise BudgetStop("Estimated total USD threshold reached. It is a soft threshold and may overshoot by one call.")
            self.requests += 1; attempts += 1
            attempt_start = time.perf_counter()
            request = Request(self.cfg["base_url"].rstrip("/") + "/chat/completions",
                              data=canonical(payload).encode("utf-8"),
                              headers={"Content-Type":"application/json", "Authorization":"Bearer " + self.key},
                              method="POST")
            try:
                opener = curl_urlopen if self.cfg.get("http_transport") == "curl" else urlopen
                with opener(request, timeout=self.cfg["timeout_seconds"]) as response:
                    raw = json.load(response)
                    request_id = response.headers.get("x-request-id", "")
                usage = raw.get("usage", {})
                cost = token_cost(usage, self.cfg)
                self.cost += cost
                self._event({"scope": scope, "stage": stage, "attempt": attempts,
                    "ok": True, "usage": usage, "estimated_usd": cost, "request_id": request_id,
                    "duration_ms":(time.perf_counter()-attempt_start)*1000})
                choice = raw["choices"][0]
                message = choice["message"]
                if message.get("refusal"):
                    raise APIError("Model refusal: " + message["refusal"])
                if choice.get("finish_reason") != "stop":
                    raise APIError("Incomplete model output; finish_reason=" + str(choice.get("finish_reason")))
                content = message.get("content")
                if not isinstance(content, str):
                    raise APIError("Missing JSON content in model response")
                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError as e:
                    raise APIError("Model returned invalid JSON") from e
                record = {"parsed": parsed, "raw_response": raw, "payload": payload,
                          "elapsed_ms": prior["elapsed_ms"]+(time.perf_counter()-start)*1000,
                          "attempts": prior["attempts"]+attempts, "usage": self.history[scope]["usage"],
                          "estimated_usd": prior["cost"]+cost,
                          "resolved_model": raw.get("model"), "request_id": request_id}
                write_json(path, record)
                return parsed, {**record, "from_cache": False,
                    "current_ms": (time.perf_counter()-start)*1000}
            except HTTPError as e:
                # Keep selected diagnostic fields, with credentials redacted.
                detail = error_detail(e, self.key)
                self._event({"scope": scope, "stage": stage, "attempt": attempts,
                             "ok": False, "http_status": e.code,
                             "error_detail": detail,
                             "duration_ms":(time.perf_counter()-attempt_start)*1000})
                description = json.dumps(detail, ensure_ascii=True)
                if e.code not in (408,409,429) and e.code < 500:
                    raise APIError(f"HTTP {e.code}: {description}. No prediction was assigned.") from e
                if attempts > self.cfg["max_retries"]:
                    raise APIError(f"HTTP {e.code} after {attempts} attempts: {description}") from e
            except (URLError, TimeoutError, ConnectionError) as e:
                self._event({"scope": scope, "stage": stage, "attempt": attempts,
                             "ok": False, "network_error": type(e).__name__,
                             "duration_ms":(time.perf_counter()-attempt_start)*1000})
                if attempts > self.cfg["max_retries"]:
                    reason = safe_text(getattr(e, "reason", str(e)), self.key)
                    raise APIError(f"Network failure after {attempts} attempts: {type(e).__name__}: {reason}") from e
            backoff_start=time.perf_counter()
            time.sleep(min(2 ** attempts, 8))
            self._event({"scope":scope,"stage":stage,"kind":"backoff",
                         "duration_ms":(time.perf_counter()-backoff_start)*1000})

class DemoClient:
    """Handwritten integration fixture. Its outputs are never research results."""
    def call(self, stage, scope, prompt, user, schema):
        if stage == "extract":
            claims = []
            for sent in user["sentences"]:
                text = sent["text"]
                if "free parking and Wi-Fi" in text:
                    for obj in ("free parking", "Wi-Fi"):
                        claims.append({"sentence_id":sent["sentence_id"], "claim_text":"Subway offers " + obj + ".",
                                       "quotes":[{"quote":"Subway offers", "occurrence":0}, {"quote":obj,"occurrence":0}]})
                else:
                    claims.append({"sentence_id":sent["sentence_id"], "claim_text":text,
                                   "quotes":[{"quote":text, "occurrence":0}]})
            result = {"claims": claims}
        else:
            text = user["target_unit"]
            bad = any(s in text for s in ("8 a.m.", "free parking", "Wi-Fi"))
            result = {"status": "unsupported" if bad else "supported",
                      "reason": "Handwritten demo fixture; not model inference."}
        record = {"parsed":result, "from_cache":False, "current_ms":0, "elapsed_ms":0,
                  "attempts":0, "usage":{}, "estimated_usd":0, "resolved_model":"DEMO_FIXTURE"}
        return result, record
