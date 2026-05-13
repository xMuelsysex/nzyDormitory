from __future__ import annotations

from backend.app.integrations.campus_portal import CampusPortalClient
from backend.app.persistence.repository import Repository
from backend.app.scheduler.collection_scheduler import CollectionScheduler
from backend.app.services.models import AlertConfig, RoomSelection, ScheduleConfig, alert_to_payload, room_to_payload, schedule_to_payload
from backend.app.shared.errors import AuthenticationError, ValidationError
from backend.app.shared.http import utc_now_iso


class MonitorService:
    def __init__(self, repository: Repository, portal: CampusPortalClient, scheduler: CollectionScheduler):
        self.repository = repository
        self.portal = portal
        self.scheduler = scheduler

    def save_room(self, payload: dict[str, object]) -> dict[str, object]:
        if not self.portal.authenticated:
            raise AuthenticationError("Campus portal login is required before selecting a room.")
        selection = RoomSelection.from_payload(payload)
        self.repository.save_room_selection(selection, utc_now_iso())
        return {"roomSelection": room_to_payload(selection)}

    def save_schedule(self, payload: dict[str, object]) -> dict[str, object]:
        if not self.portal.authenticated:
            raise AuthenticationError("Campus portal login is required before scheduling collection.")
        if self.repository.get_room_selection() is None:
            raise ValidationError("Room selection is required before scheduling collection.")
        config = ScheduleConfig.from_payload(payload)
        self.repository.save_schedule_config(config, utc_now_iso())
        self.scheduler.restart()
        return {"scheduleConfig": schedule_to_payload(config)}

    def save_alert(self, payload: dict[str, object]) -> dict[str, object]:
        config = AlertConfig.from_payload(payload)
        self.repository.save_alert_config(config, utc_now_iso())
        return {"alertConfig": alert_to_payload(config)}

    def status(self) -> dict[str, object]:
        return {
            "authenticated": self.portal.authenticated,
            "authenticationStatus": getattr(self.portal, "authentication_status", "authenticated" if self.portal.authenticated else "unauthenticated"),
            "roomSelection": room_to_payload(self.repository.get_room_selection()),
            "scheduleConfig": schedule_to_payload(self.repository.get_schedule_config()),
            "alertConfig": alert_to_payload(self.repository.get_alert_config()),
        }

    def readings(self) -> dict[str, object]:
        return {"readings": self.repository.list_readings()}

    def run_once(self) -> dict[str, object]:
        return self.scheduler.run_once()
