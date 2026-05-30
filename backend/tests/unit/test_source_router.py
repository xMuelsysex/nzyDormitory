import unittest

from backend.app.integrations.source_router import ElectricitySourceRouter
from backend.app.services.models import ElectricityReading, RoomSelection
from backend.app.shared.errors import SessionExpiredError


class FakeSource:
    def __init__(self, source_name, authenticated=False, reading_value=1.0):
        self.source_name = source_name
        self.authenticated = authenticated
        self.authentication_status = "authenticated" if authenticated else "unauthenticated"
        self.status_payload_value = None
        self.keep_alive_calls = 0
        self.fetch_calls = 0
        self.expire_next_keep_alive = False
        self.expire_next_fetch = False
        self.reading_value = reading_value

    def keep_alive(self):
        self.keep_alive_calls += 1
        if self.expire_next_keep_alive:
            self.authenticated = False
            self.authentication_status = "session_expired"
            raise SessionExpiredError("expired")

    def fetch_reading(self, selection):
        self.fetch_calls += 1
        if self.expire_next_fetch:
            self.authenticated = False
            self.authentication_status = "session_expired"
            raise SessionExpiredError("expired")
        return ElectricityReading(
            collected_at="2026-05-29T00:00:00Z",
            building=selection.building,
            room=selection.room,
            numeric_value=self.reading_value,
            unit="元",
            source=self.source_name,
        )

    def reset_session(self):
        self.authenticated = False
        self.authentication_status = "unauthenticated"

    def status_payload(self):
        if self.status_payload_value is not None:
            return self.status_payload_value
        return {
            "authenticated": self.authenticated,
            "authenticationStatus": self.authentication_status,
        }


class ElectricitySourceRouterTests(unittest.TestCase):
    def test_prefers_enterprise_wechat_when_both_sources_are_authenticated(self):
        wechat = FakeSource("enterprise_wechat", authenticated=True, reading_value=8.8)
        campus = FakeSource("campus_portal", authenticated=True, reading_value=3.3)
        router = ElectricitySourceRouter(wechat, campus)

        reading = router.fetch_reading(RoomSelection(building="C20", room="2324"))

        self.assertEqual(reading.numeric_value, 8.8)
        self.assertEqual(reading.source, "enterprise_wechat")
        self.assertEqual(wechat.fetch_calls, 1)
        self.assertEqual(campus.fetch_calls, 0)

    def test_wechat_expiry_during_collection_is_not_hidden_by_campus_fallback(self):
        wechat = FakeSource("enterprise_wechat", authenticated=True)
        wechat.expire_next_fetch = True
        campus = FakeSource("campus_portal", authenticated=True)
        router = ElectricitySourceRouter(wechat, campus)

        with self.assertRaises(SessionExpiredError):
            router.fetch_reading(RoomSelection(building="C20", room="2324"))

        self.assertEqual(router.active_source, "enterprise_wechat")
        self.assertEqual(wechat.fetch_calls, 1)
        self.assertEqual(campus.fetch_calls, 0)

    def test_wechat_expiry_during_keep_alive_is_not_hidden_by_campus_fallback(self):
        wechat = FakeSource("enterprise_wechat", authenticated=True)
        wechat.expire_next_keep_alive = True
        campus = FakeSource("campus_portal", authenticated=True)
        router = ElectricitySourceRouter(wechat, campus)

        with self.assertRaises(SessionExpiredError):
            router.keep_alive()

        self.assertEqual(router.active_source, "enterprise_wechat")
        self.assertEqual(campus.keep_alive_calls, 0)

    def test_status_by_source_exposes_both_auth_states(self):
        router = ElectricitySourceRouter(
            FakeSource("enterprise_wechat", authenticated=False),
            FakeSource("campus_portal", authenticated=True),
        )

        status = router.status_by_source()

        self.assertFalse(status["enterpriseWechat"]["authenticated"])
        self.assertTrue(status["campusPortal"]["authenticated"])

    def test_status_by_source_preserves_source_observability_fields(self):
        wechat = FakeSource("enterprise_wechat", authenticated=True)
        wechat.status_payload_value = {
            "authenticated": True,
            "authenticationStatus": "authenticated",
            "lastVerifiedAt": "2026-05-30T00:00:00Z",
            "lastKeepAliveAt": "2026-05-30T00:05:00Z",
            "lastKeepAliveError": None,
        }
        router = ElectricitySourceRouter(wechat, FakeSource("campus_portal"))

        status = router.status_by_source()

        self.assertEqual(status["enterpriseWechat"]["lastVerifiedAt"], "2026-05-30T00:00:00Z")
        self.assertEqual(status["enterpriseWechat"]["lastKeepAliveAt"], "2026-05-30T00:05:00Z")
        self.assertIsNone(status["enterpriseWechat"]["lastKeepAliveError"])
