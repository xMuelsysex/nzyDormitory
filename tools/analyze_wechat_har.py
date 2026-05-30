#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


TARGET_HOST = "wx.njucm.edu.cn"
TARGET_PATH_PREFIX = "/work/njucm"
SENSITIVE_QUERY_KEYS = {"code", "state", "token", "access_token", "openid", "userid", "user_id", "sid", "room", "name", "wid"}
TRACKED_QUERY_KEYS = ("code", "state", "wid")
WECHAT_UA_MARKERS = ("wxwork", "wxworklocal", "MicroMessenger", "wecom")
ELECTRICITY_KEYWORDS = ("电费", "电量", "余额", "剩余电费", "宿舍电费", "当前电量", "剩余电量")
BALANCE_PATTERN = re.compile(r"(?:电费|电量|余额|剩余)\D{0,24}-?\d+(?:\.\d+)?\s*(?:元|度|kwh)?", re.IGNORECASE)


@dataclass
class HarFinding:
    method: str
    url: str
    status: int | None
    query_keys: list[str] = field(default_factory=list)
    cookie_names: list[str] = field(default_factory=list)
    set_cookie_names: list[str] = field(default_factory=list)
    user_agent_markers: list[str] = field(default_factory=list)
    candidate_endpoint: bool = False
    electricity_hint: bool = False


@dataclass
class HarReport:
    findings: list[HarFinding]

    @property
    def matched_count(self) -> int:
        return len(self.findings)

    @property
    def cookie_names(self) -> list[str]:
        return sorted({name for finding in self.findings for name in finding.cookie_names})

    @property
    def set_cookie_names(self) -> list[str]:
        return sorted({name for finding in self.findings for name in finding.set_cookie_names})

    @property
    def user_agent_marker_names(self) -> list[str]:
        return sorted({marker for finding in self.findings for marker in finding.user_agent_markers})

    @property
    def user_agent_markers(self) -> dict[str, bool]:
        present = {marker.lower() for marker in self.user_agent_marker_names}
        return {marker: marker.lower() in present for marker in WECHAT_UA_MARKERS}

    @property
    def candidate_endpoints(self) -> list[str]:
        return sorted({finding.url for finding in self.findings if finding.candidate_endpoint})

    @property
    def electricity_hints(self) -> list[str]:
        return sorted({finding.url for finding in self.findings if finding.electricity_hint})

    @property
    def matched_requests(self) -> list[str]:
        return sorted({finding.url for finding in self.findings})

    @property
    def oauth_query_keys(self) -> dict[str, bool]:
        present = {key for finding in self.findings for key in finding.query_keys}
        return {key: key in present for key in TRACKED_QUERY_KEYS}


def load_har(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def analyze_har(payload: dict[str, Any]) -> HarReport:
    return analyze_har_payload(payload)


def render_markdown_report(report: HarReport) -> str:
    return render_markdown(report)


def analyze_har_file(path: Path) -> HarReport:
    return analyze_har_payload(load_har(path))


def analyze_har_payload(payload: dict[str, Any]) -> HarReport:
    entries = payload.get("log", {}).get("entries", [])
    findings = []
    for entry in entries:
        finding = analyze_entry(entry)
        if finding is not None:
            findings.append(finding)
    return HarReport(findings=findings)


def analyze_entry(entry: dict[str, Any]) -> HarFinding | None:
    request = entry.get("request", {})
    url = str(request.get("url", ""))
    parsed = urlparse(url)
    if parsed.hostname != TARGET_HOST or not parsed.path.startswith(TARGET_PATH_PREFIX):
        return None
    response = entry.get("response", {})
    content_text = str(response.get("content", {}).get("text", "") or "")
    headers = _headers_by_name(request.get("headers", []))
    response_headers = _headers_by_name(response.get("headers", []))
    sanitized_url = sanitize_url(url)
    request_cookie_names = _cookie_names(headers.get("cookie", ""))
    request_cookie_names.extend(_har_cookie_names(request.get("cookies", [])))
    response_cookie_names = _set_cookie_names(response_headers.get("set-cookie", ""))
    response_cookie_names.extend(_har_cookie_names(response.get("cookies", [])))
    return HarFinding(
        method=str(request.get("method", "GET")),
        url=sanitized_url,
        status=_safe_int(response.get("status")),
        query_keys=sorted({key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}),
        cookie_names=sorted(set(request_cookie_names)),
        set_cookie_names=sorted(set(response_cookie_names)),
        user_agent_markers=_ua_markers(headers.get("user-agent", "")),
        candidate_endpoint=_is_candidate_endpoint(parsed.path),
        electricity_hint=_has_electricity_hint(content_text),
    )


def render_markdown(report: HarReport) -> str:
    lines = [
        "# Enterprise WeChat HAR Diagnostic",
        "",
        f"- Matched wx.njucm.edu.cn/work/njucm requests: {report.matched_count}",
        f"- Cookie names: {_list_or_none(report.cookie_names)}",
        f"- Set-Cookie names: {_list_or_none(report.set_cookie_names)}",
        f"- Enterprise WeChat UA markers: {_list_or_none(report.user_agent_marker_names)}",
        f"- OAuth-like query keys: {_query_key_summary(report.oauth_query_keys)}",
        f"- Candidate endpoints: {_list_or_none(report.candidate_endpoints)}",
        f"- Electricity Payload Signals: {_list_or_none(['numeric-pattern' for _ in report.electricity_hints])}",
        f"- Responses with electricity hints: {_list_or_none(report.electricity_hints)}",
        "",
        "## Matched Requests",
        "",
    ]
    if not report.findings:
        lines.append("No matching enterprise WeChat school requests were found.")
        return "\n".join(lines)
    for index, finding in enumerate(report.findings, start=1):
        lines.extend(
            [
                f"### {index}. {finding.method} {finding.url}",
                f"- Status: {finding.status if finding.status is not None else 'unknown'}",
                f"- Query keys: {_list_or_none(finding.query_keys)}",
                f"- Cookie names: {_list_or_none(finding.cookie_names)}",
                f"- Set-Cookie names: {_list_or_none(finding.set_cookie_names)}",
                f"- UA markers: {_list_or_none(finding.user_agent_markers)}",
                f"- Candidate endpoint: {'yes' if finding.candidate_endpoint else 'no'}",
                f"- Electricity response hint: {'yes' if finding.electricity_hint else 'no'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def sanitize_url(url: str) -> str:
    parsed = urlparse(url)
    sanitized_query = []
    for key, _ in parse_qsl(parsed.query, keep_blank_values=True):
        sanitized_query.append((key, "<redacted>" if key.lower() in SENSITIVE_QUERY_KEYS else "<redacted>"))
    return urlunparse(parsed._replace(query=urlencode(sanitized_query, doseq=True, safe="<>")))


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


def _cookie_names(cookie_header: str) -> list[str]:
    names = []
    for part in cookie_header.split(";"):
        if "=" not in part:
            continue
        name = part.split("=", 1)[0].strip()
        if name:
            names.append(name)
    return sorted(set(names))


def _set_cookie_names(set_cookie_header: str) -> list[str]:
    names = []
    for part in re.split(r",\s*(?=[^;,=\s]+=)", set_cookie_header):
        if "=" not in part:
            continue
        name = part.split("=", 1)[0].strip()
        if name:
            names.append(name)
    return sorted(set(names))


def _har_cookie_names(cookies: list[dict[str, Any]]) -> list[str]:
    names = []
    for cookie in cookies:
        name = str(cookie.get("name", "")).strip()
        if name:
            names.append(name)
    return sorted(set(names))


def _ua_markers(user_agent: str) -> list[str]:
    lowered = user_agent.lower()
    return [marker for marker in WECHAT_UA_MARKERS if marker.lower() in lowered]


def _is_candidate_endpoint(path: str) -> bool:
    lowered = path.lower()
    return lowered.endswith((".ashx", ".aspx"))


def _has_electricity_hint(text: str) -> bool:
    if not text:
        return False
    return any(keyword in text for keyword in ELECTRICITY_KEYWORDS) or BALANCE_PATTERN.search(text) is not None


def _safe_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _list_or_none(values: list[str]) -> str:
    return ", ".join(f"`{value}`" for value in values) if values else "none"


def _query_key_summary(values: dict[str, bool]) -> str:
    return ", ".join(f"`{key}`={'yes' if present else 'no'}" for key, present in values.items())


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze Enterprise WeChat HAR traffic without printing secrets.")
    parser.add_argument("har_file", type=Path, help="Path to an exported HAR JSON file.")
    args = parser.parse_args()
    print(render_markdown(analyze_har_file(args.har_file)))


if __name__ == "__main__":
    main()
