from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from backend.app.services.models import AlertConfig, ElectricityReading, RoomSelection, ScheduleConfig
from backend.app.shared.errors import PersistenceError


class Repository:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        try:
            conn = sqlite3.connect(self.database_path)
            conn.row_factory = sqlite3.Row
            yield conn
            conn.commit()
        except sqlite3.Error as exc:
            raise PersistenceError("Database operation failed.") from exc
        finally:
            try:
                conn.close()
            except UnboundLocalError:
                pass

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS room_selection (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    building TEXT NOT NULL,
                    room TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS schedule_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    interval_seconds INTEGER NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS electricity_readings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collected_at TEXT NOT NULL,
                    building TEXT NOT NULL,
                    room TEXT NOT NULL,
                    numeric_value REAL NOT NULL,
                    unit TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS alert_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    threshold REAL NOT NULL,
                    recipient_email TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    cooldown_seconds INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS alert_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    last_alert_sent_at TEXT
                );
                CREATE TABLE IF NOT EXISTS collection_failures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    failed_at TEXT NOT NULL,
                    error_code TEXT NOT NULL,
                    message TEXT NOT NULL
                );
                """
            )

    def save_room_selection(self, selection: RoomSelection, updated_at: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "REPLACE INTO room_selection (id, building, room, updated_at) VALUES (1, ?, ?, ?)",
                (selection.building, selection.room, updated_at),
            )

    def get_room_selection(self) -> RoomSelection | None:
        with self.connect() as conn:
            row = conn.execute("SELECT building, room FROM room_selection WHERE id = 1").fetchone()
        if row is None:
            return None
        return RoomSelection(building=row["building"], room=row["room"])

    def save_schedule_config(self, config: ScheduleConfig, updated_at: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                REPLACE INTO schedule_config
                (id, interval_seconds, start_time, end_time, enabled, updated_at)
                VALUES (1, ?, ?, ?, ?, ?)
                """,
                (config.interval_seconds, config.start_time, config.end_time, int(config.enabled), updated_at),
            )

    def get_schedule_config(self) -> ScheduleConfig | None:
        with self.connect() as conn:
            row = conn.execute("SELECT interval_seconds, start_time, end_time, enabled FROM schedule_config WHERE id = 1").fetchone()
        if row is None:
            return None
        return ScheduleConfig(
            interval_seconds=row["interval_seconds"],
            start_time=row["start_time"],
            end_time=row["end_time"],
            enabled=bool(row["enabled"]),
        )

    def insert_reading(self, reading: ElectricityReading) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO electricity_readings (collected_at, building, room, numeric_value, unit)
                VALUES (?, ?, ?, ?, ?)
                """,
                (reading.collected_at, reading.building, reading.room, reading.numeric_value, reading.unit),
            )

    def list_readings(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT collected_at, building, room, numeric_value, unit
                FROM electricity_readings ORDER BY collected_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "collectedAt": row["collected_at"],
                "building": row["building"],
                "room": row["room"],
                "numericValue": row["numeric_value"],
                "unit": row["unit"],
            }
            for row in reversed(rows)
        ]

    def save_alert_config(self, config: AlertConfig, updated_at: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                REPLACE INTO alert_config
                (id, threshold, recipient_email, enabled, cooldown_seconds, updated_at)
                VALUES (1, ?, ?, ?, ?, ?)
                """,
                (config.threshold, config.recipient_email, int(config.enabled), config.cooldown_seconds, updated_at),
            )

    def get_alert_config(self) -> AlertConfig | None:
        with self.connect() as conn:
            row = conn.execute("SELECT threshold, recipient_email, enabled, cooldown_seconds FROM alert_config WHERE id = 1").fetchone()
        if row is None:
            return None
        return AlertConfig(
            threshold=row["threshold"],
            recipient_email=row["recipient_email"],
            enabled=bool(row["enabled"]),
            cooldown_seconds=row["cooldown_seconds"],
        )

    def get_last_alert_sent_at(self) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT last_alert_sent_at FROM alert_state WHERE id = 1").fetchone()
        return None if row is None else row["last_alert_sent_at"]

    def mark_alert_sent(self, sent_at: str) -> None:
        with self.connect() as conn:
            conn.execute("REPLACE INTO alert_state (id, last_alert_sent_at) VALUES (1, ?)", (sent_at,))

    def record_failure(self, failed_at: str, error_code: str, message: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO collection_failures (failed_at, error_code, message) VALUES (?, ?, ?)",
                (failed_at, error_code, message[:500]),
            )
