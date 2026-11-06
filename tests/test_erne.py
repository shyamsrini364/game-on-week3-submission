import importlib.util
import unittest

if importlib.util.find_spec('langgraph') is None:
    raise unittest.SkipTest('Optional agent dependencies absent: install requirements-agent.txt')

import multiprocessing
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError
from gameon.storage import GameStore
from gameon import scheduling
from gameon.erne.contracts import (RunRequest, Decision, ReadInput, ScheduleInput, Snapshot,
    Participant, Slot, DiscoveryObservation)
from gameon.erne.fixtures import create_scenario
from gameon.erne.graph import run_erne
from gameon.erne.providers import DeterministicTestProvider
from gameon.erne.tools import read_game, schedule_game, FixtureDiscovery, TransientToolError


class BadProvider(DeterministicTestProvider):
    def decide(self, request):
        return super().decide(request).model_copy(update={'selected_option_id': 'not-offered'})


class WrongGameProvider(DeterministicTestProvider):
    def decide(self, request):
        return {**super().decide(request).model_dump(), 'game_id': 'another-game'}


class WrongSnapshotProvider(DeterministicTestProvider):
    def decide(self, request):
        result = super().decide(request)
        return result.model_copy(update={'snapshot_id': 'fake'}) if request.snapshot_id else result


class UnknownToolProvider(DeterministicTestProvider):
    def decide(self, request):
        return {**super().decide(request).model_dump(), 'tool': 'pay'}


class SlowProvider(DeterministicTestProvider):
    def decide(self, request):
        time.sleep(60)


class SlowStore(GameStore):
    def load_game_session(self, game_id):
        time.sleep(60)


class FlakyStore(GameStore):
    def load_game_session(self, game_id):
        if not getattr(self, 'retried', False):
            self.retried = True
            raise TransientToolError('Temporary database error')
        return super().load_game_session(game_id)


class SlowDiscovery(FixtureDiscovery):
    def search(self, request):
        time.sleep(60)


class FlakyDiscovery(FixtureDiscovery):
    def __init__(self, always=False):
        self.calls = 0
        self.always = always

    def search(self, request):
        self.calls += 1
        if self.always or self.calls == 1:
            raise TransientToolError('private-credential-never-log')
        return super().search(request)


class BadDiscovery(FixtureDiscovery):
    def search(self, request):
        return {'snapshot_id': 'fake', 'slot_id': request.slot_id, 'status': 'empty', 'live': True}


class ExpiredStore(GameStore):
    def load_game_session(self, game_id):
        from gameon.models import TimeSlot
        game, title = super().load_game_session(game_id)
        past = datetime.now(timezone.utc) - timedelta(days=2)
        game.slots = tuple(TimeSlot(s.id, past, past + timedelta(minutes=90)) for s in game.slots)
        return game, title


class ErneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'test.sqlite3'
        self.store = GameStore(self.path)
        self.game_id = create_scenario(self.store, 'common_time')

    def tearDown(self):
        self.temp.cleanup()

    def run_agent(self, game_id=None, store=None, **kwargs):
        request = kwargs.pop('request', RunRequest(game_id=game_id or self.game_id))
        return run_erne(request, store or self.store, **kwargs)

    def test_four_scenarios(self):
        for scenario, action in [('missing_votes', 'WAIT'), ('no_common_time', 'REQUEST_ALTERNATIVES'),
                                 ('common_time', 'PROCEED_TO_DISCOVERY'), ('empty_discovery', 'REQUEST_HELP')]:
            with self.subTest(scenario=scenario):
                game_id = create_scenario(self.store, scenario)
                result = self.run_agent(game_id, discovery=FixtureDiscovery() if scenario == 'empty_discovery' else None)
                self.assertEqual(result.action, action, result)
                self.assertEqual(result.stop_reason, 'completed')
                self.assertFalse(result.llm_used)
                self.assertEqual(result.mode, 'deterministic_test')
                self.assertEqual(result.iteration_count, 4 if scenario == 'empty_discovery' else 3)
                self.assertEqual([e.name for e in result.trace if e.phase == 'tool'][:2], ['read_game', 'schedule_game'])
                if scenario == 'missing_votes':
                    self.assertEqual(len(result.missing_information), 1)
                if scenario == 'empty_discovery':
                    self.assertTrue(result.simulated)
                    self.assertEqual(result.discovery_status, 'empty')

    def test_actual_scheduler_is_called_and_tokens_are_absent(self):
        snapshot = read_game(self.store, ReadInput(game_id=self.game_id))
        with patch('gameon.scheduling.find_common_slots', wraps=scheduling.find_common_slots) as actual:
            result = schedule_game(snapshot, ScheduleInput(snapshot_id=snapshot.snapshot_id))
        actual.assert_called_once()
        self.assertEqual(result.status, 'ready')
        text = snapshot.model_dump_json()
        self.assertNotIn('token', text)
        self.assertNotIn('Synthetic', text)
        self.assertNotIn('Clayton', text)
        with self.store._connect() as c:
            for row in c.execute('SELECT token FROM players'):
                self.assertNotIn(row['token'], text)

    def test_invalid_snapshot_references_and_immutability(self):
        snapshot = read_game(self.store, ReadInput(game_id=self.game_id))
        with self.assertRaises(ValidationError):
            Snapshot.model_validate({**snapshot.model_dump(), 'players': (Participant(id='p', selected_slot_ids=('unknown',)),)})
        with self.assertRaises(ValidationError):
            snapshot.game_id = 'different'
        with self.assertRaises(ValueError):
            schedule_game(snapshot, ScheduleInput(snapshot_id='different'))
        with self.assertRaises(ValidationError):
            Slot(id='s', starts_at=datetime(2026, 1, 1), ends_at=datetime(2026, 1, 2))

    def test_provider_and_scope_guards(self):
        for provider in (BadProvider(), WrongGameProvider(), WrongSnapshotProvider(), UnknownToolProvider()):
            with self.subTest(provider=type(provider).__name__):
                result = self.run_agent(provider=provider)
                self.assertEqual(result.stop_reason, 'invalid_decision')
                self.assertEqual(result.action, 'ERROR')

    def test_bad_discovery_contract(self):
        result = self.run_agent(discovery=BadDiscovery())
        self.assertEqual(result.stop_reason, 'invalid_observation')

    def test_missing_game(self):
        self.assertEqual(self.run_agent('not-found').stop_reason, 'game_not_found')

    def test_iteration_limit(self):
        result = self.run_agent(request=RunRequest(game_id=self.game_id, max_iterations=2))
        self.assertEqual(result.stop_reason, 'iteration_limit')
        self.assertEqual(result.iteration_count, 2)
        self.assertEqual(result.action, 'REQUEST_HELP')
        self.assertEqual(self.run_agent(request={'game_id': self.game_id, 'max_iterations': 7}).stop_reason, 'invalid_request')

    def test_six_decisions_with_two_transient_retries(self):
        result = self.run_agent(store=FlakyStore(self.path, initialise=False), discovery=FlakyDiscovery())
        self.assertEqual(result.stop_reason, 'completed', result)
        self.assertEqual(result.action, 'REQUEST_HELP')
        self.assertEqual(result.iteration_count, 6)
        self.assertEqual(len([e for e in result.trace if e.phase == 'decision']), 6)
        self.assertEqual(len(result.evidence_ids), 5)

    def test_read_snapshot_leaves_records_unchanged(self):
        before = self.store.get_game(self.game_id)
        result = self.run_agent()
        self.assertEqual(result.action, 'PROCEED_TO_DISCOVERY')
        self.assertEqual(self.store.get_game(self.game_id), before)

    def test_transient_retry_and_exhaustion(self):
        for always in (False, True):
            result = self.run_agent(discovery=FlakyDiscovery(always))
            self.assertEqual(sum(e.phase == 'tool' and e.name == 'discover_courts' for e in result.trace), 2)
            self.assertEqual(result.stop_reason, 'completed')
            self.assertEqual(result.outcome, 'search_exhausted')
            self.assertLessEqual(result.iteration_count, 6)
            self.assertNotIn('private-credential', result.model_dump_json())

    def test_tool_timeout_terminates_worker(self):
        before = {p.pid for p in multiprocessing.active_children()}
        start = time.monotonic()
        result = self.run_agent(store=SlowStore(self.path, initialise=False),
                                request=RunRequest(game_id=self.game_id, tool_timeout_seconds=0.3))
        self.assertEqual(result.stop_reason, 'tool_timeout', result)
        self.assertLess(time.monotonic() - start, 10)
        self.assertEqual({p.pid for p in multiprocessing.active_children()}, before)

    def test_provider_timeout(self):
        result = self.run_agent(provider=SlowProvider(), request=RunRequest(game_id=self.game_id, tool_timeout_seconds=0.3))
        self.assertEqual(result.stop_reason, 'decision_timeout')

    def test_total_deadline_includes_startup(self):
        start = time.monotonic()
        result = self.run_agent(request=RunRequest(game_id=self.game_id, deadline_seconds=0.05))
        self.assertEqual(result.stop_reason, 'deadline_exceeded')
        self.assertLess(time.monotonic() - start, 3)

    def test_deadline_during_tool(self):
        result = self.run_agent(discovery=SlowDiscovery(),
                                request=RunRequest(game_id=self.game_id, deadline_seconds=4.0, tool_timeout_seconds=10.0))
        self.assertEqual(result.stop_reason, 'deadline_exceeded')

    def test_new_run_reads_updated_votes_and_has_new_identity(self):
        first = self.run_agent()
        with self.store._connect() as c:
            player = c.execute('SELECT id,token FROM players WHERE game_id=?', (self.game_id,)).fetchone()
        self.store.submit_vote(self.game_id, player['id'], player['token'], [])
        second = self.run_agent()
        self.assertEqual(first.action, 'PROCEED_TO_DISCOVERY')
        self.assertEqual(second.action, 'REQUEST_ALTERNATIVES')
        self.assertNotEqual(first.snapshot_id, second.snapshot_id)
        self.assertNotEqual(first.run_id, second.run_id)
        self.assertNotIn(player['token'], second.model_dump_json())

    def test_expired_times_and_custom_minimum(self):
        result = self.run_agent(store=ExpiredStore(self.path, initialise=False))
        self.assertEqual(result.action, 'REQUEST_ALTERNATIVES')
        with self.store._connect(write=True) as c:
            c.execute('UPDATE games SET min_players=5 WHERE id=?', (self.game_id,))
        self.assertEqual(self.run_agent().action, 'WAIT')

    def test_configured_model_never_silently_falls_back(self):
        provider = DeterministicTestProvider()
        provider.mode = 'llm'
        self.assertEqual(self.run_agent(provider=provider).stop_reason, 'provider_not_supported')
        self.assertEqual(self.run_agent(request={'game_id': self.game_id, 'mode': 'llm'}).stop_reason, 'provider_not_supported')


if __name__ == '__main__':
    unittest.main()
