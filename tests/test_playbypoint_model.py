"""Existing Responses adapter consumes browser observations via offline transport."""
import unittest
import test_playbypoint as browser_fixtures
from test_erne_openai import FakeResponses
from gameon.erne.openai_provider import OpenAIResponsesProvider
from gameon.erne.graph import run_erne


class BrowserModelTests(unittest.TestCase):
    setUp = browser_fixtures.BrowserSkillTests.setUp
    tearDown = browser_fixtures.BrowserSkillTests.tearDown
    adapter = browser_fixtures.BrowserSkillTests.adapter

    def test_existing_model_adapter_selects_browser_tool_without_paid_call(self):
        result = run_erne({'game_id':self.game, 'mode':'llm', 'tool_timeout_seconds':8.0}, self.store,
                         discovery=self.adapter(), provider=OpenAIResponsesProvider(transport=FakeResponses(), approved_budget_usd=1))
        self.assertEqual(result.outcome, 'discovery_recommendation', result.stop_reason)
        self.assertFalse(result.llm_used)
        self.assertFalse(result.llm_call_attempted)
        self.assertTrue(any(e.name=='get_court_options' and e.decision_source=='mock' for e in result.trace))
        self.assertTrue(any(e.source=='get_court_options' and e.payload.options[0].total_minor==6000 for e in result.observations))


if __name__ == '__main__':
    unittest.main()
