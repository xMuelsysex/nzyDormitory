from __future__ import annotations

import json
import logging
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from typing import Any

from backend.app.shared.errors import AppError

logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8"))


def send_json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def send_error(handler: BaseHTTPRequestHandler, error: Exception) -> None:
    if isinstance(error, AppError):
        send_json(handler, error.status, {"error": {"code": error.code, "message": error.message}})
        return
    logger.exception("unexpected_error")
    send_json(handler, 500, {"error": {"code": "UNEXPECTED_ERROR", "message": "Unexpected server error"}})
