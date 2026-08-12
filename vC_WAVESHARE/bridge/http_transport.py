"""HTTP transport boundary for the WearabLLM bridge.

This module owns route matching, request parsing and authentication, and the
legacy /v1 response serialization. Application behavior remains on the
injected bridge state facade.
"""

from __future__ import annotations

import hmac
import json
import re
import time
import urllib.parse
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Any, Literal, Pattern

from action_queue import validate_device_id
from bridge_contracts import InteractionInput, QueryInput
from bridge_policy import PrivilegedOperation
from observability import (
    REQUEST_ID_HEADER,
    emit_debug_content,
    emit_event,
    emit_exception,
    new_request_id,
)
from v2_protocol import error_envelope as v2_error_envelope
from v2_protocol import success_envelope as v2_success_envelope


@dataclass(frozen=True, slots=True)
class Route:
    endpoint: str
    auth_required: bool = True
    privileged_operation: PrivilegedOperation | None = None


@dataclass(frozen=True, slots=True)
class PatternRoute:
    pattern: Pattern[str]
    endpoint: str
    auth_required: bool = True
    privileged_operation: PrivilegedOperation | None = None


@dataclass(frozen=True, slots=True)
class RouteMatch:
    endpoint: str
    path_arguments: tuple[str, ...] = ()
    auth_required: bool = True
    privileged_operation: PrivilegedOperation | None = None
    protocol_version: Literal[1, 2] = 1


GET_ROUTES = {
    "/health": Route("_handle_health", auth_required=False),
    "/v1/admin/config": Route(
        "_handle_get_admin_config",
        privileged_operation=PrivilegedOperation.ADMIN_READ,
    ),
    "/v1/admin/catalog": Route(
        "_handle_admin_catalog",
        privileged_operation=PrivilegedOperation.ADMIN_READ,
    ),
    "/v1/interactions": Route("_handle_list_interactions"),
    "/v1/sensors": Route("_handle_list_sensors"),
    "/v1/conversation": Route("_handle_conversation_snapshot"),
    "/v1/devices": Route("_handle_list_devices"),
    "/v1/conversation/sessions": Route("_handle_list_sessions"),
}
GET_PATTERN_ROUTES = (
    PatternRoute(re.compile(r"/v1/interactions/([a-f0-9-]{36})"), "_handle_get_interaction"),
    PatternRoute(
        re.compile(r"/v1/devices/([A-Za-z0-9._-]{1,80})/actions"),
        "_handle_claim_action",
    ),
)
POST_ROUTES = {
    "/v1/query": Route("_handle_audio_query"),
    "/v1/query_text": Route("_handle_text_query"),
    "/v1/tts": Route("_handle_tts"),
    "/v1/heartbeat": Route("_handle_heartbeat"),
    "/v1/session/reset": Route("_handle_session_reset"),
    "/v1/device_wifi": Route(
        "_handle_device_wifi",
        privileged_operation=PrivilegedOperation.DEVICE_CONFIG_UPDATE,
    ),
    "/v1/interactions": Route("_handle_interaction"),
    "/v1/admin/config": Route(
        "_handle_admin_config",
        privileged_operation=PrivilegedOperation.ADMIN_CONFIG_UPDATE,
    ),
    "/v1/admin/api-key": Route(
        "_handle_admin_api_key",
        privileged_operation=PrivilegedOperation.API_KEY_UPDATE,
    ),
}
POST_PATTERN_ROUTES = (
    PatternRoute(
        re.compile(r"/v1/devices/([A-Za-z0-9._-]{1,80})/sensor-manifest"),
        "_handle_sensor_manifest",
    ),
    PatternRoute(
        re.compile(r"/v1/conversation/sessions/([a-f0-9-]{36})/archive"),
        "_handle_session_archive",
    ),
    PatternRoute(
        re.compile(r"/v1/conversation/sessions/([a-f0-9-]{36})/rename"),
        "_handle_session_rename",
    ),
    PatternRoute(
        re.compile(r"/v1/devices/([A-Za-z0-9._-]{1,80})/actions/([a-f0-9-]{36})/ack"),
        "_handle_action_ack",
    ),
)


def match_route(method: str, path: str) -> RouteMatch | None:
    """Match an HTTP route without reading or mutating bridge state."""
    protocol_version: Literal[1, 2] = 1
    route_path = path
    if path == "/v2/health":
        protocol_version = 2
        route_path = "/health"
    elif path.startswith("/v2/"):
        protocol_version = 2
        route_path = f"/v1/{path.removeprefix('/v2/')}"
    routes = GET_ROUTES if method == "GET" else POST_ROUTES if method == "POST" else {}
    pattern_routes = (
        GET_PATTERN_ROUTES
        if method == "GET"
        else POST_PATTERN_ROUTES
        if method == "POST"
        else ()
    )
    route = routes.get(route_path)
    if route:
        return RouteMatch(
            route.endpoint,
            auth_required=route.auth_required,
            privileged_operation=route.privileged_operation,
            protocol_version=protocol_version,
        )
    for candidate in pattern_routes:
        matched = candidate.pattern.fullmatch(route_path)
        if matched:
            return RouteMatch(
                candidate.endpoint,
                path_arguments=matched.groups(),
                auth_required=candidate.auth_required,
                privileged_operation=candidate.privileged_operation,
                protocol_version=protocol_version,
            )
    return None


def optional_bool(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("1", "true", "yes", "on"):
            return True
        if normalized in ("0", "false", "no", "off"):
            return False
    raise ValueError("optional config flags must be boolean")


def json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=True).encode("utf-8")


def string_field(payload: dict[str, Any], name: str) -> str:
    """Normalize one transport string field without accepting nested values."""
    value = payload.get(name, "")
    if not isinstance(value, str):
        return ""
    return value.strip()


def required_string_field(
    payload: dict[str, Any],
    name: str,
    *,
    error_message: str,
) -> str:
    value = string_field(payload, name)
    if not value:
        raise ValueError(error_message)
    return value


def make_handler(
    state: Any,
    *,
    event_sink: Any | None = None,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "WearabLLMBridge/0.1"

        def _begin_request(self) -> None:
            self._request_id = new_request_id()
            self._request_started = time.monotonic()
            self._response_logged = False
            self._request_route = urllib.parse.urlsplit(self.path).path
            length_raw = self.headers.get("Content-Length", "")
            self._request_bytes = int(length_raw) if length_raw.isdecimal() else None
            self._request_authenticated = False
            self._request_principal = None
            self._privilege_grant = None

        def _emit_event(self, event: str, *, level: str = "info", **fields: Any) -> None:
            emit_event(event, level=level, sink=event_sink, **fields)

        def _emit_exception(
            self,
            event: str,
            exc: BaseException,
            *,
            level: str = "error",
            **fields: Any,
        ) -> None:
            emit_exception(event, exc, level=level, sink=event_sink, **fields)

        def _log_device_id(self) -> str | None:
            try:
                return self._device_id()
            except ValueError:
                return None

        def _finish_request(
            self,
            status: HTTPStatus,
            *,
            response_bytes: int,
            error_code: str | None = None,
        ) -> None:
            if getattr(self, "_response_logged", False):
                return
            self._response_logged = True
            started = getattr(self, "_request_started", time.monotonic())
            self._emit_event(
                "http.request_complete",
                level="warning" if int(status) >= 400 else "info",
                request_id=getattr(self, "_request_id", new_request_id()),
                method=self.command,
                route=getattr(self, "_request_route", urllib.parse.urlsplit(self.path).path),
                status=int(status),
                duration_ms=(time.monotonic() - started) * 1000.0,
                request_bytes=getattr(self, "_request_bytes", None),
                response_bytes=response_bytes,
                device_id=self._log_device_id(),
                error_code=error_code,
            )

        def _audit(
            self,
            operation: str,
            outcome: str,
            *,
            status: HTTPStatus,
            error_code: str | None = None,
            action_status: str | None = None,
        ) -> None:
            self._emit_event(
                "audit.privileged_operation",
                level="warning" if int(status) >= 400 else "info",
                request_id=getattr(self, "_request_id", new_request_id()),
                operation=operation,
                outcome=outcome,
                status=int(status),
                device_id=self._log_device_id(),
                error_code=error_code,
                action_status=action_status,
            )

        def do_GET(self) -> None:
            self._begin_request()
            self._dispatch_route("GET")

        def do_OPTIONS(self) -> None:
            self._begin_request()
            self.send_response(HTTPStatus.NO_CONTENT)
            self._send_cors_headers()
            self.send_header(REQUEST_ID_HEADER, self._request_id)
            self._finish_request(HTTPStatus.NO_CONTENT, response_bytes=0)
            self.end_headers()

        def do_POST(self) -> None:
            self._begin_request()
            self._dispatch_route("POST")

        def _dispatch_route(self, method: str) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            matched = match_route(method, parsed.path)
            self._protocol_version = (
                matched.protocol_version
                if matched is not None
                else 2 if parsed.path == "/v2" or parsed.path.startswith("/v2/") else 1
            )
            authorized = self._is_authorized()
            self._request_authenticated = authorized
            if matched is None:
                if method == "POST" and not authorized:
                    self._send_error_json(
                        HTTPStatus.UNAUTHORIZED,
                        "Invalid or missing device token",
                    )
                    return
                self._send_error_json(HTTPStatus.NOT_FOUND, "Unknown endpoint")
                return
            if matched.auth_required and not authorized:
                self._send_error_json(
                    HTTPStatus.UNAUTHORIZED,
                    "Invalid or missing device token",
                )
                return
            if matched.privileged_operation is not None:
                try:
                    self._privilege_grant = state.authorize_admin_operation(
                        self._principal(),
                        matched.privileged_operation,
                    )
                except PermissionError as exc:
                    self._audit(
                        matched.privileged_operation.value,
                        "denied",
                        status=HTTPStatus.FORBIDDEN,
                        error_code="policy_denied",
                    )
                    self._send_error_json(HTTPStatus.FORBIDDEN, str(exc))
                    return
                except ValueError as exc:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                    return
            endpoint = getattr(self, matched.endpoint)
            endpoint(parsed, *matched.path_arguments)

        def _principal(self) -> Any:
            principal = getattr(self, "_request_principal", None)
            if principal is None:
                principal = state.resolve_principal(
                    self._device_id(),
                    authenticated=bool(getattr(self, "_request_authenticated", False)),
                )
                self._request_principal = principal
            return principal

        def _handle_health(self, _parsed: urllib.parse.SplitResult) -> None:
            self._send_json(
                {
                    "ok": True,
                    "service": "wearabllm-bridge",
                    "config": state.runtime_config(),
                }
            )

        def _handle_get_admin_config(self, _parsed: urllib.parse.SplitResult) -> None:
            self._send_json(
                {"ok": True, "config": state.public_agent_config()}
            )

        def _handle_admin_catalog(self, _parsed: urllib.parse.SplitResult) -> None:
            try:
                self._send_json({"ok": True, "catalog": state.openai_catalog()})
            except Exception as exc:
                self._emit_exception("http.admin_catalog_failed", exc)
                self._send_error_json(
                    HTTPStatus.BAD_GATEWAY,
                    "OpenAI catalog request failed",
                )

        def _handle_list_interactions(self, parsed: urllib.parse.SplitResult) -> None:
            params = urllib.parse.parse_qs(parsed.query)
            target = (params.get("target_device_id") or [""])[0].strip()
            limit_raw = (params.get("limit") or ["100"])[0]
            if not limit_raw.isdecimal():
                self._send_error_json(HTTPStatus.BAD_REQUEST, "Invalid limit")
                return
            try:
                actions = state.list_actions(
                    target_device_id=target,
                    limit=int(limit_raw),
                )
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json({"ok": True, "actions": actions})

        def _handle_list_sensors(self, parsed: urllib.parse.SplitResult) -> None:
            params = urllib.parse.parse_qs(parsed.query)
            device_id = (params.get("device_id") or [""])[0].strip()
            try:
                manifests = state.sensor_catalog(device_id)
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json({"ok": True, "devices": manifests})

        def _handle_get_interaction(
            self,
            _parsed: urllib.parse.SplitResult,
            action_id: str,
        ) -> None:
            try:
                action = state.get_action(action_id)
            except LookupError as exc:
                self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
                return
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json({"ok": True, "action": action})

        def _handle_claim_action(
            self,
            _parsed: urllib.parse.SplitResult,
            target_device_id: str,
        ) -> None:
            try:
                grant = state.authorize_target_access(
                    self._principal(),
                    target_device_id,
                )
                action = state.claim_action(
                    requesting_device_id=self._device_id(),
                    target_device_id=target_device_id,
                    grant=grant,
                )
            except PermissionError as exc:
                self._send_error_json(HTTPStatus.FORBIDDEN, str(exc))
                return
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json({"ok": True, "action": action})

        def _handle_list_devices(self, _parsed: urllib.parse.SplitResult) -> None:
            snapshot = state.conversation_snapshot()
            self._send_json({"ok": True, "devices": snapshot["devices"]})

        def _handle_list_sessions(self, _parsed: urllib.parse.SplitResult) -> None:
            snapshot = state.conversation_snapshot()
            self._send_json(
                {
                    "ok": True,
                    "active_session_id": snapshot.get("active_session_id"),
                    "sessions": snapshot.get("sessions", []),
                }
            )

        def _handle_conversation_snapshot(self, parsed: urllib.parse.SplitResult) -> None:
            params = urllib.parse.parse_qs(parsed.query)
            device_id = (params.get("device_id") or [""])[0].strip() or None
            session_id = (params.get("session_id") or [""])[0].strip() or None
            limit_raw = (params.get("limit") or ["200"])[0]
            if not limit_raw.isdecimal():
                self._send_error_json(HTTPStatus.BAD_REQUEST, "Invalid limit")
                return
            try:
                snapshot = state.conversation_snapshot(
                    device_id=device_id,
                    session_id=session_id,
                    limit=int(limit_raw),
                )
            except Exception as exc:
                self._emit_exception("http.conversation_snapshot_failed", exc)
                self._send_error_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "Conversation snapshot failed",
                )
                return
            self._send_json(snapshot)

        def _handle_heartbeat(self, _parsed: urllib.parse.SplitResult) -> None:
            try:
                device_id = self._device_id()
                state.touch_device(device_id)
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json({"ok": True, "device_id": device_id})

        def _handle_sensor_manifest(
            self,
            _parsed: urllib.parse.SplitResult,
            target_device_id: str,
        ) -> None:
            try:
                state.authorize_target_access(self._principal(), target_device_id)
                request = self._read_json_request(max_bytes=16_384)
                if request is None:
                    return
                manifest = state.register_sensor_manifest(target_device_id, request)
            except PermissionError as exc:
                self._send_error_json(HTTPStatus.FORBIDDEN, str(exc))
                return
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json({"ok": True, "manifest": manifest})

        def _handle_session_reset(self, _parsed: urllib.parse.SplitResult) -> None:
            try:
                payload = state.start_new_conversation()
            except Exception as exc:
                self._emit_exception("http.conversation_reset_failed", exc)
                self._send_json(
                    {"ok": False, "error": "Conversation reset failed"},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return
            self._send_json(payload)

        def _handle_session_archive(
            self,
            _parsed: urllib.parse.SplitResult,
            session_id: str,
        ) -> None:
            try:
                payload = state.archive_conversation(session_id)
            except LookupError as exc:
                self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
                return
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:
                self._emit_exception("http.conversation_archive_failed", exc)
                self._send_error_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "Conversation archive failed",
                )
                return
            self._send_json(payload)

        def _handle_session_rename(
            self,
            _parsed: urllib.parse.SplitResult,
            session_id: str,
        ) -> None:
            request = self._read_json_request(max_bytes=2_048)
            if request is None:
                return
            try:
                payload = state.rename_conversation(
                    session_id,
                    str(request.get("title", "")),
                )
            except LookupError as exc:
                self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
                return
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:
                self._emit_exception("http.conversation_rename_failed", exc)
                self._send_error_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "Conversation rename failed",
                )
                return
            self._send_json(payload)

        def _is_authorized(self) -> bool:
            expected = str(getattr(state.args, "device_token", ""))
            if not expected:
                return True
            supplied = self.headers.get("X-WearabLLM-Device-Token", "")
            return bool(supplied) and hmac.compare_digest(supplied, expected)

        def _device_id(self) -> str:
            device_id = self.headers.get("X-WearabLLM-Device-Id", "").strip()
            if not device_id:
                device_id = str(getattr(state.args, "device_id", "wearabllm-unknown"))
            return validate_device_id(device_id)

        def _handle_audio_query(self, _parsed: urllib.parse.SplitResult) -> None:
            length = self._content_length()
            if length <= 0:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "Missing audio body")
                return
            if length > state.args.max_audio_bytes:
                self._send_error_json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    f"Audio body too large: {length} bytes > {state.args.max_audio_bytes} byte limit",
                )
                return

            wav_bytes = self.rfile.read(length)
            try:
                device_id = self._device_id()
                audio_result = state.answer_audio_query(wav_bytes, device_id=device_id)
                result = audio_result.query
                payload = result.to_legacy_dict()
                saved_path = audio_result.saved_wav
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - runtime path
                self._emit_exception("http.audio_query_failed", exc)
                self._send_json(
                    {"command": "RF", "reply": "Bridge error: request failed", "transcript": ""},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return

            self._log_response(payload)
            if saved_path:
                self._emit_event("bridge.capture_saved", saved_capture=True, request_bytes=len(wav_bytes))
            self._send_json(payload)

        def _handle_text_query(self, _parsed: urllib.parse.SplitResult) -> None:
            request = self._read_json_request(
                max_bytes=None,
                missing_message="Missing JSON body",
            )
            if request is None:
                return

            try:
                transcript = required_string_field(
                    request,
                    "transcript",
                    error_message="Missing transcript",
                )
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return

            try:
                response_device = string_field(request, "response_device_id") or None
                if response_device:
                    response_device = validate_device_id(response_device)
                device_id = self._device_id()
                result = state.answer_query(
                    QueryInput(
                        transcript=transcript,
                        device_id=device_id,
                        response_device_id=response_device,
                    )
                )
                payload = result.to_legacy_dict()
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - runtime path
                self._emit_exception("http.text_query_failed", exc)
                self._send_json(
                    {
                        "command": "RF",
                        "reply": "Bridge error: request failed",
                        "transcript": transcript,
                    },
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return

            self._log_response(payload)
            self._send_json(payload)

        def _handle_interaction(self, _parsed: urllib.parse.SplitResult) -> None:
            request = self._read_json_request(max_bytes=16_384)
            if request is None:
                return
            transcript = string_field(request, "transcript")
            origin = string_field(request, "origin_device_id")
            target = string_field(request, "target_device_id")
            if not origin:
                try:
                    origin = self._device_id()
                except ValueError as exc:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                    return
            if not transcript or not target:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "transcript and target_device_id are required")
                return
            try:
                result = state.create_interaction_result(
                    InteractionInput(
                        transcript=transcript,
                        origin_device_id=origin,
                        target_device_id=target,
                        idempotency_key=str(request.get("idempotency_key", "")),
                        response_device_id=(
                            string_field(request, "response_device_id") or None
                        ),
                    )
                )
                payload = result.to_legacy_dict()
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - runtime path
                self._emit_exception("http.interaction_creation_failed", exc)
                self._send_error_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "Interaction creation failed",
                )
                return
            self._log_response(payload)
            self._send_json({"ok": True, **payload})

        def _handle_action_ack(
            self,
            _parsed: urllib.parse.SplitResult,
            target_device_id: str,
            action_id: str,
        ) -> None:
            try:
                requesting_device_id = self._device_id()
                grant = state.authorize_target_access(
                    self._principal(),
                    target_device_id,
                    operation=PrivilegedOperation.ACTION_ACKNOWLEDGE,
                )
            except PermissionError as exc:
                self._audit(
                    "action_acknowledge",
                    "denied",
                    status=HTTPStatus.FORBIDDEN,
                    error_code="target_mismatch",
                )
                self._send_error_json(HTTPStatus.FORBIDDEN, str(exc))
                return
            except ValueError as exc:
                self._audit(
                    "action_acknowledge",
                    "rejected",
                    status=HTTPStatus.BAD_REQUEST,
                    error_code="invalid_device_id",
                )
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            request = self._read_json_request(max_bytes=2_048)
            if request is None:
                self._audit(
                    "action_acknowledge",
                    "rejected",
                    status=HTTPStatus.BAD_REQUEST,
                    error_code="invalid_body",
                )
                return
            try:
                action = state.acknowledge_action(
                    requesting_device_id=requesting_device_id,
                    target_device_id=target_device_id,
                    action_id=action_id,
                    status=str(request.get("status", "")),
                    error=str(request.get("error", "")),
                    result=(
                        request.get("result")
                        if isinstance(request.get("result"), dict)
                        else None
                    ),
                    grant=grant,
                )
            except PermissionError as exc:
                self._audit(
                    "action_acknowledge",
                    "denied",
                    status=HTTPStatus.FORBIDDEN,
                    error_code="target_mismatch",
                )
                self._send_error_json(HTTPStatus.FORBIDDEN, str(exc))
                return
            except LookupError:
                self._audit(
                    "action_acknowledge",
                    "rejected",
                    status=HTTPStatus.NOT_FOUND,
                    error_code="action_not_found",
                )
                self._send_error_json(HTTPStatus.NOT_FOUND, "Action not found")
                return
            except ValueError as exc:
                self._audit(
                    "action_acknowledge",
                    "rejected",
                    status=HTTPStatus.BAD_REQUEST,
                    error_code="invalid_acknowledgement",
                )
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._audit(
                "action_acknowledge",
                "accepted",
                status=HTTPStatus.OK,
                action_status=str(action.get("status", "")),
            )
            self._send_json({"ok": True, "action": action})

        def _handle_admin_config(self, _parsed: urllib.parse.SplitResult) -> None:
            request = self._read_json_request(max_bytes=64_000)
            if request is None:
                self._audit(
                    "admin_config_update",
                    "rejected",
                    status=HTTPStatus.BAD_REQUEST,
                    error_code="invalid_body",
                )
                return
            try:
                config = state.update_agent_config(
                    request,
                    grant=self._privilege_grant,
                )
            except ValueError as exc:
                self._audit(
                    "admin_config_update",
                    "rejected",
                    status=HTTPStatus.BAD_REQUEST,
                    error_code="invalid_config",
                )
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - persistence/runtime path
                self._emit_exception("http.admin_config_update_failed", exc)
                self._audit(
                    "admin_config_update",
                    "failed",
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                    error_code="persistence_failed",
                )
                self._send_error_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "Agent config update failed",
                )
                return
            self._audit("admin_config_update", "accepted", status=HTTPStatus.OK)
            self._send_json({"ok": True, "config": config.public_dict()})

        def _handle_admin_api_key(self, _parsed: urllib.parse.SplitResult) -> None:
            request = self._read_json_request(max_bytes=2_048)
            if request is None:
                self._audit(
                    "api_key_update",
                    "rejected",
                    status=HTTPStatus.BAD_REQUEST,
                    error_code="invalid_body",
                )
                return
            try:
                payload = state.replace_openai_api_key(
                    str(request.get("api_key", "")),
                    grant=self._privilege_grant,
                )
            except ValueError as exc:
                self._audit(
                    "api_key_update",
                    "rejected",
                    status=HTTPStatus.BAD_REQUEST,
                    error_code="invalid_api_key",
                )
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:
                self._emit_exception("http.api_key_update_failed", exc)
                self._audit(
                    "api_key_update",
                    "failed",
                    status=HTTPStatus.BAD_GATEWAY,
                    error_code="provider_validation_failed",
                )
                self._send_error_json(
                    HTTPStatus.BAD_GATEWAY,
                    "OpenAI API key update failed",
                )
                return
            self._audit("api_key_update", "accepted", status=HTTPStatus.OK)
            self._send_json(payload)

        def _content_length(self) -> int:
            raw = self.headers.get("Content-Length", "0").strip()
            return int(raw) if raw.isdecimal() else 0

        def _read_json_request(
            self,
            *,
            max_bytes: int | None,
            missing_message: str = "Invalid JSON body",
        ) -> dict[str, Any] | None:
            length = self._content_length()
            if length <= 0:
                self._send_error_json(HTTPStatus.BAD_REQUEST, missing_message)
                return None
            if max_bytes is not None and length > max_bytes:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "Invalid JSON body")
                return None
            try:
                request = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_error_json(HTTPStatus.BAD_REQUEST, "Invalid JSON body")
                return None
            if not isinstance(request, dict):
                self._send_error_json(HTTPStatus.BAD_REQUEST, "JSON body must be an object")
                return None
            return request

        def _handle_tts(self, _parsed: urllib.parse.SplitResult) -> None:
            request = self._read_json_request(
                max_bytes=None,
                missing_message="Missing JSON body",
            )
            if request is None:
                return

            try:
                text = required_string_field(
                    request,
                    "text",
                    error_message="Missing text",
                )
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return

            try:
                wav_bytes = state.synthesize_tts_wav(text)
            except Exception as exc:  # pragma: no cover - runtime path
                self._emit_exception("http.tts_failed", exc)
                self._send_json(
                    {"error": "Bridge TTS error: request failed"},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return

            if bool(getattr(state.args, "debug_content_logs", False)):
                emit_debug_content("debug.tts_content", tts_text=text, sink=event_sink)
            self._send_bytes(wav_bytes, content_type="audio/wav")

        def _handle_device_wifi(self, _parsed: urllib.parse.SplitResult) -> None:
            request = self._read_json_request(
                max_bytes=None,
                missing_message="Missing JSON body",
            )
            if request is None:
                self._audit(
                    "device_config_update",
                    "rejected",
                    status=HTTPStatus.BAD_REQUEST,
                    error_code="invalid_body",
                )
                return

            try:
                preview = optional_bool(
                    request.get("preview", request.get("dry_run"))
                ) is True
                payload = state.configure_device_wifi_request(
                    request,
                    grant=self._privilege_grant,
                    preview=preview,
                )
            except PermissionError as exc:
                self._audit(
                    "device_config_update",
                    "denied",
                    status=HTTPStatus.FORBIDDEN,
                    error_code="device_config_disabled",
                )
                self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.FORBIDDEN)
                return
            except ValueError as exc:
                self._audit(
                    "device_config_update",
                    "rejected",
                    status=HTTPStatus.BAD_REQUEST,
                    error_code="invalid_device_config",
                )
                self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            except Exception as exc:  # pragma: no cover - runtime path
                self._emit_exception("http.device_config_update_failed", exc)
                self._audit(
                    "device_config_update",
                    "failed",
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                    error_code="device_config_failed",
                )
                self._send_json(
                    {"ok": False, "error": "Device config update failed"},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return

            self._audit("device_config_update", "accepted", status=HTTPStatus.OK)
            self._send_json(payload)

        def _log_response(self, payload: dict[str, Any]) -> None:
            if not bool(getattr(state.args, "debug_content_logs", False)):
                return
            emit_debug_content(
                "debug.query_content",
                transcript=str(payload.get("transcript", "")),
                reply=str(payload.get("reply", "")),
                sink=event_sink,
            )

        def log_message(self, fmt: str, *args: Any) -> None:
            # ``http.request_complete`` is the one canonical access event. The
            # BaseHTTPRequestHandler message can include unsanitized paths.
            return None

        def _send_cors_headers(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-WearabLLM-Device-Token, X-WearabLLM-Device-Id")
            self.send_header("Access-Control-Expose-Headers", REQUEST_ID_HEADER)

        def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            response_payload = payload
            if getattr(self, "_protocol_version", 1) == 2:
                if int(status) >= 400:
                    raw_message = payload.get("error", payload.get("message", payload.get("reply", "")))
                    message = raw_message if isinstance(raw_message, str) and raw_message else status.phrase
                    response_payload = v2_error_envelope(
                        status,
                        message,
                        request_id=getattr(self, "_request_id", new_request_id()),
                    )
                else:
                    response_payload = v2_success_envelope(payload)
            body = json_bytes(response_payload)
            self.send_response(status)
            self._send_cors_headers()
            self.send_header(REQUEST_ID_HEADER, getattr(self, "_request_id", new_request_id()))
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self._finish_request(
                status,
                response_bytes=len(body),
                error_code=f"http_{int(status)}" if int(status) >= 400 else None,
            )
            self.wfile.write(body)

        def _send_error_json(self, status: HTTPStatus, message: str) -> None:
            self._send_json({"error": message}, status=status)

        def _send_bytes(
            self,
            body: bytes,
            *,
            content_type: str,
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            self.send_response(status)
            self._send_cors_headers()
            self.send_header(REQUEST_ID_HEADER, getattr(self, "_request_id", new_request_id()))
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self._finish_request(
                status,
                response_bytes=len(body),
                error_code=f"http_{int(status)}" if int(status) >= 400 else None,
            )
            self.wfile.write(body)

    return Handler
