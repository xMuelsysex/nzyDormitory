import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.persistence.repository import Repository
from backend.app.scheduler.session_keeper import SessionKeeper
from backend.app.shared.errors import PortalFetchError, SessionExpiredError


class FakePortal:
    def __init__(self):
        self.authenticated = True
        self.keep_alive_calls = 0
        self.expire_next = False
        self.fail_next = False

    def keep_alive(self):
        self.keep_alive_calls += 1
        if self.expire_next:
            self.authenticated = False
            raise SessionExpiredError("Campus portal session expired. Please log in again.")
        if self.fail_next:
            raise PortalFetchError("Campus portal keep-alive failed.")


class FakeAlerts:
    def __init__(self):
        self.notifications = []

    def notify_session_expired(self, occurred_at):
        self.notifications.append(occurred_at)
        return True


class FakeTimer:
    created = []

    def __init__(self, interval, callback, args=None, kwargs=None):
        self.interval = interval
        self.callback = callback
        self.args = args or ()
        self.kwargs = kwargs or {}
        self.daemon = False
        self.started = False
        FakeTimer.created.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.started = False


class SessionKeeperTests(unittest.TestCase):
    def setUp(self):
        FakeTimer.created = []
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.repository = Repository(Path(temp_dir.name) / "test.sqlite3")
        self.portal = FakePortal()
        self.alerts = FakeAlerts()
        self.keeper = SessionKeeper(self.portal, self.repository, self.alerts, 300)

    def test_restart_schedules_keep_alive_when_authenticated(self):
        with patch("backend.app.scheduler.session_keeper.threading.Timer", FakeTimer):
            self.keeper.restart()

        self.assertEqual([timer.interval for timer in FakeTimer.created], [300])
        self.assertTrue(FakeTimer.created[0].started)

    def test_restart_does_not_schedule_when_unauthenticated(self):
        self.portal.authenticated = False

        with patch("backend.app.scheduler.session_keeper.threading.Timer", FakeTimer):
            self.keeper.restart()

        self.assertEqual(FakeTimer.created, [])

    def test_expired_session_records_failure_notifies_and_stops(self):
        self.portal.expire_next = True

        with patch("backend.app.scheduler.session_keeper.threading.Timer", FakeTimer):
            self.keeper._run_and_reschedule()

        failures = self._failures()
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["error_code"], "SESSION_EXPIRED")
        self.assertEqual(len(self.alerts.notifications), 1)
        self.assertEqual(FakeTimer.created, [])

    def test_portal_fetch_error_records_failure_and_reschedules_if_still_authenticated(self):
        self.portal.fail_next = True

        with patch("backend.app.scheduler.session_keeper.threading.Timer", FakeTimer):
            self.keeper._run_and_reschedule()

        failures = self._failures()
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["error_code"], "PORTAL_FETCH_ERROR")
        self.assertEqual([timer.interval for timer in FakeTimer.created], [300])

    def _failures(self):
        with self.repository.connect() as conn:
            return conn.execute("SELECT error_code, message FROM collection_failures ORDER BY id").fetchall()
