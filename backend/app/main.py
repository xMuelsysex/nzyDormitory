from __future__ import annotations

from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import logging
import mimetypes
import threading

from backend.app.alerts.email_alerts import EmailAlertService
from backend.app.config.settings import load_settings
from backend.app.integrations.campus_portal import CampusPortalClient
from backend.app.integrations.enterprise_wechat import EnterpriseWechatClient
from backend.app.integrations.source_router import ElectricitySourceRouter
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
campus_portal = CampusPortalClient(settings)
enterprise_wechat = EnterpriseWechatClient(settings)
portal = ElectricitySourceRouter(enterprise_wechat, campus_portal)
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
            elif path == "/wechat/guide":
                send_html(self, 200, build_wechat_guide_html(settings.enterprise_wechat_electricity_url))
            elif path == "/portal/login":
                params = parse_qs(parsed_url.query, keep_blank_values=True)
                if params.get("reset", [""])[-1] == "1":
                    session_keeper.stop()
                    campus_portal.reset_session()
                send_html(self, 200, campus_portal.load_login_page())
            elif path == "/portal/proxy":
                body, content_type = campus_portal.fetch_proxy_resource(self.path)
                send_bytes(self, 200, body, content_type)
            elif path.startswith("/portal/"):
                body, content_type = campus_portal.fetch_portal_path(self.path)
                send_bytes(self, 200, body, content_type)
            else:
                self._serve_static(path)
        except Exception as exc:
            send_error(self, exc)

    def do_POST(self) -> None:
        try:
            path = urlparse(self.path).path
            if path == "/portal/login":
                success, html_body = campus_portal.submit_login_page(read_form_body(self))
                if success:
                    handle_login_success(recover_if_room_selected=True)
                    send_redirect(self, "/?login=success")
                else:
                    send_html(self, 200, html_body)
                return
            payload = read_json_body(self)
            if path == "/wechat/session/import":
                enterprise_wechat.import_cookies(_required_string(payload, "cookieHeader"))
                handle_login_success(recover_if_room_selected=True)
                send_json(self, 200, {"source": "enterprise_wechat", "authenticationStatus": "authenticated"})
                return
            elif path == "/wechat/session/reset":
                enterprise_wechat.reset_session()
                handle_wechat_session_reset()
                send_json(self, 200, {"source": "enterprise_wechat", "authenticationStatus": "unauthenticated"})
                return
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


def _required_string(payload: dict[str, object], field: str) -> str:
    value = str(payload.get(field, "")).strip()
    if not value:
        raise ValidationError(f"{field} is required.")
    return value


def build_wechat_guide_html(electricity_url: str) -> str:
    safe_url = _safe_guide_target_url(electricity_url)
    guide_link_attrs = f'href="{safe_url}"'
    guide_link_text = "打开学校电费页"
    if safe_url == "#":
        guide_link_attrs = 'href="#" aria-disabled="true"'
        guide_link_text = "学校电费页配置无效"
    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>企业微信电费入口向导</title>
    <style>
      :root {{
        color-scheme: light;
        font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
        background: #f5f1e8;
        color: #26281f;
      }}
      * {{ box-sizing: border-box; }}
      body {{ margin: 0; min-height: 100vh; background: #f5f1e8; }}
      main {{
        max-width: 680px;
        margin: 0 auto;
        padding: 24px 18px 40px;
      }}
      .panel {{
        border: 1px solid rgba(76, 72, 58, .22);
        border-left: 5px solid #385342;
        border-radius: 16px;
        background: #fffaf0;
        padding: 18px;
        box-shadow: 0 12px 28px rgba(49, 44, 31, .10);
      }}
      h1 {{
        margin: 0 0 12px;
        font-size: 28px;
        line-height: 1.2;
      }}
      p, li {{ line-height: 1.7; }}
      .hint {{ color: #746f60; }}
      .actions {{
        display: grid;
        gap: 10px;
        margin: 18px 0;
      }}
      a {{
        color: #385342;
        font-weight: 800;
      }}
      .button {{
        display: block;
        width: 100%;
        min-height: 46px;
        border-radius: 10px;
        padding: 12px 14px;
        border: 1px solid #385342;
        background: #385342;
        color: #f9f5e8;
        text-align: center;
        text-decoration: none;
      }}
      .button.secondary {{
        border-color: rgba(76, 72, 58, .34);
        background: #f7f0df;
        color: #385342;
      }}
      code {{
        padding: 2px 5px;
        border-radius: 6px;
        background: #f7f0df;
        font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
      }}
    </style>
  </head>
  <body>
    <main>
      <section class="panel">
        <h1>企业微信电费入口向导</h1>
        <p>请在 Windows 企业微信里打开本页，然后点击下面按钮打开学校电费页。</p>
        <div class="actions">
          <a class="button" {guide_link_attrs}>{guide_link_text}</a>
          <a class="button secondary" href="/">返回值守台</a>
        </div>
        <p class="hint">如果学校页面显示 <code>messageerror.aspx</code>，说明学校入口或企业微信授权没有通过。</p>
        <p class="hint">本应用不能自动读取 <code>wx.njucm.edu.cn</code> 的 Cookie；后续请回到值守台使用手动 Cookie 导入或辅助工具。</p>
      </section>
    </main>
  </body>
</html>"""


def _safe_guide_target_url(electricity_url: str) -> str:
    parsed = urlparse(electricity_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "#"
    return escape(electricity_url, quote=True)


def handle_login_success(*, recover_if_room_selected: bool) -> None:
    session_keeper.restart()
    scheduler.restart()
    if recover_if_room_selected and repository.get_room_selection() is not None:
        start_background_collection()


def handle_session_restored_from_cookie(*, recover_if_schedule_enabled: bool) -> None:
    session_keeper.restart()
    if recover_if_schedule_enabled and _schedule_enabled() and repository.get_room_selection() is not None:
        start_background_collection()


def handle_wechat_session_reset() -> None:
    if not portal.authenticated:
        session_keeper.stop()
        scheduler.restart()


def start_background_collection() -> None:
    thread = threading.Thread(target=_run_background_collection, daemon=True)
    thread.start()


def _run_background_collection() -> None:
    try:
        scheduler.run_once()
    except Exception:
        logger.exception("background_recovery_collection_failed")


def initialize_portal_session() -> None:
    restored = False
    if settings.persist_portal_cookies and enterprise_wechat.verify_persisted_session():
        restored = True
    if settings.persist_portal_cookies and campus_portal.verify_persisted_session():
        restored = True
    if restored:
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
