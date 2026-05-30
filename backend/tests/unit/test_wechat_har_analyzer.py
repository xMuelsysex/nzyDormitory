import json
import tempfile
import unittest
from pathlib import Path

from tools.analyze_wechat_har import analyze_har, load_har, render_markdown_report, sanitize_url


def fixture_har():
    return {
        "log": {
            "entries": [
                {
                    "request": {
                        "method": "GET",
                        "url": "http://wx.njucm.edu.cn/work/njucm/card.aspx?code=SECRET_CODE&state=STUDENT123&wid=37&name=ZhangSan",
                        "queryString": [
                            {"name": "code", "value": "SECRET_CODE"},
                            {"name": "state", "value": "STUDENT123"},
                            {"name": "wid", "value": "37"},
                        ],
                        "headers": [
                            {"name": "Cookie", "value": "ASP.NET_SessionId=COOKIE_SECRET; studentId=3200000000"},
                            {"name": "User-Agent", "value": "Mozilla/5.0 MicroMessenger wxwork wxworklocal"},
                        ],
                        "cookies": [{"name": "corp_session", "value": "COOKIE_SECRET"}],
                    },
                    "response": {
                        "status": 200,
                        "headers": [{"name": "Set-Cookie", "value": "SERVERID=SET_COOKIE_SECRET; Path=/; HttpOnly"}],
                        "cookies": [{"name": "ASP.NET_SessionId", "value": "SET_COOKIE_SECRET"}],
                        "content": {"text": "<html><body>宿舍电费余额：18.25 元</body></html>"},
                    },
                },
                {
                    "request": {
                        "method": "POST",
                        "url": "http://wx.njucm.edu.cn/work/njucm/card.ashx?room=2324&token=TOKEN_SECRET",
                        "headers": [{"name": "User-Agent", "value": "Mozilla/5.0"}],
                    },
                    "response": {
                        "status": 200,
                        "headers": [],
                        "content": {"text": '{"success": true, "roomMoney": "9.50元"}'},
                    },
                },
            ]
        }
    }


class WechatHarAnalyzerTests(unittest.TestCase):
    def test_sanitize_url_redacts_query_values(self):
        sanitized = sanitize_url("http://wx.njucm.edu.cn/work/njucm/card.aspx?code=SECRET&wid=37")

        self.assertEqual(
            sanitized,
            "http://wx.njucm.edu.cn/work/njucm/card.aspx?code=<redacted>&wid=<redacted>",
        )
        self.assertNotIn("SECRET", sanitized)

    def test_analyze_har_extracts_cookie_names_only(self):
        report = render_markdown_report(analyze_har(fixture_har()))

        self.assertIn("`ASP.NET_SessionId`", report)
        self.assertIn("`studentId`", report)
        self.assertIn("`corp_session`", report)
        self.assertIn("`SERVERID`", report)
        self.assertNotIn("COOKIE_SECRET", report)
        self.assertNotIn("SET_COOKIE_SECRET", report)

    def test_analyze_har_detects_user_agent_markers_and_query_keys(self):
        analysis = analyze_har(fixture_har())

        self.assertTrue(analysis.oauth_query_keys["code"])
        self.assertTrue(analysis.oauth_query_keys["state"])
        self.assertTrue(analysis.oauth_query_keys["wid"])
        self.assertTrue(analysis.user_agent_markers["wxwork"])
        self.assertTrue(analysis.user_agent_markers["wxworklocal"])
        self.assertTrue(analysis.user_agent_markers["MicroMessenger"])

    def test_analyze_har_reports_target_paths_and_candidate_endpoints(self):
        analysis = analyze_har(fixture_har())

        self.assertIn(
            "http://wx.njucm.edu.cn/work/njucm/card.aspx?code=<redacted>&state=<redacted>&wid=<redacted>&name=<redacted>",
            analysis.matched_requests,
        )
        self.assertIn(
            "http://wx.njucm.edu.cn/work/njucm/card.ashx?room=<redacted>&token=<redacted>",
            analysis.candidate_endpoints,
        )

    def test_analyze_har_detects_electricity_payload_without_body_output(self):
        report = render_markdown_report(analyze_har(fixture_har()))

        self.assertIn("Electricity Payload Signals", report)
        self.assertIn("numeric-pattern", report)
        self.assertNotIn("宿舍电费余额：18.25 元", report)
        self.assertNotIn("roomMoney", report)
        self.assertNotIn("9.50元", report)

    def test_load_har_accepts_json_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "capture.har"
            path.write_text(json.dumps(fixture_har()), encoding="utf-8")

            loaded = load_har(path)

        self.assertEqual(len(loaded["log"]["entries"]), 2)

    def test_load_har_accepts_utf8_bom_json_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "windows-capture.har"
            path.write_text(json.dumps(fixture_har()), encoding="utf-8-sig")

            loaded = load_har(path)

        self.assertEqual(len(loaded["log"]["entries"]), 2)


if __name__ == "__main__":
    unittest.main()
