from __future__ import annotations

import logging
import threading

from backend.app.alerts.email_alerts import EmailAlertService
from backend.app.integrations.campus_portal import CampusPortalClient
from backend.app.persistence.repository import Repository
from backend.app.shared.errors import AppError, SessionExpiredError
from backend.app.shared.http import app_now_iso

logger = logging.getLogger(__name__)


class SessionKeeper:
    def __init__(self, portal: CampusPortalClient, repository: Repository, alerts: EmailAlertService, interval_seconds: int):
        self.portal = portal
        self.repository = repository
        self.alerts = alerts
        self.interval_seconds = max(1, interval_seconds)
        self._timer: threading.Timer | None = None
        self._stopped = False
        self._generation = 0
        self._lock = threading.Lock()

    def restart(self) -> None:
        with self._lock:
            self._cancel_locked()
            self._stopped = False
            self._generation += 1
            if self._should_keep_scheduled():
                self._start_timer_locked()

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            self._generation += 1
            self._cancel_locked()

    def run_once(self) -> None:
        was_session_expired = self._session_expired()
        if not self.portal.authenticated:
            if was_session_expired:
                logger.info("portal_session_still_expired")
            return
        try:
            self.portal.keep_alive()
            logger.info("portal_session_keep_alive_success")
        except SessionExpiredError as exc:
            if was_session_expired:
                logger.info("portal_session_still_expired")
                raise
            failed_at = app_now_iso()
            self.repository.record_failure(failed_at, exc.code, exc.message)
            self._notify_session_expired(failed_at)
            logger.warning("portal_session_expired")
            raise
        except AppError as exc:
            self.repository.record_failure(app_now_iso(), exc.code, exc.message)
            logger.warning("portal_session_keep_alive_failed", extra={"error_code": exc.code})
            raise

    def _notify_session_expired(self, occurred_at: str) -> None:
        try:
            self.alerts.notify_session_expired(occurred_at)
        except Exception:
            logger.warning("session_expired_alert_failed", exc_info=True)

    def _run_and_reschedule(self, generation: int | None = None) -> None:
        should_continue = False
        try:
            self.run_once()
            should_continue = self._should_keep_scheduled()
        except SessionExpiredError:
            should_continue = True
        except AppError:
            should_continue = self._should_keep_scheduled()
        except Exception:
            self.repository.record_failure(app_now_iso(), "UNEXPECTED_ERROR", "Unexpected portal keep-alive failure.")
            logger.exception("portal_session_keep_alive_failed", extra={"error_code": "UNEXPECTED_ERROR"})
            should_continue = self._should_keep_scheduled()
        finally:
            with self._lock:
                is_current_timer = generation is None or generation == self._generation
                if is_current_timer:
                    self._timer = None
                if is_current_timer and not self._stopped and should_continue:
                    self._start_timer_locked()

    def _should_keep_scheduled(self) -> bool:
        return self.portal.authenticated or self._session_expired()

    def _session_expired(self) -> bool:
        return getattr(self.portal, "authentication_status", "unauthenticated") == "session_expired"

    def _start_timer_locked(self) -> None:
        generation = self._generation
        self._timer = threading.Timer(self.interval_seconds, lambda: self._run_and_reschedule(generation))
        self._timer.daemon = True
        self._timer.start()

    def _cancel_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
