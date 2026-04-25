import unittest
from datetime import timezone, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

from backend.app.config.settings import Settings


class SettingsTests(unittest.TestCase):
    def test_asia_shanghai_falls_back_to_fixed_utc8_when_tzdata_missing(self):
        settings = Settings(
            host='127.0.0.1',
            port=8000,
            timezone='Asia/Shanghai',
            data_dir=__import__('pathlib').Path('data'),
            database_path=__import__('pathlib').Path('data/test.sqlite3'),
            campus_login_url='http://example.test/login',
            campus_electricity_url='http://example.test/electricity',
            smtp_host='',
            smtp_port=587,
            smtp_username='',
            smtp_password='',
            smtp_from='',
        )
        with patch('backend.app.config.settings.ZoneInfo', side_effect=ZoneInfoNotFoundError):
            self.assertEqual(settings.zoneinfo, timezone(timedelta(hours=8), name='Asia/Shanghai'))
