import tempfile
import unittest
from pathlib import Path

from backend.app.persistence.repository import DEFAULT_DEVICE_ID, Repository
from backend.app.services.models import AlertConfig, ElectricityReading, RoomSelection, ScheduleConfig


class RepositoryProfileTests(unittest.TestCase):
    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.repository = Repository(Path(temp_dir.name) / "test.sqlite3")
        self.profile_a = self.repository.get_or_create_profile("device-a", "2026-06-26T00:00:00Z")
        self.profile_b = self.repository.get_or_create_profile("device-b", "2026-06-26T00:00:00Z")

    def test_profile_scoped_configurations_do_not_overlap(self):
        self.repository.save_room_selection(
            RoomSelection(building="C20", room="2324"),
            "2026-06-26T00:00:00Z",
            profile_id=self.profile_a.id,
        )
        self.repository.save_room_selection(
            RoomSelection(building="C21", room="1234"),
            "2026-06-26T00:00:00Z",
            profile_id=self.profile_b.id,
        )
        self.repository.save_schedule_config(
            ScheduleConfig(interval_seconds=60, start_time="08:00", end_time="12:00", enabled=True),
            "2026-06-26T00:00:00Z",
            profile_id=self.profile_a.id,
        )
        self.repository.save_schedule_config(
            ScheduleConfig(interval_seconds=300, start_time="13:00", end_time="23:00", enabled=False),
            "2026-06-26T00:00:00Z",
            profile_id=self.profile_b.id,
        )
        self.repository.save_alert_config(
            AlertConfig(threshold=10.0, recipient_email="a@test", enabled=True, cooldown_seconds=3600),
            "2026-06-26T00:00:00Z",
            profile_id=self.profile_a.id,
        )
        self.repository.save_alert_config(
            AlertConfig(threshold=5.0, recipient_email="b@test", enabled=False, cooldown_seconds=7200),
            "2026-06-26T00:00:00Z",
            profile_id=self.profile_b.id,
        )

        self.assertEqual(self.repository.get_room_selection(profile_id=self.profile_a.id).room, "2324")
        self.assertEqual(self.repository.get_room_selection(profile_id=self.profile_b.id).room, "1234")
        self.assertEqual(self.repository.get_schedule_config(profile_id=self.profile_a.id).interval_seconds, 60)
        self.assertEqual(self.repository.get_schedule_config(profile_id=self.profile_b.id).interval_seconds, 300)
        self.assertEqual(self.repository.get_alert_config(profile_id=self.profile_a.id).recipient_email, "a@test")
        self.assertEqual(self.repository.get_alert_config(profile_id=self.profile_b.id).recipient_email, "b@test")

    def test_readings_are_profile_scoped_for_the_same_room_and_window(self):
        reading = ElectricityReading(
            collected_at="2026-06-26T00:00:00Z",
            building="C20",
            room="2324",
            numeric_value=9.5,
            unit="元",
        )

        self.assertTrue(self.repository.insert_reading(reading, "2026-06-26T00:00:00Z", profile_id=self.profile_a.id))
        self.assertTrue(self.repository.insert_reading(reading, "2026-06-26T00:00:00Z", profile_id=self.profile_b.id))
        self.assertFalse(self.repository.insert_reading(reading, "2026-06-26T00:00:00Z", profile_id=self.profile_a.id))

        self.assertEqual(self.repository.count_readings(profile_id=self.profile_a.id), 1)
        self.assertEqual(self.repository.count_readings(profile_id=self.profile_b.id), 1)
        self.assertEqual(self.repository.list_readings(profile_id=self.profile_a.id)[0]["numericValue"], 9.5)
        self.assertEqual(self.repository.list_readings(profile_id=self.profile_b.id)[0]["numericValue"], 9.5)

    def test_legacy_singleton_rows_backfill_to_default_profile(self):
        with self.repository.connect() as conn:
            conn.execute(
                "REPLACE INTO room_selection (id, building, room, updated_at) VALUES (1, ?, ?, ?)",
                ("C22", "4567", "2026-06-26T00:00:00Z"),
            )
            conn.execute(
                """
                REPLACE INTO schedule_config
                (id, interval_seconds, start_time, end_time, enabled, updated_at)
                VALUES (1, ?, ?, ?, ?, ?)
                """,
                (600, "06:00", "22:00", 1, "2026-06-26T00:00:00Z"),
            )
            conn.execute(
                """
                REPLACE INTO alert_config
                (id, threshold, recipient_email, enabled, cooldown_seconds, updated_at)
                VALUES (1, ?, ?, ?, ?, ?)
                """,
                (7.5, "default@test", 1, 1800, "2026-06-26T00:00:00Z"),
            )

        self.repository.initialize()
        default_profile = self.repository.get_or_create_profile(DEFAULT_DEVICE_ID, "2026-06-26T00:00:00Z")

        self.assertEqual(self.repository.get_room_selection(profile_id=default_profile.id).room, "4567")
        self.assertEqual(self.repository.get_schedule_config(profile_id=default_profile.id).interval_seconds, 600)
        self.assertEqual(self.repository.get_alert_config(profile_id=default_profile.id).recipient_email, "default@test")


if __name__ == "__main__":
    unittest.main()
