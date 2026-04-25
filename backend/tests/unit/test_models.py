import unittest

from backend.app.services.models import AlertConfig, RoomSelection, ScheduleConfig
from backend.app.shared.errors import ValidationError


class ModelValidationTests(unittest.TestCase):
    def test_room_selection_requires_building_and_room(self):
        with self.assertRaises(ValidationError):
            RoomSelection.from_payload({'building': '', 'room': '301'})

    def test_schedule_requires_positive_interval_and_ordered_time(self):
        config = ScheduleConfig.from_payload({
            'intervalSeconds': 60,
            'startTime': '08:00',
            'endTime': '22:00',
            'enabled': True,
        })
        self.assertEqual(config.interval_seconds, 60)

    def test_alert_requires_valid_email(self):
        with self.assertRaises(ValidationError):
            AlertConfig.from_payload({'threshold': 10, 'recipientEmail': 'bad-email'})
