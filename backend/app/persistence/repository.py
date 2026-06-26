from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from backend.app.services.models import AlertConfig, ElectricityReading, MonitorProfile, RoomSelection, ScheduleConfig
from backend.app.shared.errors import PersistenceError

DEFAULT_DEVICE_ID = "default"


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
                CREATE TABLE IF NOT EXISTS monitor_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL UNIQUE,
                    label TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS profile_room_selection (
                    profile_id INTEGER PRIMARY KEY REFERENCES monitor_profiles(id) ON DELETE CASCADE,
                    building TEXT NOT NULL,
                    room TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS profile_schedule_config (
                    profile_id INTEGER PRIMARY KEY REFERENCES monitor_profiles(id) ON DELETE CASCADE,
                    interval_seconds INTEGER NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS profile_alert_config (
                    profile_id INTEGER PRIMARY KEY REFERENCES monitor_profiles(id) ON DELETE CASCADE,
                    threshold REAL NOT NULL,
                    recipient_email TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    cooldown_seconds INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS profile_alert_state (
                    profile_id INTEGER PRIMARY KEY REFERENCES monitor_profiles(id) ON DELETE CASCADE,
                    last_alert_sent_at TEXT,
                    last_session_expired_alert_sent_at TEXT
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
                    profile_id INTEGER REFERENCES monitor_profiles(id),
                    collection_run_id INTEGER REFERENCES collection_runs(id),
                    collection_window_start TEXT,
                    collected_at TEXT NOT NULL,
                    building TEXT NOT NULL,
                    room TEXT NOT NULL,
                    numeric_value REAL NOT NULL,
                    unit TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'campus_portal'
                );
                CREATE TABLE IF NOT EXISTS collection_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id INTEGER REFERENCES rooms(id),
                    profile_id INTEGER REFERENCES monitor_profiles(id),
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
                    last_alert_sent_at TEXT,
                    last_session_expired_alert_sent_at TEXT
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

    def get_default_profile(self) -> MonitorProfile:
        with self.connect() as conn:
            profile_id = self._ensure_profile(conn, DEFAULT_DEVICE_ID, _utc_now_iso())
            return self._profile_by_id(conn, profile_id)

    def get_or_create_profile(self, device_id: str, updated_at: str) -> MonitorProfile:
        with self.connect() as conn:
            profile_id = self._ensure_profile(conn, device_id, updated_at)
            return self._profile_by_id(conn, profile_id)

    def has_any_room_selection(self) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM profile_room_selection LIMIT 1").fetchone()
        return row is not None

    def save_room_selection(self, selection: RoomSelection, updated_at: str, profile_id: int | None = None) -> None:
        with self.connect() as conn:
            scoped_profile_id = self._resolve_profile_id(conn, profile_id, updated_at)
            self._ensure_room(conn, selection, updated_at)
            conn.execute(
                """
                INSERT INTO profile_room_selection (profile_id, building, room, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(profile_id) DO UPDATE SET
                    building = excluded.building,
                    room = excluded.room,
                    updated_at = excluded.updated_at
                """,
                (scoped_profile_id, selection.building, selection.room, updated_at),
            )
            if scoped_profile_id == self._default_profile_id(conn, updated_at):
                conn.execute(
                    "REPLACE INTO room_selection (id, building, room, updated_at) VALUES (1, ?, ?, ?)",
                    (selection.building, selection.room, updated_at),
                )

    def get_room_selection(self, profile_id: int | None = None) -> RoomSelection | None:
        with self.connect() as conn:
            scoped_profile_id = self._resolve_profile_id(conn, profile_id)
            row = conn.execute(
                "SELECT building, room FROM profile_room_selection WHERE profile_id = ?",
                (scoped_profile_id,),
            ).fetchone()
        if row is None:
            return None
        return RoomSelection(building=row["building"], room=row["room"])

    def save_schedule_config(self, config: ScheduleConfig, updated_at: str, profile_id: int | None = None) -> None:
        with self.connect() as conn:
            scoped_profile_id = self._resolve_profile_id(conn, profile_id, updated_at)
            conn.execute(
                """
                INSERT INTO profile_schedule_config
                (profile_id, interval_seconds, start_time, end_time, enabled, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id) DO UPDATE SET
                    interval_seconds = excluded.interval_seconds,
                    start_time = excluded.start_time,
                    end_time = excluded.end_time,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (scoped_profile_id, config.interval_seconds, config.start_time, config.end_time, int(config.enabled), updated_at),
            )
            if scoped_profile_id == self._default_profile_id(conn, updated_at):
                conn.execute(
                    """
                    REPLACE INTO schedule_config
                    (id, interval_seconds, start_time, end_time, enabled, updated_at)
                    VALUES (1, ?, ?, ?, ?, ?)
                    """,
                    (config.interval_seconds, config.start_time, config.end_time, int(config.enabled), updated_at),
                )

    def get_schedule_config(self, profile_id: int | None = None) -> ScheduleConfig | None:
        with self.connect() as conn:
            scoped_profile_id = self._resolve_profile_id(conn, profile_id)
            row = conn.execute(
                """
                SELECT interval_seconds, start_time, end_time, enabled
                FROM profile_schedule_config WHERE profile_id = ?
                """,
                (scoped_profile_id,),
            ).fetchone()
        if row is None:
            return None
        return self._schedule_from_row(row)

    def list_enabled_schedule_profiles(self) -> list[tuple[MonitorProfile, ScheduleConfig]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT p.id, p.device_id, p.label,
                       sc.interval_seconds, sc.start_time, sc.end_time, sc.enabled
                FROM profile_schedule_config sc
                JOIN monitor_profiles p ON p.id = sc.profile_id
                JOIN profile_room_selection rs ON rs.profile_id = p.id
                WHERE sc.enabled = 1
                ORDER BY p.id
                """
            ).fetchall()
        return [
            (
                MonitorProfile(id=int(row["id"]), device_id=row["device_id"], label=row["label"]),
                self._schedule_from_row(row),
            )
            for row in rows
        ]

    def insert_reading(
        self,
        reading: ElectricityReading,
        collection_window_start: str | None = None,
        collection_run_id: int | None = None,
        profile_id: int | None = None,
    ) -> bool:
        window_start = collection_window_start or reading.collected_at
        with self.connect() as conn:
            scoped_profile_id = self._resolve_profile_id(conn, profile_id, reading.collected_at)
            room_id = self._ensure_room(conn, RoomSelection(reading.building, reading.room), reading.collected_at)
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO electricity_readings
                (room_id, profile_id, collection_run_id, collection_window_start, collected_at, building, room, numeric_value, unit, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    room_id,
                    scoped_profile_id,
                    collection_run_id,
                    window_start,
                    reading.collected_at,
                    reading.building,
                    reading.room,
                    reading.numeric_value,
                    reading.unit,
                    reading.source,
                ),
            )
            inserted = cursor.rowcount == 1
        return inserted

    def count_readings(self, profile_id: int | None = None) -> int:
        with self.connect() as conn:
            scoped_profile_id = self._resolve_profile_id(conn, profile_id)
            row = conn.execute(
                """
                SELECT COUNT(*) AS total
                FROM electricity_readings er
                LEFT JOIN collection_runs cr ON cr.id = er.collection_run_id
                WHERE (cr.id IS NULL OR cr.status = 'success')
                  AND er.profile_id = ?
                """,
                (scoped_profile_id,),
            ).fetchone()
        return int(row["total"])

    def list_readings(self, limit: int = 200, offset: int = 0, profile_id: int | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            scoped_profile_id = self._resolve_profile_id(conn, profile_id)
            rows = conn.execute(
                """
                SELECT er.collected_at, er.building, er.room, er.numeric_value, er.unit, er.source
                FROM electricity_readings er
                LEFT JOIN collection_runs cr ON cr.id = er.collection_run_id
                WHERE (cr.id IS NULL OR cr.status = 'success')
                  AND er.profile_id = ?
                ORDER BY er.collected_at DESC, er.id DESC LIMIT ? OFFSET ?
                """,
                (scoped_profile_id, limit, offset),
            ).fetchall()
        return [self._reading_to_payload(row) for row in rows]

    def get_latest_successful_reading(self, profile_id: int | None = None) -> dict[str, Any] | None:
        with self.connect() as conn:
            scoped_profile_id = self._resolve_profile_id(conn, profile_id)
            row = conn.execute(
                """
                SELECT er.collected_at, er.building, er.room, er.numeric_value, er.unit, er.source
                FROM electricity_readings er
                LEFT JOIN collection_runs cr ON cr.id = er.collection_run_id
                WHERE (cr.id IS NULL OR cr.status = 'success')
                  AND er.profile_id = ?
                ORDER BY er.collected_at DESC, er.id DESC LIMIT 1
                """,
                (scoped_profile_id,),
            ).fetchone()
        return None if row is None else self._reading_to_payload(row)

    def save_alert_config(self, config: AlertConfig, updated_at: str, profile_id: int | None = None) -> None:
        with self.connect() as conn:
            scoped_profile_id = self._resolve_profile_id(conn, profile_id, updated_at)
            conn.execute(
                """
                INSERT INTO profile_alert_config
                (profile_id, threshold, recipient_email, enabled, cooldown_seconds, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id) DO UPDATE SET
                    threshold = excluded.threshold,
                    recipient_email = excluded.recipient_email,
                    enabled = excluded.enabled,
                    cooldown_seconds = excluded.cooldown_seconds,
                    updated_at = excluded.updated_at
                """,
                (scoped_profile_id, config.threshold, config.recipient_email, int(config.enabled), config.cooldown_seconds, updated_at),
            )
            if scoped_profile_id == self._default_profile_id(conn, updated_at):
                conn.execute(
                    """
                    REPLACE INTO alert_config
                    (id, threshold, recipient_email, enabled, cooldown_seconds, updated_at)
                    VALUES (1, ?, ?, ?, ?, ?)
                    """,
                    (config.threshold, config.recipient_email, int(config.enabled), config.cooldown_seconds, updated_at),
                )

    def get_alert_config(self, profile_id: int | None = None) -> AlertConfig | None:
        with self.connect() as conn:
            scoped_profile_id = self._resolve_profile_id(conn, profile_id)
            row = conn.execute(
                """
                SELECT threshold, recipient_email, enabled, cooldown_seconds
                FROM profile_alert_config WHERE profile_id = ?
                """,
                (scoped_profile_id,),
            ).fetchone()
        if row is None:
            return None
        return AlertConfig(
            threshold=row["threshold"],
            recipient_email=row["recipient_email"],
            enabled=bool(row["enabled"]),
            cooldown_seconds=row["cooldown_seconds"],
        )

    def get_last_alert_sent_at(self, profile_id: int | None = None) -> str | None:
        with self.connect() as conn:
            scoped_profile_id = self._resolve_profile_id(conn, profile_id)
            row = conn.execute(
                "SELECT last_alert_sent_at FROM profile_alert_state WHERE profile_id = ?",
                (scoped_profile_id,),
            ).fetchone()
        return None if row is None else row["last_alert_sent_at"]

    def mark_alert_sent(self, sent_at: str, profile_id: int | None = None) -> None:
        with self.connect() as conn:
            scoped_profile_id = self._resolve_profile_id(conn, profile_id, sent_at)
            conn.execute(
                """
                INSERT INTO profile_alert_state (profile_id, last_alert_sent_at) VALUES (?, ?)
                ON CONFLICT(profile_id) DO UPDATE SET last_alert_sent_at = excluded.last_alert_sent_at
                """,
                (scoped_profile_id, sent_at),
            )
            if scoped_profile_id == self._default_profile_id(conn, sent_at):
                conn.execute(
                    """
                    INSERT INTO alert_state (id, last_alert_sent_at) VALUES (1, ?)
                    ON CONFLICT(id) DO UPDATE SET last_alert_sent_at = excluded.last_alert_sent_at
                    """,
                    (sent_at,),
                )

    def get_last_session_expired_alert_sent_at(self, profile_id: int | None = None) -> str | None:
        with self.connect() as conn:
            scoped_profile_id = self._resolve_profile_id(conn, profile_id)
            row = conn.execute(
                "SELECT last_session_expired_alert_sent_at FROM profile_alert_state WHERE profile_id = ?",
                (scoped_profile_id,),
            ).fetchone()
        return None if row is None else row["last_session_expired_alert_sent_at"]

    def mark_session_expired_alert_sent(self, sent_at: str, profile_id: int | None = None) -> None:
        with self.connect() as conn:
            scoped_profile_id = self._resolve_profile_id(conn, profile_id, sent_at)
            conn.execute(
                """
                INSERT INTO profile_alert_state (profile_id, last_session_expired_alert_sent_at) VALUES (?, ?)
                ON CONFLICT(profile_id) DO UPDATE SET last_session_expired_alert_sent_at = excluded.last_session_expired_alert_sent_at
                """,
                (scoped_profile_id, sent_at),
            )
            if scoped_profile_id == self._default_profile_id(conn, sent_at):
                conn.execute(
                    """
                    INSERT INTO alert_state (id, last_session_expired_alert_sent_at) VALUES (1, ?)
                    ON CONFLICT(id) DO UPDATE SET last_session_expired_alert_sent_at = excluded.last_session_expired_alert_sent_at
                    """,
                    (sent_at,),
                )

    def start_collection_run(
        self,
        selection: RoomSelection,
        started_at: str,
        collection_window_start: str,
        profile_id: int | None = None,
    ) -> int:
        with self.connect() as conn:
            scoped_profile_id = self._resolve_profile_id(conn, profile_id, started_at)
            room_id = self._ensure_room(conn, selection, started_at)
            cursor = conn.execute(
                """
                INSERT INTO collection_runs (room_id, profile_id, started_at, collection_window_start, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                (room_id, scoped_profile_id, started_at, collection_window_start, "running"),
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

    def list_collection_runs(self, limit: int = 200, profile_id: int | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            scoped_profile_id = self._resolve_profile_id(conn, profile_id)
            rows = conn.execute(
                """
                SELECT cr.started_at, cr.finished_at, cr.collection_window_start, cr.status,
                       cr.error_code, cr.message, cr.reading_inserted, r.building, r.room
                FROM collection_runs cr
                LEFT JOIN rooms r ON r.id = cr.room_id
                WHERE cr.profile_id = ?
                ORDER BY cr.id DESC LIMIT ?
                """,
                (scoped_profile_id, limit),
            ).fetchall()
        return [self._collection_run_to_payload(row) for row in reversed(rows)]

    def get_latest_collection_run(self, profile_id: int | None = None) -> dict[str, Any] | None:
        with self.connect() as conn:
            scoped_profile_id = self._resolve_profile_id(conn, profile_id)
            row = conn.execute(
                """
                SELECT cr.started_at, cr.finished_at, cr.collection_window_start, cr.status,
                       cr.error_code, cr.message, cr.reading_inserted, r.building, r.room
                FROM collection_runs cr
                LEFT JOIN rooms r ON r.id = cr.room_id
                WHERE cr.profile_id = ?
                ORDER BY cr.id DESC LIMIT 1
                """,
                (scoped_profile_id,),
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
        if "profile_id" not in reading_columns:
            conn.execute("ALTER TABLE electricity_readings ADD COLUMN profile_id INTEGER REFERENCES monitor_profiles(id)")
        if "collection_run_id" not in reading_columns:
            conn.execute("ALTER TABLE electricity_readings ADD COLUMN collection_run_id INTEGER REFERENCES collection_runs(id)")
        if "collection_window_start" not in reading_columns:
            conn.execute("ALTER TABLE electricity_readings ADD COLUMN collection_window_start TEXT")
        if "source" not in reading_columns:
            conn.execute("ALTER TABLE electricity_readings ADD COLUMN source TEXT NOT NULL DEFAULT 'campus_portal'")

        run_columns = self._table_columns(conn, "collection_runs")
        if "profile_id" not in run_columns:
            conn.execute("ALTER TABLE collection_runs ADD COLUMN profile_id INTEGER REFERENCES monitor_profiles(id)")

        alert_state_columns = self._table_columns(conn, "alert_state")
        if "last_session_expired_alert_sent_at" not in alert_state_columns:
            conn.execute("ALTER TABLE alert_state ADD COLUMN last_session_expired_alert_sent_at TEXT")

        default_profile_id = self._default_profile_id(conn, _utc_now_iso())
        self._backfill_current_room(conn)
        self._backfill_default_profile_data(conn, default_profile_id)
        conn.execute("UPDATE electricity_readings SET profile_id = ? WHERE profile_id IS NULL", (default_profile_id,))
        conn.execute("UPDATE collection_runs SET profile_id = ? WHERE profile_id IS NULL", (default_profile_id,))
        conn.executescript(
            """
            DROP INDEX IF EXISTS idx_electricity_readings_room_window;
            DROP INDEX IF EXISTS idx_electricity_readings_profile_room_window;
            CREATE UNIQUE INDEX IF NOT EXISTS idx_electricity_readings_room_window
            ON electricity_readings(profile_id, room_id, collection_window_start)
            WHERE profile_id IS NOT NULL AND room_id IS NOT NULL AND collection_window_start IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_collection_runs_profile_room_window
            ON collection_runs(profile_id, room_id, collection_window_start);
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

    def _backfill_default_profile_data(self, conn: sqlite3.Connection, default_profile_id: int) -> None:
        room_row = conn.execute("SELECT building, room, updated_at FROM room_selection WHERE id = 1").fetchone()
        if room_row is not None:
            conn.execute(
                """
                INSERT OR IGNORE INTO profile_room_selection (profile_id, building, room, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (default_profile_id, room_row["building"], room_row["room"], room_row["updated_at"]),
            )

        schedule_row = conn.execute(
            "SELECT interval_seconds, start_time, end_time, enabled, updated_at FROM schedule_config WHERE id = 1"
        ).fetchone()
        if schedule_row is not None:
            conn.execute(
                """
                INSERT OR IGNORE INTO profile_schedule_config
                (profile_id, interval_seconds, start_time, end_time, enabled, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    default_profile_id,
                    schedule_row["interval_seconds"],
                    schedule_row["start_time"],
                    schedule_row["end_time"],
                    schedule_row["enabled"],
                    schedule_row["updated_at"],
                ),
            )

        alert_row = conn.execute(
            "SELECT threshold, recipient_email, enabled, cooldown_seconds, updated_at FROM alert_config WHERE id = 1"
        ).fetchone()
        if alert_row is not None:
            conn.execute(
                """
                INSERT OR IGNORE INTO profile_alert_config
                (profile_id, threshold, recipient_email, enabled, cooldown_seconds, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    default_profile_id,
                    alert_row["threshold"],
                    alert_row["recipient_email"],
                    alert_row["enabled"],
                    alert_row["cooldown_seconds"],
                    alert_row["updated_at"],
                ),
            )

        state_row = conn.execute(
            "SELECT last_alert_sent_at, last_session_expired_alert_sent_at FROM alert_state WHERE id = 1"
        ).fetchone()
        if state_row is not None:
            conn.execute(
                """
                INSERT OR IGNORE INTO profile_alert_state
                (profile_id, last_alert_sent_at, last_session_expired_alert_sent_at)
                VALUES (?, ?, ?)
                """,
                (
                    default_profile_id,
                    state_row["last_alert_sent_at"],
                    state_row["last_session_expired_alert_sent_at"],
                ),
            )

    def _resolve_profile_id(
        self,
        conn: sqlite3.Connection,
        profile_id: int | None,
        updated_at: str | None = None,
    ) -> int:
        if profile_id is not None:
            return profile_id
        return self._default_profile_id(conn, updated_at or _utc_now_iso())

    def _default_profile_id(self, conn: sqlite3.Connection, updated_at: str) -> int:
        return self._ensure_profile(conn, DEFAULT_DEVICE_ID, updated_at)

    def _ensure_profile(self, conn: sqlite3.Connection, device_id: str, updated_at: str) -> int:
        conn.execute(
            """
            INSERT INTO monitor_profiles (device_id, created_at, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET updated_at = excluded.updated_at
            """,
            (device_id, updated_at, updated_at),
        )
        row = conn.execute("SELECT id FROM monitor_profiles WHERE device_id = ?", (device_id,)).fetchone()
        return int(row["id"])

    def _profile_by_id(self, conn: sqlite3.Connection, profile_id: int) -> MonitorProfile:
        row = conn.execute(
            "SELECT id, device_id, label FROM monitor_profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
        if row is None:
            raise PersistenceError("Monitor profile not found.")
        return MonitorProfile(id=int(row["id"]), device_id=row["device_id"], label=row["label"])

    def _schedule_from_row(self, row: sqlite3.Row) -> ScheduleConfig:
        return ScheduleConfig(
            interval_seconds=row["interval_seconds"],
            start_time=row["start_time"],
            end_time=row["end_time"],
            enabled=bool(row["enabled"]),
        )

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
            "source": row["source"],
        }
