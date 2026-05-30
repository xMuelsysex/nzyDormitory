import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs
from unittest.mock import patch

from backend.app.config.settings import Settings
from backend.app.integrations.enterprise_wechat import (
    EnterpriseWechatClient,
    diagnose_enterprise_wechat_response,
    map_enterprise_wechat_elect_fields,
    parse_enterprise_wechat_electricity_value,
)
from backend.app.services.models import RoomSelection
from backend.app.shared.errors import PortalParseError, SessionExpiredError


class FakeWechatResponse:
    def __init__(self, body, url="http://wx.test/work/njucm/card.aspx?wid=37", content_type="text/html; charset=utf-8"):
        self._body = body.encode("utf-8")
        self.url = url
        self._content_type = content_type
        self.headers = self

    def get(self, name, default=None):
        if name.lower() == "content-type":
            return self._content_type
        return default

    def get_content_charset(self):
        return "utf-8"

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self._body


class FakeWechatOpener:
    def __init__(self, bodies):
        self.bodies = list(bodies)
        self.requests = []

    def open(self, request, timeout=15):
        self.requests.append(request)
        body = self.bodies.pop(0) if self.bodies else ""
        return FakeWechatResponse(body, url=getattr(request, "full_url", "http://wx.test/work/njucm/card.aspx?wid=37"))


def test_settings(**overrides):
    temp_dir = tempfile.mkdtemp()
    values = {
        "host": "127.0.0.1",
        "port": 8000,
        "timezone": "Asia/Shanghai",
        "data_dir": Path(temp_dir),
        "database_path": Path(temp_dir) / "test.sqlite3",
        "campus_login_url": "http://portal.test/Default.aspx",
        "campus_electricity_url": "http://portal.test/Web/Student/FeeElect.aspx",
        "enterprise_wechat_electricity_url": "http://wx.test/work/njucm/card.aspx?wid=37",
        "smtp_host": "",
        "smtp_port": 587,
        "smtp_username": "",
        "smtp_password": "",
        "smtp_from": "",
    }
    values.update(overrides)
    return Settings(**values)


class EnterpriseWechatParserTests(unittest.TestCase):
    def test_parse_html_electricity_balance(self):
        value, unit = parse_enterprise_wechat_electricity_value("<div>宿舍电费余额：18.25 元</div>")

        self.assertEqual(value, 18.25)
        self.assertEqual(unit, "元")

    def test_parse_json_electricity_balance(self):
        value, unit = parse_enterprise_wechat_electricity_value('{"success": true, "data": {"roomMoney": "12.30元", "unit": "元"}}')

        self.assertEqual(value, 12.30)
        self.assertEqual(unit, "元")

    def test_parse_selfhelp_elect_query_message_as_degree_balance(self):
        value, unit = parse_enterprise_wechat_electricity_value(
            '{"pass": true, "message": "18.25", "bankcardbalance": "0", "cardbalance": "0", "str1": ""}',
            selfhelp_query=True,
        )

        self.assertEqual(value, 18.25)
        self.assertEqual(unit, "度")

    def test_parse_selfhelp_elect_query_message_ignores_false_pass_flag(self):
        value, unit = parse_enterprise_wechat_electricity_value(
            '{"pass": 0, "message": "18.25", "bankcardbalance": "0", "cardbalance": "0", "str1": ""}',
            selfhelp_query=True,
        )

        self.assertEqual(value, 18.25)
        self.assertEqual(unit, "度")

    def test_parse_selfhelp_elect_query_message_preserves_money_unit(self):
        value, unit = parse_enterprise_wechat_electricity_value(
            '{"pass": true, "message": "18.25元", "bankcardbalance": "0", "cardbalance": "0", "str1": ""}',
            selfhelp_query=True,
        )

        self.assertEqual(value, 18.25)
        self.assertEqual(unit, "元")

    def test_parse_selfhelp_shape_requires_query_context(self):
        with self.assertRaises(PortalParseError):
            parse_enterprise_wechat_electricity_value(
                '{"pass": true, "message": "1234567890", "bankcardbalance": "0", "cardbalance": "0", "str1": ""}'
            )

    def test_diagnose_invalid_code_as_auth_required(self):
        diagnosis = diagnose_enterprise_wechat_response("invalid code, rid: abc")

        self.assertEqual(diagnosis.kind, "auth_required")

    def test_map_enterprise_wechat_room_fields_accepts_c_building_variants(self):
        expected = {"zone": "C", "house": "20", "room": "2324", "electtype": "1"}

        self.assertEqual(map_enterprise_wechat_elect_fields(RoomSelection(building="C20", room="2324")).as_form(), expected)
        self.assertEqual(map_enterprise_wechat_elect_fields(RoomSelection(building="c20", room="2324")).as_form(), expected)
        self.assertEqual(map_enterprise_wechat_elect_fields(RoomSelection(building="20", room="2324")).as_form(), expected)

    def test_map_enterprise_wechat_room_fields_rejects_unsupported_building(self):
        with self.assertRaises(PortalParseError):
            map_enterprise_wechat_elect_fields(RoomSelection(building="A20", room="2324"))


class EnterpriseWechatClientTests(unittest.TestCase):
    def test_import_cookie_verifies_session_and_saves_authenticated_state(self):
        opener = FakeWechatOpener(["<html><body>一卡通 宿舍电费</body></html>"])
        with patch("backend.app.integrations.enterprise_wechat.build_opener", return_value=opener):
            client = EnterpriseWechatClient(test_settings(), clock=lambda: "2026-05-30T00:00:00Z")

        client.import_cookies("ASP.NET_SessionId=abc")

        self.assertTrue(client.authenticated)
        self.assertEqual(client.authentication_status, "authenticated")
        self.assertEqual(client.last_verified_at, "2026-05-30T00:00:00Z")
        self.assertIsNone(client.last_keep_alive_error)
        self.assertEqual(len(opener.requests), 1)

    def test_import_cookie_rejects_empty_unauthenticated_response(self):
        opener = FakeWechatOpener([""])
        with patch("backend.app.integrations.enterprise_wechat.build_opener", return_value=opener):
            client = EnterpriseWechatClient(test_settings())

        with self.assertRaises(SessionExpiredError):
            client.import_cookies("ASP.NET_SessionId=abc")

        self.assertFalse(client.authenticated)
        self.assertEqual(client.authentication_status, "unauthenticated")

    def test_keep_alive_records_success_timestamp_and_clears_error(self):
        opener = FakeWechatOpener(["<html><body>一卡通 宿舍电费</body></html>"])
        with patch("backend.app.integrations.enterprise_wechat.build_opener", return_value=opener):
            client = EnterpriseWechatClient(test_settings(), clock=lambda: "2026-05-30T00:05:00Z")
        client.authenticated = True
        client.last_keep_alive_error = "old error"

        client.keep_alive()

        self.assertEqual(client.last_verified_at, "2026-05-30T00:05:00Z")
        self.assertEqual(client.last_keep_alive_at, "2026-05-30T00:05:00Z")
        self.assertIsNone(client.last_keep_alive_error)

    def test_keep_alive_records_redacted_failure_status(self):
        opener = FakeWechatOpener([""])
        with patch("backend.app.integrations.enterprise_wechat.build_opener", return_value=opener):
            client = EnterpriseWechatClient(test_settings())
        client.authenticated = True

        with self.assertRaises(SessionExpiredError):
            client.keep_alive()

        self.assertEqual(client.authentication_status, "session_expired")
        self.assertIn("SESSION_EXPIRED", client.last_keep_alive_error)

    def test_status_payload_exposes_keep_alive_observability(self):
        client = EnterpriseWechatClient(test_settings(), clock=lambda: "2026-05-30T00:05:00Z")
        client.authenticated = True
        client.authentication_status = "authenticated"
        client.last_verified_at = "2026-05-30T00:04:00Z"
        client.last_keep_alive_at = "2026-05-30T00:05:00Z"

        status = client.status_payload()

        self.assertEqual(status["authenticationStatus"], "authenticated")
        self.assertEqual(status["lastVerifiedAt"], "2026-05-30T00:04:00Z")
        self.assertEqual(status["lastKeepAliveAt"], "2026-05-30T00:05:00Z")
        self.assertIsNone(status["lastKeepAliveError"])

    def test_fetch_reading_posts_verified_selfhelp_elect_query_contract(self):
        opener = FakeWechatOpener([
            "<html><body>一卡通 自助购电</body></html>",
            '{"pass": true, "message": "9.5", "bankcardbalance": "0", "cardbalance": "0", "str1": ""}',
        ])
        with patch("backend.app.integrations.enterprise_wechat.build_opener", return_value=opener):
            client = EnterpriseWechatClient(test_settings())
        client.authenticated = True

        reading = client.fetch_reading(RoomSelection(building="C20", room="2324"))

        self.assertEqual(reading.numeric_value, 9.5)
        self.assertEqual(reading.unit, "度")
        self.assertEqual(reading.source, "enterprise_wechat")
        self.assertEqual(len(opener.requests), 2)
        self.assertEqual(
            opener.requests[0].full_url,
            "http://wx.test/work/njucm/s_card_selfhelp_elect.aspx",
        )
        request = opener.requests[1]
        self.assertEqual(
            request.full_url,
            "http://wx.test/work/njucm/card.ashx?action=selfhelp_elect_query",
        )
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(
            parse_qs(request.data.decode("utf-8")),
            {"zone": ["C"], "house": ["20"], "room": ["2324"], "electtype": ["1"]},
        )

    def test_fetch_reading_parses_selfhelp_json_even_when_generic_diagnosis_unknown(self):
        opener = FakeWechatOpener([
            "<html><body>一卡通 自助购电</body></html>",
            '{"pass":1,"message":"12.3","bankcardbalance":0,"cardbalance":0}',
        ])
        with patch("backend.app.integrations.enterprise_wechat.build_opener", return_value=opener):
            client = EnterpriseWechatClient(test_settings())
        client.authenticated = True

        with patch(
            "backend.app.integrations.enterprise_wechat.diagnose_enterprise_wechat_response",
            side_effect=[
                diagnose_enterprise_wechat_response("<html><body>一卡通 自助购电</body></html>"),
                diagnose_enterprise_wechat_response("unrecognized"),
            ],
        ):
            reading = client.fetch_reading(RoomSelection(building="C20", room="2324"))

        self.assertEqual(reading.numeric_value, 12.3)
        self.assertEqual(reading.unit, "度")
        self.assertEqual(reading.source, "enterprise_wechat")

    def test_fetch_reading_accepts_numeric_building_and_preserves_room_input(self):
        opener = FakeWechatOpener([
            "<html><body>一卡通 自助购电</body></html>",
            '{"pass": true, "message": "6.75元", "bankcardbalance": "0", "cardbalance": "0", "str1": ""}',
        ])
        with patch("backend.app.integrations.enterprise_wechat.build_opener", return_value=opener):
            client = EnterpriseWechatClient(test_settings())
        client.authenticated = True

        reading = client.fetch_reading(RoomSelection(building="20", room="0324"))

        self.assertEqual(reading.numeric_value, 6.75)
        self.assertEqual(reading.unit, "元")
        self.assertEqual(reading.source, "enterprise_wechat")
        self.assertEqual(parse_qs(opener.requests[1].data.decode("utf-8"))["room"], ["0324"])

    def test_fetch_reading_rejects_unsupported_building_before_query_post(self):
        opener = FakeWechatOpener(["<html><body>一卡通 自助购电</body></html>"])
        with patch("backend.app.integrations.enterprise_wechat.build_opener", return_value=opener):
            client = EnterpriseWechatClient(test_settings())
        client.authenticated = True

        with self.assertRaises(PortalParseError):
            client.fetch_reading(RoomSelection(building="A20", room="2324"))

        self.assertEqual(opener.requests, [])

    def test_fetch_reading_treats_empty_query_response_as_session_expired(self):
        opener = FakeWechatOpener(["<html><body>一卡通 自助购电</body></html>", ""])
        with patch("backend.app.integrations.enterprise_wechat.build_opener", return_value=opener):
            client = EnterpriseWechatClient(test_settings())
        client.authenticated = True

        with self.assertRaises(SessionExpiredError):
            client.fetch_reading(RoomSelection(building="C20", room="2324"))

        self.assertFalse(client.authenticated)
        self.assertEqual(client.authentication_status, "session_expired")

    def test_fetch_reading_treats_malformed_query_json_as_parse_error(self):
        opener = FakeWechatOpener(["<html><body>一卡通 自助购电</body></html>", '{"pass":1,'])
        with patch("backend.app.integrations.enterprise_wechat.build_opener", return_value=opener):
            client = EnterpriseWechatClient(test_settings())
        client.authenticated = True

        with self.assertRaises(PortalParseError):
            client.fetch_reading(RoomSelection(building="C20", room="2324"))

        self.assertTrue(client.authenticated)

    def test_fetch_reading_accepts_false_pass_flag_when_query_message_has_numeric_balance(self):
        opener = FakeWechatOpener([
            "<html><body>一卡通 自助购电</body></html>",
            '{"pass":0,"message":"7.25","bankcardbalance":0,"cardbalance":0}',
        ])
        with patch("backend.app.integrations.enterprise_wechat.build_opener", return_value=opener):
            client = EnterpriseWechatClient(test_settings())
        client.authenticated = True

        reading = client.fetch_reading(RoomSelection(building="C20", room="2324"))

        self.assertEqual(reading.numeric_value, 7.25)
        self.assertEqual(reading.unit, "度")
        self.assertTrue(client.authenticated)
