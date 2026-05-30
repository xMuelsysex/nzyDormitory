from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any

from backend.app.shared.errors import ValidationError

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class RoomSelection:
    building: str
    room: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "RoomSelection":
        building = str(payload.get("building", "")).strip()
        room = str(payload.get("room", "")).strip()
        if not building or not room:
            raise ValidationError("Building and room are required.")
        return cls(building=building, room=room)


@dataclass(frozen=True)
class ScheduleConfig:
    interval_seconds: int
    start_time: str
    end_time: str
    enabled: bool

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ScheduleConfig":
        try:
            interval_seconds = int(payload.get("intervalSeconds", 0))
        except (TypeError, ValueError) as exc:
            raise ValidationError("Interval must be a positive integer.") from exc
        if interval_seconds <= 0:
            raise ValidationError("Interval must be greater than zero.")
        start_time = str(payload.get("startTime", "")).strip()
        end_time = str(payload.get("endTime", "")).strip()
        _parse_time(start_time, "startTime")
        _parse_time(end_time, "endTime")
        if start_time >= end_time:
            raise ValidationError("Start time must be earlier than end time.")
        return cls(
            interval_seconds=interval_seconds,
            start_time=start_time,
            end_time=end_time,
            enabled=bool(payload.get("enabled", True)),
        )


@dataclass(frozen=True)
class ElectricityReading:
    collected_at: str
    building: str
    room: str
    numeric_value: float
    unit: str = ""
    source: str = "campus_portal"


@dataclass(frozen=True)
class AlertConfig:
    threshold: float
    recipient_email: str
    enabled: bool
    cooldown_seconds: int

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "AlertConfig":
        try:
            threshold = float(payload.get("threshold", 0))
        except (TypeError, ValueError) as exc:
            raise ValidationError("Threshold must be numeric.") from exc
        recipient_email = str(payload.get("recipientEmail", "")).strip()
        if not EMAIL_RE.match(recipient_email):
            raise ValidationError("Recipient email is invalid.")
        try:
            cooldown_seconds = int(payload.get("cooldownSeconds", 3600) or 3600)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Cooldown must be an integer.") from exc
        if cooldown_seconds < 0:
            raise ValidationError("Cooldown cannot be negative.")
        return cls(
            threshold=threshold,
            recipient_email=recipient_email,
            enabled=bool(payload.get("enabled", True)),
            cooldown_seconds=cooldown_seconds,
        )


def _parse_time(value: str, field: str) -> time:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise ValidationError(f"{field} must use HH:MM format.") from exc


def schedule_to_payload(config: ScheduleConfig | None) -> dict[str, Any] | None:
    if config is None:
        return None
    return {
        "intervalSeconds": config.interval_seconds,
        "startTime": config.start_time,
        "endTime": config.end_time,
        "enabled": config.enabled,
    }


def room_to_payload(selection: RoomSelection | None) -> dict[str, Any] | None:
    if selection is None:
        return None
    return {"building": selection.building, "room": selection.room}


def alert_to_payload(config: AlertConfig | None) -> dict[str, Any] | None:
    if config is None:
        return None
    return {
        "threshold": config.threshold,
        "recipientEmail": config.recipient_email,
        "enabled": config.enabled,
        "cooldownSeconds": config.cooldown_seconds,
    }
