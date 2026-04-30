from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
import logging
import mimetypes

from backend.app.alerts.email_alerts import EmailAlertService
from backend.app.config.settings import load_settings
from backend.app.integrations.campus_portal import CampusPortalClient
from backend.app.persistence.repository import Repository
from backend.app.scheduler.collection_scheduler import CollectionScheduler
from backend.app.services.monitor_service import MonitorService
from backend.app.shared.http import read_form_body, read_json_body, send_bytes, send_error, send_html, send_json, send_redirect

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

settings = load_settings()
repository = Repository(settings.database_path)
portal = CampusPortalClient(settings)
alert_service = EmailAlertService(settings, repository)
scheduler = CollectionScheduler(repository, portal, alert_service, settings.zoneinfo)
service = MonitorService(repository, portal, scheduler)
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "src"


class DormElectricityHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        try:
            path = urlparse(self.path).path
            if path == "/api/status":
                send_json(self, 200, service.status())
            elif path == "/api/readings":
                send_json(self, 200, service.readings())
            elif path == "/portal/login":
                send_html(self, 200, portal.load_login_page())
            elif path == "/portal/proxy":
                body, content_type = portal.fetch_proxy_resource(self.path)
                send_bytes(self, 200, body, content_type)
            elif path.startswith("/portal/"):
                body, content_type = portal.fetch_portal_path(path)
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


def main() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    scheduler.restart()
    server = ThreadingHTTPServer((settings.host, settings.port), DormElectricityHandler)
    logger.info("server_started", extra={"host": settings.host, "port": settings.port})
    try:
        server.serve_forever()
    finally:
        scheduler.stop()
        server.server_close()


if __name__ == "__main__":
    main()
