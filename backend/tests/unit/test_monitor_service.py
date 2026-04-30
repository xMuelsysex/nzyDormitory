import tempfile
import unittest
from pathlib import Path

from backend.app.persistence.repository import Repository
from backend.app.services.monitor_service import MonitorService
from backend.app.shared.errors import AuthenticationError, ValidationError


class FakePortal:
    def __init__(self, authenticated=False):
        self.authenticated = authenticated


class FakeScheduler:
    def __init__(self):
        self.restarted = False

    def restart(self):
        self.restarted = True


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
