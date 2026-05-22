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
        if not self._cooldown_elapsed(self.repository.get_last_alert_sent_at(), config.cooldown_seconds):
            logger.info("alert_skipped_cooldown", extra={"building": reading.building, "room": reading.room})
            return False
        self._send(config, reading)
        self.repository.mark_alert_sent(reading.collected_at)
        return True

    def notify_session_expired(self, occurred_at: str) -> bool:
        config = self.repository.get_alert_config()
        if config is None or not config.enabled:
            return False
        if not self.settings.smtp_host or not self.settings.smtp_from:
            logger.info("session_expired_alert_skipped_smtp_unconfigured")
            return False
        last_sent = self.repository.get_last_session_expired_alert_sent_at()
        if not self._cooldown_elapsed(last_sent, config.cooldown_seconds):
            logger.info("session_expired_alert_skipped_cooldown")
            return False
        try:
            self._send_session_expired(config, occurred_at)
        except EmailDeliveryError:
            logger.warning("session_expired_alert_failed", exc_info=True)
            return False
        self.repository.mark_session_expired_alert_sent(occurred_at)
        return True

    def _cooldown_elapsed(self, last_sent: str | None, cooldown_seconds: int) -> bool:
        if not last_sent:
            return True
        try:
            sent_at = datetime.fromisoformat(last_sent.replace("Z", "+00:00"))
        except ValueError:
            return True
        return datetime.now(sent_at.tzinfo) - sent_at >= timedelta(seconds=cooldown_seconds)

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
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailDeliveryError("Failed to send alert email.") from exc

    def _send_session_expired(self, config: AlertConfig, occurred_at: str) -> None:
        message = EmailMessage()
        message["Subject"] = "Dorm electricity collection paused: login expired"
        message["From"] = self.settings.smtp_from
        message["To"] = config.recipient_email
        message.set_content(
            "Campus portal login has expired, so dorm electricity collection is paused.\n\n"
            "Please open the Dorm Electricity Monitor and log in to the campus portal again.\n"
            f"Detected at: {occurred_at}\n"
        )
        try:
            with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=15) as smtp:
                smtp.starttls()
                if self.settings.smtp_username:
                    smtp.login(self.settings.smtp_username, self.settings.smtp_password)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailDeliveryError("Failed to send session expired alert email.") from exc
