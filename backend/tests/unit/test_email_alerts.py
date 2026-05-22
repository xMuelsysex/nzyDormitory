import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.alerts.email_alerts import EmailAlertService
from backend.app.config.settings import Settings
from backend.app.persistence.repository import Repository
from backend.app.services.models import AlertConfig, ElectricityReading


def make_settings(temp_dir, smtp_host="smtp.test", smtp_from="monitor@test"):
    return Settings(
        host="127.0.0.1",
        port=8000,
        timezone="Asia/Shanghai",
        data_dir=Path(temp_dir),
        database_path=Path(temp_dir) / "test.sqlite3",
        campus_login_url="http://portal.test/Default.aspx",
        campus_electricity_url="http://portal.test/Web/Student/FeeElect.aspx",
        smtp_host=smtp_host,
        smtp_port=587,
        smtp_username="",
        smtp_password="",
        smtp_from=smtp_from,
    )


class FakeSMTP:
    sent_messages = []

    def __init__(self, host, port, timeout=15):
        self.host = host
        self.port = port
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def starttls(self):
        pass

    def login(self, username, password):
        pass

    def send_message(self, message):
        FakeSMTP.sent_messages.append(message)


class EmailAlertServiceTests(unittest.TestCase):
    def setUp(self):
        FakeSMTP.sent_messages = []
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.repository = Repository(Path(temp_dir.name) / "test.sqlite3")
        self.settings = make_settings(temp_dir.name)
        self.service = EmailAlertService(self.settings, self.repository)
        self.repository.save_alert_config(
            AlertConfig(threshold=10.0, recipient_email="owner@test", enabled=True, cooldown_seconds=3600),
            "2026-05-13T00:00:00Z",
        )

    def test_session_expired_alert_uses_independent_cooldown_state(self):
        self.repository.mark_alert_sent("2026-05-13T00:30:00Z")

        with patch("backend.app.alerts.email_alerts.smtplib.SMTP", FakeSMTP):
            sent = self.service.notify_session_expired("2026-05-13T01:00:00Z")

        self.assertTrue(sent)
        self.assertEqual(len(FakeSMTP.sent_messages), 1)
        self.assertEqual(self.repository.get_last_alert_sent_at(), "2026-05-13T00:30:00Z")
        self.assertEqual(self.repository.get_last_session_expired_alert_sent_at(), "2026-05-13T01:00:00Z")

    def test_session_expired_alert_cooldown_does_not_suppress_low_balance_alert(self):
        self.repository.mark_session_expired_alert_sent("2026-05-13T00:30:00Z")
        reading = ElectricityReading(
            collected_at="2026-05-13T01:00:00Z",
            building="C20",
            room="2324",
            numeric_value=5.0,
            unit="元",
        )

        with patch("backend.app.alerts.email_alerts.smtplib.SMTP", FakeSMTP):
            sent = self.service.evaluate(reading)

        self.assertTrue(sent)
        self.assertEqual(len(FakeSMTP.sent_messages), 1)
        self.assertEqual(self.repository.get_last_alert_sent_at(), "2026-05-13T01:00:00Z")
        self.assertEqual(self.repository.get_last_session_expired_alert_sent_at(), "2026-05-13T00:30:00Z")

    def test_session_expired_alert_skips_when_smtp_unconfigured(self):
        service = EmailAlertService(make_settings(self.settings.data_dir, smtp_host=""), self.repository)

        self.assertFalse(service.notify_session_expired("2026-05-13T01:00:00Z"))
        self.assertIsNone(self.repository.get_last_session_expired_alert_sent_at())
