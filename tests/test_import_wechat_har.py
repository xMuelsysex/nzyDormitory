import json
import unittest
from urllib.request import Request

from tools.import_wechat_har import extract_import_data, import_to_app, render_report


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def har_entry(method, url, headers=None, cookies=None, post_text="", response_headers=None, response_cookies=None):
    post_data = {"mimeType": "application/x-www-form-urlencoded", "text": post_text} if post_text else {}
    return {
        "request": {
            "method": method,
            "url": url,
            "headers": headers or [],
            "cookies": cookies or [],
            "postData": post_data,
        },
        "response": {
            "status": 200,
            "headers": response_headers or [],
            "cookies": response_cookies or [],
            "content": {"text": ""},
        },
    }


class ImportWechatHarTests(unittest.TestCase):
    def test_extracts_session_cookie_and_room_fields_from_har(self):
        data = extract_import_data(
            {
                "log": {
                    "entries": [
                        har_entry(
                            "GET",
                            "http://wx.njucm.edu.cn/work/njucm/s_card_selfhelp_elect.aspx",
                            headers=[{"name": "Cookie", "value": "student=hidden; ASP.NET_SessionId=COOKIE_SECRET"}],
                        ),
                        har_entry(
                            "POST",
                            "http://wx.njucm.edu.cn/work/njucm/card.ashx?action=selfhelp_elect_query",
                            post_text="zone=C&house=20&room=2324&electtype=1",
                        ),
                    ]
                }
            }
        )

        self.assertEqual(data.cookie_header, "ASP.NET_SessionId=COOKIE_SECRET")
        self.assertEqual(data.elect_fields, {"zone": "C", "house": "20", "room": "2324", "electtype": "1"})
        self.assertEqual(data.room_selection_payload, {"building": "C20", "room": "2324"})

    def test_extracts_session_cookie_from_set_cookie_when_request_cookie_is_absent(self):
        data = extract_import_data(
            {
                "log": {
                    "entries": [
                        har_entry(
                            "GET",
                            "http://wx.njucm.edu.cn/work/njucm/card.aspx?wid=37",
                            response_headers=[
                                {"name": "Set-Cookie", "value": "SERVERID=node; Path=/, ASP.NET_SessionId=SET_SECRET; Path=/"}
                            ],
                        )
                    ]
                }
            }
        )

        self.assertEqual(data.cookie_header, "ASP.NET_SessionId=SET_SECRET")

    def test_extracts_post_params_array_as_room_fields(self):
        payload = {
            "log": {
                "entries": [
                    {
                        "request": {
                            "method": "POST",
                            "url": "http://wx.njucm.edu.cn/work/njucm/card.ashx?action=selfhelp_elect_query",
                            "headers": [],
                            "postData": {
                                "params": [
                                    {"name": "zone", "value": "C"},
                                    {"name": "house", "value": "20"},
                                    {"name": "room", "value": "2324"},
                                    {"name": "electtype", "value": "1"},
                                ]
                            },
                        },
                        "response": {"status": 200, "headers": [], "content": {"text": ""}},
                    }
                ]
            }
        }

        data = extract_import_data(payload)

        self.assertEqual(data.room_selection_payload, {"building": "C20", "room": "2324"})

    def test_import_to_app_posts_session_then_room_without_logging_cookie(self):
        requests = []

        def fake_urlopen(request: Request, timeout):
            requests.append((request.full_url, json.loads(request.data.decode("utf-8")), timeout))
            return FakeResponse({"ok": True})

        data = extract_import_data(
            {
                "log": {
                    "entries": [
                        har_entry(
                            "POST",
                            "http://wx.njucm.edu.cn/work/njucm/card.ashx?action=selfhelp_elect_query",
                            headers=[{"name": "Cookie", "value": "ASP.NET_SessionId=COOKIE_SECRET"}],
                            post_text="zone=C&house=20&room=2324&electtype=1",
                        )
                    ]
                }
            }
        )

        summary = import_to_app(data, base_url="http://127.0.0.1:8123", device_id="device-test", urlopen_func=fake_urlopen, timeout=3)
        report = render_report(data, summary)

        self.assertTrue(summary.session_imported)
        self.assertTrue(summary.room_imported)
        self.assertEqual(requests[0][0], "http://127.0.0.1:8123/wechat/session/import")
        self.assertEqual(requests[0][1], {"cookieHeader": "ASP.NET_SessionId=COOKIE_SECRET"})
        self.assertEqual(requests[1][0], "http://127.0.0.1:8123/api/room-selection")
        self.assertEqual(requests[1][1], {"building": "C20", "room": "2324", "deviceId": "device-test"})
        self.assertNotIn("COOKIE_SECRET", report)

    def test_ignores_non_target_hosts(self):
        data = extract_import_data(
            {
                "log": {
                    "entries": [
                        har_entry(
                            "GET",
                            "http://example.test/work/njucm/card.aspx",
                            headers=[{"name": "Cookie", "value": "ASP.NET_SessionId=COOKIE_SECRET"}],
                        )
                    ]
                }
            }
        )

        self.assertIsNone(data.cookie_header)
        self.assertIsNone(data.room_selection_payload)


if __name__ == "__main__":
    unittest.main()
