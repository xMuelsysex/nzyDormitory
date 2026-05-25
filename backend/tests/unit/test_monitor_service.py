import tempfile
import unittest
from pathlib import Path

from backend.app.persistence.repository import Repository
from backend.app.services.monitor_service import MonitorService
from backend.app.services.models import ElectricityReading
from backend.app.shared.errors import AuthenticationError, ValidationError


class FakePortal:
    def __init__(self, authenticated=False):
        self.authenticated = authenticated


class FakeScheduler:
    def __init__(self):
        self.restarted = False
        self.restart_kwargs = None

    def restart(self, **kwargs):
        self.restarted = True
        self.restart_kwargs = kwargs


class MonitorServiceTests(unittest.TestCase):
    def make_service(self, authenticated=False):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        repository = Repository(Path(temp_dir.name) / "test.sqlite3")
        scheduler = FakeScheduler()
        service = MonitorService(repository, FakePortal(authenticated), scheduler)
        return service, scheduler

    def test_room_selection_requires_portal_login(self):
        service, _ = self.make_service(authenticated=False)

        with self.assertRaises(AuthenticationError):
            service.save_room({"building": "1号楼", "room": "301"})

    def test_schedule_requires_room_selection(self):
        service, _ = self.make_service(authenticated=True)

        with self.assertRaises(ValidationError):
            service.save_schedule(
                {
                    "intervalSeconds": 60,
                    "startTime": "08:00",
                    "endTime": "22:00",
                    "enabled": True,
                }
            )

    def test_schedule_restart_after_valid_config(self):
        service, scheduler = self.make_service(authenticated=True)
        service.save_room({"building": "1号楼", "room": "301"})

        service.save_schedule(
            {
                "intervalSeconds": 60,
                "startTime": "08:00",
                "endTime": "22:00",
                "enabled": True,
            }
        )

        self.assertTrue(scheduler.restarted)
        self.assertEqual(scheduler.restart_kwargs, {"run_immediately": True})

    def test_disabled_schedule_does_not_start_immediate_collection(self):
        service, scheduler = self.make_service(authenticated=True)
        service.save_room({"building": "1号楼", "room": "301"})

        service.save_schedule(
            {
                "intervalSeconds": 60,
                "startTime": "08:00",
                "endTime": "22:00",
                "enabled": False,
            }
        )

        self.assertTrue(scheduler.restarted)
        self.assertEqual(scheduler.restart_kwargs, {"run_immediately": False})

    def test_status_exposes_session_expired_authentication_state(self):
        service, _ = self.make_service(authenticated=False)
        service.portal.authentication_status = "session_expired"

        status = service.status()

        self.assertFalse(status["authenticated"])
        self.assertEqual(status["authenticationStatus"], "session_expired")

    def test_status_and_readings_expose_database_current_reading(self):
        service, _ = self.make_service(authenticated=False)
        service.repository.insert_reading(
            ElectricityReading(
                collected_at="2026-05-13T00:00:00Z",
                building="C20",
                room="2324",
                numeric_value=20.93,
                unit="元",
            ),
            "2026-05-13T00:00:00Z",
        )

        status = service.status()
        readings = service.readings()

        self.assertEqual(status["currentReading"]["numericValue"], 20.93)
        self.assertIsNone(status["lastCollectionRun"])
        self.assertEqual(readings["currentReading"]["numericValue"], 20.93)
        self.assertEqual(readings["items"][0]["numericValue"], 20.93)
        self.assertEqual(readings["page"], 1)
        self.assertEqual(readings["pageSize"], 20)
        self.assertEqual(readings["total"], 1)
        self.assertNotIn("readings", readings)

    def test_readings_are_paginated_latest_first(self):
        service, _ = self.make_service(authenticated=False)
        for hour, value in (("00", 20.0), ("01", 19.0), ("02", 18.0)):
            collected_at = f"2026-05-13T{hour}:00:00Z"
            service.repository.insert_reading(
                ElectricityReading(
                    collected_at=collected_at,
                    building="C20",
                    room="2324",
                    numeric_value=value,
                    unit="元",
                ),
                collected_at,
            )

        first_page = service.readings(page=1, page_size=2)
        second_page = service.readings(page=2, page_size=2)
        out_of_range = service.readings(page=3, page_size=2)

        self.assertEqual([item["numericValue"] for item in first_page["items"]], [18.0, 19.0])
        self.assertEqual([item["numericValue"] for item in second_page["items"]], [20.0])
        self.assertEqual(out_of_range["items"], [])
        self.assertEqual(out_of_range["total"], 3)
