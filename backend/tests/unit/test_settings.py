import unittest
from datetime import timezone, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

from backend.app.config.settings import (
    DEFAULT_CAMPUS_WEBVPN_ELECTRICITY_URL,
    DEFAULT_CAMPUS_WEBVPN_URL,
    Settings,
    load_settings,
)


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

    def test_load_settings_defaults_to_webvpn_campus_urls(self):
        with patch.dict('os.environ', {}, clear=True):
            settings = load_settings()

        self.assertEqual(settings.campus_login_url, DEFAULT_CAMPUS_WEBVPN_URL)
        self.assertEqual(settings.campus_electricity_url, DEFAULT_CAMPUS_WEBVPN_ELECTRICITY_URL)

    def test_load_settings_preserves_campus_url_overrides(self):
        with patch.dict(
            'os.environ',
            {
                'CAMPUS_LOGIN_URL': 'http://example.test/login',
                'CAMPUS_ELECTRICITY_URL': 'http://example.test/electricity',
            },
            clear=True,
        ):
            settings = load_settings()

        self.assertEqual(settings.campus_login_url, 'http://example.test/login')
        self.assertEqual(settings.campus_electricity_url, 'http://example.test/electricity')
