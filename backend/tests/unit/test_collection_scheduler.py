import tempfile
import unittest
from datetime import UTC
from pathlib import Path
from unittest.mock import patch

from backend.app.persistence.repository import Repository
from backend.app.scheduler.collection_scheduler import CollectionScheduler
from backend.app.services.models import ElectricityReading, RoomSelection, ScheduleConfig
from backend.app.shared.errors import SessionExpiredError


class FakePortal:
    def __init__(self):
        self.authenticated = True
        self.authentication_status = "authenticated"
        self.reading = ElectricityReading(
            collected_at="2026-05-13T00:00:00Z",
            building="C20",
            room="2324",
            numeric_value=20.93,
            unit="元",
        )
        self.expire_next_fetch = False

    def fetch_reading(self, selection):
        if self.expire_next_fetch:
            self.authenticated = False
            self.authentication_status = "session_expired"
            raise SessionExpiredError("Campus portal session expired. Please log in again.")
        return self.reading

    def mark_logged_in(self):
        self.authenticated = True
        self.authentication_status = "authenticated"
        self.expire_next_fetch = False


class FakeAlerts:
    def evaluate(self, reading):
        return False


class FakeTimer:
    created = []

    def __init__(self, interval, callback):
        self.interval = interval
        self.callback = callback
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
        self.scheduler = CollectionScheduler(self.repository, self.portal, FakeAlerts(), UTC)

    def test_expired_session_records_failure_and_keeps_future_schedule(self):
        self.portal.expire_next_fetch = True

        with patch("backend.app.scheduler.collection_scheduler.threading.Timer", FakeTimer):
            self.scheduler._run_and_reschedule()

        failures = self._failures()
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["error_code"], "SESSION_EXPIRED")
        self.assertEqual(len(FakeTimer.created), 1)
        self.assertEqual(FakeTimer.created[0].interval, 3600)
        self.assertTrue(FakeTimer.created[0].started)

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

        runs = self.repository.list_collection_runs()
        self.assertEqual([run["status"] for run in runs], ["success", "duplicate"])
        self.assertEqual([run["readingInserted"] for run in runs], [True, False])

    def _failures(self):
        with self.repository.connect() as conn:
            rows = conn.execute("SELECT error_code, message FROM collection_failures ORDER BY id").fetchall()
        return rows
