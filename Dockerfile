FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    DATA_DIR=/app/data

WORKDIR /app

COPY backend ./backend
COPY frontend ./frontend

RUN mkdir -p /app/data

EXPOSE 8000

CMD ["python", "-m", "backend.app.main"]
