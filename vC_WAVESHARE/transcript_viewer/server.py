#!/usr/bin/env python3
"""Local-only WearabLLM conversation console.

Binds to 127.0.0.1 and keeps device tokens out of browser JavaScript.
Proxies:
  - hosted/local bridge conversation + reply endpoints
  - optional Supabase transcript log for the event feed
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
V3_DIR = ROOT / "vC_WAVESHARE"
DEFAULT_SDKCONFIG = V3_DIR / "firmware" / "sdkconfig"
STATIC_DIR = Path(__file__).resolve().parent / "static"
DEPLOY_SCRIPT = V3_DIR / "scripts" / "deploy_hf_space.py"
DEFAULT_HF_SPACE = os.environ.get("WEARABLLM_HF_SPACE", "brick-factorial/wearabllm-agent")


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


def bridge_base_url(bridge_query_url: str) -> str:
    """Turn .../v1/query into the bridge origin."""
    raw = bridge_query_url.strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlsplit(raw)
    if not parsed.scheme or not parsed.netloc:
        return ""
    path = parsed.path or ""
    for suffix in ("/v1/query", "/v1/query_text", "/v1/tts"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    path = path.rstrip("/")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


class ConsoleHandler(SimpleHTTPRequestHandler):
    transcript_endpoint = ""
    transcript_token = ""
    bridge_base = ""
    bridge_token = ""
    default_device_id = "web-console"
    hf_space_repo = DEFAULT_HF_SPACE
    allow_deploy = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, format: str, *args) -> None:
        if self.path.startswith("/api/") and args and str(args[1]).startswith("200"):
            return
        super().log_message(format, *args)

    def end_headers(self) -> None:
        # Avoid sticky browser caches of the console UI during rapid iteration.
        if self.path.startswith("/api/") or self.path in {
            "/",
            "/index.html",
            "/app.js",
            "/styles.css",
        }:
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if path == "/health":
            self.send_json(
                200,
                {
                    "ok": True,
                    "service": "wearabllm-console",
                    "bridge_configured": bool(self.bridge_base),
                    "transcripts_configured": bool(self.transcript_endpoint and self.transcript_token),
                },
            )
            return
        if path == "/api/bootstrap":
            self.send_json(
                200,
                {
                    "ok": True,
                    "default_device_id": self.default_device_id,
                    "bridge_configured": bool(self.bridge_base),
                    "transcripts_configured": bool(self.transcript_endpoint and self.transcript_token),
                    "hf_space_repo": self.hf_space_repo,
                    "deploy_available": bool(self.allow_deploy and DEPLOY_SCRIPT.is_file()),
                    "known_devices": [
                        {
                            "id": "wearabllm-esp32",
                            "label": "Waveshare",
                            "kind": "home",
                            "status": "active",
                            "description": "Waveshare ESP32-S3 on the local network",
                        },
                        {
                            "id": "wearabllm-android",
                            "label": "Android",
                            "kind": "phone",
                            "status": "active",
                            "description": "Android phone for prompting Sphere from Wi-Fi or cellular",
                        },
                        {
                            "id": "web-console",
                            "label": "Web console",
                            "kind": "web",
                            "status": "active",
                            "description": "This browser — reply here to continue the shared thread",
                        },
                        {
                            "id": "ducati-temp-sensor",
                            "label": "Ducati sensor",
                            "kind": "sensor",
                            "status": "active",
                            "description": "Hybrid BLE and outbound Wi-Fi ESP32-S3 temperature sensor",
                        },
                        {
                            "id": "wearabllm-wearable",
                            "label": "Wearable",
                            "kind": "wearable",
                            "status": "planned",
                            "description": "Future portable body that joins the same principal conversation",
                        },
                    ],
                },
            )
            return
        if path == "/api/conversation":
            self.proxy_bridge_get("/v1/conversation", parsed.query)
            return
        if path == "/api/devices":
            self.proxy_bridge_get("/v1/devices", parsed.query)
            return
        if path == "/api/sessions":
            self.proxy_bridge_get("/v1/conversation/sessions", parsed.query)
            return
        if path == "/api/interactions":
            self.proxy_bridge_get("/v1/interactions", parsed.query)
            return
        if path == "/api/body-actions/next":
            self.proxy_body_action_claim("web-console")
            return
        interaction_match = re.fullmatch(r"/api/interactions/([a-f0-9-]{36})", path)
        if interaction_match:
            self.proxy_bridge_get(f"/v1/interactions/{interaction_match.group(1)}", "")
            return
        if path == "/api/transcripts":
            self.proxy_transcripts(parsed.query)
            return
        if path == "/api/admin/config":
            self.proxy_bridge_get("/v1/admin/config", "")
            return
        if path == "/api/admin/catalog":
            self.proxy_bridge_get("/v1/admin/catalog", "")
            return
        super().do_GET()

    def do_POST(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/api/reply":
            self.proxy_reply()
            return
        if parsed.path == "/api/session/reset":
            self.proxy_session_reset()
            return
        session_archive_match = re.fullmatch(
            r"/api/sessions/([a-f0-9-]{36})/archive", parsed.path
        )
        if session_archive_match:
            self.proxy_session_archive(session_archive_match.group(1))
            return
        session_rename_match = re.fullmatch(
            r"/api/sessions/([a-f0-9-]{36})/rename", parsed.path
        )
        if session_rename_match:
            self.proxy_session_rename(session_rename_match.group(1))
            return
        if parsed.path == "/api/heartbeat":
            self.proxy_heartbeat("web-console")
            return
        action_ack_match = re.fullmatch(r"/api/body-actions/([a-f0-9-]{36})/ack", parsed.path)
        if action_ack_match:
            self.proxy_body_action_ack("web-console", action_ack_match.group(1))
            return
        if parsed.path == "/api/admin/config":
            self.proxy_admin_config_update()
            return
        if parsed.path == "/api/admin/api-key":
            self.proxy_admin_api_key_update()
            return
        if parsed.path == "/api/admin/deploy":
            self.handle_local_deploy()
            return
        self.send_json(404, {"error": "not_found"})

    def proxy_bridge_get(self, bridge_path: str, query: str) -> None:
        if not self.bridge_base:
            self.send_json(503, {"error": "bridge_not_configured"})
            return
        url = f"{self.bridge_base}{bridge_path}"
        if query:
            url = f"{url}?{query}"
        request = urllib.request.Request(
            url,
            headers=self._bridge_headers(),
            method="GET",
        )
        self._forward(request)

    def proxy_reply(self) -> None:
        if not self.bridge_base:
            self.send_json(503, {"error": "bridge_not_configured"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 16_384:
            self.send_json(400, {"error": "invalid_body"})
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_json(400, {"error": "invalid_json"})
            return
        transcript = str(payload.get("transcript", "")).strip()
        device_id = str(payload.get("device_id", self.default_device_id)).strip() or self.default_device_id
        target_device_id = str(payload.get("target_device_id", "")).strip()
        if not transcript:
            self.send_json(400, {"error": "missing_transcript"})
            return
        if not self._valid_device_id(device_id):
            self.send_json(400, {"error": "invalid_device_id"})
            return
        interaction = bool(target_device_id)
        body = json.dumps(
            {
                "transcript": transcript,
                "origin_device_id": device_id,
                "target_device_id": target_device_id,
                "idempotency_key": str(payload.get("idempotency_key", "")),
            }
            if interaction
            else {"transcript": transcript}
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.bridge_base}/v1/interactions" if interaction else f"{self.bridge_base}/v1/query_text",
            data=body,
            headers={
                **self._bridge_headers(),
                "Content-Type": "application/json",
                "X-WearabLLM-Device-Id": device_id,
            },
            method="POST",
        )
        self._forward(request)

    def proxy_session_reset(self) -> None:
        if not self.bridge_base:
            self.send_json(503, {"error": "bridge_not_configured"})
            return
        request = urllib.request.Request(
            f"{self.bridge_base}/v1/session/reset",
            data=b"{}",
            headers={
                **self._bridge_headers(),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        self._forward(request)

    def proxy_session_archive(self, session_id: str) -> None:
        if not self.bridge_base:
            self.send_json(503, {"error": "bridge_not_configured"})
            return
        request = urllib.request.Request(
            f"{self.bridge_base}/v1/conversation/sessions/{session_id}/archive",
            data=b"{}",
            headers={
                **self._bridge_headers(),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        self._forward(request)

    def proxy_session_rename(self, session_id: str) -> None:
        if not self.bridge_base:
            self.send_json(503, {"error": "bridge_not_configured"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 2_048:
            self.send_json(400, {"error": "invalid_body"})
            return
        raw = self.rfile.read(length)
        request = urllib.request.Request(
            f"{self.bridge_base}/v1/conversation/sessions/{session_id}/rename",
            data=raw,
            headers={
                **self._bridge_headers(),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        self._forward(request)

    def proxy_heartbeat(self, device_id: str) -> None:
        if not self.bridge_base:
            self.send_json(503, {"error": "bridge_not_configured"})
            return
        request = urllib.request.Request(
            f"{self.bridge_base}/v1/heartbeat",
            data=b"{}",
            headers={
                **self._bridge_headers(),
                "Content-Type": "application/json",
                "X-WearabLLM-Device-Id": device_id,
            },
            method="POST",
        )
        self._forward(request)

    def proxy_body_action_claim(self, device_id: str) -> None:
        if not self.bridge_base:
            self.send_json(503, {"error": "bridge_not_configured"})
            return
        request = urllib.request.Request(
            f"{self.bridge_base}/v1/devices/{device_id}/actions",
            headers={**self._bridge_headers(), "X-WearabLLM-Device-Id": device_id},
            method="GET",
        )
        self._forward(request)

    def proxy_body_action_ack(self, device_id: str, action_id: str) -> None:
        if not self.bridge_base:
            self.send_json(503, {"error": "bridge_not_configured"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 2_048:
            self.send_json(400, {"error": "invalid_body"})
            return
        raw = self.rfile.read(length)
        request = urllib.request.Request(
            f"{self.bridge_base}/v1/devices/{device_id}/actions/{action_id}/ack",
            data=raw,
            headers={
                **self._bridge_headers(),
                "Content-Type": "application/json",
                "X-WearabLLM-Device-Id": device_id,
            },
            method="POST",
        )
        self._forward(request)

    def proxy_admin_config_update(self) -> None:
        if not self.bridge_base:
            self.send_json(503, {"error": "bridge_not_configured"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 64_000:
            self.send_json(400, {"error": "invalid_body"})
            return
        raw = self.rfile.read(length)
        request = urllib.request.Request(
            f"{self.bridge_base}/v1/admin/config",
            data=raw,
            headers={
                **self._bridge_headers(),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        self._forward(request)

    def proxy_admin_api_key_update(self) -> None:
        if not self.bridge_base:
            self.send_json(503, {"error": "bridge_not_configured"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 2_048:
            self.send_json(400, {"error": "invalid_body"})
            return
        raw = self.rfile.read(length)
        request = urllib.request.Request(
            f"{self.bridge_base}/v1/admin/api-key",
            data=raw,
            headers={
                **self._bridge_headers(),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        self._forward(request)

    def handle_local_deploy(self) -> None:
        """Run the laptop-side HF Space deploy script. Secrets never enter the browser."""
        if not self.allow_deploy:
            self.send_json(403, {"error": "deploy_disabled"})
            return
        if not DEPLOY_SCRIPT.is_file():
            self.send_json(500, {"error": "deploy_script_missing"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        dry_run = False
        repo_id = self.hf_space_repo
        if length > 0:
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError:
                self.send_json(400, {"error": "invalid_json"})
                return
            dry_run = bool(payload.get("dry_run"))
            if payload.get("repo_id"):
                repo_id = str(payload["repo_id"]).strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", repo_id or ""):
            self.send_json(400, {"error": "invalid_repo_id"})
            return
        command = [
            os.environ.get("WEARABLLM_PYTHON", "python3"),
            str(DEPLOY_SCRIPT),
            "--repo-id",
            repo_id,
        ]
        if dry_run:
            command.append("--dry-run")
        try:
            result = subprocess.run(
                command,
                cwd=str(V3_DIR),
                text=True,
                capture_output=True,
                timeout=180,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self.send_json(504, {"error": "deploy_timeout"})
            return
        except OSError as exc:
            self.send_json(500, {"error": f"deploy_failed: {exc}"})
            return
        output = (result.stdout or "") + (("\n" + result.stderr) if result.stderr else "")
        self.send_json(
            200 if result.returncode == 0 else 500,
            {
                "ok": result.returncode == 0,
                "repo_id": repo_id,
                "dry_run": dry_run,
                "exit_code": result.returncode,
                "output": output[-8000:],
            },
        )

    def proxy_transcripts(self, query: str) -> None:
        if not self.transcript_endpoint or not self.transcript_token:
            self.send_json(503, {"error": "transcripts_not_configured", "transcripts": []})
            return
        params = urllib.parse.parse_qs(query)
        safe_params: dict[str, str] = {"limit": "100"}
        for key in ("limit", "after_id"):
            if key in params and params[key]:
                value = params[key][0]
                if not value.isdecimal():
                    self.send_json(400, {"error": "invalid_query"})
                    return
                safe_params[key] = value
        url = f"{self.transcript_endpoint}?{urllib.parse.urlencode(safe_params)}"
        request = urllib.request.Request(
            url,
            headers={"X-WearabLLM-Device-Token": self.transcript_token},
        )
        self._forward(request)

    def _bridge_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "X-WearabLLM-Client": "web-console",
            "X-WearabLLM-Client-Version": "0.1.0",
        }
        if self.bridge_token:
            headers["X-WearabLLM-Device-Token"] = self.bridge_token
        return headers

    @staticmethod
    def _valid_device_id(device_id: str) -> bool:
        return bool(re.fullmatch(r"[A-Za-z0-9._-]{1,80}", device_id))

    def _forward(self, request: urllib.request.Request) -> None:
        try:
            with urllib.request.urlopen(request, timeout=60) as upstream:
                body = upstream.read()
                self.send_response(upstream.status)
                self.send_header("Content-Type", upstream.headers.get("Content-Type", "application/json"))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        except urllib.error.HTTPError as exc:
            detail = exc.read()
            if detail:
                self.send_response(exc.code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(detail)))
                self.end_headers()
                self.wfile.write(detail)
            else:
                self.send_json(exc.code, {"error": "upstream_rejected_request"})
        except (urllib.error.URLError, TimeoutError):
            self.send_json(502, {"error": "upstream_unreachable"})

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local WearabLLM conversation console.")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--sdkconfig", type=Path, default=DEFAULT_SDKCONFIG)
    parser.add_argument("--bridge-url", default="", help="Override bridge base or /v1/query URL")
    parser.add_argument("--bridge-token", default="", help="Override bridge device token")
    parser.add_argument("--hf-space", default=DEFAULT_HF_SPACE, help="HF Space repo for deploy button")
    parser.add_argument("--no-deploy", action="store_true", help="Disable local deploy button actions")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if not args.sdkconfig.is_file():
        parser.error(f"sdkconfig not found: {args.sdkconfig}")

    bridge_url = args.bridge_url or read_kconfig_string(args.sdkconfig, "WEARABLLM_BRIDGE_URL")
    bridge_token = args.bridge_token or read_kconfig_string(args.sdkconfig, "WEARABLLM_BRIDGE_AUTH_TOKEN")
    transcript_endpoint = read_kconfig_string(args.sdkconfig, "WEARABLLM_TRANSCRIPT_LOG_URL")
    transcript_token = read_kconfig_string(args.sdkconfig, "WEARABLLM_TRANSCRIPT_DEVICE_TOKEN")
    device_id = read_kconfig_string(args.sdkconfig, "WEARABLLM_DEVICE_ID") or "wearabllm-esp32"

    base = bridge_base_url(bridge_url)
    if not base:
        parser.error("Bridge URL is not configured (sdkconfig WEARABLLM_BRIDGE_URL or --bridge-url)")

    ConsoleHandler.bridge_base = base
    ConsoleHandler.bridge_token = bridge_token
    ConsoleHandler.transcript_endpoint = transcript_endpoint
    ConsoleHandler.transcript_token = transcript_token
    ConsoleHandler.default_device_id = "web-console"
    ConsoleHandler.hf_space_repo = args.hf_space
    ConsoleHandler.allow_deploy = not args.no_deploy

    server = ThreadingHTTPServer(("127.0.0.1", args.port), ConsoleHandler)
    local_url = f"http://127.0.0.1:{args.port}"
    print(f"WearabLLM console: {local_url}")
    print(f"Bridge: {base}")
    print(f"Home body id (from sdkconfig): {device_id}")
    print("Device tokens stay in the Python process; browser JS never sees them.")
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
