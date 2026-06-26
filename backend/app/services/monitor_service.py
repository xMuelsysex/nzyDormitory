from __future__ import annotations

from backend.app.integrations.campus_portal import CampusPortalClient
from backend.app.persistence.repository import Repository
from backend.app.scheduler.collection_scheduler import CollectionScheduler
from backend.app.services.models import AlertConfig, RoomSelection, ScheduleConfig, alert_to_payload, room_to_payload, schedule_to_payload
from backend.app.shared.errors import AuthenticationError, ValidationError
from backend.app.shared.http import app_now_iso


class MonitorService:
    def __init__(self, repository: Repository, portal: CampusPortalClient, scheduler: CollectionScheduler):
        self.repository = repository
        self.portal = portal
        self.scheduler = scheduler

    def save_room(self, payload: dict[str, object], device_id: str | None = None) -> dict[str, object]:
        if not self.portal.authenticated:
            raise AuthenticationError("Enterprise WeChat session import or campus portal login is required before selecting a room.")
        profile_id = self._profile_id(device_id)
        selection = RoomSelection.from_payload(payload)
        self.repository.save_room_selection(selection, app_now_iso(), profile_id=profile_id)
        return {"roomSelection": room_to_payload(selection)}

    def save_schedule(self, payload: dict[str, object], device_id: str | None = None) -> dict[str, object]:
        if not self.portal.authenticated:
            raise AuthenticationError("Enterprise WeChat session import or campus portal login is required before scheduling collection.")
        profile_id = self._profile_id(device_id)
        if self.repository.get_room_selection(profile_id=profile_id) is None:
            raise ValidationError("Room selection is required before scheduling collection.")
        config = ScheduleConfig.from_payload(payload)
        self.repository.save_schedule_config(config, app_now_iso(), profile_id=profile_id)
        self.scheduler.restart(run_immediately=config.enabled)
        return {"scheduleConfig": schedule_to_payload(config)}

    def save_alert(self, payload: dict[str, object], device_id: str | None = None) -> dict[str, object]:
        profile_id = self._profile_id(device_id)
        config = AlertConfig.from_payload(payload)
        self.repository.save_alert_config(config, app_now_iso(), profile_id=profile_id)
        return {"alertConfig": alert_to_payload(config)}

    def status(self, device_id: str | None = None) -> dict[str, object]:
        profile_id = self._profile_id(device_id)
        return {
            "authenticated": self.portal.authenticated,
            "authenticationStatus": getattr(self.portal, "authentication_status", "authenticated" if self.portal.authenticated else "unauthenticated"),
            "authenticationSource": getattr(self.portal, "active_source", "campus_portal"),
            "sources": self.portal.status_by_source() if hasattr(self.portal, "status_by_source") else {},
            "roomSelection": room_to_payload(self.repository.get_room_selection(profile_id=profile_id)),
            "scheduleConfig": schedule_to_payload(self.repository.get_schedule_config(profile_id=profile_id)),
            "alertConfig": alert_to_payload(self.repository.get_alert_config(profile_id=profile_id)),
            "currentReading": self.repository.get_latest_successful_reading(profile_id=profile_id),
            "lastCollectionRun": self.repository.get_latest_collection_run(profile_id=profile_id),
        }

    def readings(self, page: int = 1, page_size: int = 20, device_id: str | None = None) -> dict[str, object]:
        profile_id = self._profile_id(device_id)
        offset = (page - 1) * page_size
        return {
            "items": self.repository.list_readings(limit=page_size, offset=offset, profile_id=profile_id),
            "page": page,
            "pageSize": page_size,
            "total": self.repository.count_readings(profile_id=profile_id),
            "currentReading": self.repository.get_latest_successful_reading(profile_id=profile_id),
        }

    def run_once(self, device_id: str | None = None) -> dict[str, object]:
        return self.scheduler.run_once(profile_id=self._profile_id(device_id))

    def _profile_id(self, device_id: str | None) -> int:
        if device_id is None:
            return self.repository.get_default_profile().id
        return self.repository.get_or_create_profile(device_id, app_now_iso()).id
