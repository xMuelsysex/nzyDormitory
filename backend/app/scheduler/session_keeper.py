from __future__ import annotations

import logging
import threading

from backend.app.alerts.email_alerts import EmailAlertService
from backend.app.integrations.campus_portal import CampusPortalClient
from backend.app.persistence.repository import Repository
from backend.app.shared.errors import AppError, SessionExpiredError
from backend.app.shared.http import utc_now_iso

logger = logging.getLogger(__name__)


class SessionKeeper:
    def __init__(self, portal: CampusPortalClient, repository: Repository, alerts: EmailAlertService, interval_seconds: int):
        self.portal = portal
        self.repository = repository
        self.alerts = alerts
        self.interval_seconds = max(1, interval_seconds)
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def restart(self) -> None:
        with self._lock:
            self._cancel_locked()
            if self.portal.authenticated:
                self._start_timer_locked()

    def stop(self) -> None:
        with self._lock:
            self._cancel_locked()

    def run_once(self) -> None:
        try:
            self.portal.keep_alive()
            logger.info("portal_session_keep_alive_success")
        except SessionExpiredError as exc:
            failed_at = utc_now_iso()
            self.repository.record_failure(failed_at, exc.code, exc.message)
            self._notify_session_expired(failed_at)
            logger.warning("portal_session_expired")
            raise
        except AppError as exc:
            self.repository.record_failure(utc_now_iso(), exc.code, exc.message)
            logger.warning("portal_session_keep_alive_failed", extra={"error_code": exc.code})
            raise

    def _notify_session_expired(self, occurred_at: str) -> None:
        try:
            self.alerts.notify_session_expired(occurred_at)
        except Exception:
            logger.warning("session_expired_alert_failed", exc_info=True)

    def _run_and_reschedule(self) -> None:
        should_continue = False
        try:
            self.run_once()
            should_continue = self.portal.authenticated
        except SessionExpiredError:
            should_continue = False
        except AppError:
            should_continue = self.portal.authenticated
        except Exception:
            self.repository.record_failure(utc_now_iso(), "UNEXPECTED_ERROR", "Unexpected portal keep-alive failure.")
            logger.exception("portal_session_keep_alive_failed", extra={"error_code": "UNEXPECTED_ERROR"})
            should_continue = self.portal.authenticated
        finally:
            with self._lock:
                self._timer = None
                if should_continue:
                    self._start_timer_locked()

    def _start_timer_locked(self) -> None:
        self._timer = threading.Timer(self.interval_seconds, self._run_and_reschedule)
        self._timer.daemon = True
        self._timer.start()

    def _cancel_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
