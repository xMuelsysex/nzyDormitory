import unittest
from http.cookiejar import CookieJar

from backend.app.integrations.campus_portal import (
    CampusPortalClient,
    build_login_submission,
    build_fee_elect_query_fields,
    diagnose_portal_response,
    import_cookie_header,
    map_fee_elect_room_fields,
    parse_electricity_value,
    proxy_target_from_path,
    rewrite_css_urls,
    rewrite_html_urls,
    rewrite_login_page,
)
from backend.app.config.settings import Settings
from pathlib import Path
from backend.app.shared.errors import PortalParseError, SessionExpiredError
from backend.app.services.models import RoomSelection


class FakePortalResponse:
    def __init__(self, body, url='http://portal.test/Account.aspx', content_type='text/html; charset=utf-8'):
        self._body = body.encode('utf-8')
        self.url = url
        self._content_type = content_type
        self.headers = self

    def get(self, name, default=None):
        if name.lower() == 'content-type':
            return self._content_type
        return default

    def get_content_charset(self):
        return 'utf-8'

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self._body


class FakePortalOpener:
    def __init__(self, body):
        self.body = body

    def open(self, request, timeout=15):
        return FakePortalResponse(self.body)


class FakeSequenceOpener:
    def __init__(self, bodies):
        self.bodies = list(bodies)
        self.requests = []

    def open(self, request, timeout=15):
        self.requests.append(request)
        return FakePortalResponse(self.bodies.pop(0), url=getattr(request, 'full_url', 'http://portal.test/Web/Student/FeeElect.aspx'))


class FakeResourceOpener:
    def __init__(self, body='<html></html>', content_type='text/html; charset=utf-8', response_url=None):
        self.body = body
        self.content_type = content_type
        self.response_url = response_url
        self.requests = []

    def open(self, request, timeout=15):
        self.requests.append(request)
        return FakePortalResponse(
            self.body,
            url=self.response_url or getattr(request, 'full_url', 'http://portal.test/web/app.js?v=1'),
            content_type=self.content_type,
        )


class FakeLoginSequenceOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout=15):
        self.requests.append(request)
        body, response_url = self.responses.pop(0)
        return FakePortalResponse(body, url=response_url)


def test_settings():
    return Settings(
        host='127.0.0.1',
        port=8000,
        timezone='Asia/Shanghai',
        data_dir=Path('data'),
        database_path=Path('data/test.sqlite3'),
        campus_login_url='http://portal.test/Default.aspx',
        campus_electricity_url='http://portal.test/Web/Student/FeeElect.aspx',
        smtp_host='',
        smtp_port=587,
        smtp_username='',
        smtp_password='',
        smtp_from='',
    )


class CampusPortalParserTests(unittest.TestCase):
    def test_parse_electricity_value_with_balance_label(self):
        value, unit = parse_electricity_value('<span>当前电量：12.5 度</span>')
        self.assertEqual(value, 12.5)
        self.assertEqual(unit, '度')

    def test_parse_electricity_value_with_fee_elect_room_money_span(self):
        value, unit = parse_electricity_value('<span id="lblRoomMoney">20.93 元</span>')
        self.assertEqual(value, 20.93)
        self.assertEqual(unit, '元')

    def test_parse_electricity_value_raises_for_unknown_shape(self):
        with self.assertRaises(PortalParseError):
            parse_electricity_value('<html>no value</html>')

    def test_diagnose_login_page_vs_portal_home_vs_electricity_page(self):
        self.assertEqual(diagnose_portal_response('<input type="password" />').kind, 'login_page')
        self.assertEqual(diagnose_portal_response('<h1>友情提醒</h1><p>校园卡 查询</p>').kind, 'portal_home')
        self.assertEqual(diagnose_portal_response('<span>自助购电 当前电量：12.5 度</span>').kind, 'electricity_page')

    def test_login_page_detector_matches_real_field_names(self):
        self.assertEqual(diagnose_portal_response('<input name="UserPwd" type="password" /><input name="InputCode" />').kind, 'login_page')
        self.assertEqual(
            diagnose_portal_response(
                '<form><input type="hidden" name="__EVENTVALIDATION" value="x" />'
                '<input name="UserName" /><button>登录</button></form>',
            ).kind,
            'login_page',
        )

    def test_electricity_webforms_page_is_not_login_page(self):
        self.assertEqual(
            diagnose_portal_response(
                '<form><input type="hidden" name="__EVENTVALIDATION" value="x" />'
                '<span>自助购电</span><span id="lblRoomMoney">当前电量：12.5 度</span></form>',
            ).kind,
            'electricity_page',
        )

    def test_authenticated_page_detector_does_not_accept_login_form(self):
        from backend.app.integrations.campus_portal import _looks_like_authenticated_page, _looks_like_login_page

        self.assertFalse(_looks_like_authenticated_page('<input name="UserPwd" type="password" /><input name="InputCode" />'))
        self.assertTrue(_looks_like_authenticated_page('<a>安全退出</a><span>业务办理</span>'))
        authenticated_page = '<nav>修改密码</nav><main>账号管理中心</main><a>退出</a>'
        self.assertFalse(_looks_like_login_page(authenticated_page))
        self.assertTrue(_looks_like_authenticated_page(authenticated_page))
        self.assertTrue(_looks_like_authenticated_page('<html><head><title>管理中心</title></head><frameset></frameset></html>'))

    def test_fee_elect_building_mapping_supports_c_zone_and_numeric_house(self):
        mapped = map_fee_elect_room_fields(RoomSelection(building='C20', room='2324'))
        self.assertEqual(mapped.zone_id, '1')
        self.assertEqual(mapped.house, '20')
        self.assertEqual(mapped.room, '2324')

        mapped = map_fee_elect_room_fields(RoomSelection(building='20', room='2324'))
        self.assertEqual(mapped.zone_id, '1')
        self.assertEqual(mapped.house, '20')

        mapped = map_fee_elect_room_fields(RoomSelection(building='c20', room='2324'))
        self.assertEqual(mapped.zone_id, '1')
        self.assertEqual(mapped.house, '20')

    def test_build_fee_elect_query_fields_preserves_state_and_omits_purchase_submit(self):
        fields = build_fee_elect_query_fields(
            '''
            <form action="FeeElect.aspx" method="post">
              <input type="hidden" name="__VIEWSTATE" value="view" />
              <input type="hidden" name="__EVENTVALIDATION" value="event" />
              <input name="txtSchoolCardBalance" value="88.00" />
              <input name="FeeAmtTxt" value="" />
              <input name="btkOK" value="下一步" />
            </form>
            ''',
            RoomSelection(building='C20', room='2324'),
        )

        self.assertEqual(fields['__VIEWSTATE'], 'view')
        self.assertEqual(fields['__EVENTVALIDATION'], 'event')
        self.assertEqual(fields['txtSchoolCardBalance'], '88.00')
        self.assertEqual(fields['ZoneID'], '1')
        self.assertEqual(fields['txtHouse'], '20')
        self.assertEqual(fields['txtRoom'], '2324')
        self.assertEqual(fields['btnQuery'], '查询电量')
        self.assertEqual(fields['FeeAmtTxt'], '10')
        self.assertNotIn('btkOK', fields)

    def test_build_login_submission_preserves_hidden_fields(self):
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

    def test_rewrite_login_page_targets_login_form_when_page_has_multiple_forms(self):
        page = '''
        <html><body>
          <form action="Search.aspx" method="get"><input name="q" /></form>
          <form action=Default.aspx method=post>
            <input name="UserName" />
            <input name="UserPwd" type="password" />
            <input name="InputCode" />
          </form>
        </body></html>
        '''

        rewritten = rewrite_login_page(page, 'http://portal.test/Default.aspx')

        self.assertIn('<form action="Search.aspx" method="get">', rewritten)
        self.assertIn('<form method="post" action="/portal/login">', rewritten)
        self.assertIn('name="__portal_action" value="http://portal.test/Default.aspx"', rewritten)
        self.assertNotIn('action=Default.aspx', rewritten)
        self.assertNotIn('method=post', rewritten)

    def test_rewrite_login_page_rewrites_unquoted_asset_urls(self):
        page = '''
        <html><head><script src=js/login.js></script><link href=css/login.css rel=stylesheet></head>
        <body><form action=Default.aspx method=post><input name="UserPwd" type="password" /></form></body></html>
        '''

        rewritten = rewrite_login_page(page, 'http://portal.test/auth/index.aspx')

        self.assertIn('src="/portal/proxy?url=http%3A%2F%2Fportal.test%2Fauth%2Fjs%2Flogin.js"', rewritten)
        self.assertIn('href="/portal/proxy?url=http%3A%2F%2Fportal.test%2Fauth%2Fcss%2Flogin.css"', rewritten)

    def test_rewrite_login_page_strips_webvpn_bundle_script_and_preserves_portal_assets(self):
        page = '''
        <html>
          <head>
            <script src="/portal/proxy?url=https%3A%2F%2Fwebvpn.njucm.edu.cn%2Fwebvpn%2Fbundle.debug.js"></script>
            <script src="js/jquery1.42.min.js"></script>
            <script src="js/jquery.SuperSlide.2.1.1.js"></script>
          </head>
          <body>
            <form action=Default.aspx method=post>
              <input name="UserPwd" type="password" />
              <img src="images/logo.png" />
              <img src="ValidateCode.aspx" id="captcha" />
            </form>
          </body>
        </html>
        '''

        rewritten = rewrite_login_page(page, 'https://webvpn.njucm.edu.cn/http/webvpn-token/Default.aspx')

        self.assertNotIn('bundle.debug.js', rewritten)
        self.assertIn('%2Fhttp%2Fwebvpn-token%2Fjs%2Fjquery1.42.min.js', rewritten)
        self.assertIn('%2Fhttp%2Fwebvpn-token%2Fjs%2Fjquery.SuperSlide.2.1.1.js', rewritten)
        self.assertIn('%2Fhttp%2Fwebvpn-token%2Fimages%2Flogo.png', rewritten)
        self.assertIn('%2Fhttp%2Fwebvpn-token%2FValidateCode.aspx', rewritten)

    def test_rewrite_html_urls_strips_absolute_and_root_relative_webvpn_bundle_scripts(self):
        page = '''
        <html>
          <head>
            <script src="/webvpn/bundle.debug.js"></script>
            <script src="https://webvpn.njucm.edu.cn/webvpn/bundle.js"></script>
            <script src="/webvpn/bundle_v2.js"></script>
            <script src="/js/login.js"></script>
          </head>
          <body><img src="/ValidateCode.aspx" /></body>
        </html>
        '''

        rewritten = rewrite_html_urls(page, 'https://webvpn.njucm.edu.cn/http/webvpn-token/Default.aspx')

        self.assertNotIn('/webvpn/bundle', rewritten)
        self.assertNotIn('bundle.debug.js', rewritten)
        self.assertNotIn('bundle_v2.js', rewritten)
        self.assertIn('%2Fhttp%2Fwebvpn-token%2Fjs%2Flogin.js', rewritten)
        self.assertIn('%2Fhttp%2Fwebvpn-token%2FValidateCode.aspx', rewritten)

    def test_rewrite_login_page_preserves_webvpn_gateway_prefix_for_root_relative_urls(self):
        page = '''
        <html><head><script src=/web/auths/login.js></script></head>
        <body><form action=/web/auths/Default.aspx method=post><input name="UserPwd" type="password" /></form></body></html>
        '''
        login_url = 'https://webvpn.test/http/webvpn-token/web/auths/index.aspx'

        rewritten = rewrite_login_page(page, login_url)

        self.assertIn(
            'src="/portal/proxy?url=https%3A%2F%2Fwebvpn.test%2Fhttp%2Fwebvpn-token%2Fweb%2Fauths%2Flogin.js"',
            rewritten,
        )
        self.assertIn(
            'name="__portal_action" value="https://webvpn.test/http/webvpn-token/web/auths/Default.aspx"',
            rewritten,
        )

    def test_rewrite_login_page_maps_njucm_webvpn_root_assets_to_login_app(self):
        page = '''
        <html>
          <head>
            <script src=/js/jquery-3.4.1.min.js></script>
            <link href="/themes/default/login.css" rel="stylesheet" />
            <link href=/favicon.ico rel="icon" />
          </head>
          <body>
            <form action="/login?service=https%3A%2F%2Fexample.test" method=post>
              <input name="username" />
              <input name="password" type="password" />
            </form>
          </body>
        </html>
        '''
        login_url = 'https://webvpn.njucm.edu.cn/http/webvpn34f6d2940beaaa8a549e2c772ae7c064/web/auths/index.aspx'

        rewritten = rewrite_login_page(page, login_url)

        self.assertIn(
            'src="/portal/proxy?url=https%3A%2F%2Fwebvpn.njucm.edu.cn%2Fhttp%2Fwebvpn34f6d2940beaaa8a549e2c772ae7c064%2Fweb%2Fauths%2Fjs%2Fjquery-3.4.1.min.js"',
            rewritten,
        )
        self.assertIn(
            'href="/portal/proxy?url=https%3A%2F%2Fwebvpn.njucm.edu.cn%2Fhttp%2Fwebvpn34f6d2940beaaa8a549e2c772ae7c064%2Fweb%2Fauths%2Fthemes%2Fdefault%2Flogin.css"',
            rewritten,
        )
        self.assertIn(
            'href="/portal/proxy?url=https%3A%2F%2Fwebvpn.njucm.edu.cn%2Fhttp%2Fwebvpn34f6d2940beaaa8a549e2c772ae7c064%2Fweb%2Fauths%2Ffavicon.ico"',
            rewritten,
        )
        self.assertIn(
            'name="__portal_action" value="https://webvpn.njucm.edu.cn/http/webvpn34f6d2940beaaa8a549e2c772ae7c064/login?service=https%3A%2F%2Fexample.test"',
            rewritten,
        )
        self.assertNotIn('%2Fhttp%2Fwebvpn34f6d2940beaaa8a549e2c772ae7c064%2Fjs%2Fjquery-3.4.1.min.js', rewritten)

    def test_client_rewrites_webvpn_login_redirect_against_final_ids_page(self):
        page = '''
        <html>
          <head>
            <script src=/js/jquery-3.4.1.min.js></script>
            <link href=/themes/sudy_default/images/pc/logoNew.png rel=preload />
            <link href=/favicon.ico rel=icon />
          </head>
          <body>
            <form action="/login?service=https://webvpn.njucm.edu.cn/enlink/api/client/callback/cas" method=post>
              <input name="username" />
              <input name="password" type="password" />
            </form>
          </body>
        </html>
        '''
        settings = test_settings()
        settings = Settings(
            host=settings.host,
            port=settings.port,
            timezone=settings.timezone,
            data_dir=settings.data_dir,
            database_path=settings.database_path,
            campus_login_url='https://webvpn.njucm.edu.cn/http/webvpn34f6d2940beaaa8a549e2c772ae7c064/web/auths/index.aspx',
            campus_electricity_url=settings.campus_electricity_url,
            smtp_host=settings.smtp_host,
            smtp_port=settings.smtp_port,
            smtp_username=settings.smtp_username,
            smtp_password=settings.smtp_password,
            smtp_from=settings.smtp_from,
        )
        client = CampusPortalClient(settings)
        client.opener = FakeResourceOpener(
            page,
            response_url='https://ids.njucm.edu.cn/login?service=https://webvpn.njucm.edu.cn/enlink/api/client/callback/cas',
        )

        rewritten = client.load_login_page()

        self.assertIn('/portal/proxy?url=https%3A%2F%2Fids.njucm.edu.cn%2Fjs%2Fjquery-3.4.1.min.js', rewritten)
        self.assertIn('/portal/proxy?url=https%3A%2F%2Fids.njucm.edu.cn%2Fthemes%2Fsudy_default%2Fimages%2Fpc%2FlogoNew.png', rewritten)
        self.assertIn('/portal/proxy?url=https%3A%2F%2Fids.njucm.edu.cn%2Ffavicon.ico', rewritten)
        self.assertIn(
            'name="__portal_action" value="https://ids.njucm.edu.cn/login?service=https://webvpn.njucm.edu.cn/enlink/api/client/callback/cas"',
            rewritten,
        )

        client.opener = FakeResourceOpener('console.log("ok")', content_type='application/javascript')
        body, content_type = client.fetch_proxy_resource(
            '/portal/proxy?url=https%3A%2F%2Fids.njucm.edu.cn%2Fjs%2Fjquery-3.4.1.min.js',
        )

        self.assertEqual(body, b'console.log("ok")')
        self.assertEqual(content_type, 'application/javascript')
        self.assertEqual(getattr(client.opener.requests[0], 'full_url'), 'https://ids.njucm.edu.cn/js/jquery-3.4.1.min.js')
        with self.assertRaises(Exception):
            client.fetch_proxy_resource('/portal/proxy?url=https%3A%2F%2Fevil.test%2Fjs%2Fjquery.js')

    def test_rewrite_login_page_maps_njucm_webvpn_cas_root_assets_to_ids_host(self):
        page = '''
        <html>
          <head>
            <script src=/js/jquery-3.4.1.min.js></script>
            <link href="/themes/sudy_default/login.css" rel="stylesheet" />
            <link href=/images/logo.png rel=preload />
            <link href=/favicon.ico rel=icon />
          </head>
          <body>
            <form action="/login?service=https://webvpn.njucm.edu.cn/enlink/api/client/callback/cas" method=post>
              <input name="username" />
              <input name="password" type="password" />
            </form>
          </body>
        </html>
        '''
        login_url = 'https://webvpn.njucm.edu.cn/login?service=https://webvpn.njucm.edu.cn/enlink/api/client/callback/cas'

        rewritten = rewrite_login_page(page, login_url)

        self.assertIn('/portal/proxy?url=https%3A%2F%2Fids.njucm.edu.cn%2Fjs%2Fjquery-3.4.1.min.js', rewritten)
        self.assertIn('/portal/proxy?url=https%3A%2F%2Fids.njucm.edu.cn%2Fthemes%2Fsudy_default%2Flogin.css', rewritten)
        self.assertIn('/portal/proxy?url=https%3A%2F%2Fids.njucm.edu.cn%2Fimages%2Flogo.png', rewritten)
        self.assertIn('/portal/proxy?url=https%3A%2F%2Fids.njucm.edu.cn%2Ffavicon.ico', rewritten)
        self.assertIn(
            'name="__portal_action" value="https://webvpn.njucm.edu.cn/login?service=https://webvpn.njucm.edu.cn/enlink/api/client/callback/cas"',
            rewritten,
        )
        self.assertNotIn('https%3A%2F%2Fwebvpn.njucm.edu.cn%2Fjs%2Fjquery-3.4.1.min.js', rewritten)

    def test_client_allows_njucm_ids_assets_after_webvpn_cas_login_response(self):
        page = '''
        <html>
          <head><script src=/js/jquery-3.4.1.min.js></script></head>
          <body>
            <form action="/login?service=https://webvpn.njucm.edu.cn/enlink/api/client/callback/cas" method=post>
              <input name="username" />
              <input name="password" type="password" />
            </form>
          </body>
        </html>
        '''
        settings = test_settings()
        settings = Settings(
            host=settings.host,
            port=settings.port,
            timezone=settings.timezone,
            data_dir=settings.data_dir,
            database_path=settings.database_path,
            campus_login_url='https://webvpn.njucm.edu.cn/http/webvpn-token/web/auths/index.aspx',
            campus_electricity_url=settings.campus_electricity_url,
            smtp_host=settings.smtp_host,
            smtp_port=settings.smtp_port,
            smtp_username=settings.smtp_username,
            smtp_password=settings.smtp_password,
            smtp_from=settings.smtp_from,
        )
        client = CampusPortalClient(settings)
        client.opener = FakeResourceOpener(
            page,
            response_url='https://webvpn.njucm.edu.cn/login?service=https://webvpn.njucm.edu.cn/enlink/api/client/callback/cas',
        )

        rewritten = client.load_login_page()

        self.assertIn('/portal/proxy?url=https%3A%2F%2Fids.njucm.edu.cn%2Fjs%2Fjquery-3.4.1.min.js', rewritten)

        client.opener = FakeResourceOpener('console.log("ok")', content_type='application/javascript')
        body, content_type = client.fetch_proxy_resource(
            '/portal/proxy?url=https%3A%2F%2Fids.njucm.edu.cn%2Fjs%2Fjquery-3.4.1.min.js',
        )

        self.assertEqual(body, b'console.log("ok")')
        self.assertEqual(content_type, 'application/javascript')
        self.assertEqual(getattr(client.opener.requests[0], 'full_url'), 'https://ids.njucm.edu.cn/js/jquery-3.4.1.min.js')
        with self.assertRaises(Exception):
            client.fetch_proxy_resource('/portal/proxy?url=https%3A%2F%2Fids.njucm.edu.cn%2Flogin')

    def test_rewrite_css_urls_routes_assets_through_local_proxy(self):
        rewritten = rewrite_css_urls('body{background:url(../images/bg.png)}', 'http://portal.test/css/login.css', 'http://portal.test/Default.aspx')

        self.assertIn('/portal/proxy?url=http%3A%2F%2Fportal.test%2Fimages%2Fbg.png', rewritten)

    def test_rewrite_css_urls_preserves_webvpn_gateway_prefix_for_root_relative_assets(self):
        rewritten = rewrite_css_urls(
            'body{background:url(/web/auths/images/bg.png)}',
            'https://webvpn.test/http/webvpn-token/web/auths/login.css',
            'https://webvpn.test/http/webvpn-token/web/auths/index.aspx',
        )

        self.assertIn(
            '/portal/proxy?url=https%3A%2F%2Fwebvpn.test%2Fhttp%2Fwebvpn-token%2Fweb%2Fauths%2Fimages%2Fbg.png',
            rewritten,
        )

    def test_rewrite_css_urls_maps_njucm_webvpn_root_assets_to_stylesheet_app(self):
        rewritten = rewrite_css_urls(
            'body{background:url(/themes/default/bg.png)}',
            'https://webvpn.njucm.edu.cn/http/webvpn34f6d2940beaaa8a549e2c772ae7c064/web/auths/login.css',
            'https://webvpn.njucm.edu.cn/http/webvpn34f6d2940beaaa8a549e2c772ae7c064/web/auths/index.aspx',
        )

        self.assertIn(
            '/portal/proxy?url=https%3A%2F%2Fwebvpn.njucm.edu.cn%2Fhttp%2Fwebvpn34f6d2940beaaa8a549e2c772ae7c064%2Fweb%2Fauths%2Fthemes%2Fdefault%2Fbg.png',
            rewritten,
        )

    def test_proxy_target_from_path_rejects_other_hosts(self):
        target = proxy_target_from_path('/portal/proxy?url=http%3A%2F%2Fportal.test%2Fimg.png', 'http://portal.test/Default.aspx')
        self.assertEqual(target, 'http://portal.test/img.png')
        with self.assertRaises(Exception):
            proxy_target_from_path('/portal/proxy?url=http%3A%2F%2Fevil.test%2Fimg.png', 'http://portal.test/Default.aspx')
        with self.assertRaises(Exception):
            proxy_target_from_path('/portal/proxy?url=ftp%3A%2F%2Fportal.test%2Fimg.png', 'http://portal.test/Default.aspx')

    def test_submit_login_page_rejects_tampered_action_host(self):
        client = CampusPortalClient(test_settings())

        with self.assertRaises(Exception):
            client.submit_login_page({
                '__portal_action': 'http://evil.test/steal',
                'UserName': 'student',
                'UserPwd': 'secret',
            })

    def test_submit_login_page_accepts_authenticated_account_page_with_password_menu(self):
        client = CampusPortalClient(test_settings())
        client.opener = FakePortalOpener('<nav>修改密码</nav><main>账号管理中心</main><a>退出</a>')

        success, body = client.submit_login_page({
            '__portal_action': 'http://portal.test/Default.aspx',
            'UserName': 'student',
            'UserPwd': 'secret',
            'InputCode': '1234',
        })

        self.assertTrue(success)
        self.assertEqual(body, '')
        self.assertTrue(client.authenticated)
        self.assertEqual(client.authentication_status, 'authenticated')

    def test_submit_login_page_restores_authenticated_status_after_session_expiry(self):
        client = CampusPortalClient(test_settings())
        client.authenticated = False
        client.authentication_status = 'session_expired'
        client.opener = FakePortalOpener('<nav>修改密码</nav><main>账号管理中心</main><a>退出</a>')

        success, body = client.submit_login_page({
            '__portal_action': 'http://portal.test/Default.aspx',
            'UserName': 'student',
            'UserPwd': 'secret',
            'InputCode': '1234',
        })

        self.assertTrue(success)
        self.assertEqual(body, '')
        self.assertTrue(client.authenticated)
        self.assertEqual(client.authentication_status, 'authenticated')

    def test_fetch_reading_marks_login_page_as_session_expired(self):
        client = CampusPortalClient(test_settings())
        client.authenticated = True
        client.authentication_status = 'authenticated'
        client.opener = FakePortalOpener('<input name="UserPwd" type="password" /><input name="InputCode" />')

        with self.assertRaises(SessionExpiredError):
            client.fetch_reading(RoomSelection(building='C20', room='2324'))

        self.assertFalse(client.authenticated)
        self.assertEqual(client.authentication_status, 'session_expired')

    def test_submit_login_page_keeps_proxying_after_cas_success_returns_portal_login(self):
        settings = test_settings()
        settings = Settings(
            host=settings.host,
            port=settings.port,
            timezone=settings.timezone,
            data_dir=settings.data_dir,
            database_path=settings.database_path,
            campus_login_url='https://webvpn.njucm.edu.cn/http/webvpn-token/web/auths/index.aspx',
            campus_electricity_url=settings.campus_electricity_url,
            smtp_host=settings.smtp_host,
            smtp_port=settings.smtp_port,
            smtp_username=settings.smtp_username,
            smtp_password=settings.smtp_password,
            smtp_from=settings.smtp_from,
        )
        second_step_login = '''
        <html><body>
          <h1>统一身份认证成功</h1>
          <form action="Default.aspx" method="post">
            <input type="hidden" name="__VIEWSTATE" value="view" />
            <input type="hidden" name="__EVENTVALIDATION" value="event" />
            <input name="UserName" />
            <input name="UserPwd" type="password" />
            <input name="InputCode" />
          </form>
        </body></html>
        '''
        client = CampusPortalClient(settings)
        client._login_page_url = 'https://ids.njucm.edu.cn/login?service=https://webvpn.njucm.edu.cn/callback'
        client.opener = FakeLoginSequenceOpener([
            (
                second_step_login,
                'https://webvpn.njucm.edu.cn/http/webvpn-token/Default.aspx',
            ),
        ])

        success, body = client.submit_login_page({
            '__portal_action': 'https://ids.njucm.edu.cn/login?service=https://webvpn.njucm.edu.cn/callback',
            'username': 'student-id',
            'password': 'cas-password',
        })

        self.assertFalse(success)
        self.assertFalse(client.authenticated)
        self.assertEqual(client._login_page_url, 'https://webvpn.njucm.edu.cn/http/webvpn-token/Default.aspx')
        self.assertIn('action="/portal/login"', body)
        self.assertIn(
            'name="__portal_action" value="https://webvpn.njucm.edu.cn/http/webvpn-token/Default.aspx"',
            body,
        )

    def test_load_login_page_unwraps_management_frameset_to_second_step_login(self):
        settings = test_settings()
        settings = Settings(
            host=settings.host,
            port=settings.port,
            timezone=settings.timezone,
            data_dir=settings.data_dir,
            database_path=settings.database_path,
            campus_login_url='https://webvpn.njucm.edu.cn/http/webvpn-token/Default.aspx',
            campus_electricity_url=settings.campus_electricity_url,
            smtp_host=settings.smtp_host,
            smtp_port=settings.smtp_port,
            smtp_username=settings.smtp_username,
            smtp_password=settings.smtp_password,
            smtp_from=settings.smtp_from,
        )
        frameset = '''
        <html><head><title>管理中心</title></head>
          <frameset>
            <frame src="Top.aspx" />
            <frame src="LeftMenu.aspx" />
            <frame src="web/student/accountinfo.aspx" />
          </frameset>
        </html>
        '''
        second_step_login = '''
        <html><body>
          <form action="/Default.aspx" method="post">
            <input type="hidden" name="__VIEWSTATE" value="view" />
            <input type="hidden" name="__EVENTVALIDATION" value="event" />
            <input name="UserName" />
            <input name="UserPwd" type="password" />
            <input name="InputCode" />
          </form>
        </body></html>
        '''
        client = CampusPortalClient(settings)
        client.opener = FakeLoginSequenceOpener([
            (frameset, 'https://webvpn.njucm.edu.cn/http/webvpn-token/Default.aspx'),
            (second_step_login, 'https://webvpn.njucm.edu.cn/http/webvpn-token/web/student/accountinfo.aspx'),
        ])

        body = client.load_login_page()

        self.assertFalse(client.authenticated)
        self.assertEqual(
            getattr(client.opener.requests[1], 'full_url'),
            'https://webvpn.njucm.edu.cn/http/webvpn-token/web/student/accountinfo.aspx',
        )
        self.assertEqual(client._login_page_url, 'https://webvpn.njucm.edu.cn/http/webvpn-token/web/student/accountinfo.aspx')
        self.assertIn('action="/portal/login"', body)
        self.assertIn(
            'name="__portal_action" value="https://webvpn.njucm.edu.cn/http/webvpn-token/Default.aspx"',
            body,
        )

    def test_load_login_page_skips_cross_host_frameset_login_candidates(self):
        settings = test_settings()
        settings = Settings(
            host=settings.host,
            port=settings.port,
            timezone=settings.timezone,
            data_dir=settings.data_dir,
            database_path=settings.database_path,
            campus_login_url='https://webvpn.njucm.edu.cn/http/webvpn-token/Default.aspx',
            campus_electricity_url=settings.campus_electricity_url,
            smtp_host=settings.smtp_host,
            smtp_port=settings.smtp_port,
            smtp_username=settings.smtp_username,
            smtp_password=settings.smtp_password,
            smtp_from=settings.smtp_from,
        )
        frameset = '''
        <html><head><title>管理中心</title></head>
          <frameset>
            <frame src="https://evil.test/login.aspx" />
            <frame src="web/student/accountinfo.aspx" />
          </frameset>
        </html>
        '''
        second_step_login = '''
        <html><body>
          <form action="/Default.aspx" method="post">
            <input name="UserName" />
            <input name="UserPwd" type="password" />
            <input name="InputCode" />
          </form>
        </body></html>
        '''
        client = CampusPortalClient(settings)
        client.opener = FakeLoginSequenceOpener([
            (frameset, 'https://webvpn.njucm.edu.cn/http/webvpn-token/Default.aspx'),
            (second_step_login, 'https://webvpn.njucm.edu.cn/http/webvpn-token/web/student/accountinfo.aspx'),
        ])

        body = client.load_login_page()

        requested_urls = [getattr(request, 'full_url') for request in client.opener.requests]
        self.assertNotIn('https://evil.test/login.aspx', requested_urls)
        self.assertIn('https://webvpn.njucm.edu.cn/http/webvpn-token/web/student/accountinfo.aspx', requested_urls)
        self.assertIn('action="/portal/login"', body)

    def test_submit_login_page_unwraps_post_cas_frameset_to_second_step_login(self):
        settings = test_settings()
        settings = Settings(
            host=settings.host,
            port=settings.port,
            timezone=settings.timezone,
            data_dir=settings.data_dir,
            database_path=settings.database_path,
            campus_login_url='https://webvpn.njucm.edu.cn/http/webvpn-token/web/auths/index.aspx',
            campus_electricity_url=settings.campus_electricity_url,
            smtp_host=settings.smtp_host,
            smtp_port=settings.smtp_port,
            smtp_username=settings.smtp_username,
            smtp_password=settings.smtp_password,
            smtp_from=settings.smtp_from,
        )
        frameset = '''
        <html><head><title>管理中心</title></head>
          <frameset>
            <frame src="Top.aspx" />
            <frame src="LeftMenu.aspx" />
            <frame src="web/student/accountinfo.aspx" />
          </frameset>
        </html>
        '''
        second_step_login = '''
        <html><body>
          <form action="/Default.aspx" method="post">
            <input type="hidden" name="__VIEWSTATE" value="view" />
            <input type="hidden" name="__EVENTVALIDATION" value="event" />
            <input name="UserName" />
            <input name="UserPwd" type="password" />
            <input name="InputCode" />
          </form>
        </body></html>
        '''
        client = CampusPortalClient(settings)
        client._login_page_url = 'https://ids.njucm.edu.cn/login?service=https://webvpn.njucm.edu.cn/callback'
        client.opener = FakeLoginSequenceOpener([
            (frameset, 'https://webvpn.njucm.edu.cn/http/webvpn-token/Default.aspx'),
            (second_step_login, 'https://webvpn.njucm.edu.cn/http/webvpn-token/web/student/accountinfo.aspx'),
        ])

        success, body = client.submit_login_page({
            '__portal_action': 'https://ids.njucm.edu.cn/login?service=https://webvpn.njucm.edu.cn/callback',
            'username': 'student-id',
            'password': 'cas-password',
        })

        self.assertFalse(success)
        self.assertFalse(client.authenticated)
        self.assertEqual(
            getattr(client.opener.requests[1], 'full_url'),
            'https://webvpn.njucm.edu.cn/http/webvpn-token/web/student/accountinfo.aspx',
        )
        self.assertEqual(client._login_page_url, 'https://webvpn.njucm.edu.cn/http/webvpn-token/web/student/accountinfo.aspx')
        self.assertIn('action="/portal/login"', body)
        self.assertIn(
            'name="__portal_action" value="https://webvpn.njucm.edu.cn/http/webvpn-token/Default.aspx"',
            body,
        )

    def test_submit_login_page_marks_authenticated_after_second_step_success(self):
        client = CampusPortalClient(test_settings())
        client._login_page_url = 'https://webvpn.njucm.edu.cn/http/webvpn-token/Default.aspx'
        client.opener = FakeLoginSequenceOpener([
            (
                '<nav>管理中心</nav><a>安全退出</a><section>自助购电</section>',
                'https://webvpn.njucm.edu.cn/http/webvpn-token/Main.aspx',
            ),
        ])

        success, body = client.submit_login_page({
            '__portal_action': 'https://webvpn.njucm.edu.cn/http/webvpn-token/Default.aspx',
            '__VIEWSTATE': 'view',
            '__EVENTVALIDATION': 'event',
            'UserName': 'id-card',
            'UserPwd': 'portal-password',
            'InputCode': '1234',
        })

        self.assertTrue(success)
        self.assertEqual(body, '')
        self.assertTrue(client.authenticated)
        self.assertEqual(
            getattr(client.opener.requests[0], 'full_url'),
            'https://webvpn.njucm.edu.cn/http/webvpn-token/Default.aspx',
        )

    def test_client_keeps_submit_login_page_method_after_proxy_resource_method(self):
        client = CampusPortalClient(test_settings())

        self.assertTrue(callable(client.submit_login_page))

    def test_fetch_portal_path_preserves_dynamic_resource_query(self):
        client = CampusPortalClient(test_settings())
        opener = FakeResourceOpener('console.log("ok")', content_type='application/javascript')
        client.opener = opener

        body, content_type = client.fetch_portal_path('/portal/web/auths/app.js?v=1&lang=zh')

        self.assertEqual(body, b'console.log("ok")')
        self.assertEqual(content_type, 'application/javascript')
        self.assertEqual(getattr(opener.requests[0], 'full_url'), 'http://portal.test/web/auths/app.js?v=1&lang=zh')

    def test_fetch_portal_path_preserves_webvpn_gateway_prefix(self):
        settings = test_settings()
        settings = Settings(
            host=settings.host,
            port=settings.port,
            timezone=settings.timezone,
            data_dir=settings.data_dir,
            database_path=settings.database_path,
            campus_login_url='https://webvpn.test/http/webvpn-token/web/auths/index.aspx',
            campus_electricity_url=settings.campus_electricity_url,
            smtp_host=settings.smtp_host,
            smtp_port=settings.smtp_port,
            smtp_username=settings.smtp_username,
            smtp_password=settings.smtp_password,
            smtp_from=settings.smtp_from,
        )
        client = CampusPortalClient(settings)
        opener = FakeResourceOpener('console.log("ok")', content_type='application/javascript')
        client.opener = opener

        client.fetch_portal_path('/portal/web/auths/app.js?v=1')

        self.assertEqual(
            getattr(opener.requests[0], 'full_url'),
            'https://webvpn.test/http/webvpn-token/web/auths/app.js?v=1',
        )

    def test_fetch_portal_path_uses_final_login_response_url_after_webvpn_redirect(self):
        settings = test_settings()
        settings = Settings(
            host=settings.host,
            port=settings.port,
            timezone=settings.timezone,
            data_dir=settings.data_dir,
            database_path=settings.database_path,
            campus_login_url='https://webvpn.test/http/webvpn-token/web/auths/index.aspx',
            campus_electricity_url=settings.campus_electricity_url,
            smtp_host=settings.smtp_host,
            smtp_port=settings.smtp_port,
            smtp_username=settings.smtp_username,
            smtp_password=settings.smtp_password,
            smtp_from=settings.smtp_from,
        )
        client = CampusPortalClient(settings)
        client.opener = FakeResourceOpener(
            '<form action="/login" method="post"><input name="password" type="password" /></form>',
            response_url='https://ids.test/login?service=https%3A%2F%2Fwebvpn.test%2Fcallback',
        )

        client.load_login_page()
        opener = FakeResourceOpener('console.log("ok")', content_type='application/javascript')
        client.opener = opener
        client.fetch_portal_path('/portal/web/auths/app.js?v=1')

        self.assertEqual(getattr(opener.requests[0], 'full_url'), 'https://ids.test/web/auths/app.js?v=1')

    def test_keep_alive_uses_electricity_url_and_keeps_authenticated_session(self):
        client = CampusPortalClient(test_settings())
        client.authenticated = True
        client.authentication_status = 'authenticated'
        opener = FakeResourceOpener('<span>自助购电</span>')
        client.opener = opener

        client.keep_alive()

        self.assertTrue(client.authenticated)
        self.assertEqual(client.authentication_status, 'authenticated')
        self.assertEqual(getattr(opener.requests[0], 'full_url'), 'http://portal.test/Web/Student/FeeElect.aspx')

    def test_keep_alive_marks_login_page_as_session_expired(self):
        client = CampusPortalClient(test_settings())
        client.authenticated = True
        client.authentication_status = 'authenticated'
        client.opener = FakeResourceOpener('<input type="password" />')

        with self.assertRaises(SessionExpiredError):
            client.keep_alive()

        self.assertFalse(client.authenticated)
        self.assertEqual(client.authentication_status, 'session_expired')

    def test_fetch_reading_queries_fee_elect_page_with_current_session(self):
        fee_elect_page = '''
        <form action="FeeElect.aspx" method="post">
          <span>自助购电</span>
          <input type="hidden" name="__VIEWSTATE" value="view" />
          <input type="hidden" name="__EVENTVALIDATION" value="event" />
          <input name="txtSchoolCardBalance" value="88.00" />
          <input name="FeeAmtTxt" value="10" />
          <input name="btkOK" value="下一步" />
        </form>
        '''
        result_page = '<span>自助购电</span><span id="lblRoomMoney">20.93 元</span>'
        client = CampusPortalClient(test_settings())
        client.authenticated = True
        opener = FakeSequenceOpener([fee_elect_page, result_page])
        client.opener = opener

        reading = client.fetch_reading(RoomSelection(building='C20', room='2324'))

        self.assertEqual(reading.numeric_value, 20.93)
        self.assertEqual(reading.unit, '元')
        self.assertEqual(reading.building, 'C20')
        self.assertEqual(reading.room, '2324')
        self.assertEqual(len(opener.requests), 2)
        self.assertEqual(getattr(opener.requests[0], 'full_url'), 'http://portal.test/Web/Student/FeeElect.aspx')
        post_data = opener.requests[1].data.decode('utf-8')
        self.assertIn('__VIEWSTATE=view', post_data)
        self.assertIn('__EVENTVALIDATION=event', post_data)
        self.assertIn('txtSchoolCardBalance=88.00', post_data)
        self.assertIn('ZoneID=1', post_data)
        self.assertIn('txtHouse=20', post_data)
        self.assertIn('txtRoom=2324', post_data)
        self.assertIn('btnQuery=%E6%9F%A5%E8%AF%A2%E7%94%B5%E9%87%8F', post_data)
        self.assertNotIn('btkOK', post_data)
