"""Offline Responses protocol tests. No API credential or paid call is used."""
import importlib.util
import unittest
if importlib.util.find_spec('langgraph') is None:
    raise unittest.SkipTest('Install requirements-agent.txt')
import json
import tempfile
import time
import urllib.error
from pathlib import Path
from unittest.mock import patch
from gameon.storage import GameStore
from gameon.erne.choice_demo import create_choice_game, TwoSlotDiscovery
from gameon.erne.contracts import RunRequest
from gameon.erne.graph import _execute_graph, run_erne
from gameon.erne.openai_provider import OpenAIResponsesProvider, ProviderError, MAX_CALL_USD
from gameon.erne.evaluate_llm import FailingOnceDiscovery, evaluate
from gameon.erne.fixtures import create_scenario


class Pipe:
    def send(self, item):
        pass


class FakeResponses:
    def __init__(self, failure=None):
        self.failure = failure
        self.payloads = []

    def __call__(self, body, timeout):
        payload = json.loads(body)
        self.payloads.append(payload)
        if self.failure == 'http':
            raise urllib.error.HTTPError('private-url', 429, 'private-credential', {}, None)
        if self.failure == 'slow':
            time.sleep(60)
        request = json.loads(payload['input'])
        options = request['options']
        choice = next((o for o in options if o['outcome'] == 'discovery_recommendation'), None)
        if choice is None:
            searches = [o for o in options if o['tool'] == 'discover_courts']
            choice = next((o for o in searches if o['arguments']['slot_id'] == request['preferred_slot_id']), None)
            choice = choice or (searches[0] if searches else options[0])
        answer = dict(decision_id=request['decision_id'], snapshot_id=request['snapshot_id'],
            selected_option_id=choice['option_id'], evidence_ids=choice['evidence_ids'],
            reason_code=choice['reason_code'], explanation='Short factual fixture action summary.')
        if self.failure == 'invalid':
            answer['selected_option_id'] = 'not-offered'
        if self.failure == 'schema':
            answer['unexpected'] = 'pay'
        response = {'status': 'completed', 'usage': {'input_tokens': 100, 'output_tokens': 20,
            'input_tokens_details': {'cached_tokens': 10}, 'output_tokens_details': {'reasoning_tokens': 8}},
            'output': [{'type': 'reasoning', 'private_text': 'NEVER_RETAIN_REASONING'},
                {'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(answer)}]}]}
        if self.failure == 'refusal':
            response['output'] = [{'type': 'message', 'content': [{'type': 'refusal', 'refusal': 'Private text'}]}]
        if self.failure == 'incomplete':
            response['status'] = 'incomplete'
        if self.failure == 'no_usage':
            response.pop('usage')
        return response


class OpenAIAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = GameStore(Path(self.temp.name) / 'test.sqlite3')
        self.game_id, self.slots = create_choice_game(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def run_graph(self, *, transport=None, budget=1.0, calls=4, game_id=None, preferred=None, discovery=None):
        provider = OpenAIResponsesProvider(transport=transport or FakeResponses(), approved_budget_usd=budget, max_calls=calls)
        request = RunRequest(game_id=game_id or self.game_id, mode='llm', preferred_slot_id=preferred or self.slots[0])
        return _execute_graph(Pipe(), request, self.store, provider,
            discovery or TwoSlotDiscovery(self.slots[1]), 'test-run'), provider

    def test_response_selection_drives_tool_and_observation_drives_next_request(self):
        fake = FakeResponses()
        result, provider = self.run_graph(transport=fake)
        self.assertEqual(result.outcome, 'discovery_recommendation')
        self.assertEqual(len(fake.payloads), 3)
        history = json.loads(fake.payloads[1]['input'])['observations']
        self.assertEqual(history[-1]['payload']['status'], 'empty')
        searches = [e.payload.slot_id for e in result.observations if e.source == 'discover_courts']
        self.assertEqual(searches, self.slots)
        decisions = {e.iteration: e for e in result.trace if e.phase == 'decision'}
        for event in result.trace:
            if event.phase == 'tool':
                self.assertEqual(event.option_id, decisions[event.iteration].selection.selected_option_id)
        self.assertEqual([decisions[i].decision_source for i in (1, 2)], ['policy', 'policy'])
        self.assertFalse(result.llm_used)
        self.assertFalse(result.llm_call_attempted)
        self.assertTrue(all(c.source == 'mock' for c in result.inference_calls))
        self.assertNotIn('NEVER_RETAIN_REASONING', result.model_dump_json())
        self.assertEqual(result.inference_calls[0].estimated_cost_usd, 0.0002)
        self.assertEqual(provider.calls, 3)

    def test_protocol_configuration_and_preference(self):
        fake = FakeResponses()
        result, _ = self.run_graph(transport=fake, preferred=self.slots[1])
        self.assertEqual(result.iteration_count, 4)
        payload = fake.payloads[0]
        self.assertEqual(payload['model'], 'gpt-6.1-terra')
        self.assertEqual(payload['reasoning'], {'effort': 'medium'})
        self.assertFalse(payload['store'])
        self.assertEqual(payload['service_tier'], 'default')
        self.assertEqual(payload['max_output_tokens'], 4096)
        self.assertTrue(payload['text']['format']['strict'])
        self.assertNotIn('tools', payload)
        request = json.loads(payload['input'])
        self.assertEqual(request['preferred_slot_id'], self.slots[1])
        self.assertEqual(payload['text']['format']['schema']['properties']['selected_option_id']['enum'],
            [o['option_id'] for o in request['options']])

    def test_invalid_refused_incomplete_and_http_stop_without_discovery(self):
        for failure, code in [('invalid', 'invalid_decision'), ('schema', 'model_invalid_response'),
                              ('refusal', 'model_refused'), ('incomplete', 'model_incomplete'),
                              ('http', 'model_rate_limited')]:
            with self.subTest(failure=failure):
                fake = FakeResponses(failure)
                result, _ = self.run_graph(transport=fake)
                self.assertEqual(result.stop_reason, code)
                self.assertFalse(any(e.source == 'discover_courts' for e in result.observations))
                self.assertEqual(len(fake.payloads), 1)  # no hidden retry
                self.assertNotIn('private-credential', result.model_dump_json())

    def test_budget_blocks_before_call_and_caps_continuation(self):
        fake = FakeResponses()
        result, _ = self.run_graph(transport=fake, budget=0.0)
        self.assertEqual(result.stop_reason, 'model_budget_exhausted')
        self.assertEqual(fake.payloads, [])
        result, provider = self.run_graph(calls=1)
        self.assertEqual(result.stop_reason, 'model_budget_exhausted')
        self.assertEqual(provider.calls, 1)
        self.assertAlmostEqual(provider.reserved_usd, MAX_CALL_USD)

    def test_credentials_missing_no_fallback(self):
        provider = OpenAIResponsesProvider(approved_budget_usd=1.0)
        with patch.dict('os.environ', {}, clear=True):
            result = _execute_graph(Pipe(), RunRequest(game_id=self.game_id, mode='llm'), self.store,
                provider, TwoSlotDiscovery(self.slots[1]), 'test-run')
        self.assertEqual(result.stop_reason, 'model_credentials_missing')
        self.assertFalse(result.llm_call_attempted)
        self.assertFalse(result.llm_used)

    def test_retryable_tool_error_is_visible_to_next_selection(self):
        fake = FakeResponses()
        result, _ = self.run_graph(transport=fake, discovery=FailingOnceDiscovery())
        second = json.loads(fake.payloads[1]['input'])
        self.assertEqual(second['observations'][-1]['payload']['code'], 'transient_failure')
        self.assertEqual({o['reason_code'] for o in second['options']}, {'retry_transient', 'alternative_slot', 'human_help'})
        self.assertEqual(result.outcome, 'discovery_recommendation')

    def test_missing_votes_options_and_no_discovery(self):
        game_id = create_scenario(self.store, 'missing_votes')
        fake = FakeResponses()
        result = _execute_graph(Pipe(), RunRequest(game_id=game_id, mode='llm'), self.store,
            OpenAIResponsesProvider(transport=fake, approved_budget_usd=1.0), None, 'test-run')
        self.assertEqual(result.action, 'WAIT')
        self.assertEqual({o['action'] for o in json.loads(fake.payloads[0]['input'])['options']}, {'WAIT', 'REQUEST_HELP'})

    def test_unknown_usage_is_not_zero(self):
        result, _ = self.run_graph(transport=FakeResponses('no_usage'))
        self.assertTrue(all(c.estimated_cost_usd is None for c in result.inference_calls))
        self.assertTrue(all(c.reserved_cost_usd > 0 for c in result.inference_calls))

    def test_parent_deadline_retains_attempt_and_kills_worker(self):
        provider = OpenAIResponsesProvider(transport=FakeResponses('slow'), approved_budget_usd=1.0)
        result = run_erne(RunRequest(game_id=self.game_id, mode='llm', tool_timeout_seconds=0.4),
            self.store, provider=provider, discovery=TwoSlotDiscovery(self.slots[1]))
        self.assertEqual(result.stop_reason, 'decision_timeout')
        self.assertEqual(len(result.inference_calls), 1)
        self.assertEqual(result.inference_calls[0].status, 'started')
        self.assertGreater(result.elapsed_seconds, 0)
        self.assertFalse(result.llm_used)

    def test_evaluation_rejects_unapproved_budget(self):
        with self.assertRaises(ValueError):
            evaluate(None, 0.0, 16)

    def test_oversized_input_is_blocked_without_transport(self):
        fake = FakeResponses()
        with patch('gameon.erne.openai_provider.MAX_INPUT_BYTES', 1):
            result, _ = self.run_graph(transport=fake)
        self.assertEqual(result.stop_reason, 'model_input_limit')
        self.assertEqual(fake.payloads, [])

    def test_live_branch_provenance_with_mocked_http_not_actual_inference(self):
        # Exercise the real adapter branch but patch its HTTP function: no network.
        for failure, accepted in [(None, True), ('invalid', False)]:
            fake = FakeResponses(failure)
            provider = OpenAIResponsesProvider(approved_budget_usd=1.0)
            with patch.dict('os.environ', {'OPENAI_API_KEY': 'dummy-not-a-secret'}), patch(
                    'gameon.erne.openai_provider.post_response', side_effect=lambda body, key, timeout: fake(body, timeout)):
                result = _execute_graph(Pipe(), RunRequest(game_id=self.game_id, mode='llm'), self.store,
                    provider, TwoSlotDiscovery(self.slots[1]), 'test-run')
            self.assertTrue(result.llm_call_attempted)
            self.assertEqual(result.llm_used, accepted)
            self.assertNotIn('dummy-not-a-secret', result.model_dump_json())

    def test_provider_errors_are_sanitized(self):
        self.assertEqual(str(ProviderError('private-credential')), 'model_request_failed')
