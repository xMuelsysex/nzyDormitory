import unittest

from backend.app.integrations.campus_portal import parse_electricity_value
from backend.app.shared.errors import PortalParseError


class CampusPortalParserTests(unittest.TestCase):
    def test_parse_electricity_value_with_balance_label(self):
        value, unit = parse_electricity_value('<span>当前电量：12.5 度</span>')
        self.assertEqual(value, 12.5)
        self.assertEqual(unit, '度')

    def test_parse_electricity_value_raises_for_unknown_shape(self):
        with self.assertRaises(PortalParseError):
            parse_electricity_value('<html>no value</html>')
