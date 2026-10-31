"""Place beside experiment.py; diagnose one real verifier request, not an experiment.

Uses the existing project and key from OPENAI_API_KEY. No key/payload is printed.
Version 2: includes redacted response summaries and proxy route metadata.
One request maximum, no retries, separate diagnostic output directory.
"""
import argparse
import gzip
import html
import io
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import getproxies
from unittest.mock import patch


def redact(value, key):
    text = str(value)
    if key:
        text = text.replace(key, "[REDACTED]")
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r"(?i)Bearer\s+[^\s\"'<>]+", "Bearer [REDACTED]", text)
    text = re.sub(r"(?i)((?:proxy-)?authorization\s*[:=]\s*)(?:Basic\s+)?[^\s\"'<>]+", r"\1[REDACTED]", text)
    return text[:2000]


def response_preview(body, key, encoding=""):
    if "gzip" in encoding.lower():
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
                body = stream.read(65536)
        except (OSError, EOFError):
            pass
    text = html.unescape(body.decode("utf-8", errors="replace"))
    text = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"<[^>]*>", " ", text)
    return redact(" ".join(text.split()), key) or "[empty body]"


def route_info(value, key):
    try:
        parsed = urlsplit(value if "://" in value else "http://" + value)
        return {"scheme": parsed.scheme, "host": redact(parsed.hostname or "", key),
                "port": parsed.port, "credentials_present": parsed.username is not None}
    except ValueError:
        return {"parseable": False}


def show_error(error, key):
    body = error.read(65536)
    # Preserve the response stream for the original error handler.
    error.fp = io.BytesIO(body)
    headers = error.headers or {}
    encoding = headers.get("Content-Encoding", "")
    result = {"http_status": error.code,
              "content_type": redact(headers.get("Content-Type", ""), key),
              "server": redact(headers.get("Server", ""), key),
              "via": redact(headers.get("Via", ""), key),
              "content_encoding": redact(encoding, key),
              "captured_body_bytes": len(body)}
    try:
        obj = json.loads(body.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeError):
        obj = None
    detail = obj.get("error") if isinstance(obj, dict) else None
    if isinstance(detail, dict):
        for field in ("message", "type", "param", "code"):
            value = detail.get(field)
            if value is None or isinstance(value, (str, int, float, bool)):
                result[field] = redact(value, key) if value is not None else None
    else:
        result["message"] = "No standard JSON error object. See the redacted body preview."
        result["body_preview"] = response_preview(body, key, encoding)
    result["request_id"] = redact(headers.get("x-request-id", ""), key)
    print("API_ERROR_DETAIL:", flush=True)
    print(json.dumps(result, ensure_ascii=True, indent=2), flush=True)


def diagnose(config, input_path, response_id, out):
    from ragstudy import api
    from ragstudy.core import VERIFY_PROMPT, VERIFY_SCHEMA, read_jsonl
    from ragstudy.engine import load_config, validate_dataset

    cfg = load_config(config)
    rows = read_jsonl(input_path)
    validate_dataset(rows, cfg)
    row = next((r for r in rows if response_id is None or r["id"] == response_id), None)
    if row is None:
        raise ValueError("Requested response ID is absent from the input file.")
    cfg = dict(cfg)
    cfg["max_retries"] = 0
    client = api.APIClient(cfg, out, max_requests=1, max_usd=2)
    original_urlopen = api.urlopen

    def detailed_urlopen(*args, **kwargs):
        try:
            return original_urlopen(*args, **kwargs)
        except HTTPError as error:
            show_error(error, client.key)
            raise

    user = {"question": row["question"], "evidence": row["evidence"],
            "response_context": row["response"], "target_unit": row["response"]}
    print("DIAGNOSTIC ONLY: at most one real API request; usage may be billed.")
    print("DIAGNOSTIC_VERSION: 2")
    print("Model:", cfg["model"], "| Response ID:", row["id"])
    endpoint = urlsplit(cfg["base_url"])
    print("CONNECTION_INFO:")
    print(json.dumps({"endpoint_host": redact(endpoint.hostname or "", client.key),
                      "endpoint_path": redact(endpoint.path, client.key),
                      "proxy_routes": {kind: route_info(value, client.key)
                                       for kind, value in getproxies().items()
                                       if kind in ("http", "https", "all")},
                      "key_is_ascii": client.key.isascii(),
                      "key_has_whitespace": any(c.isspace() for c in client.key)},
                     ensure_ascii=True, indent=2))
    with patch.object(api, "urlopen", detailed_urlopen):
        result, _ = client.call("verify", "diagnostic/" + row["id"],
                                VERIFY_PROMPT, user, VERIFY_SCHEMA)
    if result.get("status") not in ("supported", "unsupported", "contradicted") or not isinstance(result.get("reason"), str):
        raise ValueError("The API returned an invalid verifier result.")
    print("API_OK: one structured verifier response received.")
    print("Diagnostic only. No experimental predictions or metrics were written.")


def main():
    root = Path(__file__).resolve().parent
    if not (root / "experiment.py").is_file() or not (root / "ragstudy").is_dir():
        print("ERROR: put diagnose_api.py beside experiment.py in the extracted project folder.")
        return 2
    os.chdir(root)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--input", default="data/smoke3.jsonl")
    parser.add_argument("--id", default="16280")
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    try:
        diagnose(args.config, args.input, args.id, root / "runs" / "api_diagnostic" / stamp)
        return 0
    except Exception as error:
        print("DIAGNOSTIC_STOP:", redact(error, os.getenv("OPENAI_API_KEY", "").strip()))
        return 1


if __name__ == "__main__":
    sys.exit(main())
