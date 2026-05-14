from __future__ import annotations

from datetime import UTC, datetime, tzinfo
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
        started_at = utc_now_iso()
        collection_window_start = self._collection_window_start()
        run_id = self.repository.start_collection_run(selection, started_at, collection_window_start)
        reading_inserted = False
        run_finalized = False
        try:
            if not self.portal.authenticated:
                if getattr(self.portal, "authentication_status", "unauthenticated") == "session_expired":
                    raise SessionExpiredError("Campus portal session expired. Please log in again.")
                raise AuthenticationError("Campus portal login is required before collection.")
            reading = self.portal.fetch_reading(selection)
            reading_inserted = self.repository.insert_reading(reading, collection_window_start, run_id)
            run_status = "success" if reading_inserted else "duplicate"
            self.repository.finish_collection_run(run_id, utc_now_iso(), run_status, reading_inserted)
            run_finalized = True
            logger.info(
                "collection_success",
                extra={"building": reading.building, "room": reading.room, "reading_inserted": reading_inserted},
            )
            alert_sent = self.alerts.evaluate(reading) if reading_inserted else False
            stored_reading = self.repository.get_latest_successful_reading()
            return {
                "reading": stored_reading,
                "alertSent": alert_sent,
            }
        except AppError as exc:
            if not run_finalized:
                self.repository.finish_collection_run(
                    run_id,
                    utc_now_iso(),
                    "failed",
                    reading_inserted,
                    exc.code,
                    exc.message,
                )
            raise
        except Exception:
            if not run_finalized:
                self.repository.finish_collection_run(
                    run_id,
                    utc_now_iso(),
                    "failed",
                    reading_inserted,
                    "UNEXPECTED_ERROR",
                    "Unexpected collection failure.",
                )
            raise

    def _run_and_reschedule(self) -> None:
        try:
            if self._inside_active_window():
                self.run_once()
            else:
                logger.info("collection_skipped_outside_window")
        except AppError as exc:
            self.repository.record_failure(utc_now_iso(), exc.code, exc.message)
            logger.warning("collection_failed", extra={"error_code": exc.code})
        except Exception:
            self.repository.record_failure(utc_now_iso(), "UNEXPECTED_ERROR", "Unexpected collection failure.")
            logger.exception("collection_failed", extra={"error_code": "UNEXPECTED_ERROR"})
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

    def _collection_window_start(self) -> str:
        config = self.repository.get_schedule_config()
        interval_seconds = max(1, config.interval_seconds if config else 1)
        now = datetime.now(UTC).replace(microsecond=0)
        epoch_seconds = int(now.timestamp())
        window_epoch = epoch_seconds - (epoch_seconds % interval_seconds)
        return datetime.fromtimestamp(window_epoch, UTC).isoformat().replace("+00:00", "Z")

    def _cancel_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
