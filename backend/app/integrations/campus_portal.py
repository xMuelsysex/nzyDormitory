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


@dataclass(frozen=True)
class LoginSubmission:
    url: str
    fields: dict[str, str]


@dataclass(frozen=True)
class PortalResponseDiagnosis:
    kind: str
    message: str


class CampusPortalClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.cookie_jar = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookie_jar))
        self.authenticated = False

    def load_login_page(self) -> str:
        try:
            with self.opener.open(_get_request(self.settings.campus_login_url), timeout=15) as response:
                body = response.read().decode(_response_charset(response), errors="ignore")
        except OSError as exc:
            raise PortalFetchError("Campus login portal is unavailable.") from exc
        return rewrite_login_page(body, self.settings.campus_login_url)

    def fetch_proxy_resource(self, request_path: str) -> tuple[bytes, str]:
        return self._fetch_and_rewrite_resource(proxy_target_from_path(request_path, self.settings.campus_login_url))

    def fetch_portal_path(self, portal_path: str) -> tuple[bytes, str]:
        relative_path = portal_path.removeprefix("/portal/")
        target = urljoin(self.settings.campus_login_url, relative_path)
        if not _is_allowed_portal_url(target, self.settings.campus_login_url):
            raise PortalFetchError("Portal proxy target is not allowed.")
        return self._fetch_and_rewrite_resource(target)

    def _fetch_and_rewrite_resource(self, target: str) -> tuple[bytes, str]:
        try:
            with self.opener.open(_get_request(target), timeout=15) as response:
                body = response.read()
                content_type = response.headers.get("Content-Type") or mimetypes.guess_type(urlparse(target).path)[0] or "application/octet-stream"
        except OSError as exc:
            raise PortalFetchError("Campus portal resource is unavailable.") from exc
        if "text/html" in content_type.lower():
            text = body.decode(_charset_from_content_type(content_type), errors="ignore")
            body = rewrite_login_page(text, target).encode("utf-8")
            content_type = "text/html; charset=utf-8"
        elif "text/css" in content_type.lower():
            text = body.decode(_charset_from_content_type(content_type), errors="ignore")
            body = rewrite_css_urls(text, target, self.settings.campus_login_url).encode("utf-8")
            content_type = "text/css; charset=utf-8"
        return body, content_type

    def submit_login_page(self, fields: dict[str, str]) -> tuple[bool, str]:
        action = fields.pop("__portal_action", self.settings.campus_login_url)
        target = urljoin(self.settings.campus_login_url, action)
        if not _is_allowed_portal_url(target, self.settings.campus_login_url):
            raise PortalFetchError("Portal login target is not allowed.")
        request = Request(
            target,
            data=urlencode(fields).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": self.settings.campus_login_url,
                "User-Agent": "Mozilla/5.0 DormElectricityMonitor/1.0",
            },
        )
        try:
            with self.opener.open(request, timeout=15) as response:
                body = response.read().decode(_response_charset(response), errors="ignore")
                response_url = getattr(response, "url", self.settings.campus_login_url)
        except OSError as exc:
            raise PortalFetchError("Campus login portal is unavailable.") from exc
        if _looks_like_login_failure(body) or _looks_like_login_page(body) or not _looks_like_authenticated_page(body):
            self.authenticated = False
            return False, rewrite_login_page(body, response_url)
        self.authenticated = True
        return True, ""

    def login(self, username: str, password: str) -> None:
        if not username or not password:
            raise AuthenticationError("Username and password are required.")
        try:
            with self.opener.open(_get_request(self.settings.campus_login_url), timeout=15) as response:
                login_page = response.read().decode(_response_charset(response), errors="ignore")
            submission = build_login_submission(self.settings.campus_login_url, login_page, username, password)
            data = urlencode(submission.fields).encode("utf-8")
            request = Request(
                submission.url,
                data=data,
                method="POST",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": self.settings.campus_login_url,
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
            with self.opener.open(_get_request(self.settings.campus_electricity_url), timeout=15) as response:
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
    action = urljoin(login_url, _login_form_action(page_html))
    rewritten = re.sub(r"<form\b([^>]*)>", _rewrite_form_start(action), page_html, count=1, flags=re.IGNORECASE)
    marker = f'<input type="hidden" name="__portal_action" value="{_escape_attr(action)}" />'
    if "__portal_action" not in rewritten:
        rewritten = re.sub(r"<form\b([^>]*)>", lambda match: f"{match.group(0)}{marker}", rewritten, count=1, flags=re.IGNORECASE)
    return rewrite_html_urls(rewritten, login_url)


def rewrite_html_urls(page_html: str, base_url: str) -> str:
    rewritten = page_html
    for attr in ("src", "href"):
        rewritten = re.sub(
            rf"\b{attr}\s*=\s*(['\"])(.*?)\1",
            lambda match: f'{attr}={match.group(1)}{_proxied_url(match.group(2), base_url)}{match.group(1)}',
            rewritten,
            flags=re.IGNORECASE,
        )
    return rewritten


def rewrite_css_urls(css: str, base_url: str, login_url: str) -> str:
    return re.sub(
        r"url\((['\"]?)(.*?)\1\)",
        lambda match: f"url({match.group(1)}{_proxied_url(match.group(2), base_url, login_url)}{match.group(1)})",
        css,
        flags=re.IGNORECASE,
    )


def proxy_target_from_path(request_path: str, login_url: str) -> str:
    query = urlparse(request_path).query
    values = parse_qs(query).get("url", [])
    if not values:
        raise PortalFetchError("Portal proxy target is missing.")
    target = values[-1]
    if not _is_allowed_portal_url(target, login_url):
        raise PortalFetchError("Portal proxy target is not allowed.")
    return target


def _proxied_url(value: str, base_url: str, login_url: str | None = None) -> str:
    stripped = value.strip()
    if not stripped or stripped.startswith(("#", "data:", "javascript:", "mailto:", "tel:")):
        return value
    absolute = urljoin(base_url, stripped)
    if not _is_allowed_portal_url(absolute, login_url or base_url):
        return value
    return f"/portal/proxy?url={quote(absolute, safe='')}"


def _is_allowed_portal_url(target: str, login_url: str) -> bool:
    target_host = urlparse(target).netloc
    login_host = urlparse(login_url).netloc
    return bool(target_host) and target_host == login_host


def _escape_attr(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def _login_form_action(page_html: str) -> str:
    form = _select_login_form(page_html)
    if form is None:
        return ""
    return form.action


def _rewrite_form_start(action: str):
    def replace(match: re.Match[str]) -> str:
        attrs = re.sub(r"\saction\s*=\s*(['\"]).*?\1", "", match.group(1), flags=re.IGNORECASE)
        attrs = re.sub(r"\smethod\s*=\s*(['\"]).*?\1", "", attrs, flags=re.IGNORECASE)
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


def diagnose_portal_response(page_html: str) -> PortalResponseDiagnosis:
    text = _visible_text(page_html)
    lowered = text.lower()
    if _looks_like_login_page(page_html):
        return PortalResponseDiagnosis("login_page", "Campus portal returned the login page, so the app is not authenticated or the session expired.")
    if any(token in text for token in ("自助购电", "电费", "电量", "余额", "购电")):
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
        r"(?:余额|电费|剩余电量|当前电量)\s*[:：]?\s*(-?\d+(?:\.\d+)?)\s*(元|度|kWh|KWH)?",
        r"(-?\d+(?:\.\d+)?)\s*(元|度|kWh|KWH)\s*(?:余额|剩余|电量)?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            unit = match.group(2) or ""
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


def _select_login_form(page_html: str) -> _ParsedForm | None:
    parser = _LoginFormParser()
    parser.feed(page_html)
    if not parser.forms:
        return None
    for form in parser.forms:
        if _find_password_field(form.inputs) is not None:
            return form
    return parser.forms[0]


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
    lowered = body.lower()
    return (
        "password" in lowered
        or "密码" in body
        or "inputcode" in lowered
        or "__eventvalidation" in lowered
        or "name=\"UserPwd\"" in body
    )


def _looks_like_authenticated_page(body: str) -> bool:
    text = _visible_text(body)
    return any(token in text for token in ("安全退出", "退出登录", "注销", "自助购电", "业务办理", "服务大厅"))


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
