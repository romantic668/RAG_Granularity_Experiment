"""Optional curl transport; credentials travel via stdin, never process arguments.

TLS verification stays enabled. Each call starts a curl process, and the measured
client latency includes that overhead. No redirects or curl config files are used.
"""
import html
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
from email.parser import BytesParser
from http import HTTPStatus
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit


def safe_text(value, key="", limit=1800):
    text = html.unescape(str(value))
    if key:
        text = text.replace(key, "[REDACTED]")
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r"(?i)Bearer\s+[^\s\"'<>]+", "Bearer [REDACTED]", text)
    return " ".join(text.split())[:limit]


def error_detail(error, key=""):
    headers = error.headers or {}
    body = error.read(65536)
    detail = {"http_status": error.code,
              "content_type": safe_text(headers.get("Content-Type", ""), key),
              "request_id": safe_text(headers.get("x-request-id", ""), key)}
    try:
        obj = json.loads(body)
    except (ValueError, UnicodeError):
        obj = None
    server_error = obj.get("error") if isinstance(obj, dict) else None
    if isinstance(server_error, dict):
        for field in ("message", "type", "param", "code"):
            value = server_error.get(field)
            if value is None or isinstance(value, (str, int, float, bool)):
                detail[field] = safe_text(value, key) if value is not None else None
    else:
        text = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", " ", body.decode("utf-8", errors="replace"))
        text = re.sub(r"<[^>]*>", " ", text)
        detail["body_preview"] = safe_text(text, key) or "[empty body]"
    return detail


def _config_quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('\r', '\\r').replace('\n', '\\n').replace('\t', '\\t') + '"'


class CurlResponse(io.BytesIO):
    def __init__(self, body, headers, status, url):
        super().__init__(body)
        self.headers, self.status, self.code, self.url = headers, status, status, url

    def getcode(self):
        return self.status

    def geturl(self):
        return self.url


def curl_urlopen(request, timeout=120):
    binary = shutil.which("curl.exe") if os.name == "nt" else shutil.which("curl")
    if not binary:
        raise URLError("curl executable not found; this transport requires curl on PATH")
    if urlsplit(request.full_url).scheme not in ("http", "https"):
        raise URLError("curl transport only accepts HTTP/HTTPS URLs")
    secrets = [value for name, value in request.header_items() if name.lower() == "authorization"]
    key = secrets[0].removeprefix("Bearer ") if secrets else ""
    config = "\n".join("header = " + _config_quote(name + ": " + value)
                       for name, value in request.header_items()) + "\n"
    with tempfile.TemporaryDirectory(prefix="ragstudy_http_") as tmp:
        folder = Path(tmp)
        body_path, header_path, upload_path = folder/"response.bin", folder/"headers.txt", folder/"request.bin"
        command = [binary, "--disable", "--silent", "--show-error", "--http1.1",
                   "--proto", "=http,https", "--max-time", str(timeout),
                   "--request", request.get_method(), "--url", request.full_url,
                   "--config", "-", "--dump-header", str(header_path),
                   "--output", str(body_path), "--write-out", "%{http_code}"]
        if request.data is not None:
            upload_path.write_bytes(request.data)
            command.extend(["--data-binary", "@" + str(upload_path)])
        try:
            completed = subprocess.run(command, input=config.encode("utf-8"),
                                       capture_output=True, timeout=float(timeout)+5,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as error:
            raise TimeoutError("curl request timed out") from error
        except OSError as error:
            raise URLError("Could not start curl: " + type(error).__name__) from error
        if completed.returncode:
            message = safe_text(completed.stderr.decode("utf-8", errors="replace"), key)
            raise URLError("curl exit " + str(completed.returncode) + ": " + message)
        try:
            status = int(completed.stdout.strip())
        except ValueError as error:
            raise URLError("curl returned no valid HTTP status") from error
        raw_headers = header_path.read_bytes() if header_path.exists() else b""
        blocks = [part for part in re.split(rb"\r?\n\r?\n", raw_headers) if part.startswith(b"HTTP/")]
        header_bytes = blocks[-1].partition(b"\n")[2] if blocks else b""
        headers = BytesParser().parsebytes(header_bytes)
        body = body_path.read_bytes() if body_path.exists() else b""
    if status < 200 or status >= 300:
        phrase = HTTPStatus(status).phrase if status in HTTPStatus._value2member_map_ else "HTTP error"
        raise HTTPError(request.full_url, status, phrase, headers, io.BytesIO(body))
    return CurlResponse(body, headers, status, request.full_url)
