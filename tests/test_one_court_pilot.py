"""No network or provider credentials; existing graph replanning regression."""
import unittest
from scripts.one_court_pilot_demo import demonstrate


class OneCourtPilotTests(unittest.TestCase):
    def test_available_alternative_and_exhausted(self):
        result = demonstrate()
        a, b, c = result['scenarios']
        self.assertEqual(a['proposed_time_number'], 1)
        self.assertEqual(b['proposed_time_number'], 2)
        self.assertIsNone(c['proposed_time_number'])
        self.assertIsNone(c['price_fixture'])
        self.assertTrue(a['price_fixture']['simulated'])
        self.assertTrue(all(s['records_unchanged'] for s in result['scenarios']))
        self.assertEqual(result['live_provider_calls'], 0)


if __name__ == '__main__':
    unittest.main()
