from __future__ import annotations

import html
import json
import os
import re
import threading
from dataclasses import dataclass
from typing import Callable
from http.cookiejar import CookieJar, LoadError, MozillaCookieJar
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener

from backend.app.config.settings import Settings
from backend.app.integrations.campus_portal import import_cookie_header
from backend.app.services.models import ElectricityReading, RoomSelection
from backend.app.shared.errors import AuthenticationError, PortalFetchError, PortalParseError, SessionExpiredError
from backend.app.shared.http import utc_now_iso


ENTERPRISE_WECHAT_SOURCE = "enterprise_wechat"
_ELECTRICITY_PAGE_NAME = "s_card_selfhelp_elect.aspx"
_ELECTRICITY_QUERY_ENDPOINT = "card.ashx?action=selfhelp_elect_query"
_SELFHELP_SESSION_PROBE_FORM = {"zone": "", "house": "", "room": "", "electtype": "1"}


@dataclass(frozen=True)
class EnterpriseWechatDiagnosis:
    kind: str
    message: str


@dataclass(frozen=True)
class EnterpriseWechatElectFields:
    zone: str
    house: str
    room: str
    electtype: str = "1"

    def as_form(self) -> dict[str, str]:
        return {
            "zone": self.zone,
            "house": self.house,
            "room": self.room,
            "electtype": self.electtype,
        }


class EnterpriseWechatClient:
    def __init__(self, settings: Settings, clock: Callable[[], str] = utc_now_iso):
        self.settings = settings
        self._lock = threading.RLock()
        self.cookie_jar = self._new_cookie_jar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookie_jar))
        self.authenticated = False
        self.authentication_status = "unauthenticated"
        self.last_verified_at: str | None = None
        self.last_keep_alive_at: str | None = None
        self.last_keep_alive_error: str | None = None
        self._clock = clock

    @property
    def source_name(self) -> str:
        return ENTERPRISE_WECHAT_SOURCE

    def _new_cookie_jar(self) -> CookieJar:
        if self.settings.persist_portal_cookies:
            return MozillaCookieJar(str(self.settings.enterprise_wechat_cookie_path))
        return CookieJar()

    def _rebuild_opener_locked(self) -> None:
        self.opener = build_opener(HTTPCookieProcessor(self.cookie_jar))

    def import_cookies(self, cookie_header: str) -> None:
        with self._lock:
            imported = import_cookie_header(self.cookie_jar, cookie_header, self.settings.enterprise_wechat_electricity_url)
            if imported == 0:
                raise AuthenticationError("No valid enterprise WeChat cookies were found in the provided cookie header.")
            try:
                self.verify_session()
            except (AuthenticationError, SessionExpiredError, PortalFetchError, PortalParseError):
                self.authenticated = False
                self.authentication_status = "unauthenticated"
                raise
            self.authenticated = True
            self.authentication_status = "authenticated"
            self.save_cookies()

    def verify_session(self) -> bool:
        with self._lock:
            body = self._post_form(
                self._electricity_query_url(),
                _SELFHELP_SESSION_PROBE_FORM,
                self._electricity_page_url(),
            )
            diagnosis = diagnose_enterprise_wechat_response(body)
            if diagnosis.kind in {"auth_required", "empty"}:
                self.authenticated = False
                self.authentication_status = "session_expired"
                raise SessionExpiredError("Enterprise WeChat session expired. Please import a fresh session cookie.")
            if diagnosis.kind == "portal_error":
                raise PortalFetchError(diagnosis.message)
            if diagnosis.kind == "unknown":
                raise PortalParseError(diagnosis.message)
            self._mark_verified_locked()
            return True

    def keep_alive(self) -> None:
        with self._lock:
            if not self.authenticated:
                return
            try:
                self.verify_session()
            except (AuthenticationError, SessionExpiredError, PortalFetchError, PortalParseError) as exc:
                self._mark_keep_alive_error_locked(exc)
                raise
            self.last_keep_alive_at = self.last_verified_at
            self.last_keep_alive_error = None
            self.save_cookies()

    def verify_persisted_session(self) -> bool:
        with self._lock:
            if not self.load_persisted_cookies():
                return False
            self.authenticated = True
            self.authentication_status = "authenticated"
            try:
                self.keep_alive()
            except SessionExpiredError:
                self.clear_persisted_cookies()
                return False
            except (PortalFetchError, PortalParseError):
                self.authenticated = False
                self.authentication_status = "unauthenticated"
                return False
            return True

    def reset_session(self) -> None:
        with self._lock:
            self.cookie_jar = self._new_cookie_jar()
            self._rebuild_opener_locked()
            self.authenticated = False
            self.authentication_status = "unauthenticated"
            self.last_verified_at = None
            self.last_keep_alive_at = None
            self.last_keep_alive_error = None
            self.clear_persisted_cookies()

    def save_cookies(self) -> None:
        with self._lock:
            if not self.settings.persist_portal_cookies or not isinstance(self.cookie_jar, MozillaCookieJar):
                return
            self.settings.enterprise_wechat_cookie_path.parent.mkdir(parents=True, exist_ok=True)
            self.cookie_jar.save(ignore_discard=True, ignore_expires=True)
            try:
                os.chmod(self.settings.enterprise_wechat_cookie_path, 0o600)
            except OSError:
                pass

    def load_persisted_cookies(self) -> bool:
        with self._lock:
            if not self.settings.persist_portal_cookies or not isinstance(self.cookie_jar, MozillaCookieJar):
                return False
            if not self.settings.enterprise_wechat_cookie_path.exists():
                return False
            try:
                self.cookie_jar.load(ignore_discard=True, ignore_expires=True)
            except (OSError, LoadError):
                self.authenticated = False
                self.authentication_status = "unauthenticated"
                return False
            self._rebuild_opener_locked()
            return any(True for _ in self.cookie_jar)

    def clear_persisted_cookies(self) -> None:
        with self._lock:
            if not self.settings.persist_portal_cookies:
                return
            try:
                self.settings.enterprise_wechat_cookie_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def fetch_reading(self, selection: RoomSelection) -> ElectricityReading:
        with self._lock:
            if not self.authenticated:
                if self.authentication_status == "session_expired":
                    raise SessionExpiredError("Enterprise WeChat session expired. Please import a fresh session cookie.")
                raise AuthenticationError("Enterprise WeChat session import is required before collection.")
            query_fields = map_enterprise_wechat_elect_fields(selection)
            body = self._post_form(self._electricity_query_url(), query_fields.as_form(), self._electricity_page_url())
            diagnosis = diagnose_enterprise_wechat_response(body)
            self._raise_for_terminal_diagnosis(diagnosis)
            return self._reading_from_body(body, selection)

    def _reading_from_body(self, body: str, selection: RoomSelection) -> ElectricityReading:
        value, unit = parse_enterprise_wechat_electricity_value(body, selfhelp_query=True)
        self._mark_verified_locked()
        self.save_cookies()
        return ElectricityReading(
            collected_at=utc_now_iso(),
            building=selection.building,
            room=selection.room,
            numeric_value=value,
            unit=unit,
            source=ENTERPRISE_WECHAT_SOURCE,
        )

    def _raise_for_terminal_diagnosis(self, diagnosis: EnterpriseWechatDiagnosis) -> None:
        if diagnosis.kind in {"auth_required", "empty"}:
            self.authenticated = False
            self.authentication_status = "session_expired"
            raise SessionExpiredError("Enterprise WeChat session expired. Please import a fresh session cookie.")
        if diagnosis.kind == "portal_error":
            raise PortalFetchError(diagnosis.message)

    def status_payload(self) -> dict[str, object]:
        return {
            "authenticated": self.authenticated,
            "authenticationStatus": self.authentication_status,
            "lastVerifiedAt": self.last_verified_at,
            "lastKeepAliveAt": self.last_keep_alive_at,
            "lastKeepAliveError": self.last_keep_alive_error,
        }

    def _mark_verified_locked(self) -> None:
        self.authenticated = True
        self.authentication_status = "authenticated"
        self.last_verified_at = self._clock()
        self.last_keep_alive_error = None

    def _mark_keep_alive_error_locked(self, error: Exception) -> None:
        code = getattr(error, "code", error.__class__.__name__)
        message = getattr(error, "message", str(error))
        self.last_keep_alive_error = f"{code}: {message}"

    def _post_form(self, url: str, fields: dict[str, str], referer: str) -> str:
        if not _is_allowed_enterprise_wechat_url(url, self.settings.enterprise_wechat_electricity_url):
            raise PortalFetchError("Enterprise WeChat portal target is not allowed.")
        try:
            with self.opener.open(_post_request(url, fields, referer), timeout=15) as response:
                body = response.read()
                content_type = response.headers.get("Content-Type") or "application/json"
                charset = _charset_from_content_type(content_type)
        except OSError as exc:
            raise PortalFetchError("Enterprise WeChat electricity query is unavailable.") from exc
        return body.decode(charset, errors="ignore")

    def _electricity_page_url(self) -> str:
        return _enterprise_wechat_portal_url(self.settings.enterprise_wechat_electricity_url, _ELECTRICITY_PAGE_NAME)

    def _electricity_query_url(self) -> str:
        return _enterprise_wechat_portal_url(self.settings.enterprise_wechat_electricity_url, _ELECTRICITY_QUERY_ENDPOINT)


def map_enterprise_wechat_elect_fields(selection: RoomSelection) -> EnterpriseWechatElectFields:
    building = selection.building.strip()
    room = selection.room.strip()
    match = re.fullmatch(r"(?i)C\s*-?\s*(\d{1,3})", building)
    if match:
        return EnterpriseWechatElectFields(zone="C", house=match.group(1).zfill(2), room=room)
    if re.fullmatch(r"\d{1,3}", building):
        return EnterpriseWechatElectFields(zone="C", house=building.zfill(2), room=room)
    raise PortalParseError("Unsupported dorm building format for enterprise WeChat electricity query.")


def _enterprise_wechat_portal_url(configured_url: str, relative_path: str) -> str:
    return urljoin(_enterprise_wechat_base_directory_url(configured_url), relative_path)


def _enterprise_wechat_base_directory_url(configured_url: str) -> str:
    parsed = urlparse(configured_url)
    path = parsed.path or "/"
    if path.endswith("/"):
        directory = path
    else:
        leaf = path.rsplit("/", 1)[-1]
        directory = f"{path.rstrip('/')}/" if "." not in leaf else f"{path.rsplit('/', 1)[0]}/"
    return parsed._replace(path=directory, params="", query="", fragment="").geturl()


def diagnose_enterprise_wechat_response(body: str) -> EnterpriseWechatDiagnosis:
    stripped = body.strip()
    if not stripped:
        return EnterpriseWechatDiagnosis("empty", "Enterprise WeChat returned an empty response; the session is likely missing or expired.")
    text = _visible_text(stripped)
    lowered = stripped.lower()
    if _looks_like_auth_required(stripped, text):
        return EnterpriseWechatDiagnosis("auth_required", "Enterprise WeChat returned an OAuth/login page or invalid code response.")
    if any(token in lowered for token in ("exception", "stack trace")) or any(token in text for token in ("服务器错误", "系统异常")):
        return EnterpriseWechatDiagnosis("portal_error", "Enterprise WeChat returned an error page.")
    if _looks_like_electricity_payload(stripped, text):
        return EnterpriseWechatDiagnosis("electricity_page", "Enterprise WeChat returned electricity data.")
    if any(token in text for token in ("一卡通", "校园卡", "卡余额", "自助购电", "电费", "缴费", "宿舍")):
        return EnterpriseWechatDiagnosis("workbench_page", "Enterprise WeChat returned the card workbench page.")
    return EnterpriseWechatDiagnosis("unknown", "Enterprise WeChat returned an unrecognized response; inspect the authorized page before updating selectors.")


def parse_enterprise_wechat_electricity_value(body: str, *, selfhelp_query: bool = False) -> tuple[float, str]:
    json_value = _parse_json_electricity_value(body, selfhelp_query=selfhelp_query)
    if json_value is not None:
        return json_value
    text = _visible_text(body)
    patterns = [
        r"(?:电费余额|剩余电费|宿舍电费|当前电费|电费|余额|剩余电量|当前电量)\s*[:：]?\s*(-?\d+(?:\.\d+)?)\s*(元|度|kWh|KWH)?",
        r"(-?\d+(?:\.\d+)?)\s*(元|度|kWh|KWH)\s*(?:电费余额|剩余电费|宿舍电费|电费|余额|剩余|电量)?",
        r"(?:Balance|balance|money|Money|amount|Amount)\D{0,12}(-?\d+(?:\.\d+)?)\s*(元|度|kWh|KWH)?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1)), match.group(2) or ""
    raise PortalParseError("Could not extract electricity value from enterprise WeChat response.")


def _parse_json_electricity_value(body: str, *, selfhelp_query: bool = False) -> tuple[float, str] | None:
    stripped = body.strip().lstrip("\ufeff")
    if not stripped.startswith(("{", "[")):
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        raise PortalParseError("Enterprise WeChat JSON response was malformed.")
    selfhelp_value = _parse_selfhelp_elect_response(payload, selfhelp_query=selfhelp_query)
    if selfhelp_value is not None:
        return selfhelp_value
    found = _walk_json_for_balance(payload)
    if found is None:
        raise PortalParseError("Enterprise WeChat JSON response did not contain an electricity balance field.")
    return found


def _parse_selfhelp_elect_response(payload: object, *, selfhelp_query: bool = False) -> tuple[float, str] | None:
    if not isinstance(payload, dict):
        return None
    normalized = {str(key).lower(): item for key, item in payload.items()}
    if not any(key in normalized for key in ("pass", "bankcardbalance", "cardbalance", "str1")):
        return None
    if not selfhelp_query:
        return None
    if "message" not in normalized:
        raise PortalParseError("Enterprise WeChat electricity query response did not contain a balance message.")
    message = normalized["message"]
    if not isinstance(message, str):
        raise PortalParseError("Enterprise WeChat electricity query returned a non-text balance message.")
    numeric = _to_float(message)
    if numeric is None:
        raise PortalParseError("Enterprise WeChat electricity query message did not contain an electricity balance.")
    return numeric, _unit_from_text(message) or "度"


def _walk_json_for_balance(value: object) -> tuple[float, str] | None:
    if isinstance(value, dict):
        normalized = {str(key).lower(): item for key, item in value.items()}
        for key in (
            "electricitybalance",
            "electricity_balance",
            "electricityfee",
            "electricity_fee",
            "electbalance",
            "elect_balance",
            "roommoney",
            "room_money",
        ):
            if key not in normalized:
                continue
            numeric = _to_float(normalized[key])
            if numeric is not None:
                return numeric, _json_unit(value)
        for key, item in value.items():
            lowered_key = str(key).lower()
            if any(token in lowered_key for token in ("electric", "elect", "roommoney", "room_money")):
                numeric = _to_float(item)
                if numeric is not None:
                    return numeric, _json_unit(value)
        for item in value.values():
            found = _walk_json_for_balance(item)
            if found is not None:
                return found
    if isinstance(value, list):
        for item in value:
            found = _walk_json_for_balance(item)
            if found is not None:
                return found
    return None


def _json_unit(payload: dict[object, object]) -> str:
    for key in ("unit", "Unit", "dw", "DW"):
        unit = payload.get(key)
        if isinstance(unit, str):
            return unit
    return ""


def _to_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value)
        if match:
            return float(match.group(0))
    return None


def _unit_from_text(value: str) -> str:
    match = re.search(r"(元|度|kWh|KWH)", value)
    return match.group(1) if match else ""


def _looks_like_auth_required(body: str, text: str) -> bool:
    lowered = body.lower()
    return (
        "invalid code" in lowered
        or "oauth2" in lowered
        or "weixin.qq.com/connect/oauth2/authorize" in lowered
        or any(token in text for token in ("请在企业微信", "企业微信授权", "重新登录", "请先登录", "未登录", "登录超时", "invalid code"))
    )


def _looks_like_electricity_payload(body: str, text: str) -> bool:
    try:
        if _parse_json_electricity_value(body, selfhelp_query=True) is not None:
            return True
    except PortalParseError:
        if _looks_like_selfhelp_query_payload(body):
            return True
        return False
    return any(token in text for token in ("电费余额", "剩余电费", "宿舍电费", "当前电量", "剩余电量"))


def _looks_like_selfhelp_query_payload(body: str) -> bool:
    stripped = body.strip().lstrip("\ufeff")
    if not stripped.startswith("{"):
        return False
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    normalized_keys = {str(key).lower() for key in payload}
    return "message" in normalized_keys and bool(
        normalized_keys.intersection({"pass", "bankcardbalance", "cardbalance", "str1"})
    )


def _visible_text(page_html: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", page_html))


def _is_allowed_enterprise_wechat_url(target: str, base_url: str) -> bool:
    parsed_target = urlparse(target)
    parsed_base = urlparse(base_url)
    return parsed_target.scheme in {"http", "https"} and parsed_target.netloc == parsed_base.netloc


def _post_request(url: str, fields: dict[str, str], referer: str) -> Request:
    return Request(
        url,
        data=urlencode(fields).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json,text/javascript,*/*;q=0.8",
            "Referer": referer,
            "User-Agent": "Mozilla/5.0 DormElectricityMonitor/1.0",
        },
    )


def _charset_from_content_type(content_type: str) -> str:
    match = re.search(r"charset=([^;]+)", content_type, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return "utf-8"
