"""Saved directory records and synthetic players; no web or paid model calls."""
import importlib.util
import json
import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
if importlib.util.find_spec('langgraph') is None:
    raise unittest.SkipTest('Install optional requirements-agent.txt')
from gameon.storage import GameStore
from gameon.venues.contracts import VenuePreferences, GroupProfile, PlayerPreference, Venue
from gameon.venues.directory import CuratedDirectory, deduplicate, distance, rank_venues
from gameon.venues.profiles import load_preferences
from gameon.erne.contracts import RunRequest, ReadInput, ScheduleInput, VenueSearchInput, RankVenuesInput, FinalResult
from gameon.erne.tools import read_game, schedule_game, TransientToolError
from gameon.erne.graph import _execute_graph, run_erne
from gameon.erne.providers import DeterministicTestProvider, select
from gameon.erne.venue_demo import create_venue_game
from gameon.erne.fixtures import create_scenario


class Connection:
    def send(self, value):
        pass


class ChooseRank(DeterministicTestProvider):
    def decide(self, request):
        rank = next((o for o in request.options if o.tool == 'rank_venues'), None)
        return select(request, rank) if rank else super().decide(request)


class BrokenDirectory(CuratedDirectory):
    def search(self, *args):
        raise TransientToolError('private provider details')


class SlowDirectory(CuratedDirectory):
    def search(self, *args):
        time.sleep(4)
        return super().search(*args)


class CorruptDirectory(CuratedDirectory):
    def search(self, *args):
        return super().search(*args).model_copy(update={'snapshot_id': 'wrong-snapshot'})


class VenueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = GameStore(Path(self.temp.name)/'test.sqlite3')
        self.game_id, self.preferences = create_venue_game(self.store)
        # Saved real identities; refreshed fixture dates keep tests independent of wall date.
        saved = json.loads((Path(__file__).parent/'fixtures'/'melbourne_venues.json').read_text(encoding='utf-8'))
        self.today = datetime.now(timezone.utc).date()
        self.venues = tuple(Venue.model_validate_json(json.dumps(v)).model_copy(update={'checked_on': self.today}) for v in saved)
        self.directory = CuratedDirectory(self.venues)

    def tearDown(self):
        self.temp.cleanup()

    def run_graph(self, preferences=None, directory=None, provider=None, maximum=6):
        return _execute_graph(Connection(), RunRequest(game_id=self.game_id,
            venue_preferences=preferences or self.preferences, max_iterations=maximum), self.store,
            provider or DeterministicTestProvider(), directory if directory is not None else self.directory, 'test')

    def tools(self, result):
        return [e.name for e in result.trace if e.phase == 'tool']

    def test_complete_real_graph_preferred_expanded_rank_read_only(self):
        before = self.store.get_game(self.game_id)
        result = self.run_graph()
        self.assertEqual(result.outcome, 'discovery_recommendation')
        self.assertEqual(self.tools(result), ['read_game', 'schedule_game', 'search_pickleball_venues', 'search_pickleball_venues', 'rank_venues'])
        searches = [e.payload for e in result.observations if e.source == 'search_pickleball_venues']
        self.assertEqual([s.scope for s in searches], ['preferred', 'expanded'])
        self.assertEqual({v.id for v in searches[0].venues}, {'level-up-knox', 'pickleholic-clayton'})
        self.assertEqual(len(result.recommendations), 3)
        self.assertTrue(all(r.availability_status == 'not_checked' for r in result.recommendations))
        self.assertFalse(result.llm_used)
        self.assertFalse(result.simulated)
        self.assertEqual(self.store.get_game(self.game_id), before)
        self.assertEqual(FinalResult.model_validate_json(result.model_dump_json()), result)

    def test_same_state_can_rank_now_or_expand(self):
        immediate, expanded = self.run_graph(provider=ChooseRank()), self.run_graph()
        self.assertEqual(len(immediate.recommendations), 2)
        self.assertEqual(len(expanded.recommendations), 3)
        for result in (immediate, expanded):
            fourth = [e for e in result.trace if e.phase == 'decision'][3]
            self.assertTrue({'rank_venues', 'search_pickleball_venues'} <= {o.tool for o in fourth.offered_options})
            tool = [e for e in result.trace if e.phase == 'tool'][3]
            self.assertEqual(tool.option_id, fourth.selection.selected_option_id)

    def test_missing_preferred_expands(self):
        prefs = VenuePreferences(group=GroupProfile(preferred_venue_ids=('not-in-directory',)))
        result = self.run_graph(preferences=prefs)
        first = result.observations[2].payload
        self.assertEqual(first.status, 'empty')
        self.assertEqual(first.missing_preferred_ids, ('not-in-directory',))
        self.assertEqual(result.outcome, 'discovery_recommendation')

    def test_unsuitable_indoor_unknown_excluded(self):
        prefs = VenuePreferences(group=GroupProfile(preferred_venue_ids=('the-rally-altona',), indoor_only=True))
        result = self.run_graph(preferences=prefs)
        self.assertIn('the-rally-altona', result.observations[2].payload.excluded_ids)
        self.assertNotIn('the-rally-altona', [r.venue.id for r in result.recommendations])

    def test_conflicting_preferences_and_fairer_alternative(self):
        prefs = self.preferences.model_copy(update={'group': GroupProfile(preferred_venue_ids=('palace-pascoe-vale',))})
        result = self.run_graph(preferences=prefs)
        self.assertEqual({v.id for v in result.observations[2].payload.venues},
                         {'palace-pascoe-vale', 'pickleholic-clayton', 'level-up-knox'})
        self.assertNotEqual(result.recommendations[0].venue.id, 'palace-pascoe-vale')
        self.assertEqual(result.recommendations[0].individual_preference_count, 2)

    def test_restriction_never_expands(self):
        prefs = VenuePreferences(group=GroupProfile(preferred_venue_ids=('level-up-knox',), restrict_to_preferences=True))
        result = self.run_graph(preferences=prefs)
        self.assertEqual(self.tools(result).count('search_pickleball_venues'), 1)
        self.assertEqual([r.venue.id for r in result.recommendations], ['level-up-knox'])

    def test_restricted_missing_is_exhausted(self):
        prefs = VenuePreferences(group=GroupProfile(preferred_venue_ids=('missing',), restrict_to_preferences=True))
        self.assertEqual(self.run_graph(preferences=prefs).outcome, 'search_exhausted')

    def test_empty_directory_exhausted(self):
        result = self.run_graph(directory=CuratedDirectory(()))
        self.assertEqual(result.outcome, 'search_exhausted')
        self.assertEqual(self.tools(result).count('search_pickleball_venues'), 2)
        self.assertEqual(result.recommendations, ())

    def test_transient_failure_has_one_retry_and_safe_error(self):
        result = self.run_graph(directory=BrokenDirectory(self.venues))
        self.assertEqual(result.outcome, 'search_exhausted')
        self.assertEqual(self.tools(result).count('search_pickleball_venues'), 2)
        self.assertNotIn('private provider details', result.model_dump_json())

    def test_timeout_kills_worker(self):
        result = run_erne(RunRequest(game_id=self.game_id, venue_preferences=self.preferences,
            tool_timeout_seconds=1.0), self.store, discovery=SlowDirectory(self.venues))
        self.assertEqual(result.stop_reason, 'tool_timeout')
        self.assertEqual(result.outcome, 'human_handoff')

    def test_snapshot_mismatch_rejected(self):
        self.assertEqual(self.run_graph(directory=CorruptDirectory(self.venues)).stop_reason, 'invalid_observation')

    def test_duplicate_and_malformed_records(self):
        first = self.venues[0]
        self.assertEqual(deduplicate((first, first)), (first,))
        for bad in (first.model_copy(update={'name': 'Conflicting'}), first.model_copy(update={'id': 'other-id'})):
            with self.assertRaises(ValueError):
                deduplicate((first, bad))
        for update in ({'id': '../bad'}, {'website_url': 'javascript:bad'}, {'booking_provider': 'invented'},
                       {'price': {'aud_per_court_hour': 0}}, {'checked_on': 'not-a-date'}):
            with self.assertRaises(ValueError):
                Venue.model_validate({**first.model_dump(), **update})

    def test_no_common_time_does_not_search(self):
        self.game_id = create_scenario(self.store, 'no_common_time')
        result = self.run_graph(preferences=VenuePreferences())
        self.assertEqual(result.outcome, 'alternatives_requested')
        self.assertEqual(self.tools(result), ['read_game', 'schedule_game'])

    def test_missing_votes_wait(self):
        self.game_id = create_scenario(self.store, 'missing_votes')
        self.assertEqual(self.run_graph(preferences=VenuePreferences()).outcome, 'waiting')

    def test_unknown_suburb_handoff_not_guessed(self):
        with self.store._connect() as connection:
            connection.execute('UPDATE players SET suburb=? WHERE game_id=?', ('Unknown suburb', self.game_id))
        result = self.run_graph()
        self.assertEqual(result.outcome, 'human_handoff')
        self.assertNotIn('search_pickleball_venues', self.tools(result))
        self.assertIn('Mapped suburb reference needed for 4 players', result.missing_information)

    def test_stale_and_future_verification_excluded(self):
        for days in (-91, 1):
            directory = CuratedDirectory(tuple(v.model_copy(update={'checked_on': self.today+timedelta(days=days)}) for v in self.venues))
            self.assertEqual(self.run_graph(directory=directory).outcome, 'search_exhausted')

    def test_profiles_reusable_group_but_player_scope_is_validated(self):
        group_file, player_file = Path(self.temp.name)/'group.json', Path(self.temp.name)/'players.json'
        group_file.write_text(self.preferences.group.model_dump_json(), encoding='utf-8')
        player_file.write_text(json.dumps([p.model_dump(mode='json') for p in self.preferences.players]), encoding='utf-8')
        self.assertEqual(load_preferences(group_file, player_file), self.preferences)
        self.game_id = create_scenario(self.store, 'common_time')
        self.assertEqual(self.run_graph(preferences=load_preferences(group_file)).outcome, 'discovery_recommendation')
        self.assertEqual(self.run_graph(preferences=load_preferences(group_file, player_file)).stop_reason, 'tool_error')

    def test_distance_and_five_players(self):
        clayton = self.directory.points['clayton']
        self.assertEqual(distance(clayton, clayton), 0)
        self.assertAlmostEqual(distance(clayton, self.directory.points['springvale']), 4.09, delta=0.1)
        player = self.store.add_player(self.game_id, 'Synthetic fifth', 'Maidstone')
        slot = self.store.get_game(self.game_id)['slots'][0]['id']
        self.store.submit_vote(self.game_id, player['player_id'], player['player_token'], [slot])
        self.assertEqual(len(self.run_graph().recommendations[0].distances), 5)

    def test_small_budget_and_no_preferences(self):
        self.assertLessEqual(self.run_graph(maximum=4).iteration_count, 4)
        result = self.run_graph(preferences=VenuePreferences())
        self.assertEqual(result.outcome, 'discovery_recommendation')
        self.assertEqual(len(result.recommendations), 3)

    def test_invalid_provider_choice_rejected(self):
        class Invalid(DeterministicTestProvider):
            def decide(self, request):
                response = super().decide(request)
                return response.model_copy(update={'evidence_ids': ('invented',)}) if len(request.observations) >= 3 else response
        self.assertEqual(self.run_graph(provider=Invalid()).stop_reason, 'invalid_decision')

    def test_option_order_does_not_change_baseline(self):
        from gameon.erne.policy import permitted_options
        with patch('gameon.erne.graph.permitted_options', side_effect=lambda *args: tuple(reversed(permitted_options(*args)))):
            result = self.run_graph()
        self.assertEqual([r.venue.id for r in result.recommendations], [r.venue.id for r in self.run_graph().recommendations])

    def test_mock_responses_accepts_venue_context_with_existing_input_cap(self):
        from gameon.erne.openai_provider import OpenAIResponsesProvider, MAX_INPUT_BYTES
        payloads = []
        def transport(body, timeout):
            self.assertLessEqual(len(body), MAX_INPUT_BYTES)
            payload = json.loads(body)
            context = json.loads(payload['input'])
            payloads.append(context)
            options = context['options']
            choice = next((o for o in options if o['tool'] == 'search_pickleball_venues' and o['arguments']['scope'] == 'expanded'), None)
            choice = choice or next((o for o in options if o['tool'] == 'search_pickleball_venues'), None)
            choice = choice or next((o for o in options if o['tool'] == 'rank_venues'), None)
            choice = choice or next(o for o in options if o['outcome'] == 'discovery_recommendation')
            response = dict(decision_id=context['decision_id'], snapshot_id=context['snapshot_id'],
                selected_option_id=choice['option_id'], evidence_ids=choice['evidence_ids'],
                reason_code=choice['reason_code'], explanation='Mock transport selecting a supplied option.')
            return {'status': 'completed', 'output': [{'type': 'message', 'content': [
                {'type': 'output_text', 'text': json.dumps(response)}]}]}
        provider = OpenAIResponsesProvider(transport=transport, approved_budget_usd=1.0)
        result = _execute_graph(Connection(), RunRequest(game_id=self.game_id, mode='llm', venue_preferences=self.preferences),
            self.store, provider, self.directory, 'mock-venue-run')
        self.assertEqual(result.outcome, 'discovery_recommendation')
        self.assertEqual(len(payloads), 4)
        self.assertFalse(result.llm_used)
        self.assertFalse(result.llm_call_attempted)
        self.assertTrue(all(c.prompt_version == 'erne-selection-2' and c.source == 'mock' for c in result.inference_calls))
        self.assertEqual(len([e for e in payloads[-1]['observations'] if e['source'] == 'search_pickleball_venues']), 2)


if __name__ == '__main__':
    unittest.main()
