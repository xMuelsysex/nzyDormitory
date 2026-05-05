import errno
import unittest
from io import BytesIO
from unittest.mock import patch

from backend.app.main import DormElectricityHandler, FRONTEND_DIR
from backend.app.shared.errors import ValidationError
from backend.app.shared.http import is_client_disconnect, read_json_body, send_error


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
