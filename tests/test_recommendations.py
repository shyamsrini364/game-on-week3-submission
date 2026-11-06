"""The web service reuses real V0.3 tools without a graph or model runner."""
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch
import test_groups
from gameon.venues.directory import search_pickleball_venues


class RecommendationTests(unittest.TestCase):
    setUp, tearDown = test_groups.GroupTests.setUp, test_groups.GroupTests.tearDown
    call, group, join, game = test_groups.GroupTests.call, test_groups.GroupTests.group, test_groups.GroupTests.join, test_groups.GroupTests.game

    def ready(self, favourites=True):
        group = self.group()
        if favourites:
            self.call('/api/groups/'+group['group_id']+'/settings', {'name':'Synthetic four',
                'favourites':['level-up-knox'], 'restrict_venues':False}, token=group['member_token'])
        members = [group, self.join(group, 'Springvale', ['pickleholic-clayton']),
                   self.join(group, 'Glen Waverley', ['pickleholic-clayton']),
                   self.join(group, 'Wantirna South', ['level-up-knox'])]
        game = self.game(group)
        slot = self.call('/api/games/'+game, token=group['member_token'])['slots'][0]['id']
        for member in members:
            player = self.call('/api/games/'+game+'/players', {}, token=member['member_token'], status=201)
            self.call('/api/games/'+game+'/votes', {**player,'slot_ids':[slot]}, token=member['member_token'])
        return group, members, game

    def recommendations(self, group, game):
        return self.call('/api/games/'+game+'/recommendations', token=group['member_token'])

    def test_grounded_recommendations_search_order_and_read_only(self):
        group, members, game = self.ready()
        before = self.call('/api/games/'+game, token=group['member_token'])
        with patch('gameon.venues.service.search_pickleball_venues', wraps=search_pickleball_venues) as search:
            result = self.recommendations(group, game)
        self.assertEqual([c.args[1].scope for c in search.call_args_list], ['preferred','expanded'])
        self.assertEqual(result['status'], 'recommended')
        self.assertFalse(result['llm_used'])
        self.assertEqual(result['availability_status'], 'not_checked')
        recommendations = result['recommendations']
        # Expanded canonical coverage contributes a nearer shared facility;
        # saved preferences still receive their existing ranking bonus.
        self.assertEqual({r['venue']['id'] for r in recommendations}, {'level-up-knox','pickleholic-clayton','glen-waverley-bowls'})
        self.assertEqual(recommendations[0]['venue']['id'], 'level-up-knox')
        for recommendation in recommendations:
            self.assertEqual(recommendation['availability_status'], 'not_checked')
            self.assertTrue(recommendation['venue']['website_url'].startswith('https://'))
            if recommendation['venue']['booking_url'] is not None:
                self.assertTrue(recommendation['venue']['booking_url'].startswith('https://'))
            self.assertTrue(recommendation['venue']['sources'])
            self.assertEqual(len(recommendation['distances']), 4)
        self.assertEqual(self.call('/api/games/'+game, token=group['member_token']), before)
        self.assertNotIn('token', json.dumps(result))

    def test_recommendations_are_private_to_group(self):
        group, _, game = self.ready()
        other = self.group('Different group')
        path = '/api/games/'+game+'/recommendations'
        for token in (None, other['member_token'], group['invitation_token']):
            self.call(path, token=token, status=403)

    def test_individual_and_group_preferences_persist_and_change_score(self):
        group, members, game = self.ready()
        before = {r['venue']['id']: r for r in self.recommendations(group, game)['recommendations']}
        self.call('/api/groups/'+group['group_id']+'/me', {'name':'Alex','suburb':'Clayton',
            'favourites':['pickleholic-clayton']}, token=group['member_token'])
        after = {r['venue']['id']: r for r in self.recommendations(group, game)['recommendations']}
        self.assertAlmostEqual(before['pickleholic-clayton']['ranking_score'] - after['pickleholic-clayton']['ranking_score'], 0.5)
        second = self.game(group)
        slot = self.call('/api/games/'+second, token=group['member_token'])['slots'][0]['id']
        for member in members:
            player = self.call('/api/games/'+second+'/players', {}, token=member['member_token'], status=201)
            self.call('/api/games/'+second+'/votes', {**player,'slot_ids':[slot]}, token=member['member_token'])
        again = {r['venue']['id']: r for r in self.recommendations(group, second)['recommendations']}
        self.assertEqual(again['pickleholic-clayton']['ranking_score'], after['pickleholic-clayton']['ranking_score'])
        self.assertTrue(again['level-up-knox']['group_preferred'])

    def test_explicit_restriction_prevents_expansion(self):
        group, _, game = self.ready()
        self.call('/api/groups/'+group['group_id']+'/settings', {'name':'Restricted group',
            'favourites':['level-up-knox'], 'restrict_venues':True}, token=group['member_token'])
        result = self.recommendations(group, game)
        self.assertEqual([s['scope'] for s in result['searches']], ['preferred'])
        self.assertEqual({r['venue']['id'] for r in result['recommendations']}, {'level-up-knox','pickleholic-clayton'})

    def test_missing_votes_no_overlap_and_unknown_suburbs(self):
        group = self.group()
        game = self.game(group)
        self.assertEqual(self.recommendations(group, game)['status'], 'waiting_for_responses')
        group, members, game = self.ready()
        self.call('/api/groups/'+group['group_id']+'/me', {'name':'Alex','suburb':'Unmapped suburb','favourites':[]}, token=group['member_token'])
        result = self.recommendations(group, game)
        self.assertEqual(result['status'], 'needs_suburbs')
        self.assertEqual(result['recommendations'], [])
        self.assertEqual(result['searches'], [])
        player = self.call('/api/games/'+game+'/players', {}, token=members[1]['member_token'], status=201)
        self.call('/api/games/'+game+'/votes', {**player,'slot_ids':[]}, token=members[1]['member_token'])
        self.assertEqual(self.recommendations(group, game)['status'], 'no_common_time')

    def test_standalone_recommendations_and_one_snapshot_transaction(self):
        from gameon.erne.fixtures import create_scenario
        game = create_scenario(self.store, 'common_time')
        with patch.object(self.store, '_connect', wraps=self.store._connect) as connect:
            from gameon.venues.service import recommend_for_game
            result = recommend_for_game(self.store, game)
            self.assertEqual(connect.call_count, 1)
        self.assertEqual(len(result['recommendations']), 3)
        self.assertEqual(self.call('/api/games/'+game+'/recommendations')['status'], 'recommended')


class RuntimeImportTests(unittest.TestCase):
    def test_server_imports_no_agent_runner_or_model(self):
        command = "import sys; import app; import gameon.venues.service; assert 'langgraph' not in sys.modules; assert 'gameon.erne.graph' not in sys.modules; assert 'gameon.erne.openai_provider' not in sys.modules"
        subprocess.run([sys.executable, '-c', command], check=True, capture_output=True)


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'TEST_DATABASE_URL not configured')
class PostgresRecommendationTests(RecommendationTests):
    from test_postgres import PostgresTests as _PG
    setUp = _PG.setUp
    tearDown = _PG.tearDown
    drop_test_schema = _PG.drop_test_schema


if __name__ == '__main__':
    unittest.main()
