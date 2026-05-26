from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import logging
import mimetypes
import threading

from backend.app.alerts.email_alerts import EmailAlertService
from backend.app.config.settings import load_settings
from backend.app.integrations.campus_portal import CampusPortalClient
from backend.app.persistence.repository import Repository
from backend.app.scheduler.collection_scheduler import CollectionScheduler
from backend.app.scheduler.session_keeper import SessionKeeper
from backend.app.services.monitor_service import MonitorService
from backend.app.shared.errors import ValidationError
from backend.app.shared.http import app_now_iso, read_form_body, read_json_body, send_bytes, send_error, send_html, send_json, send_redirect

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

settings = load_settings()
repository = Repository(settings.database_path)
portal = CampusPortalClient(settings)
alert_service = EmailAlertService(settings, repository)
scheduler = CollectionScheduler(repository, portal, alert_service, settings.zoneinfo)
session_keeper = SessionKeeper(portal, repository, alert_service, settings.session_keep_alive_interval_seconds)
service = MonitorService(repository, portal, scheduler)
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "src"
READINGS_PAGE_SIZES = {10, 20}


class DormElectricityHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        try:
            parsed_url = urlparse(self.path)
            path = parsed_url.path
            if path == "/api/status":
                send_json(self, 200, service.status())
            elif path == "/api/readings":
                page, page_size = parse_readings_pagination(parsed_url.query)
                send_json(self, 200, service.readings(page, page_size))
            elif path == "/portal/login":
                params = parse_qs(parsed_url.query, keep_blank_values=True)
                if params.get("reset", [""])[-1] == "1":
                    session_keeper.stop()
                    portal.reset_session()
                send_html(self, 200, portal.load_login_page())
            elif path == "/portal/proxy":
                body, content_type = portal.fetch_proxy_resource(self.path)
                send_bytes(self, 200, body, content_type)
            elif path.startswith("/portal/"):
                body, content_type = portal.fetch_portal_path(self.path)
                send_bytes(self, 200, body, content_type)
            else:
                self._serve_static(path)
        except Exception as exc:
            send_error(self, exc)

    def do_POST(self) -> None:
        try:
            path = urlparse(self.path).path
            if path == "/portal/login":
                success, html_body = portal.submit_login_page(read_form_body(self))
                if success:
                    handle_login_success(recover_if_room_selected=True)
                    send_redirect(self, "/?login=success")
                else:
                    send_html(self, 200, html_body)
                return
            payload = read_json_body(self)
            if path == "/api/room-selection":
                send_json(self, 200, service.save_room(payload))
            elif path == "/api/schedule-config":
                send_json(self, 200, service.save_schedule(payload))
            elif path == "/api/alert-config":
                send_json(self, 200, service.save_alert(payload))
            elif path == "/api/collection/run-once":
                send_json(self, 200, service.run_once())
            else:
                send_json(self, 404, {"error": {"code": "NOT_FOUND", "message": "Endpoint not found"}})
        except Exception as exc:
            send_error(self, exc)

    def log_message(self, format: str, *args: object) -> None:
        logger.info("http_request", extra={"client": self.client_address[0]})

    def _serve_static(self, path: str) -> None:
        file_path = (FRONTEND_DIR / ("index.html" if path in {"/", ""} else path.lstrip("/"))).resolve()
        frontend_root = FRONTEND_DIR.resolve()
        if not file_path.is_file() or (file_path != frontend_root and frontend_root not in file_path.parents):
            send_json(self, 404, {"error": {"code": "NOT_FOUND", "message": "File not found"}})
            return
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def parse_readings_pagination(query: str) -> tuple[int, int]:
    params = parse_qs(query, keep_blank_values=True)
    page = _positive_query_int(params, "page", 1)
    page_size = _positive_query_int(params, "pageSize", 20)
    if page_size not in READINGS_PAGE_SIZES:
        raise ValidationError("pageSize must be 10 or 20.")
    return page, page_size


def _positive_query_int(params: dict[str, list[str]], name: str, default: int) -> int:
    values = params.get(name)
    if not values:
        return default
    value = values[-1]
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValidationError(f"{name} must be a positive integer.") from exc
    if str(parsed) != value or parsed <= 0:
        raise ValidationError(f"{name} must be a positive integer.")
    return parsed


def handle_login_success(*, recover_if_room_selected: bool) -> None:
    session_keeper.restart()
    scheduler.restart()
    if recover_if_room_selected and repository.get_room_selection() is not None:
        start_background_collection()


def handle_session_restored_from_cookie(*, recover_if_schedule_enabled: bool) -> None:
    session_keeper.restart()
    if recover_if_schedule_enabled and _schedule_enabled() and repository.get_room_selection() is not None:
        start_background_collection()


def start_background_collection() -> None:
    thread = threading.Thread(target=_run_background_collection, daemon=True)
    thread.start()


def _run_background_collection() -> None:
    try:
        scheduler.run_once()
    except Exception:
        logger.exception("background_recovery_collection_failed")


def initialize_portal_session() -> None:
    if settings.persist_portal_cookies and portal.verify_persisted_session():
        handle_session_restored_from_cookie(recover_if_schedule_enabled=True)
    elif settings.persist_portal_cookies and portal.authentication_status == "session_expired":
        alert_service.notify_session_expired(app_now_iso())


def _schedule_enabled() -> bool:
    config = repository.get_schedule_config()
    return bool(config and config.enabled)


def main() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    initialize_portal_session()
    scheduler.restart()
    server = ThreadingHTTPServer((settings.host, settings.port), DormElectricityHandler)
    logger.info("server_started", extra={"host": settings.host, "port": settings.port})
    try:
        server.serve_forever()
    finally:
        session_keeper.stop()
        scheduler.stop()
        server.server_close()


if __name__ == "__main__":
    main()
