"""Real LangGraph tests; scripted selections are not LLM evaluations."""
import importlib.util
import unittest
if importlib.util.find_spec('langgraph') is None:
    raise unittest.SkipTest('Install optional requirements-agent.txt')
import tempfile
from pathlib import Path
from unittest.mock import patch
from gameon.storage import GameStore
from gameon.erne.contracts import RunRequest, DiscoveryObservation, ToolFailure
from gameon.erne.graph import _execute_graph
from gameon.erne.providers import DeterministicTestProvider, select
from gameon.erne.policy import permitted_options
from gameon.erne.choice_demo import create_choice_game, SlotChoiceProvider, TwoSlotDiscovery
from gameon.erne.tools import FixtureDiscovery, TransientToolError


class RecordingConnection:
    def send(self, value):
        pass


class RecordingDiscovery(FixtureDiscovery):
    def __init__(self, transient=False):
        self.calls = []
        self.transient = transient

    def search(self, request):
        self.calls.append(request)
        if self.transient:
            raise TransientToolError('private detail')
        return super().search(request)


class HandoffProvider(DeterministicTestProvider):
    def decide(self, request):
        option = next((o for o in request.options if o.outcome == 'human_handoff'), None)
        return select(request, option) if option else super().decide(request)


class CorruptProvider(DeterministicTestProvider):
    def __init__(self, change):
        self.change = change

    def decide(self, request):
        result = super().decide(request)
        return result.model_copy(update=self.change) if request.eligible_slot_ids else result


class ChoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = GameStore(Path(self.temp.name) / 'choices.sqlite3')
        self.game_id, self.slots = create_choice_game(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def run_graph(self, provider=None, discovery=None, maximum=6):
        return _execute_graph(RecordingConnection(), RunRequest(game_id=self.game_id, max_iterations=maximum),
            self.store, provider or DeterministicTestProvider(), discovery or FixtureDiscovery(), 'test-run')

    def test_same_state_different_selected_slots_reach_tool(self):
        before = self.store.get_game(self.game_id)
        for slot in self.slots:
            adapter = RecordingDiscovery()
            result = self.run_graph(SlotChoiceProvider(slot), adapter)
            self.assertEqual(adapter.calls[0].slot_id, slot)
            decision = [e for e in result.trace if e.phase == 'decision'][2]
            searches = [o for o in decision.offered_options if o.tool == 'discover_courts']
            self.assertEqual({o.arguments.slot_id for o in searches}, set(self.slots))
            tool = [e for e in result.trace if e.phase == 'tool'][2]
            self.assertEqual(tool.option_id, decision.selection.selected_option_id)
            self.assertEqual(decision.decision_source, 'scripted')
            self.assertFalse(result.llm_used)
        self.assertEqual(self.store.get_game(self.game_id), before)

    def test_history_keeps_empty_and_success(self):
        result = self.run_graph(SlotChoiceProvider(self.slots[0]), TwoSlotDiscovery(self.slots[1]))
        observed = [e for e in result.observations if e.source == 'discover_courts']
        self.assertEqual([e.payload.slot_id for e in observed], self.slots)
        self.assertEqual([e.payload.status for e in observed], ['empty', 'results'])
        self.assertEqual(len({e.evidence_id for e in observed}), 2)
        self.assertEqual(result.outcome, 'discovery_recommendation')

    def test_handoff_executes_no_discovery(self):
        adapter = RecordingDiscovery()
        result = self.run_graph(HandoffProvider(), adapter)
        self.assertEqual(result.outcome, 'human_handoff')
        self.assertEqual(adapter.calls, [])

    def test_invalid_selections_fail_closed(self):
        for change in ({'selected_option_id': 'invented'}, {'snapshot_id': 'stale'},
                       {'decision_id': 'stale'}, {'evidence_ids': ('invented',)},
                       {'evidence_ids': ()}, {'reason_code': 'candidates_found'},
                       {'tool': 'pay'}):
            with self.subTest(change=change):
                adapter = RecordingDiscovery()
                result = self.run_graph(CorruptProvider(change), adapter)
                self.assertEqual(result.stop_reason, 'invalid_decision')
                self.assertEqual(adapter.calls, [])

    def test_option_order_and_explanation_do_not_change_execution(self):
        class RewordProvider(DeterministicTestProvider):
            def decide(self, request):
                return super().decide(request).model_copy(update={'explanation': 'A differently worded rationale.'})
        baseline = RecordingDiscovery()
        self.run_graph(discovery=baseline)
        reordered = RecordingDiscovery()
        with patch('gameon.erne.graph.permitted_options', side_effect=lambda *args: tuple(reversed(permitted_options(*args)))):
            self.run_graph(RewordProvider(), reordered)
        self.assertEqual([x.slot_id for x in baseline.calls], [x.slot_id for x in reordered.calls])

    def test_empty_is_not_retried_and_global_budget_is_two(self):
        adapter = RecordingDiscovery()
        result = self.run_graph(discovery=adapter)
        self.assertEqual([c.slot_id for c in adapter.calls], self.slots)
        self.assertEqual(result.outcome, 'search_exhausted')
        final = [e for e in result.trace if e.phase == 'decision'][-1]
        self.assertFalse(any(o.kind == 'tool' for o in final.offered_options))

    def test_transient_failure_offers_retry_other_slot_and_help(self):
        adapter = RecordingDiscovery(transient=True)
        result = self.run_graph(discovery=adapter)
        fourth = [e for e in result.trace if e.phase == 'decision'][3]
        self.assertEqual({o.reason_code for o in fourth.offered_options}, {'retry_transient', 'alternative_slot', 'human_help'})
        self.assertEqual(len(adapter.calls), 2)
        self.assertEqual(result.outcome, 'search_exhausted')
        self.assertTrue(all(isinstance(e.payload, ToolFailure) for e in result.observations if e.source == 'discover_courts'))

    def test_last_decision_is_reserved_for_finish(self):
        adapter = RecordingDiscovery()
        result = self.run_graph(discovery=adapter, maximum=3)
        self.assertEqual(result.iteration_count, 3)
        self.assertEqual(adapter.calls, [])
        self.assertEqual(result.outcome, 'search_exhausted')

    def test_discovery_snapshot_mismatch(self):
        class WrongScope(FixtureDiscovery):
            def search(self, request):
                return DiscoveryObservation(snapshot_id='wrong', slot_id=request.slot_id, status='empty')
        self.assertEqual(self.run_graph(discovery=WrongScope()).stop_reason, 'invalid_observation')

    def test_slot_expiry_during_selection_rejected(self):
        class ExpiringProvider(DeterministicTestProvider):
            def decide(self, request):
                if request.eligible_slot_ids:
                    expiry.start()
                return super().decide(request)
        expiry = patch('gameon.erne.policy.eligible_slots', return_value=())
        try:
            adapter = RecordingDiscovery()
            result = self.run_graph(ExpiringProvider(), adapter)
            self.assertEqual(result.stop_reason, 'invalid_decision')
            self.assertEqual(adapter.calls, [])
        finally:
            expiry.stop()

    def test_retry_and_handoff_are_real_choices_after_failure(self):
        class AfterFailure(DeterministicTestProvider):
            def __init__(self, code):
                self.code = code
            def decide(self, request):
                if any(isinstance(e.payload, ToolFailure) for e in request.observations):
                    option = next((o for o in request.options if o.reason_code == self.code), None)
                    if option:
                        return select(request, option)
                return super().decide(request)
        retry = RecordingDiscovery(transient=True)
        result = self.run_graph(AfterFailure('retry_transient'), retry)
        self.assertEqual([c.slot_id for c in retry.calls], [self.slots[0]] * 2)
        self.assertEqual(result.outcome, 'search_exhausted')
        handoff = RecordingDiscovery(transient=True)
        result = self.run_graph(AfterFailure('human_help'), handoff)
        self.assertEqual(len(handoff.calls), 1)
        self.assertEqual(result.outcome, 'human_handoff')

    def test_stale_option_from_earlier_decision_cannot_replay(self):
        class Replay(DeterministicTestProvider):
            saved = None
            def decide(self, request):
                result = super().decide(request)
                if self.saved:
                    return self.saved
                if request.eligible_slot_ids:
                    self.saved = result
                return result
        adapter = RecordingDiscovery()
        result = self.run_graph(Replay(), adapter)
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(result.stop_reason, 'invalid_decision')

    def test_unavailable_is_not_empty_and_not_retried(self):
        class Unavailable(RecordingDiscovery):
            def search(self, request):
                self.calls.append(request)
                return DiscoveryObservation(snapshot_id=request.snapshot_id, slot_id=request.slot_id, status='unavailable')
        adapter = Unavailable()
        result = self.run_graph(discovery=adapter)
        self.assertEqual([c.slot_id for c in adapter.calls], self.slots)
        self.assertEqual(result.discovery_status, 'unavailable')
        self.assertEqual(result.outcome, 'search_exhausted')

    def test_no_tool_when_snapshot_output_has_wrong_game(self):
        from gameon.erne.tools import read_game
        def bad_read(store, request):
            return read_game(store, request).model_copy(update={'game_id': 'wrong'})
        with patch('gameon.erne.graph.read_game', side_effect=bad_read):
            result = self.run_graph()
        self.assertEqual(result.stop_reason, 'invalid_observation')
        self.assertEqual([e.name for e in result.trace if e.phase == 'tool'], ['read_game'])
