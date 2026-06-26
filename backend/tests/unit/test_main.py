import errno
import unittest
from datetime import datetime
from io import BytesIO
from unittest.mock import Mock, patch

from backend.app.main import DormElectricityHandler, FRONTEND_DIR, build_wechat_guide_html, parse_device_id, parse_readings_pagination
from backend.app.shared.errors import ValidationError
from backend.app.shared.http import app_now_iso, is_client_disconnect, read_json_body, send_error


class StaticFileTests(unittest.TestCase):
    def test_frontend_dir_points_to_existing_index(self):
        self.assertTrue((FRONTEND_DIR / 'index.html').is_file())

    def test_root_path_serves_index_html(self):
        handler = DormElectricityHandler.__new__(DormElectricityHandler)
        handler.send_response = lambda status: setattr(handler, 'status', status)
        handler.send_header = lambda name, value: None
        handler.end_headers = lambda: None
        handler.wfile = type('Writer', (), {'write': lambda self, data: setattr(handler, 'body', data)})()

        handler._serve_static('/')

        self.assertEqual(handler.status, 200)
        self.assertIn(b'<!doctype html>', handler.body)

    def test_missing_static_file_returns_not_found(self):
        handler = DormElectricityHandler.__new__(DormElectricityHandler)
        handler.wfile = type('Writer', (), {'write': lambda self, data: setattr(handler, 'body', data)})()

        with patch('backend.app.main.send_json') as send_json:
            handler._serve_static('/missing.js')

        send_json.assert_called_once_with(handler, 404, {'error': {'code': 'NOT_FOUND', 'message': 'File not found'}})

    def test_legacy_credential_login_endpoint_is_not_exposed(self):
        handler = DormElectricityHandler.__new__(DormElectricityHandler)
        handler.path = '/api/login'

        with patch('backend.app.main.read_json_body', return_value={}), patch('backend.app.main.send_json') as send_json:
            handler.do_POST()

        send_json.assert_called_once_with(handler, 404, {'error': {'code': 'NOT_FOUND', 'message': 'Endpoint not found'}})

    def test_legacy_cookie_import_endpoint_is_not_exposed(self):
        handler = DormElectricityHandler.__new__(DormElectricityHandler)
        handler.path = '/api/session/import'

        with patch('backend.app.main.read_json_body', return_value={}), patch('backend.app.main.send_json') as send_json:
            handler.do_POST()

        send_json.assert_called_once_with(handler, 404, {'error': {'code': 'NOT_FOUND', 'message': 'Endpoint not found'}})


class WechatGuideTests(unittest.TestCase):
    def test_wechat_guide_route_serves_configured_link_and_fallback_copy(self):
        handler = DormElectricityHandler.__new__(DormElectricityHandler)
        handler.path = '/wechat/guide'
        settings = type('Settings', (), {'enterprise_wechat_electricity_url': 'http://wx.test/card.aspx?wid=37'})()

        with (
            patch('backend.app.main.settings', settings),
            patch('backend.app.main.send_html') as send_html,
        ):
            handler.do_GET()

        html = send_html.call_args.args[2]
        send_html.assert_called_once()
        self.assertEqual(send_html.call_args.args[:2], (handler, 200))
        self.assertIn('href="http://wx.test/card.aspx?wid=37"', html)
        self.assertIn('Windows 企业微信', html)
        self.assertIn('messageerror.aspx', html)
        self.assertIn('手动 Cookie 导入或辅助工具', html)
        self.assertIn('href="/"', html)

    def test_wechat_guide_html_escapes_configured_url(self):
        html = build_wechat_guide_html('http://wx.test/card.aspx?wid=37&next="bad"')

        self.assertIn('href="http://wx.test/card.aspx?wid=37&amp;next=&quot;bad&quot;"', html)
        self.assertNotIn('href="http://wx.test/card.aspx?wid=37&next="bad""', html)

    def test_wechat_guide_html_rejects_unsafe_target_scheme(self):
        html = build_wechat_guide_html('javascript:alert(1)')

        self.assertIn('href="#" aria-disabled="true"', html)
        self.assertIn('学校电费页配置无效', html)
        self.assertNotIn('javascript:alert(1)', html)


class PortalLoginFlowTests(unittest.TestCase):
    def test_reset_login_clears_session_before_loading_page(self):
        handler = DormElectricityHandler.__new__(DormElectricityHandler)
        handler.path = '/portal/login?reset=1'
        portal = Mock()
        portal.load_login_page.return_value = '<form></form>'
        session_keeper = Mock()

        with (
            patch('backend.app.main.campus_portal', portal),
            patch('backend.app.main.session_keeper', session_keeper),
            patch('backend.app.main.send_html') as send_html,
        ):
            handler.do_GET()

        session_keeper.stop.assert_called_once_with()
        portal.reset_session.assert_called_once_with()
        portal.load_login_page.assert_called_once_with()
        send_html.assert_called_once_with(handler, 200, '<form></form>')

    def test_plain_login_does_not_reset_existing_session(self):
        handler = DormElectricityHandler.__new__(DormElectricityHandler)
        handler.path = '/portal/login'
        portal = Mock()
        portal.load_login_page.return_value = '<form></form>'

        with (
            patch('backend.app.main.campus_portal', portal),
            patch('backend.app.main.send_html'),
        ):
            handler.do_GET()

        portal.reset_session.assert_not_called()
        portal.load_login_page.assert_called_once_with()

    def test_successful_login_starts_recovery_before_redirect(self):
        handler = DormElectricityHandler.__new__(DormElectricityHandler)
        handler.path = '/portal/login'
        portal = Mock()
        portal.submit_login_page.return_value = (True, '')

        with (
            patch('backend.app.main.campus_portal', portal),
            patch('backend.app.main.read_form_body', return_value={'UserName': 'student'}),
            patch('backend.app.main.handle_login_success') as handle_login_success,
            patch('backend.app.main.send_redirect') as send_redirect,
        ):
            handler.do_POST()

        handle_login_success.assert_called_once_with(recover_if_room_selected=True)
        send_redirect.assert_called_once_with(handler, '/?login=success')

    def test_cookie_restored_session_starts_keeper_and_recovery_when_schedule_enabled(self):
        repository = Mock()
        repository.get_schedule_config.return_value = type('Schedule', (), {'enabled': True})()
        repository.get_room_selection.return_value = object()
        session_keeper = Mock()

        with (
            patch('backend.app.main.session_keeper', session_keeper),
            patch('backend.app.main.repository', repository),
            patch('backend.app.main.start_background_collection') as start_background_collection,
        ):
            from backend.app.main import handle_session_restored_from_cookie
            handle_session_restored_from_cookie(recover_if_schedule_enabled=True)

        session_keeper.restart.assert_called_once_with()
        start_background_collection.assert_called_once_with()

    def test_cookie_restored_session_skips_recovery_when_schedule_disabled(self):
        repository = Mock()
        repository.get_schedule_config.return_value = type('Schedule', (), {'enabled': False})()
        repository.get_room_selection.return_value = object()
        session_keeper = Mock()

        with (
            patch('backend.app.main.session_keeper', session_keeper),
            patch('backend.app.main.repository', repository),
            patch('backend.app.main.start_background_collection') as start_background_collection,
        ):
            from backend.app.main import handle_session_restored_from_cookie
            handle_session_restored_from_cookie(recover_if_schedule_enabled=True)

        session_keeper.restart.assert_called_once_with()
        start_background_collection.assert_not_called()

    def test_wechat_cookie_import_verifies_session_and_starts_recovery(self):
        handler = DormElectricityHandler.__new__(DormElectricityHandler)
        handler.path = '/wechat/session/import'
        enterprise_wechat = Mock()

        with (
            patch('backend.app.main.enterprise_wechat', enterprise_wechat),
            patch('backend.app.main.read_json_body', return_value={'cookieHeader': 'ASP.NET_SessionId=abc'}),
            patch('backend.app.main.handle_login_success') as handle_login_success,
            patch('backend.app.main.send_json') as send_json,
        ):
            handler.do_POST()

        enterprise_wechat.import_cookies.assert_called_once_with('ASP.NET_SessionId=abc')
        handle_login_success.assert_called_once_with(recover_if_room_selected=True)
        send_json.assert_called_once_with(
            handler,
            200,
            {'source': 'enterprise_wechat', 'authenticationStatus': 'authenticated'},
        )

    def test_wechat_cookie_import_requires_cookie_header(self):
        handler = DormElectricityHandler.__new__(DormElectricityHandler)
        handler.path = '/wechat/session/import'

        with (
            patch('backend.app.main.read_json_body', return_value={}),
            patch('backend.app.main.send_error') as send_error,
        ):
            handler.do_POST()

        error = send_error.call_args.args[1]
        self.assertIsInstance(error, ValidationError)
        self.assertEqual(error.message, 'cookieHeader is required.')

    def test_wechat_session_reset_clears_only_wechat_session(self):
        handler = DormElectricityHandler.__new__(DormElectricityHandler)
        handler.path = '/wechat/session/reset'
        enterprise_wechat = Mock()

        with (
            patch('backend.app.main.enterprise_wechat', enterprise_wechat),
            patch('backend.app.main.read_json_body', return_value={}),
            patch('backend.app.main.handle_wechat_session_reset') as handle_wechat_session_reset,
            patch('backend.app.main.send_json') as send_json,
        ):
            handler.do_POST()

        enterprise_wechat.reset_session.assert_called_once_with()
        handle_wechat_session_reset.assert_called_once_with()
        send_json.assert_called_once_with(
            handler,
            200,
            {'source': 'enterprise_wechat', 'authenticationStatus': 'unauthenticated'},
        )


class ReadingsPaginationTests(unittest.TestCase):
    def test_defaults_to_first_page_and_twenty_items(self):
        self.assertEqual(parse_readings_pagination(""), (1, 20))

    def test_accepts_page_size_whitelist(self):
        self.assertEqual(parse_readings_pagination("page=2&pageSize=10"), (2, 10))

    def test_rejects_invalid_page(self):
        for query in ("page=0", "page=-1", "page=abc", "page=1.5"):
            with self.subTest(query=query), self.assertRaises(ValidationError):
                parse_readings_pagination(query)

    def test_rejects_non_whitelisted_page_size(self):
        for query in ("pageSize=0", "pageSize=15", "pageSize=abc"):
            with self.subTest(query=query), self.assertRaises(ValidationError):
                parse_readings_pagination(query)


class DeviceIdTests(unittest.TestCase):
    def test_parse_device_id_accepts_generated_browser_id(self):
        self.assertEqual(parse_device_id("deviceId=device-abc_123:xyz"), "device-abc_123:xyz")

    def test_parse_device_id_requires_value(self):
        with self.assertRaises(ValidationError) as context:
            parse_device_id("")

        self.assertEqual(context.exception.message, "deviceId is required.")

    def test_parse_device_id_rejects_control_characters(self):
        with self.assertRaises(ValidationError) as context:
            parse_device_id("deviceId=bad%0Avalue")

        self.assertEqual(context.exception.message, "deviceId is invalid.")


class JsonBodyTests(unittest.TestCase):
    def test_invalid_json_body_raises_validation_error(self):
        handler = type('Handler', (), {})()
        handler.headers = {'Content-Length': '8'}
        handler.rfile = BytesIO(b'not json')

        with self.assertRaises(ValidationError):
            read_json_body(handler)

    def test_json_array_body_raises_validation_error(self):
        handler = type('Handler', (), {})()
        handler.headers = {'Content-Length': '2'}
        handler.rfile = BytesIO(b'[]')

        with self.assertRaises(ValidationError):
            read_json_body(handler)


class AppTimeTests(unittest.TestCase):
    def test_app_now_defaults_to_beijing_time(self):
        with patch.dict('os.environ', {}, clear=True):
            timestamp = app_now_iso()

        parsed = datetime.fromisoformat(timestamp)
        self.assertEqual(parsed.utcoffset().total_seconds(), 8 * 60 * 60)
        self.assertNotIn('Z', timestamp)


class ClientDisconnectTests(unittest.TestCase):
    def test_client_disconnect_is_not_returned_as_unexpected_error(self):
        handler = type('Handler', (), {})()

        with (
            patch('backend.app.shared.http.send_json') as send_json,
            patch('backend.app.shared.http.logger.exception') as log_exception,
        ):
            send_error(handler, ConnectionAbortedError('client aborted'))

        send_json.assert_not_called()
        log_exception.assert_not_called()

    def test_error_response_write_disconnect_is_ignored(self):
        handler = type('Handler', (), {})()

        with (
            patch('backend.app.shared.http.send_json', side_effect=BrokenPipeError('closed')) as send_json,
            patch('backend.app.shared.http.logger.exception') as log_exception,
        ):
            send_error(handler, ValidationError('bad request'))

        send_json.assert_called_once()
        log_exception.assert_not_called()

    def test_client_disconnect_detects_socket_errno_variants(self):
        self.assertTrue(is_client_disconnect(OSError(errno.ECONNRESET, 'reset')))
        self.assertFalse(is_client_disconnect(OSError(errno.EINVAL, 'invalid')))
