# Dorm Electricity Monitor

A lightweight full-stack web app for monitoring dorm electricity balance from the campus self-service electricity purchase system.

## Runtime

- Python 3.11+
- No third-party Python dependencies required for the initial implementation
- Designed for Ubuntu server deployment

## Quick start

```bash
python -m backend.app.main
```

Open `http://127.0.0.1:8000` in a browser.

## Environment variables

```text
APP_HOST=127.0.0.1
APP_PORT=8000
APP_TIMEZONE=Asia/Shanghai
DATA_DIR=./data
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM=
```

SMTP settings are required only when email alerts are enabled.
