from __future__ import annotations

from datetime import datetime, tzinfo
import logging
import threading

from backend.app.alerts.email_alerts import EmailAlertService
from backend.app.integrations.campus_portal import CampusPortalClient
from backend.app.persistence.repository import Repository
from backend.app.shared.errors import AppError, AuthenticationError, SchedulerError, SessionExpiredError
from backend.app.shared.http import utc_now_iso

logger = logging.getLogger(__name__)


class CollectionScheduler:
    def __init__(self, repository: Repository, portal: CampusPortalClient, alerts: EmailAlertService, timezone: tzinfo):
        self.repository = repository
        self.portal = portal
        self.alerts = alerts
        self.timezone = timezone
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def restart(self) -> None:
        with self._lock:
            self._cancel_locked()
            config = self.repository.get_schedule_config()
            if config and config.enabled:
                self._timer = threading.Timer(config.interval_seconds, self._run_and_reschedule)
                self._timer.daemon = True
                self._timer.start()
                logger.info("schedule_started")

    def stop(self) -> None:
        with self._lock:
            self._cancel_locked()

    def run_once(self) -> dict[str, object]:
        selection = self.repository.get_room_selection()
        if selection is None:
            raise SchedulerError("Room selection is required before collection.")
        if not self.portal.authenticated:
            if getattr(self.portal, "authentication_status", "unauthenticated") == "session_expired":
                raise SessionExpiredError("Campus portal session expired. Please log in again.")
            raise AuthenticationError("Campus portal login is required before collection.")
        reading = self.portal.fetch_reading(selection)
        self.repository.insert_reading(reading)
        alert_sent = self.alerts.evaluate(reading)
        logger.info("collection_success", extra={"building": reading.building, "room": reading.room})
        return {
            "reading": {
                "collectedAt": reading.collected_at,
                "building": reading.building,
                "room": reading.room,
                "numericValue": reading.numeric_value,
                "unit": reading.unit,
            },
            "alertSent": alert_sent,
        }

    def _run_and_reschedule(self) -> None:
        try:
            if self._inside_active_window():
                self.run_once()
            else:
                logger.info("collection_skipped_outside_window")
        except AppError as exc:
            self.repository.record_failure(utc_now_iso(), exc.code, exc.message)
            logger.warning("collection_failed", extra={"error_code": exc.code})
        finally:
            with self._lock:
                config = self.repository.get_schedule_config()
                if config and config.enabled:
                    self._timer = threading.Timer(config.interval_seconds, self._run_and_reschedule)
                    self._timer.daemon = True
                    self._timer.start()

    def _inside_active_window(self) -> bool:
        config = self.repository.get_schedule_config()
        if config is None or not config.enabled:
            return False
        now = datetime.now(self.timezone).strftime("%H:%M")
        return config.start_time <= now <= config.end_time

    def _cancel_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
