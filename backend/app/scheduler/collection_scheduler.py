from __future__ import annotations

from datetime import datetime, tzinfo
import logging
import threading

from backend.app.alerts.email_alerts import EmailAlertService
from backend.app.integrations.campus_portal import CampusPortalClient
from backend.app.persistence.repository import Repository
from backend.app.services.models import ScheduleConfig
from backend.app.shared.errors import AppError, AuthenticationError, SchedulerError, SessionExpiredError
from backend.app.shared.http import app_now_iso

logger = logging.getLogger(__name__)


class CollectionScheduler:
    def __init__(self, repository: Repository, portal: CampusPortalClient, alerts: EmailAlertService, timezone: tzinfo):
        self.repository = repository
        self.portal = portal
        self.alerts = alerts
        self.timezone = timezone
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._run_lock = threading.Lock()

    def restart(self, *, run_immediately: bool = False) -> None:
        with self._lock:
            self._cancel_locked()
            schedules = self.repository.list_enabled_schedule_profiles()
            if schedules:
                first_interval = 0 if run_immediately else self._next_interval([config for _, config in schedules])
                self._start_collection_timer_locked(first_interval)
                logger.info("schedule_started", extra={"profiles": len(schedules)})

    def stop(self) -> None:
        with self._lock:
            self._cancel_locked()

    def run_once(self, profile_id: int | None = None) -> dict[str, object]:
        with self._run_lock:
            scoped_profile_id = self.repository.get_default_profile().id if profile_id is None else profile_id
            config = self.repository.get_schedule_config(profile_id=scoped_profile_id)
            return self._run_profile(scoped_profile_id, config)

    def run_due_profiles(self) -> None:
        with self._run_lock:
            for profile, config in self.repository.list_enabled_schedule_profiles():
                if self._session_expired():
                    logger.info("collection_skipped_session_expired", extra={"profile_id": profile.id})
                    break
                if not self._inside_active_window(config):
                    logger.info("collection_skipped_outside_window", extra={"profile_id": profile.id})
                    continue
                try:
                    self._run_profile(profile.id, config)
                except AppError as exc:
                    self.repository.record_failure(app_now_iso(), exc.code, exc.message)
                    logger.warning("collection_failed", extra={"profile_id": profile.id, "error_code": exc.code})
                except Exception:
                    self.repository.record_failure(app_now_iso(), "UNEXPECTED_ERROR", "Unexpected collection failure.")
                    logger.exception("collection_failed", extra={"profile_id": profile.id, "error_code": "UNEXPECTED_ERROR"})

    def _run_profile(self, profile_id: int, config: ScheduleConfig | None) -> dict[str, object]:
        selection = self.repository.get_room_selection(profile_id=profile_id)
        if selection is None:
            raise SchedulerError("Room selection is required before collection.")
        started_at = app_now_iso()
        collection_window_start = self._collection_window_start(config)
        run_id = self.repository.start_collection_run(selection, started_at, collection_window_start, profile_id=profile_id)
        reading_inserted = False
        run_finalized = False
        try:
            if not self.portal.authenticated:
                if getattr(self.portal, "authentication_status", "unauthenticated") == "session_expired":
                    raise SessionExpiredError(self._session_expired_message())
                raise AuthenticationError("Enterprise WeChat session import or campus portal login is required before collection.")
            self.portal.keep_alive()
            reading = self.portal.fetch_reading(selection)
            reading_inserted = self.repository.insert_reading(reading, collection_window_start, run_id, profile_id=profile_id)
            run_status = "success" if reading_inserted else "duplicate"
            self.repository.finish_collection_run(run_id, app_now_iso(), run_status, reading_inserted)
            run_finalized = True
            logger.info(
                "collection_success",
                extra={
                    "profile_id": profile_id,
                    "building": reading.building,
                    "room": reading.room,
                    "reading_inserted": reading_inserted,
                },
            )
            alert_sent = self.alerts.evaluate(reading, profile_id) if reading_inserted else False
            stored_reading = self.repository.get_latest_successful_reading(profile_id=profile_id)
            return {
                "reading": stored_reading,
                "alertSent": alert_sent,
                "readingInserted": reading_inserted,
            }
        except AppError as exc:
            if not run_finalized:
                self.repository.finish_collection_run(
                    run_id,
                    app_now_iso(),
                    "failed",
                    reading_inserted,
                    exc.code,
                    exc.message,
                )
            self._notify_session_expired(exc, profile_id)
            raise
        except Exception:
            if not run_finalized:
                self.repository.finish_collection_run(
                    run_id,
                    app_now_iso(),
                    "failed",
                    reading_inserted,
                    "UNEXPECTED_ERROR",
                    "Unexpected collection failure.",
                )
            raise

    def _run_and_reschedule(self) -> None:
        try:
            self.run_due_profiles()
        finally:
            with self._lock:
                schedules = self.repository.list_enabled_schedule_profiles()
                if schedules:
                    self._start_collection_timer_locked(self._next_interval([config for _, config in schedules]))

    def _notify_session_expired(self, error: AppError, profile_id: int) -> None:
        if not isinstance(error, SessionExpiredError):
            return
        notify = getattr(self.alerts, "notify_session_expired", None)
        if not callable(notify):
            return
        try:
            notify(app_now_iso(), profile_id)
        except Exception:
            logger.warning("session_expired_alert_failed", exc_info=True)

    def _inside_active_window(self, config: ScheduleConfig) -> bool:
        now = datetime.now(self.timezone).strftime("%H:%M")
        return config.start_time <= now <= config.end_time

    def _session_expired(self) -> bool:
        return (
            not self.portal.authenticated
            and getattr(self.portal, "authentication_status", "unauthenticated") == "session_expired"
        )

    def _session_expired_message(self) -> str:
        if getattr(self.portal, "active_source", "campus_portal") == "enterprise_wechat":
            return "Enterprise WeChat session expired. Please import a fresh session cookie."
        return "Campus portal session expired. Please log in again."

    def _collection_window_start(self, config: ScheduleConfig | None = None) -> str:
        effective_config = config or self.repository.get_schedule_config()
        interval_seconds = max(1, effective_config.interval_seconds if effective_config else 1)
        now = datetime.now(self.timezone).replace(microsecond=0)
        epoch_seconds = int(now.timestamp())
        window_epoch = epoch_seconds - (epoch_seconds % interval_seconds)
        return datetime.fromtimestamp(window_epoch, self.timezone).isoformat()

    def _next_interval(self, configs: list[ScheduleConfig]) -> int:
        return max(1, min(config.interval_seconds for config in configs))

    def _start_collection_timer_locked(self, interval_seconds: int) -> None:
        self._timer = threading.Timer(interval_seconds, self._run_and_reschedule)
        self._timer.daemon = True
        self._timer.start()

    def _cancel_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
