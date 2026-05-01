from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timezone, timedelta, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    timezone: str
    data_dir: Path
    database_path: Path
    campus_login_url: str
    campus_electricity_url: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from: str

    @property
    def zoneinfo(self) -> tzinfo:
        try:
            return ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError:
            if self.timezone in {"Asia/Shanghai", "Asia/Chongqing"}:
                return timezone(timedelta(hours=8), name=self.timezone)
            raise


def load_settings() -> Settings:
    data_dir = Path(os.getenv("DATA_DIR", "data"))
    return Settings(
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "8000")),
        timezone=os.getenv("APP_TIMEZONE", "Asia/Shanghai"),
        data_dir=data_dir,
        database_path=data_dir / "dorm_electricity.sqlite3",
        campus_login_url=os.getenv("CAMPUS_LOGIN_URL", "http://10.80.34.137:92/Default.aspx"),
        campus_electricity_url=os.getenv("CAMPUS_ELECTRICITY_URL", "http://10.80.34.137:92/Web/Student/FeeElect.aspx"),
        smtp_host=os.getenv("SMTP_HOST", ""),
        smtp_port=int(os.getenv("SMTP_PORT", "587")),
        smtp_username=os.getenv("SMTP_USERNAME", ""),
        smtp_password=os.getenv("SMTP_PASSWORD", ""),
        smtp_from=os.getenv("SMTP_FROM", os.getenv("SMTP_USERNAME", "")),
    )
