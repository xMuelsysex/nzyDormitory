#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urljoin, urlparse
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.analyze_wechat_har import TARGET_HOST, TARGET_PATH_PREFIX, load_har


SESSION_COOKIE_NAME = "ASP.NET_SessionId"
SELFHELP_QUERY_ENDPOINT = "/work/njucm/card.ashx"
SELFHELP_QUERY_ACTION = "selfhelp_elect_query"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"


@dataclass(frozen=True)
class WechatHarImportData:
    cookie_header: str | None
    elect_fields: dict[str, str] | None

    @property
    def cookie_found(self) -> bool:
        return self.cookie_header is not None

    @property
    def room_selection_payload(self) -> dict[str, str] | None:
        if not self.elect_fields:
            return None
        zone = self.elect_fields.get("zone", "").strip().upper()
        house = self.elect_fields.get("house", "").strip()
        room = self.elect_fields.get("room", "").strip()
        if not zone or not house or not room:
            return None
        return {"building": f"{zone}{house}", "room": room}


@dataclass(frozen=True)
class ImportSummary:
    session_imported: bool
    room_imported: bool


Urlopen = Callable[..., Any]


def extract_import_data(payload: dict[str, Any]) -> WechatHarImportData:
    entries = payload.get("log", {}).get("entries", [])
    cookie_header: str | None = None
    elect_fields: dict[str, str] | None = None
    for entry in entries:
        request = entry.get("request", {})
        response = entry.get("response", {})
        if not _is_target_request(request):
            continue
        candidate_cookie = _extract_session_cookie(request, response)
        if candidate_cookie is not None:
            cookie_header = candidate_cookie
        if elect_fields is None and _is_selfhelp_query_request(request):
            candidate_fields = _extract_elect_fields(request)
            if candidate_fields is not None:
                elect_fields = candidate_fields
    return WechatHarImportData(cookie_header=cookie_header, elect_fields=elect_fields)


def import_to_app(
    import_data: WechatHarImportData,
    *,
    base_url: str = DEFAULT_BASE_URL,
    urlopen_func: Urlopen = urlopen,
    timeout: float = 10.0,
) -> ImportSummary:
    if import_data.cookie_header is None:
        raise ValueError(f"No {SESSION_COOKIE_NAME} cookie was found in the HAR.")
    _post_json(
        base_url,
        "/wechat/session/import",
        {"cookieHeader": import_data.cookie_header},
        urlopen_func=urlopen_func,
        timeout=timeout,
    )
    room_payload = import_data.room_selection_payload
    room_imported = False
    if room_payload is not None:
        _post_json(
            base_url,
            "/api/room-selection",
            room_payload,
            urlopen_func=urlopen_func,
            timeout=timeout,
        )
        room_imported = True
    return ImportSummary(session_imported=True, room_imported=room_imported)


def render_report(import_data: WechatHarImportData, summary: ImportSummary | None = None) -> str:
    lines = ["Enterprise WeChat HAR import"]
    lines.append(
        f"- Cookie: {'found `ASP.NET_SessionId` (value redacted)' if import_data.cookie_found else 'not found'}"
    )
    room_payload = import_data.room_selection_payload
    if room_payload is None:
        lines.append("- Room selection: not found")
    else:
        lines.append(f"- Room selection: found {room_payload['building']} {room_payload['room']}")
    if summary is not None:
        lines.append(f"- Session import: {'ok' if summary.session_imported else 'skipped'}")
        lines.append(f"- Room import: {'ok' if summary.room_imported else 'skipped'}")
    return "\n".join(lines)


def _post_json(
    base_url: str,
    path: str,
    payload: dict[str, object],
    *,
    urlopen_func: Urlopen,
    timeout: float,
) -> dict[str, Any]:
    url = urljoin(_normalized_base_url(base_url), path.lstrip("/"))
    data = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen_func(request, timeout=timeout) as response:
            raw_body = response.read()
            status = int(getattr(response, "status", getattr(response, "code", 200)))
    except HTTPError as exc:
        message = _response_error_message(exc.read())
        raise RuntimeError(f"POST {path} failed with HTTP {exc.code}: {message}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach local app at {base_url}: {exc.reason}") from exc

    if status < 200 or status >= 300:
        raise RuntimeError(f"POST {path} failed with HTTP {status}: {_response_error_message(raw_body)}")
    if not raw_body:
        return {}
    try:
        parsed = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"POST {path} returned invalid JSON.") from exc
    return parsed if isinstance(parsed, dict) else {}


def _normalized_base_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/"


def _response_error_message(raw_body: bytes) -> str:
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "unreadable response"
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
    return "request failed"


def _is_target_request(request: dict[str, Any]) -> bool:
    parsed = urlparse(str(request.get("url", "")))
    return parsed.hostname == TARGET_HOST and parsed.path.startswith(TARGET_PATH_PREFIX)


def _is_selfhelp_query_request(request: dict[str, Any]) -> bool:
    parsed = urlparse(str(request.get("url", "")))
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    return (
        str(request.get("method", "")).upper() == "POST"
        and parsed.hostname == TARGET_HOST
        and parsed.path == SELFHELP_QUERY_ENDPOINT
        and query.get("action") == SELFHELP_QUERY_ACTION
    )


def _extract_elect_fields(request: dict[str, Any]) -> dict[str, str] | None:
    post_data = request.get("postData", {})
    fields: dict[str, str] = {}
    for param in post_data.get("params", []) or []:
        name = str(param.get("name", "")).strip()
        if name:
            fields[name] = str(param.get("value", ""))
    text = str(post_data.get("text", "") or "")
    if text:
        fields.update({key: value for key, value in parse_qsl(text, keep_blank_values=True)})
    required = {"zone", "house", "room", "electtype"}
    if not required.issubset(fields):
        return None
    return {key: fields[key] for key in ("zone", "house", "room", "electtype")}


def _extract_session_cookie(request: dict[str, Any], response: dict[str, Any]) -> str | None:
    request_headers = _headers_by_name(request.get("headers", []))
    response_headers = _headers_by_name(response.get("headers", []))
    return (
        _session_cookie_from_cookie_header(request_headers.get("cookie", ""))
        or _session_cookie_from_har_cookies(request.get("cookies", []))
        or _session_cookie_from_set_cookie_header(response_headers.get("set-cookie", ""))
        or _session_cookie_from_har_cookies(response.get("cookies", []))
    )


def _headers_by_name(headers: list[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for header in headers:
        name = str(header.get("name", "")).lower()
        value = str(header.get("value", ""))
        if not name:
            continue
        if name in result:
            result[name] = f"{result[name]}; {value}"
        else:
            result[name] = value
    return result


def _session_cookie_from_cookie_header(cookie_header: str) -> str | None:
    for part in cookie_header.split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        if name.strip().lower() == SESSION_COOKIE_NAME.lower() and value:
            return f"{name.strip()}={value.strip()}"
    return None


def _session_cookie_from_set_cookie_header(set_cookie_header: str) -> str | None:
    for part in re.split(r",\s*(?=[^;,=\s]+=)", set_cookie_header):
        first_pair = part.split(";", 1)[0]
        cookie = _session_cookie_from_cookie_header(first_pair)
        if cookie is not None:
            return cookie
    return None


def _session_cookie_from_har_cookies(cookies: list[dict[str, Any]]) -> str | None:
    for cookie in cookies:
        name = str(cookie.get("name", "")).strip()
        value = str(cookie.get("value", "")).strip()
        if name.lower() == SESSION_COOKIE_NAME.lower() and value:
            return f"{name}={value}"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import Enterprise WeChat auth from an exported HAR.")
    parser.add_argument("har_file", type=Path, help="Path to the exported HAR file.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"Local app base URL. Default: {DEFAULT_BASE_URL}")
    parser.add_argument("--dry-run", action="store_true", help="Parse the HAR and print the redacted report without importing.")
    args = parser.parse_args(argv)

    import_data = extract_import_data(load_har(args.har_file))
    if args.dry_run:
        print(render_report(import_data))
        return 0 if import_data.cookie_found else 2
    try:
        summary = import_to_app(import_data, base_url=args.base_url)
    except (RuntimeError, ValueError) as exc:
        print(render_report(import_data), file=sys.stderr)
        print(f"Import failed: {exc}", file=sys.stderr)
        return 1
    print(render_report(import_data, summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
