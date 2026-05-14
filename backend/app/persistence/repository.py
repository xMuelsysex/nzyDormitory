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
            conn.execute("PRAGMA foreign_keys = ON")
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
                CREATE TABLE IF NOT EXISTS rooms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    building TEXT NOT NULL,
                    room TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(building, room)
                );
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
                    room_id INTEGER REFERENCES rooms(id),
                    collection_run_id INTEGER REFERENCES collection_runs(id),
                    collection_window_start TEXT,
                    collected_at TEXT NOT NULL,
                    building TEXT NOT NULL,
                    room TEXT NOT NULL,
                    numeric_value REAL NOT NULL,
                    unit TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS collection_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id INTEGER REFERENCES rooms(id),
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    collection_window_start TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_code TEXT,
                    message TEXT,
                    reading_inserted INTEGER NOT NULL DEFAULT 0
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
            self._migrate_schema(conn)

    def save_room_selection(self, selection: RoomSelection, updated_at: str) -> None:
        with self.connect() as conn:
            self._ensure_room(conn, selection, updated_at)
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

    def insert_reading(
        self,
        reading: ElectricityReading,
        collection_window_start: str | None = None,
        collection_run_id: int | None = None,
    ) -> bool:
        window_start = collection_window_start or reading.collected_at
        with self.connect() as conn:
            room_id = self._ensure_room(conn, RoomSelection(reading.building, reading.room), reading.collected_at)
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO electricity_readings
                (room_id, collection_run_id, collection_window_start, collected_at, building, room, numeric_value, unit)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    room_id,
                    collection_run_id,
                    window_start,
                    reading.collected_at,
                    reading.building,
                    reading.room,
                    reading.numeric_value,
                    reading.unit,
                ),
            )
            inserted = cursor.rowcount == 1
        return inserted

    def list_readings(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT er.collected_at, er.building, er.room, er.numeric_value, er.unit
                FROM electricity_readings er
                LEFT JOIN collection_runs cr ON cr.id = er.collection_run_id
                WHERE cr.id IS NULL OR cr.status = 'success'
                ORDER BY er.collected_at DESC, er.id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._reading_to_payload(row) for row in reversed(rows)]

    def get_latest_successful_reading(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT er.collected_at, er.building, er.room, er.numeric_value, er.unit
                FROM electricity_readings er
                LEFT JOIN collection_runs cr ON cr.id = er.collection_run_id
                WHERE cr.id IS NULL OR cr.status = 'success'
                ORDER BY er.collected_at DESC, er.id DESC LIMIT 1
                """
            ).fetchone()
        return None if row is None else self._reading_to_payload(row)

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

    def start_collection_run(self, selection: RoomSelection, started_at: str, collection_window_start: str) -> int:
        with self.connect() as conn:
            room_id = self._ensure_room(conn, selection, started_at)
            cursor = conn.execute(
                """
                INSERT INTO collection_runs (room_id, started_at, collection_window_start, status)
                VALUES (?, ?, ?, ?)
                """,
                (room_id, started_at, collection_window_start, "running"),
            )
            run_id = int(cursor.lastrowid)
        return run_id

    def finish_collection_run(
        self,
        run_id: int,
        finished_at: str,
        status: str,
        reading_inserted: bool = False,
        error_code: str | None = None,
        message: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE collection_runs
                SET finished_at = ?, status = ?, error_code = ?, message = ?, reading_inserted = ?
                WHERE id = ?
                """,
                (finished_at, status, error_code, None if message is None else message[:500], int(reading_inserted), run_id),
            )

    def list_collection_runs(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT cr.started_at, cr.finished_at, cr.collection_window_start, cr.status,
                       cr.error_code, cr.message, cr.reading_inserted, r.building, r.room
                FROM collection_runs cr
                LEFT JOIN rooms r ON r.id = cr.room_id
                ORDER BY cr.id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._collection_run_to_payload(row) for row in reversed(rows)]

    def get_latest_collection_run(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT cr.started_at, cr.finished_at, cr.collection_window_start, cr.status,
                       cr.error_code, cr.message, cr.reading_inserted, r.building, r.room
                FROM collection_runs cr
                LEFT JOIN rooms r ON r.id = cr.room_id
                ORDER BY cr.id DESC LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return self._collection_run_to_payload(row)

    def _collection_run_to_payload(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "startedAt": row["started_at"],
            "finishedAt": row["finished_at"],
            "collectionWindowStart": row["collection_window_start"],
            "status": row["status"],
            "errorCode": row["error_code"],
            "message": row["message"],
            "readingInserted": bool(row["reading_inserted"]),
            "building": row["building"],
            "room": row["room"],
        }

    def record_failure(self, failed_at: str, error_code: str, message: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO collection_failures (failed_at, error_code, message) VALUES (?, ?, ?)",
                (failed_at, error_code, message[:500]),
            )

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        reading_columns = self._table_columns(conn, "electricity_readings")
        if "room_id" not in reading_columns:
            conn.execute("ALTER TABLE electricity_readings ADD COLUMN room_id INTEGER REFERENCES rooms(id)")
        if "collection_run_id" not in reading_columns:
            conn.execute("ALTER TABLE electricity_readings ADD COLUMN collection_run_id INTEGER REFERENCES collection_runs(id)")
        if "collection_window_start" not in reading_columns:
            conn.execute("ALTER TABLE electricity_readings ADD COLUMN collection_window_start TEXT")
        self._backfill_current_room(conn)
        conn.executescript(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_electricity_readings_room_window
            ON electricity_readings(room_id, collection_window_start)
            WHERE room_id IS NOT NULL AND collection_window_start IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_collection_runs_room_window
            ON collection_runs(room_id, collection_window_start);
            """
        )

    def _table_columns(self, conn: sqlite3.Connection, table_name: str) -> set[str]:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {row["name"] for row in rows}

    def _backfill_current_room(self, conn: sqlite3.Connection) -> None:
        row = conn.execute("SELECT building, room, updated_at FROM room_selection WHERE id = 1").fetchone()
        if row is None:
            return
        self._ensure_room(conn, RoomSelection(building=row["building"], room=row["room"]), row["updated_at"])

    def _ensure_room(self, conn: sqlite3.Connection, selection: RoomSelection, updated_at: str) -> int:
        conn.execute(
            """
            INSERT INTO rooms (building, room, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(building, room) DO UPDATE SET updated_at = excluded.updated_at
            """,
            (selection.building, selection.room, updated_at, updated_at),
        )
        row = conn.execute(
            "SELECT id FROM rooms WHERE building = ? AND room = ?",
            (selection.building, selection.room),
        ).fetchone()
        return int(row["id"])

    def _reading_to_payload(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "collectedAt": row["collected_at"],
            "building": row["building"],
            "room": row["room"],
            "numericValue": row["numeric_value"],
            "unit": row["unit"],
        }
