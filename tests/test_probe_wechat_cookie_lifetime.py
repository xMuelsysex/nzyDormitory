import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.services.models import ElectricityReading, RoomSelection
from backend.app.shared.errors import SessionExpiredError
from tools.probe_wechat_cookie_lifetime import (
    ProbeConfig,
    load_probe_source,
    probe_once,
    render_probe_header,
    render_probe_result,
    run_probe_loop,
)


def har_entry(method, url, headers=None, post_text=""):
    return {
        "request": {
            "method": method,
            "url": url,
            "headers": headers or [],
            "cookies": [],
            "postData": {"text": post_text} if post_text else {},
        },
        "response": {"status": 200, "headers": [], "cookies": [], "content": {"text": ""}},
    }


class FakeWechatClient:
    def __init__(self):
        self.keep_alive_calls = 0
        self.fetch_calls = 0
        self.fail_keep_alive = False
        self.fail_fetch = False

    def keep_alive(self):
        self.keep_alive_calls += 1
        if self.fail_keep_alive:
            raise SessionExpiredError("expired")

    def fetch_reading(self, selection):
        self.fetch_calls += 1
        if self.fail_fetch:
            raise SessionExpiredError("expired")
        return ElectricityReading(
            collected_at="2026-05-30T00:00:00Z",
            building=selection.building,
            room=selection.room,
            numeric_value=12.3,
            unit="度",
            source="enterprise_wechat",
        )


class ProbeWechatCookieLifetimeTests(unittest.TestCase):
    def write_har(self, payload):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "capture.har"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_load_probe_source_extracts_cookie_and_room_from_har(self):
        path = self.write_har(
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

        cookie_header, selection = load_probe_source(path)

        self.assertEqual(cookie_header, "ASP.NET_SessionId=COOKIE_SECRET")
        self.assertEqual(selection, RoomSelection(building="C20", room="2324"))

    def test_load_probe_source_accepts_manual_room_override(self):
        path = self.write_har(
            {
                "log": {
                    "entries": [
                        har_entry(
                            "GET",
                            "http://wx.njucm.edu.cn/work/njucm/s_card_selfhelp_elect.aspx",
                            headers=[{"name": "Cookie", "value": "ASP.NET_SessionId=COOKIE_SECRET"}],
                        )
                    ]
                }
            }
        )

        _, selection = load_probe_source(path, building="C21", room="0101")

        self.assertEqual(selection, RoomSelection(building="C21", room="0101"))

    def test_probe_once_queries_room_when_selection_is_available(self):
        client = FakeWechatClient()

        with patch("tools.probe_wechat_cookie_lifetime.utc_now_iso", return_value="2026-05-30T00:00:00Z"):
            result = probe_once(client, RoomSelection(building="C20", room="2324"))

        self.assertTrue(result.ok)
        self.assertEqual(result.action, "query")
        self.assertIn("C20 2324", result.message)
        self.assertEqual(client.fetch_calls, 1)
        self.assertEqual(client.keep_alive_calls, 0)

    def test_probe_once_can_run_keep_alive_only(self):
        client = FakeWechatClient()

        result = probe_once(client, RoomSelection(building="C20", room="2324"), keep_alive_only=True)

        self.assertTrue(result.ok)
        self.assertEqual(result.action, "keep-alive")
        self.assertEqual(client.keep_alive_calls, 1)
        self.assertEqual(client.fetch_calls, 0)

    def test_probe_once_returns_failure_without_throwing_cookie_values(self):
        client = FakeWechatClient()
        client.fail_fetch = True

        result = probe_once(client, RoomSelection(building="C20", room="2324"))

        self.assertFalse(result.ok)
        self.assertEqual(result.action, "query")
        self.assertIn("SESSION_EXPIRED", result.message)
        self.assertNotIn("COOKIE_SECRET", result.message)

    def test_run_probe_loop_stops_after_failure(self):
        client = FakeWechatClient()
        client.fail_keep_alive = True
        outputs = []

        exit_code = run_probe_loop(
            client,
            None,
            ProbeConfig(interval_seconds=1, max_checks=3, keep_alive_only=True),
            sleep_func=lambda _: self.fail("should not sleep after failure"),
            output=outputs.append,
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(len(outputs), 1)
        self.assertIn("FAIL keep-alive", outputs[0])

    def test_render_output_is_redacted(self):
        header = render_probe_header(RoomSelection(building="C20", room="2324"), ProbeConfig(300, 1, False))
        result = render_probe_result(type("Result", (), {"checked_at": "now", "ok": True, "action": "query", "message": "ok"})())

        self.assertIn("value redacted", header)
        self.assertIn("C20 2324", header)
        self.assertEqual(result, "[now] OK query: ok")


if __name__ == "__main__":
    unittest.main()
