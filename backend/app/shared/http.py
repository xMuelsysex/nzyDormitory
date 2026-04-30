from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qs

from backend.app.shared.errors import AppError, ValidationError

logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    try:
        length = int(handler.headers.get("Content-Length", "0") or 0)
    except ValueError as exc:
        raise ValidationError("Content-Length must be numeric.") from exc
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("Request body must be valid JSON.") from exc
    if not isinstance(data, dict):
        raise ValidationError("Request body must be a JSON object.")
    return data


def read_form_body(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    length = int(handler.headers.get("Content-Length", "0") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8", errors="ignore")
    return {key: values[-1] for key, values in parse_qs(raw, keep_blank_values=True).items()}


def send_json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def send_html(handler: BaseHTTPRequestHandler, status: int, html_body: str) -> None:
    body = html_body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def send_redirect(handler: BaseHTTPRequestHandler, location: str) -> None:
    handler.send_response(303)
    handler.send_header("Location", location)
    handler.send_header("Content-Length", "0")
    handler.end_headers()


def send_bytes(handler: BaseHTTPRequestHandler, status: int, body: bytes, content_type: str) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def send_error(handler: BaseHTTPRequestHandler, error: Exception) -> None:
    if isinstance(error, AppError):
        send_json(handler, error.status, {"error": {"code": error.code, "message": error.message}})
        return
    logger.exception("unexpected_error")
    send_json(handler, 500, {"error": {"code": "UNEXPECTED_ERROR", "message": "Unexpected server error"}})
