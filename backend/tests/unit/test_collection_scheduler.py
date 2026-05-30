import tempfile
import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from backend.app.persistence.repository import Repository
from backend.app.scheduler.collection_scheduler import CollectionScheduler
from backend.app.services.models import ElectricityReading, RoomSelection, ScheduleConfig
from backend.app.shared.errors import AuthenticationError, EmailDeliveryError, SessionExpiredError


class FakePortal:
    def __init__(self):
        self.authenticated = True
        self.authentication_status = "authenticated"
        self.active_source = "campus_portal"
        self.reading = ElectricityReading(
            collected_at="2026-05-13T00:00:00Z",
            building="C20",
            room="2324",
            numeric_value=20.93,
            unit="元",
        )
        self.expire_next_fetch = False
        self.keep_alive_calls = 0
        self.expire_next_keep_alive = False

    def fetch_reading(self, selection):
        if self.expire_next_fetch:
            self.authenticated = False
            self.authentication_status = "session_expired"
            raise SessionExpiredError("Campus portal session expired. Please log in again.")
        return self.reading

    def keep_alive(self):
        self.keep_alive_calls += 1
        if self.expire_next_keep_alive:
            self.authenticated = False
            self.authentication_status = "session_expired"
            raise SessionExpiredError("Campus portal session expired. Please log in again.")

    def mark_logged_in(self):
        self.authenticated = True
        self.authentication_status = "authenticated"
        self.expire_next_fetch = False


class BrokenPortal(FakePortal):
    def fetch_reading(self, selection):
        raise RuntimeError("boom")


class FakeAlerts:
    def __init__(self):
        self.evaluations = 0
        self.session_expired_notifications = []

    def evaluate(self, reading):
        self.evaluations += 1
        return False

    def notify_session_expired(self, occurred_at):
        self.session_expired_notifications.append(occurred_at)
        return True


class BrokenAlerts(FakeAlerts):
    def evaluate(self, reading):
        self.evaluations += 1
        raise EmailDeliveryError("SMTP settings are not configured.")


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


class CollectionSchedulerSessionExpiryTests(unittest.TestCase):
    def setUp(self):
        FakeTimer.created = []
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.repository = Repository(Path(temp_dir.name) / "test.sqlite3")
        self.repository.save_room_selection(RoomSelection(building="C20", room="2324"), "2026-05-13T00:00:00Z")
        self.repository.save_schedule_config(
            ScheduleConfig(interval_seconds=3600, start_time="00:00", end_time="23:59", enabled=True),
            "2026-05-13T00:00:00Z",
        )
        self.portal = FakePortal()
        self.alerts = FakeAlerts()
        self.scheduler = CollectionScheduler(self.repository, self.portal, self.alerts, UTC)

    def test_expired_session_records_failure_and_keeps_future_schedule(self):
        self.portal.expire_next_fetch = True

        with patch("backend.app.scheduler.collection_scheduler.threading.Timer", FakeTimer):
            self.scheduler._run_and_reschedule()

        failures = self._failures()
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["error_code"], "SESSION_EXPIRED")
        self.assertEqual([timer.interval for timer in FakeTimer.created], [3600])
        self.assertTrue(FakeTimer.created[0].started)

    def test_scheduled_collection_skips_when_session_already_expired(self):
        self.portal.authenticated = False
        self.portal.authentication_status = "session_expired"

        with patch("backend.app.scheduler.collection_scheduler.threading.Timer", FakeTimer):
            self.scheduler._run_and_reschedule()

        self.assertEqual(self._failures(), [])
        self.assertEqual(self.repository.list_collection_runs(), [])
        self.assertEqual([timer.interval for timer in FakeTimer.created], [3600])

    def test_expired_enterprise_wechat_session_records_source_specific_message(self):
        self.portal.authenticated = False
        self.portal.authentication_status = "session_expired"
        self.portal.active_source = "enterprise_wechat"

        with self.assertRaises(SessionExpiredError):
            self.scheduler.run_once()

        runs = self.repository.list_collection_runs()
        self.assertEqual(runs[0]["errorCode"], "SESSION_EXPIRED")
        self.assertEqual(
            runs[0]["message"],
            "Enterprise WeChat session expired. Please import a fresh session cookie.",
        )

    def test_relogin_after_expiry_allows_collection_without_rescheduling(self):
        self.portal.authenticated = False
        self.portal.authentication_status = "session_expired"

        with self.assertRaises(SessionExpiredError):
            self.scheduler.run_once()

        runs = self.repository.list_collection_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "failed")
        self.assertEqual(runs[0]["errorCode"], "SESSION_EXPIRED")

        self.portal.mark_logged_in()
        result = self.scheduler.run_once()

        self.assertEqual(result["reading"]["numericValue"], 20.93)
        self.assertEqual(len(self.repository.list_readings()), 1)

        runs = self.repository.list_collection_runs()
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[1]["status"], "success")
        self.assertTrue(runs[1]["readingInserted"])
        self.assertEqual(self.repository.get_latest_successful_reading()["numericValue"], 20.93)

    def test_database_initializes_minimal_foundation_tables(self):
        with self.repository.connect() as conn:
            table_rows = conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name IN ('rooms', 'electricity_readings', 'collection_runs')
                """
            ).fetchall()
            reading_indexes = conn.execute("PRAGMA index_list(electricity_readings)").fetchall()

        self.assertEqual({row["name"] for row in table_rows}, {"rooms", "electricity_readings", "collection_runs"})
        self.assertIn("idx_electricity_readings_room_window", {row["name"] for row in reading_indexes})

    def test_collection_writes_reading_and_success_run(self):
        result = self.scheduler.run_once()

        self.assertEqual(result["reading"]["numericValue"], 20.93)
        self.assertEqual(len(self.repository.list_readings()), 1)
        self.assertEqual(self.portal.keep_alive_calls, 1)

        runs = self.repository.list_collection_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "success")
        self.assertTrue(runs[0]["readingInserted"])

    def test_collection_refreshes_session_before_fetch_and_records_keep_alive_expiry(self):
        self.portal.expire_next_keep_alive = True

        with self.assertRaises(SessionExpiredError):
            self.scheduler.run_once()

        self.assertEqual(self.portal.keep_alive_calls, 1)
        self.assertFalse(self.portal.authenticated)
        self.assertEqual(self.portal.authentication_status, "session_expired")
        runs = self.repository.list_collection_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "failed")
        self.assertEqual(runs[0]["errorCode"], "SESSION_EXPIRED")
        self.assertEqual(len(self.repository.list_readings()), 0)

    def test_authentication_failure_records_failed_run(self):
        self.portal.authenticated = False
        self.portal.authentication_status = "unauthenticated"

        with self.assertRaises(AuthenticationError):
            self.scheduler.run_once()

        runs = self.repository.list_collection_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "failed")
        self.assertEqual(runs[0]["errorCode"], "AUTHENTICATION_ERROR")

    def test_same_room_and_collection_window_does_not_insert_duplicate_reading(self):
        with patch.object(
            self.scheduler,
            "_collection_window_start",
            return_value="2026-05-13T00:00:00Z",
        ):
            first = self.scheduler.run_once()
            second = self.scheduler.run_once()

        self.assertFalse(first["alertSent"])
        self.assertFalse(second["alertSent"])
        self.assertEqual(len(self.repository.list_readings()), 1)
        self.assertEqual(self.alerts.evaluations, 1)

        runs = self.repository.list_collection_runs()
        self.assertEqual([run["status"] for run in runs], ["success", "duplicate"])
        self.assertEqual([run["readingInserted"] for run in runs], [True, False])

    def test_alert_failure_does_not_hide_successful_history_reading(self):
        scheduler = CollectionScheduler(self.repository, self.portal, BrokenAlerts(), UTC)

        with self.assertRaises(EmailDeliveryError):
            scheduler.run_once()

        readings = self.repository.list_readings()
        self.assertEqual(len(readings), 1)
        self.assertEqual(readings[0]["numericValue"], 20.93)

        runs = self.repository.list_collection_runs()
        self.assertEqual(runs[-1]["status"], "success")
        self.assertTrue(runs[-1]["readingInserted"])

    def test_run_once_returns_database_latest_successful_reading(self):
        self.repository.insert_reading(
            ElectricityReading(
                collected_at="2026-05-13T01:00:00Z",
                building="C20",
                room="2324",
                numeric_value=18.5,
                unit="元",
            ),
            "2026-05-13T00:00:00Z",
        )

        with patch.object(
            self.scheduler,
            "_collection_window_start",
            return_value="2026-05-13T00:00:00Z",
        ):
            result = self.scheduler.run_once()

        self.assertEqual(result["reading"]["numericValue"], 18.5)
        self.assertEqual(len(self.repository.list_readings()), 1)

        runs = self.repository.list_collection_runs()
        self.assertEqual(runs[-1]["status"], "duplicate")
        self.assertFalse(runs[-1]["readingInserted"])

    def test_collection_window_start_uses_scheduler_timezone(self):
        beijing = timezone(timedelta(hours=8), name="Asia/Shanghai")
        scheduler = CollectionScheduler(self.repository, self.portal, self.alerts, beijing)

        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                fixed_now = datetime(2026, 5, 13, 10, 17, 42, tzinfo=beijing)
                return fixed_now if tz is None else fixed_now.astimezone(tz)

        with patch("backend.app.scheduler.collection_scheduler.datetime", FixedDatetime):
            window_start = scheduler._collection_window_start()

        self.assertEqual(window_start, "2026-05-13T10:00:00+08:00")

    def test_scheduled_unexpected_failure_records_brief_status(self):
        scheduler = CollectionScheduler(self.repository, BrokenPortal(), self.alerts, UTC)

        with patch("backend.app.scheduler.collection_scheduler.threading.Timer", FakeTimer):
            scheduler._run_and_reschedule()

        failures = self._failures()
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["error_code"], "UNEXPECTED_ERROR")
        self.assertEqual(failures[0]["message"], "Unexpected collection failure.")

        runs = self.repository.list_collection_runs()
        self.assertEqual(runs[-1]["status"], "failed")
        self.assertEqual(runs[-1]["errorCode"], "UNEXPECTED_ERROR")

    def test_restart_only_schedules_collection_timer(self):
        with patch("backend.app.scheduler.collection_scheduler.threading.Timer", FakeTimer):
            self.scheduler.restart()

        self.assertEqual([timer.interval for timer in FakeTimer.created], [3600])
        self.assertTrue(FakeTimer.created[0].started)

    def test_restart_can_schedule_immediate_collection_timer(self):
        with patch("backend.app.scheduler.collection_scheduler.threading.Timer", FakeTimer):
            self.scheduler.restart(run_immediately=True)

        self.assertEqual([timer.interval for timer in FakeTimer.created], [0])
        self.assertTrue(FakeTimer.created[0].started)

    def test_session_expiry_notifies_alert_service(self):
        self.portal.authenticated = False
        self.portal.authentication_status = "session_expired"

        with self.assertRaises(SessionExpiredError):
            self.scheduler.run_once()

        self.assertEqual(len(self.alerts.session_expired_notifications), 1)

    def _failures(self):
        with self.repository.connect() as conn:
            rows = conn.execute("SELECT error_code, message FROM collection_failures ORDER BY id").fetchall()
        return rows
