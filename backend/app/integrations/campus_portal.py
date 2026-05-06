from __future__ import annotations

import html
import mimetypes
import re
from dataclasses import dataclass
from http.cookiejar import Cookie, CookieJar
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener

from backend.app.config.settings import Settings
from backend.app.services.models import ElectricityReading, RoomSelection
from backend.app.shared.errors import AuthenticationError, PortalFetchError, PortalParseError
from backend.app.shared.http import utc_now_iso


_NJUCM_WEBVPN_HOST = "webvpn.njucm.edu.cn"
_NJUCM_CAS_ASSET_HOST = "ids.njucm.edu.cn"


@dataclass(frozen=True)
class LoginSubmission:
    url: str
    fields: dict[str, str]


@dataclass(frozen=True)
class PortalResponseDiagnosis:
    kind: str
    message: str


@dataclass(frozen=True)
class FeeElectRoomFields:
    zone_id: str
    house: str
    room: str


class CampusPortalClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.cookie_jar = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookie_jar))
        self.authenticated = False
        self._login_page_url = settings.campus_login_url

    def load_login_page(self) -> str:
        try:
            with self.opener.open(_get_request(self.settings.campus_login_url), timeout=15) as response:
                body = response.read().decode(_response_charset(response), errors="ignore")
                self._login_page_url = getattr(response, "url", self.settings.campus_login_url)
        except OSError as exc:
            raise PortalFetchError("Campus login portal is unavailable.") from exc
        frame_login = self._fetch_frame_login_page(body, self._login_page_url)
        if frame_login is not None:
            body, self._login_page_url = frame_login
        return rewrite_login_page(body, self._login_page_url)

    def fetch_proxy_resource(self, request_path: str) -> tuple[bytes, str]:
        return self._fetch_and_rewrite_resource(
            proxy_target_from_path(request_path, self.settings.campus_login_url, self._login_page_url),
        )

    def fetch_portal_path(self, portal_path: str) -> tuple[bytes, str]:
        parsed_path = urlparse(portal_path)
        relative_path = parsed_path.path.removeprefix("/portal/")
        base_url = self._login_page_url or self.settings.campus_login_url
        target = _absolute_portal_url(f"/{relative_path}", base_url)
        if parsed_path.query:
            separator = "&" if urlparse(target).query else "?"
            target = f"{target}{separator}{parsed_path.query}"
        if not _is_allowed_portal_url(target, self.settings.campus_login_url, (base_url,)):
            raise PortalFetchError("Portal proxy target is not allowed.")
        return self._fetch_and_rewrite_resource(target)

    def _fetch_and_rewrite_resource(self, target: str) -> tuple[bytes, str]:
        try:
            with self.opener.open(_get_request(target), timeout=15) as response:
                body = response.read()
                response_url = getattr(response, "url", target)
                content_type = response.headers.get("Content-Type") or mimetypes.guess_type(urlparse(target).path)[0] or "application/octet-stream"
        except OSError as exc:
            raise PortalFetchError("Campus portal resource is unavailable.") from exc
        if "text/html" in content_type.lower():
            text = body.decode(_charset_from_content_type(content_type), errors="ignore")
            body = rewrite_login_page(text, response_url).encode("utf-8")
            content_type = "text/html; charset=utf-8"
        elif "text/css" in content_type.lower():
            text = body.decode(_charset_from_content_type(content_type), errors="ignore")
            body = rewrite_css_urls(text, response_url, response_url).encode("utf-8")
            content_type = "text/css; charset=utf-8"
        return body, content_type

    def submit_login_page(self, fields: dict[str, str]) -> tuple[bool, str]:
        action = fields.pop("__portal_action", self._login_page_url)
        target = urljoin(self._login_page_url, action)
        if not _is_allowed_portal_url(target, self.settings.campus_login_url, (self._login_page_url,)):
            raise PortalFetchError("Portal login target is not allowed.")
        request = Request(
            target,
            data=urlencode(fields).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": self._login_page_url,
                "User-Agent": "Mozilla/5.0 DormElectricityMonitor/1.0",
            },
        )
        try:
            with self.opener.open(request, timeout=15) as response:
                body = response.read().decode(_response_charset(response), errors="ignore")
                response_url = getattr(response, "url", self._login_page_url)
        except OSError as exc:
            raise PortalFetchError("Campus login portal is unavailable.") from exc
        frame_login = self._fetch_frame_login_page(body, response_url)
        if frame_login is not None:
            body, response_url = frame_login
        if _looks_like_login_failure(body) or _looks_like_login_page(body) or not _looks_like_authenticated_page(body):
            self.authenticated = False
            self._login_page_url = response_url
            return False, rewrite_login_page(body, response_url)
        self.authenticated = True
        return True, ""

    def _fetch_frame_login_page(self, body: str, response_url: str) -> tuple[str, str] | None:
        if not _looks_like_frame_page(body):
            return None
        allowed_urls = (self._login_page_url, response_url)
        for frame_url in _candidate_frame_urls(body, response_url):
            if not _is_allowed_portal_url(frame_url, self.settings.campus_login_url, allowed_urls):
                continue
            try:
                with self.opener.open(_get_request(frame_url), timeout=15) as response:
                    frame_body = response.read().decode(_response_charset(response), errors="ignore")
                    final_url = getattr(response, "url", frame_url)
            except OSError:
                continue
            if not _is_allowed_portal_url(final_url, self.settings.campus_login_url, allowed_urls):
                continue
            if _looks_like_login_page(frame_body):
                return frame_body, final_url
        return None

    def login(self, username: str, password: str) -> None:
        if not username or not password:
            raise AuthenticationError("Username and password are required.")
        try:
            with self.opener.open(_get_request(self.settings.campus_login_url), timeout=15) as response:
                login_page = response.read().decode(_response_charset(response), errors="ignore")
                login_page_url = getattr(response, "url", self.settings.campus_login_url)
            submission = build_login_submission(login_page_url, login_page, username, password)
            data = urlencode(submission.fields).encode("utf-8")
            request = Request(
                submission.url,
                data=data,
                method="POST",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": login_page_url,
                    "User-Agent": "Mozilla/5.0 DormElectricityMonitor/1.0",
                },
            )
            with self.opener.open(request, timeout=15) as response:
                body = response.read().decode(_response_charset(response), errors="ignore")
        except OSError as exc:
            raise PortalFetchError("Campus login portal is unavailable.") from exc
        if _looks_like_login_failure(body):
            raise AuthenticationError("Campus login failed. Please verify credentials or import a browser session cookie.")
        self.authenticated = True

    def import_cookies(self, cookie_header: str) -> None:
        imported = import_cookie_header(self.cookie_jar, cookie_header, self.settings.campus_login_url)
        if imported == 0:
            raise AuthenticationError("No valid cookies were found in the provided cookie header.")
        self.authenticated = True

    def fetch_reading(self, selection: RoomSelection) -> ElectricityReading:
        if not self.authenticated:
            raise AuthenticationError("Campus portal login is required before collection.")
        try:
            electricity_url = self.settings.campus_electricity_url
            with self.opener.open(_get_request(electricity_url), timeout=15) as response:
                body = response.read().decode(_response_charset(response), errors="ignore")
        except OSError as exc:
            raise PortalFetchError("Electricity portal is unavailable.") from exc
        diagnosis = diagnose_portal_response(body)
        if diagnosis.kind == "login_page":
            self.authenticated = False
            raise AuthenticationError("Campus portal session expired or login did not complete. Please log in again.")
        if diagnosis.kind != "electricity_page":
            raise PortalParseError(diagnosis.message)
        fields = build_fee_elect_query_fields(body, selection)
        request = Request(
            electricity_url,
            data=urlencode(fields).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": electricity_url,
                "User-Agent": "Mozilla/5.0 DormElectricityMonitor/1.0",
            },
        )
        try:
            with self.opener.open(request, timeout=15) as response:
                body = response.read().decode(_response_charset(response), errors="ignore")
        except OSError as exc:
            raise PortalFetchError("Electricity portal is unavailable.") from exc
        diagnosis = diagnose_portal_response(body)
        if diagnosis.kind == "login_page":
            self.authenticated = False
            raise AuthenticationError("Campus portal session expired or login did not complete. Please log in again.")
        if diagnosis.kind != "electricity_page":
            raise PortalParseError(diagnosis.message)
        value, unit = parse_electricity_value(body)
        return ElectricityReading(
            collected_at=utc_now_iso(),
            building=selection.building,
            room=selection.room,
            numeric_value=value,
            unit=unit,
        )


def rewrite_login_page(page_html: str, login_url: str) -> str:
    login_form_index = _login_form_index(page_html)
    if login_form_index is None:
        return rewrite_html_urls(page_html, login_url)
    action = _absolute_portal_url(_login_form_action(page_html), login_url)
    marker = f'<input type="hidden" name="__portal_action" value="{_escape_attr(action)}" />'

    form_index = -1

    def replace_login_form(match: re.Match[str]) -> str:
        nonlocal form_index
        form_index += 1
        if form_index != login_form_index:
            return match.group(0)
        rewritten_form = _rewrite_form_start(action)(match)
        if "__portal_action" not in page_html:
            rewritten_form = f"{rewritten_form}{marker}"
        return rewritten_form

    rewritten = re.sub(r"<form\b([^>]*)>", replace_login_form, page_html, flags=re.IGNORECASE)
    return rewrite_html_urls(rewritten, login_url)


def rewrite_html_urls(page_html: str, base_url: str) -> str:
    rewritten = _strip_webvpn_client_scripts(page_html)
    for attr in ("src", "href"):
        rewritten = re.sub(
            rf"\b{attr}\s*=\s*(['\"])(.*?)\1",
            lambda match: f'{attr}={match.group(1)}{_proxied_url(match.group(2), base_url, webvpn_root_assets_from_base=True)}{match.group(1)}',
            rewritten,
            flags=re.IGNORECASE,
        )
        rewritten = re.sub(
            rf"\b{attr}\s*=\s*([^\s'\">]+)",
            lambda match: f'{attr}="{_proxied_url(match.group(1), base_url, webvpn_root_assets_from_base=True)}"',
            rewritten,
            flags=re.IGNORECASE,
        )
    return rewritten


def _strip_webvpn_client_scripts(page_html: str) -> str:
    def replace(match: re.Match[str]) -> str:
        source = _script_src_from_attrs(match.group(1))
        if source is not None and _is_webvpn_client_bundle_url(source):
            return ""
        return match.group(0)

    return re.sub(
        r"<script\b([^>]*)>\s*</script\s*>",
        replace,
        page_html,
        flags=re.IGNORECASE | re.DOTALL,
    )


def _script_src_from_attrs(attrs: str) -> str | None:
    match = re.search(r"\bsrc\s*=\s*(?:([\"'])(.*?)\1|([^\s>]+))", attrs, flags=re.IGNORECASE | re.DOTALL)
    if match is None:
        return None
    return html.unescape(match.group(2) or match.group(3) or "")


def _is_webvpn_client_bundle_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    candidates = parse_qs(parsed.query).get("url", [])
    if candidates:
        return any(_is_webvpn_client_bundle_url(candidate) for candidate in candidates)
    return _is_webvpn_client_bundle_path(parsed.path)


def _is_webvpn_client_bundle_path(path: str) -> bool:
    segments = [segment.lower() for segment in path.split("/") if segment]
    return len(segments) >= 2 and segments[-2] == "webvpn" and re.fullmatch(r"bundle[^/]*\.js", segments[-1]) is not None


def rewrite_css_urls(css: str, base_url: str, login_url: str) -> str:
    return re.sub(
        r"url\((['\"]?)(.*?)\1\)",
        lambda match: f"url({match.group(1)}{_proxied_url(match.group(2), base_url, login_url, webvpn_root_assets_from_base=True)}{match.group(1)})",
        css,
        flags=re.IGNORECASE,
    )


def proxy_target_from_path(request_path: str, login_url: str, *extra_allowed_urls: str) -> str:
    query = urlparse(request_path).query
    values = parse_qs(query).get("url", [])
    if not values:
        raise PortalFetchError("Portal proxy target is missing.")
    target = values[-1]
    if not _is_allowed_portal_url(target, login_url, extra_allowed_urls):
        raise PortalFetchError("Portal proxy target is not allowed.")
    return target


def _proxied_url(
    value: str,
    base_url: str,
    login_url: str | None = None,
    *,
    webvpn_root_assets_from_base: bool = False,
) -> str:
    stripped = value.strip()
    if not stripped or stripped.startswith(("#", "data:", "javascript:", "mailto:", "tel:")):
        return value
    absolute = _absolute_portal_url(stripped, base_url, webvpn_root_assets_from_base=webvpn_root_assets_from_base)
    if not _is_allowed_portal_url(absolute, login_url or base_url):
        return value
    return f"/portal/proxy?url={quote(absolute, safe='')}"


def _absolute_portal_url(value: str, base_url: str, *, webvpn_root_assets_from_base: bool = False) -> str:
    if not value:
        return base_url
    stripped = value.strip()
    if stripped.startswith("/") and not stripped.startswith("//"):
        parsed = urlparse(base_url)
        if (
            webvpn_root_assets_from_base
            and _is_njucm_webvpn_cas_login_url(base_url)
            and _looks_like_root_static_asset(stripped)
        ):
            return parsed._replace(
                scheme="https",
                netloc=_NJUCM_CAS_ASSET_HOST,
                path=stripped,
                params="",
                query="",
                fragment="",
            ).geturl()
        gateway_prefix = _webvpn_gateway_prefix(parsed.path)
        if gateway_prefix and not stripped.startswith(f"{gateway_prefix}/"):
            if webvpn_root_assets_from_base and _looks_like_root_static_asset(stripped):
                base_dir = parsed.path.rsplit("/", 1)[0]
                return parsed._replace(path=f"{base_dir}{stripped}", params="", query="", fragment="").geturl()
            return parsed._replace(path=f"{gateway_prefix}{stripped}", params="", query="", fragment="").geturl()
    return urljoin(base_url, stripped)


def _webvpn_gateway_prefix(path: str) -> str:
    match = re.match(r"^/(?:http|https)/[^/]+", path, flags=re.IGNORECASE)
    return match.group(0) if match else ""


def _looks_like_root_static_asset(path: str) -> bool:
    first_segment = path.strip("/").split("/", 1)[0].lower()
    return first_segment in {"css", "favicon.ico", "images", "img", "js", "scripts", "themes"}


def _is_njucm_webvpn_cas_login_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.hostname == _NJUCM_WEBVPN_HOST and parsed.path.rstrip("/").lower() == "/login"


def _is_njucm_cas_asset_target(target: str, context_urls: tuple[str, ...]) -> bool:
    parsed = urlparse(target)
    return (
        parsed.scheme == "https"
        and parsed.hostname == _NJUCM_CAS_ASSET_HOST
        and _looks_like_root_static_asset(parsed.path)
        and any(_is_njucm_webvpn_cas_login_url(url) for url in context_urls if url)
    )


def _is_allowed_portal_url(target: str, login_url: str, extra_allowed_urls: tuple[str, ...] = ()) -> bool:
    parsed_target = urlparse(target)
    if parsed_target.scheme not in {"http", "https"}:
        return False
    target_host = parsed_target.netloc
    allowed_hosts = {urlparse(url).netloc for url in (login_url, *extra_allowed_urls) if url}
    context_urls = (login_url, *extra_allowed_urls)
    return bool(target_host) and (target_host in allowed_hosts or _is_njucm_cas_asset_target(target, context_urls))


def _escape_attr(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def _login_form_action(page_html: str) -> str:
    form = _select_login_form(page_html)
    if form is None:
        return ""
    return form.action


def _login_form_index(page_html: str) -> int | None:
    parser = _LoginFormParser()
    parser.feed(page_html)
    if not parser.forms:
        return None
    for index, form in enumerate(parser.forms):
        if _find_password_field(form.inputs) is not None:
            return index
    return 0


def _rewrite_form_start(action: str):
    def replace(match: re.Match[str]) -> str:
        attrs = re.sub(r"\saction\s*=\s*(?:(['\"]).*?\1|[^\s>]+)", "", match.group(1), flags=re.IGNORECASE)
        attrs = re.sub(r"\smethod\s*=\s*(?:(['\"]).*?\1|[^\s>]+)", "", attrs, flags=re.IGNORECASE)
        return f'<form{attrs} method="post" action="/portal/login">'
    return replace


def build_login_submission(login_url: str, login_page: str, username: str, password: str) -> LoginSubmission:
    form = _select_login_form(login_page)
    if form is None:
        return LoginSubmission(login_url, {"username": username, "password": password})
    fields = dict(form.inputs)
    username_field = _find_username_field(form.inputs)
    password_field = _find_password_field(form.inputs)
    if username_field is None or password_field is None:
        return LoginSubmission(login_url, {"username": username, "password": password})
    fields[username_field] = username
    fields[password_field] = password
    return LoginSubmission(urljoin(login_url, form.action or login_url), fields)


def import_cookie_header(cookie_jar: CookieJar, cookie_header: str, origin_url: str) -> int:
    host = urlparse(origin_url).hostname or "localhost"
    imported = 0
    for part in cookie_header.split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        cookie_jar.set_cookie(_make_cookie(name, value, host))
        imported += 1
    return imported


def build_fee_elect_query_fields(page_html: str, selection: RoomSelection) -> dict[str, str]:
    form = _select_form_with_fields(page_html, ("__VIEWSTATE", "__EVENTVALIDATION", "txtHouse", "txtRoom", "FeeAmtTxt"))
    fields = dict(form.inputs) if form is not None else {}
    room_fields = map_fee_elect_room_fields(selection)
    fields.pop("btkOK", None)
    fields["ZoneID"] = room_fields.zone_id
    fields["txtHouse"] = room_fields.house
    fields["txtRoom"] = room_fields.room
    fields["btnQuery"] = "查询电量"
    fields["FeeAmtTxt"] = fields.get("FeeAmtTxt") or "10"
    return fields


def map_fee_elect_room_fields(selection: RoomSelection) -> FeeElectRoomFields:
    building = selection.building.strip()
    room = selection.room.strip()
    match = re.fullmatch(r"(?i)\s*C\s*-?\s*(\d{1,3})\s*", building)
    if match:
        return FeeElectRoomFields(zone_id="1", house=match.group(1), room=room)
    if re.fullmatch(r"\d{1,3}", building):
        return FeeElectRoomFields(zone_id="1", house=building, room=room)
    raise PortalParseError("Unsupported dorm building format for FeeElect query.")


def diagnose_portal_response(page_html: str) -> PortalResponseDiagnosis:
    text = _visible_text(page_html)
    lowered = text.lower()
    if _looks_like_login_page(page_html):
        return PortalResponseDiagnosis("login_page", "Campus portal returned the login page, so the app is not authenticated or the session expired.")
    if "lblRoomMoney" in page_html or any(token in text for token in ("自助购电", "电费", "电量", "余额", "购电")):
        return PortalResponseDiagnosis("electricity_page", "Campus portal returned an electricity-related page.")
    if any(token in text for token in ("没有权限", "无权限", "未授权", "请先登录")):
        return PortalResponseDiagnosis("access_denied", "Campus portal returned an access-denied page after login.")
    if any(token in text for token in ("友情提醒", "校园卡", "查询", "业务办理", "服务大厅")):
        return PortalResponseDiagnosis("portal_home", "Campus portal returned the portal home page, not the self-service electricity page. Login likely succeeded, but the electricity URL or navigation target is wrong.")
    if "error" in lowered or "exception" in lowered:
        return PortalResponseDiagnosis("portal_error", "Campus portal returned an error page instead of electricity data.")
    return PortalResponseDiagnosis("unknown", "Campus portal returned a page that does not look like login, home, or electricity data; update the electricity page selector/parser after inspecting the authorized portal page.")


def parse_electricity_value(page_html: str) -> tuple[float, str]:
    text = _visible_text(page_html)
    patterns = [
        r"<span\b[^>]*\bid\s*=\s*([\"'])lblRoomMoney\1[^>]*>\s*(-?\d+(?:\.\d+)?)\s*(元|度|kWh|KWH)?\s*</span>",
        r"(?:余额|电费|剩余电量|当前电量)\s*[:：]?\s*(-?\d+(?:\.\d+)?)\s*(元|度|kWh|KWH)?",
        r"(-?\d+(?:\.\d+)?)\s*(元|度|kWh|KWH)\s*(?:余额|剩余|电量)?",
    ]
    for pattern in patterns:
        target = page_html if "lblRoomMoney" in pattern else text
        match = re.search(pattern, target, re.IGNORECASE)
        if match:
            value_index = 2 if "lblRoomMoney" in pattern else 1
            value = float(match.group(value_index))
            unit = match.group(value_index + 1) or ""
            return value, unit
    raise PortalParseError("Could not extract electricity value from portal response.")


def _visible_text(page_html: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", page_html))


@dataclass(frozen=True)
class _ParsedForm:
    action: str
    inputs: dict[str, str]
    input_types: dict[str, str]


class _LoginFormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms: list[_ParsedForm] = []
        self._action = ""
        self._inputs: dict[str, str] | None = None
        self._types: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {name.lower(): value or "" for name, value in attrs}
        if tag.lower() == "form":
            self._action = attr.get("action", "")
            self._inputs = {}
            self._types = {}
            return
        if tag.lower() != "input" or self._inputs is None or self._types is None:
            return
        name = attr.get("name", "")
        if not name:
            return
        self._inputs[name] = attr.get("value", "")
        self._types[name] = attr.get("type", "text").lower()

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "form" and self._inputs is not None and self._types is not None:
            self.forms.append(_ParsedForm(self._action, dict(self._inputs), dict(self._types)))
            self._inputs = None
            self._types = None
            self._action = ""


class _FrameSrcParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() not in {"frame", "iframe"}:
            return
        attr = {name.lower(): value or "" for name, value in attrs}
        src = attr.get("src", "").strip()
        if src:
            self.sources.append(src)


def _select_login_form(page_html: str) -> _ParsedForm | None:
    parser = _LoginFormParser()
    parser.feed(page_html)
    if not parser.forms:
        return None
    for form in parser.forms:
        if _find_password_field(form.inputs) is not None:
            return form
    return parser.forms[0]


def _select_form_with_fields(page_html: str, field_names: tuple[str, ...]) -> _ParsedForm | None:
    parser = _LoginFormParser()
    parser.feed(page_html)
    lowered_names = {name.lower() for name in field_names}
    for form in parser.forms:
        if any(name.lower() in lowered_names for name in form.inputs):
            return form
    return parser.forms[0] if parser.forms else None


def _find_username_field(inputs: dict[str, str]) -> str | None:
    candidates = ("user", "account", "login", "name", "uid", "xh", "txtuser", "txtname", "username")
    return _find_named_field(inputs, candidates)


def _find_password_field(inputs: dict[str, str]) -> str | None:
    candidates = ("pwd", "pass", "password", "txtpwd")
    return _find_named_field(inputs, candidates)


def _find_named_field(inputs: dict[str, str], candidates: tuple[str, ...]) -> str | None:
    for name in inputs:
        lowered = name.lower()
        if any(candidate in lowered for candidate in candidates):
            return name
    return None


def _looks_like_login_failure(body: str) -> bool:
    lowered = body.lower()
    return any(token in lowered for token in ["login failed", "密码错误", "登录失败", "验证码", "captcha"])


def _looks_like_login_page(body: str) -> bool:
    return (
        _has_input_named(body, ("userpwd", "inputcode"))
        or _has_password_input(body)
        or _has_webforms_login_state(body)
    )


def _looks_like_frame_page(body: str) -> bool:
    lowered = body.lower()
    return "<frameset" in lowered or "<frame" in lowered or "<iframe" in lowered


def _candidate_frame_urls(body: str, response_url: str) -> list[str]:
    parser = _FrameSrcParser()
    parser.feed(body)
    candidates: list[tuple[int, int, str]] = []
    for index, source in enumerate(parser.sources):
        absolute = _absolute_portal_url(source, response_url)
        candidates.append((-_frame_login_score(absolute), index, absolute))
    return [url for _, _, url in sorted(candidates)]


def _frame_login_score(url: str) -> int:
    lowered = urlparse(url).path.lower()
    score = 0
    for token in ("login", "default", "accountinfo", "account", "student"):
        if token in lowered:
            score += 1
    return score


def _has_webforms_login_state(body: str) -> bool:
    lowered = body.lower()
    if "__eventvalidation" not in lowered:
        return False
    text = _visible_text(body)
    return _has_input_named(body, ("username", "txtusername", "txtuser", "txtname")) and any(
        token in text
        for token in ("登录", "验证码", "用户名", "账号")
    )


def _has_input_named(body: str, names: tuple[str, ...]) -> bool:
    return any(
        _has_input_attr(body, "name", name)
        for name in names
    )


def _has_password_input(body: str) -> bool:
    return _has_input_attr(body, "type", "password")


def _has_input_attr(body: str, attr: str, value: str) -> bool:
    attr_pattern = re.escape(attr)
    value_pattern = re.escape(value)
    quoted = rf"<input\b[^>]*\b{attr_pattern}\s*=\s*([\"']){value_pattern}\1"
    unquoted = rf"<input\b[^>]*\b{attr_pattern}\s*=\s*{value_pattern}(?:\s|/?>)"
    return re.search(quoted, body, re.IGNORECASE) is not None or re.search(unquoted, body, re.IGNORECASE) is not None


def _looks_like_authenticated_page(body: str) -> bool:
    text = _visible_text(body)
    return any(token in text for token in ("管理中心", "安全退出", "退出登录", "退出", "注销", "自助购电", "业务办理", "服务大厅"))


def _get_request(url: str) -> Request:
    return Request(url, headers={"User-Agent": "Mozilla/5.0 DormElectricityMonitor/1.0"})


def _charset_from_content_type(content_type: str) -> str:
    match = re.search(r"charset=([^;]+)", content_type, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return "utf-8"


def _response_charset(response: object) -> str:
    headers = getattr(response, "headers", None)
    if headers is None:
        return "utf-8"
    return headers.get_content_charset() or "utf-8"


def _make_cookie(name: str, value: str, host: str) -> Cookie:
    return Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=host,
        domain_specified=False,
        domain_initial_dot=False,
        path="/",
        path_specified=True,
        secure=False,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={},
        rfc2109=False,
    )
