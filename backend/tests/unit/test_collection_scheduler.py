import tempfile
import unittest
from datetime import UTC
from pathlib import Path
from unittest.mock import patch

from backend.app.persistence.repository import Repository
from backend.app.scheduler.collection_scheduler import CollectionScheduler
from backend.app.services.models import ElectricityReading, RoomSelection, ScheduleConfig
from backend.app.shared.errors import AuthenticationError


class FakePortal:
    def __init__(self):
        self.authenticated = True
        self.reading = ElectricityReading(
            collected_at="2026-05-13T00:00:00Z",
            building="C20",
            room="2324",
            numeric_value=20.93,
            unit="元",
        )

    def fetch_reading(self, selection):
        return self.reading


class FakeAlerts:
    def evaluate(self, reading):
        return False


class CollectionSchedulerPersistenceTests(unittest.TestCase):
    def setUp(self):
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

        runs = self.repository.list_collection_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "success")
        self.assertTrue(runs[0]["readingInserted"])

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

    def test_authentication_failure_records_failed_run(self):
        self.portal.authenticated = False

        with self.assertRaises(AuthenticationError):
            self.scheduler.run_once()

        runs = self.repository.list_collection_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "failed")
        self.assertEqual(runs[0]["errorCode"], "AUTHENTICATION_ERROR")
