"""Stable response-envelope helpers for the WearabLLM ``/v2`` protocol."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, Mapping


class V2EnvelopeError(ValueError):
    """Raised when an untrusted v2 response does not satisfy the envelope."""


def success_envelope(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Wrap endpoint data in the normalized v2 success contract."""
    data = dict(payload)
    data.pop("ok", None)
    return {"ok": True, "data": data}


def error_code(status: HTTPStatus) -> str:
    """Return the stable public error code for an HTTP status."""
    return {
        HTTPStatus.BAD_REQUEST: "bad_request",
        HTTPStatus.UNAUTHORIZED: "unauthorized",
        HTTPStatus.FORBIDDEN: "forbidden",
        HTTPStatus.NOT_FOUND: "not_found",
        HTTPStatus.REQUEST_ENTITY_TOO_LARGE: "payload_too_large",
        HTTPStatus.BAD_GATEWAY: "upstream_failure",
        HTTPStatus.INTERNAL_SERVER_ERROR: "internal_error",
    }.get(status, f"http_{int(status)}")


def error_envelope(
    status: HTTPStatus,
    message: str,
    *,
    request_id: str,
) -> dict[str, Any]:
    """Build the typed, content-bounded v2 error contract."""
    return {
        "ok": False,
        "error": {
            "code": error_code(status),
            "message": message,
            "request_id": request_id,
        },
    }


def unwrap_envelope(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return v2 success data for trusted bridge-side tooling."""
    if payload.get("ok") is not True:
        error = payload.get("error")
        if isinstance(error, Mapping):
            message = error.get("message")
            if isinstance(message, str) and message:
                raise V2EnvelopeError(message)
        raise V2EnvelopeError("v2 response did not report success")
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise V2EnvelopeError("v2 success data must be an object")
    return dict(data)
