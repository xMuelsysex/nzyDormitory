from __future__ import annotations

import html
import re
from http.cookiejar import CookieJar
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

from backend.app.config.settings import Settings
from backend.app.services.models import ElectricityReading, RoomSelection
from backend.app.shared.errors import AuthenticationError, PortalFetchError, PortalParseError
from backend.app.shared.http import utc_now_iso


class CampusPortalClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.cookie_jar = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookie_jar))
        self.authenticated = False

    def login(self, username: str, password: str) -> None:
        if not username or not password:
            raise AuthenticationError("Username and password are required.")
        data = urlencode({"username": username, "password": password}).encode("utf-8")
        request = Request(self.settings.campus_login_url, data=data, method="POST")
        try:
            with self.opener.open(request, timeout=15) as response:
                body = response.read().decode("utf-8", errors="ignore")
        except OSError as exc:
            raise PortalFetchError("Campus login portal is unavailable.") from exc
        if _looks_like_login_failure(body):
            raise AuthenticationError("Campus login failed. Please verify credentials.")
        self.authenticated = True

    def fetch_reading(self, selection: RoomSelection) -> ElectricityReading:
        if not self.authenticated:
            raise AuthenticationError("Campus portal login is required before collection.")
        try:
            with self.opener.open(self.settings.campus_electricity_url, timeout=15) as response:
                body = response.read().decode("utf-8", errors="ignore")
        except OSError as exc:
            raise PortalFetchError("Electricity portal is unavailable.") from exc
        value, unit = parse_electricity_value(body)
        return ElectricityReading(
            collected_at=utc_now_iso(),
            building=selection.building,
            room=selection.room,
            numeric_value=value,
            unit=unit,
        )


def parse_electricity_value(page_html: str) -> tuple[float, str]:
    text = html.unescape(re.sub(r"<[^>]+>", " ", page_html))
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


def _looks_like_login_failure(body: str) -> bool:
    lowered = body.lower()
    return any(token in lowered for token in ["login failed", "密码错误", "登录失败", "验证码"])
