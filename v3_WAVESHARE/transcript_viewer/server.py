#!/usr/bin/env python3
"""Local-only WearabLLM transcript viewer and credential-hiding proxy."""

from __future__ import annotations

import argparse
import ast
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SDKCONFIG = ROOT / "v3_WAVESHARE" / "firmware" / "sdkconfig"
STATIC_DIR = Path(__file__).resolve().parent / "static"


def read_kconfig_string(path: Path, key: str) -> str:
    prefix = f"CONFIG_{key}="
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            value = line[len(prefix) :]
            try:
                parsed = ast.literal_eval(value)
            except (SyntaxError, ValueError) as exc:
                raise ValueError(f"invalid {key} value in {path}") from exc
            return parsed if isinstance(parsed, str) else ""
    return ""


class ViewerHandler(SimpleHTTPRequestHandler):
    endpoint = ""
    device_token = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, format: str, *args) -> None:
        if self.path.startswith("/api/") and args and str(args[1]).startswith("200"):
            return
        super().log_message(format, *args)

    def do_GET(self) -> None:
        if self.path.startswith("/api/transcripts"):
            self.proxy_transcripts()
            return
        if self.path == "/health":
            self.send_json(200, {"ok": True})
            return
        super().do_GET()

    def proxy_transcripts(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        safe_params: dict[str, str] = {"limit": "100"}
        for key in ("limit", "after_id"):
            if key in params and params[key]:
                value = params[key][0]
                if not value.isdecimal():
                    self.send_json(400, {"error": "invalid_query"})
                    return
                safe_params[key] = value
        url = f"{self.endpoint}?{urllib.parse.urlencode(safe_params)}"
        request = urllib.request.Request(
            url,
            headers={"X-WearabLLM-Device-Token": self.device_token},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as upstream:
                body = upstream.read()
                self.send_response(upstream.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        except urllib.error.HTTPError as exc:
            self.send_json(exc.code, {"error": "upstream_rejected_request"})
        except (urllib.error.URLError, TimeoutError):
            self.send_json(502, {"error": "supabase_unreachable"})

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local transcript viewer.")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--sdkconfig", type=Path, default=DEFAULT_SDKCONFIG)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if not args.sdkconfig.is_file():
        parser.error(f"sdkconfig not found: {args.sdkconfig}")

    endpoint = read_kconfig_string(args.sdkconfig, "WEARABLLM_TRANSCRIPT_LOG_URL")
    token = read_kconfig_string(args.sdkconfig, "WEARABLLM_TRANSCRIPT_DEVICE_TOKEN")
    if not endpoint.startswith("https://") or not token:
        parser.error("Supabase transcript URL/token are not configured in sdkconfig")

    ViewerHandler.endpoint = endpoint
    ViewerHandler.device_token = token
    server = ThreadingHTTPServer(("127.0.0.1", args.port), ViewerHandler)
    local_url = f"http://127.0.0.1:{args.port}"
    print(f"WearabLLM transcripts: {local_url}")
    print("The viewer is local-only; the device token stays in the Python process.")
    if not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(local_url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
