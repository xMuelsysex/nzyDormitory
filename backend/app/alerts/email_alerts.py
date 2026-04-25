from __future__ import annotations

from datetime import datetime, timedelta
import logging
import smtplib
from email.message import EmailMessage

from backend.app.config.settings import Settings
from backend.app.persistence.repository import Repository
from backend.app.services.models import AlertConfig, ElectricityReading
from backend.app.shared.errors import EmailDeliveryError

logger = logging.getLogger(__name__)


class EmailAlertService:
    def __init__(self, settings: Settings, repository: Repository):
        self.settings = settings
        self.repository = repository

    def evaluate(self, reading: ElectricityReading) -> bool:
        config = self.repository.get_alert_config()
        if config is None or not config.enabled:
            return False
        if reading.numeric_value >= config.threshold:
            return False
        if not self._cooldown_elapsed(config):
            logger.info("alert_skipped_cooldown", extra={"building": reading.building, "room": reading.room})
            return False
        self._send(config, reading)
        self.repository.mark_alert_sent(reading.collected_at)
        return True

    def _cooldown_elapsed(self, config: AlertConfig) -> bool:
        last_sent = self.repository.get_last_alert_sent_at()
        if not last_sent:
            return True
        try:
            sent_at = datetime.fromisoformat(last_sent.replace("Z", "+00:00"))
        except ValueError:
            return True
        return datetime.now(sent_at.tzinfo) - sent_at >= timedelta(seconds=config.cooldown_seconds)

    def _send(self, config: AlertConfig, reading: ElectricityReading) -> None:
        if not self.settings.smtp_host or not self.settings.smtp_from:
            raise EmailDeliveryError("SMTP settings are not configured.")
        message = EmailMessage()
        message["Subject"] = "Dorm electricity balance alert"
        message["From"] = self.settings.smtp_from
        message["To"] = config.recipient_email
        message.set_content(
            f"Dorm electricity value is below threshold.\n\n"
            f"Building: {reading.building}\n"
            f"Room: {reading.room}\n"
            f"Current value: {reading.numeric_value:g} {reading.unit}\n"
            f"Threshold: {config.threshold:g}\n"
            f"Collected at: {reading.collected_at}\n"
        )
        try:
            with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=15) as smtp:
                smtp.starttls()
                if self.settings.smtp_username:
                    smtp.login(self.settings.smtp_username, self.settings.smtp_password)
                smtp.send_message(message)
        except OSError as exc:
            raise EmailDeliveryError("Failed to send alert email.") from exc
