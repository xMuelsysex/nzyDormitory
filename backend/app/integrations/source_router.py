from __future__ import annotations

from dataclasses import dataclass

from backend.app.integrations.campus_portal import CampusPortalClient
from backend.app.integrations.enterprise_wechat import EnterpriseWechatClient
from backend.app.services.models import ElectricityReading, RoomSelection
from backend.app.shared.errors import AuthenticationError, SessionExpiredError


@dataclass(frozen=True)
class SourceStatus:
    authenticated: bool
    authentication_status: str


class ElectricitySourceRouter:
    def __init__(self, enterprise_wechat: EnterpriseWechatClient, campus_portal: CampusPortalClient):
        self.enterprise_wechat = enterprise_wechat
        self.campus_portal = campus_portal
        self._active_source_name = "enterprise_wechat" if enterprise_wechat.authenticated else "campus_portal"

    @property
    def authenticated(self) -> bool:
        return self.active_client.authenticated

    @property
    def authentication_status(self) -> str:
        return getattr(self.active_client, "authentication_status", "authenticated" if self.authenticated else "unauthenticated")

    @property
    def active_source(self) -> str:
        if (
            self._active_source_name == "enterprise_wechat"
            and getattr(self.enterprise_wechat, "authentication_status", "") == "session_expired"
        ):
            return "enterprise_wechat"
        if self.enterprise_wechat.authenticated:
            return "enterprise_wechat"
        if self.campus_portal.authenticated:
            return "campus_portal"
        return self._active_source_name

    @property
    def active_client(self) -> EnterpriseWechatClient | CampusPortalClient:
        if self.active_source == "enterprise_wechat":
            return self.enterprise_wechat
        return self.campus_portal

    def status_by_source(self) -> dict[str, dict[str, object]]:
        return {
            "enterpriseWechat": self._client_status(self.enterprise_wechat),
            "campusPortal": self._client_status(self.campus_portal),
        }

    def keep_alive(self) -> None:
        client = self.active_client
        try:
            client.keep_alive()
            self._active_source_name = getattr(client, "source_name", self.active_source)
        except SessionExpiredError:
            self._active_source_name = getattr(client, "source_name", self.active_source)
            raise

    def fetch_reading(self, selection: RoomSelection) -> ElectricityReading:
        if self.enterprise_wechat.authenticated:
            try:
                reading = self.enterprise_wechat.fetch_reading(selection)
                self._active_source_name = "enterprise_wechat"
                return reading
            except SessionExpiredError:
                self._active_source_name = "enterprise_wechat"
                raise
        if self.campus_portal.authenticated:
            self.campus_portal.keep_alive()
            reading = self.campus_portal.fetch_reading(selection)
            self._active_source_name = "campus_portal"
            return reading
        if getattr(self.enterprise_wechat, "authentication_status", "") == "session_expired":
            self._active_source_name = "enterprise_wechat"
            raise SessionExpiredError("Enterprise WeChat session expired. Please import a fresh session cookie.")
        if getattr(self.campus_portal, "authentication_status", "") == "session_expired":
            self._active_source_name = "campus_portal"
            raise SessionExpiredError("Campus portal session expired. Please log in again.")
        raise AuthenticationError("Enterprise WeChat session import or campus portal login is required before collection.")

    def reset_session(self) -> None:
        self.enterprise_wechat.reset_session()
        self.campus_portal.reset_session()
        self._active_source_name = "enterprise_wechat"

    def _client_status(self, client: EnterpriseWechatClient | CampusPortalClient) -> dict[str, object]:
        status_payload = getattr(client, "status_payload", None)
        if callable(status_payload):
            return status_payload()
        return {
            "authenticated": client.authenticated,
            "authenticationStatus": getattr(client, "authentication_status", "authenticated" if client.authenticated else "unauthenticated"),
        }
