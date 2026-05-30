import unittest

from tools.analyze_wechat_har import analyze_har_payload, render_markdown, sanitize_url


def har_entry(url, request_headers=None, response_headers=None, response_text="", status=200):
    return {
        "request": {
            "method": "GET",
            "url": url,
            "headers": request_headers or [],
        },
        "response": {
            "status": status,
            "headers": response_headers or [],
            "content": {"text": response_text},
        },
    }


class AnalyzeWechatHarTests(unittest.TestCase):
    def test_redacts_sensitive_query_values(self):
        sanitized = sanitize_url("http://wx.njucm.edu.cn/work/njucm/default.aspx?code=secret&state=abc&wid=37")

        self.assertIn("code=<redacted>", sanitized)
        self.assertIn("state=<redacted>", sanitized)
        self.assertIn("wid=<redacted>", sanitized)
        self.assertNotIn("secret", sanitized)

    def test_extracts_cookie_names_and_wechat_user_agent_markers(self):
        report = analyze_har_payload(
            {
                "log": {
                    "entries": [
                        har_entry(
                            "http://wx.njucm.edu.cn/work/njucm/card.aspx?wid=37&code=secret",
                            request_headers=[
                                {"name": "Cookie", "value": "ASP.NET_SessionId=abc; token=secret"},
                                {"name": "User-Agent", "value": "Mozilla/5.0 wxwork MicroMessenger"},
                            ],
                            response_headers=[
                                {"name": "Set-Cookie", "value": "ASP.NET_SessionId=def; path=/, user=123; path=/"},
                            ],
                            response_text="宿舍电费余额：12.3 元",
                        )
                    ]
                }
            }
        )

        self.assertEqual(report.matched_count, 1)
        self.assertEqual(report.cookie_names, ["ASP.NET_SessionId", "token"])
        self.assertEqual(report.set_cookie_names, ["ASP.NET_SessionId", "user"])
        self.assertTrue(report.user_agent_markers["MicroMessenger"])
        self.assertTrue(report.user_agent_markers["wxwork"])
        self.assertEqual(len(report.electricity_hints), 1)

        rendered = render_markdown(report)
        self.assertNotIn("secret", rendered)
        self.assertNotIn("abc", rendered)
        self.assertIn("code=<redacted>", rendered)

    def test_ignores_non_target_hosts(self):
        report = analyze_har_payload(
            {
                "log": {
                    "entries": [
                        har_entry("http://example.test/work/njucm/card.aspx?wid=37"),
                        har_entry("http://wx.njucm.edu.cn/other/card.aspx?wid=37"),
                    ]
                }
            }
        )

        self.assertEqual(report.matched_count, 0)

    def test_finds_candidate_ashx_endpoint(self):
        report = analyze_har_payload(
            {"log": {"entries": [har_entry("http://wx.njucm.edu.cn/work/njucm/card.ashx?wid=37")]}}
        )

        self.assertEqual(len(report.candidate_endpoints), 1)
        self.assertIn("card.ashx", report.candidate_endpoints[0])


if __name__ == "__main__":
    unittest.main()
