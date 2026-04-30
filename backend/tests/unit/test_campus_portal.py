import unittest
from http.cookiejar import CookieJar

from backend.app.integrations.campus_portal import (
    CampusPortalClient,
    build_login_submission,
    diagnose_portal_response,
    import_cookie_header,
    parse_electricity_value,
    proxy_target_from_path,
    rewrite_css_urls,
    rewrite_login_page,
)
from backend.app.config.settings import Settings
from pathlib import Path
from backend.app.shared.errors import PortalParseError


class CampusPortalParserTests(unittest.TestCase):
    def test_parse_electricity_value_with_balance_label(self):
        value, unit = parse_electricity_value('<span>当前电量：12.5 度</span>')
        self.assertEqual(value, 12.5)
        self.assertEqual(unit, '度')

    def test_parse_electricity_value_raises_for_unknown_shape(self):
        with self.assertRaises(PortalParseError):
            parse_electricity_value('<html>no value</html>')

    def test_diagnose_login_page_vs_portal_home_vs_electricity_page(self):
        self.assertEqual(diagnose_portal_response('<input type="password" />').kind, 'login_page')
        self.assertEqual(diagnose_portal_response('<h1>友情提醒</h1><p>校园卡 查询</p>').kind, 'portal_home')
        self.assertEqual(diagnose_portal_response('<span>自助购电 当前电量：12.5 度</span>').kind, 'electricity_page')

    def test_login_page_detector_matches_real_field_names(self):
        self.assertEqual(diagnose_portal_response('<input name="UserPwd" type="password" /><input name="InputCode" />').kind, 'login_page')

    def test_authenticated_page_detector_does_not_accept_login_form(self):
        from backend.app.integrations.campus_portal import _looks_like_authenticated_page

        self.assertFalse(_looks_like_authenticated_page('<input name="UserPwd" type="password" /><input name="InputCode" />'))
        self.assertTrue(_looks_like_authenticated_page('<a>安全退出</a><span>业务办理</span>'))

        submission = build_login_submission(
            'http://portal.test/Default.aspx',
            '''
            <form action="Default.aspx" method="post">
              <input type="hidden" name="__VIEWSTATE" value="abc" />
              <input type="text" name="txtUserName" value="" />
              <input type="password" name="txtPassword" value="" />
              <input type="submit" name="btnLogin" value="登录" />
            </form>
            ''',
            'student',
            'secret',
        )

        self.assertEqual(submission.url, 'http://portal.test/Default.aspx')
        self.assertEqual(submission.fields['__VIEWSTATE'], 'abc')
        self.assertEqual(submission.fields['txtUserName'], 'student')
        self.assertEqual(submission.fields['txtPassword'], 'secret')
        self.assertEqual(submission.fields['btnLogin'], '登录')

    def test_import_cookie_header_adds_browser_cookies(self):
        jar = CookieJar()

        count = import_cookie_header(jar, 'ASP.NET_SessionId=abc; token=xyz', 'http://portal.test/Default.aspx')

        self.assertEqual(count, 2)
        self.assertEqual({cookie.name: cookie.value for cookie in jar}, {'ASP.NET_SessionId': 'abc', 'token': 'xyz'})

    def test_rewrite_login_page_routes_form_and_assets_through_local_proxy(self):
        page = '''
        <html><head><link href="css/login.css" rel="stylesheet" /></head>
        <body><form action="Default.aspx" method="post"><img src="images/logo.png" /><input name="u" /></form></body></html>
        '''

        rewritten = rewrite_login_page(page, 'http://portal.test/Default.aspx')

        self.assertIn('action="/portal/login"', rewritten)
        self.assertIn('method="post"', rewritten)
        self.assertIn('name="__portal_action"', rewritten)
        self.assertIn('http://portal.test/Default.aspx', rewritten)
        self.assertIn('/portal/proxy?url=http%3A%2F%2Fportal.test%2Fcss%2Flogin.css', rewritten)
        self.assertIn('/portal/proxy?url=http%3A%2F%2Fportal.test%2Fimages%2Flogo.png', rewritten)
        self.assertNotIn('请在此页面完成校园门户登录', rewritten)

    def test_rewrite_css_urls_routes_assets_through_local_proxy(self):
        rewritten = rewrite_css_urls('body{background:url(../images/bg.png)}', 'http://portal.test/css/login.css', 'http://portal.test/Default.aspx')

        self.assertIn('/portal/proxy?url=http%3A%2F%2Fportal.test%2Fimages%2Fbg.png', rewritten)

    def test_proxy_target_from_path_rejects_other_hosts(self):
        target = proxy_target_from_path('/portal/proxy?url=http%3A%2F%2Fportal.test%2Fimg.png', 'http://portal.test/Default.aspx')
        self.assertEqual(target, 'http://portal.test/img.png')
        with self.assertRaises(Exception):
            proxy_target_from_path('/portal/proxy?url=http%3A%2F%2Fevil.test%2Fimg.png', 'http://portal.test/Default.aspx')

    def test_submit_login_page_rejects_tampered_action_host(self):
        client = CampusPortalClient(Settings(
            host='127.0.0.1',
            port=8000,
            timezone='Asia/Shanghai',
            data_dir=Path('data'),
            database_path=Path('data/test.sqlite3'),
            campus_login_url='http://portal.test/Default.aspx',
            campus_electricity_url='http://portal.test/web/auths/index.aspx',
            smtp_host='',
            smtp_port=587,
            smtp_username='',
            smtp_password='',
            smtp_from='',
        ))

        with self.assertRaises(Exception):
            client.submit_login_page({
                '__portal_action': 'http://evil.test/steal',
                'UserName': 'student',
                'UserPwd': 'secret',
            })

    def test_client_keeps_submit_login_page_method_after_proxy_resource_method(self):
        client = CampusPortalClient(Settings(
            host='127.0.0.1',
            port=8000,
            timezone='Asia/Shanghai',
            data_dir=Path('data'),
            database_path=Path('data/test.sqlite3'),
            campus_login_url='http://portal.test/Default.aspx',
            campus_electricity_url='http://portal.test/web/auths/index.aspx',
            smtp_host='',
            smtp_port=587,
            smtp_username='',
            smtp_password='',
            smtp_from='',
        ))

        self.assertTrue(callable(client.submit_login_page))
