"""Actual local graph and web security; no paid inference."""
import json
import os
import unittest
from unittest.mock import patch
import test_recommendations
from gameon.erne.web_service import plan_game, capture, PlanningConflict, PlanningUnavailable
from gameon.erne.openai_provider import OpenAIResponsesProvider
from test_erne_openai import FakeResponses


class PlanningResponses(FakeResponses):
    """Offline fixture chooses to rank observed results instead of another search."""
    def __call__(self, body, timeout):
        response = super().__call__(body, timeout)
        request = json.loads(json.loads(body)['input'])
        option = next((o for o in request['options'] if o['tool']=='rank_venues'), None)
        if option:
            response['output'][-1]['content'][0]['text'] = json.dumps({
                'decision_id':request['decision_id'], 'snapshot_id':request['snapshot_id'],
                'selected_option_id':option['option_id'], 'evidence_ids':option['evidence_ids'],
                'reason_code':option['reason_code'], 'explanation':'Rank the observed venue results.'})
        return response


class ErneWebTests(unittest.TestCase):
    _base = test_recommendations.RecommendationTests
    setUp, tearDown = _base.setUp, _base.tearDown
    call, group, join, game, ready = _base.call, _base.group, _base.join, _base.game, _base.ready
    def plan(self, owner, game, status=200):
        return self.call('/api/games/'+game+'/plan', {}, token=owner['member_token'], status=status)

    def test_plan_waiting_and_privacy(self):
        owner, members, game = self.ready()
        player = self.call('/api/games/'+game+'/players', {}, token=members[1]['member_token'], status=201)
        self.call('/api/games/'+game+'/votes', {**player, 'slot_ids':[]}, token=members[1]['member_token'])
        result = self.plan(owner, game)
        self.assertEqual(result['outcome'], 'alternatives_requested')
        self.assertFalse(result['llm_used'])
        self.assertEqual(result['execution_mode'], 'deterministic')
        text = json.dumps(result)
        for value in (owner['member_token'], player['player_id'], 'Alex', 'Sam'):
            self.assertNotIn(value, text)
        self.assertEqual([e['action'] for e in result['trace'] if e['phase']=='tool'], ['read_game','schedule_game'])

    def test_plan_missing_votes_and_recommendations(self):
        owner = self.group(); game = self.game(owner)
        waiting = self.plan(owner, game)
        self.assertEqual(waiting['outcome'], 'waiting')
        owner, _, game = self.ready()
        before = self.store.get_game(game)
        result = self.plan(owner, game)
        self.assertEqual(result['outcome'], 'discovery_recommendation', result)
        self.assertTrue(result['recommendations'])
        self.assertIn('search_pickleball_venues', [e['action'] for e in result['trace']])
        self.assertEqual(before, self.store.get_game(game))
        self.assertEqual(result['planning_state'],'approval_required')
        self.assertEqual(result['progress_delivery'],'completed_run')
        self.assertTrue(all(e['run_id']==result['run_id'] for e in result['activity_timeline']))
        self.plan(owner, game, status=409)

    def test_plan_permissions_hosted_and_paid_fail_closed(self):
        owner, members, game = self.ready()
        other = self.group('Other')
        for who in (members[1], other, {'member_token':owner['invitation_token']}, {'member_token':''}):
            self.plan(who, game, status=403)
        standalone = self.game()
        self.plan(owner, standalone, status=403)
        with patch.dict(os.environ, {'VERCEL':'1'}):
            self.plan(owner, game, status=503)
        with self.assertRaises(PlanningUnavailable):
            plan_game(self.store, game, owner['member_token'], provider=OpenAIResponsesProvider(approved_budget_usd=1))

    def test_plan_stale_state_rejected(self):
        owner, _, game = self.ready()
        from gameon.erne.graph import run_erne
        def changed(*args, **kwargs):
            result = run_erne(*args, **kwargs)
            self.call('/api/groups/'+owner['group_id']+'/settings',
                {'name':'Changed', 'favourites':[], 'restrict_venues':True}, token=owner['member_token'])
            return result
        with patch('gameon.erne.graph.run_erne', side_effect=changed):
            self.plan(owner, game, status=409)

    def test_offline_model_decisions_and_unavailability(self):
        owner, _, game = self.ready()
        result = plan_game(self.store, game, owner['member_token'], provider=OpenAIResponsesProvider(
            transport=FakeResponses(), approved_budget_usd=1))
        self.assertEqual(result['execution_mode'], 'mock_model')
        self.assertTrue(any(e['provenance']=='fixture' for e in result['activity_timeline']))
        self.assertTrue(any(e['decision_source']=='mock' for e in result['trace']))
        self.assertFalse(result['llm_used'])
        owner, _, game = self.ready()
        failed = plan_game(self.store, game, owner['member_token'], provider=OpenAIResponsesProvider(
            transport=FakeResponses('http'), approved_budget_usd=1))
        self.assertEqual(failed['stop_reason'], 'model_rate_limited')
        self.assertNotIn('private', json.dumps(failed))

    def test_approval_alternatives_tampering_and_expiry(self):
        owner, members, game = self.ready()
        before = self.store.get_game(game)
        result = self.plan(owner, game)
        self.assertGreater(len(result['recommendations']), 1)
        completed = [e for e in result['trace'] if e['phase'] == 'observation']
        self.assertEqual(len(completed), len(result['activities']))
        self.assertTrue(all(a['status']=='completed' for a in result['activities']))
        path = '/api/games/'+game+'/booking-handoff'
        selection = result['recommendations'][1]['selection']
        handoff = self.call(path, selection, token=owner['member_token'])
        self.assertEqual(handoff['selection'], selection)
        self.assertEqual(handoff['booking_status'], 'not_confirmed')
        self.assertEqual(handoff['venue_name'], result['recommendations'][1]['name'])
        self.call(path, selection, token=members[1]['member_token'], status=403)
        self.call(path, {**selection, 'venue_id':'invented'}, token=owner['member_token'], status=400)
        self.call(path, {**selection, 'proposal':selection['proposal'][:-1]+'x'}, token=owner['member_token'], status=400)
        with patch('gameon.erne.proposals.time.time', return_value=9999999999):
            self.call(path, selection, token=owner['member_token'], status=400)
        self.assertEqual(before, self.store.get_game(game))
        # Approval is session selection, not persistence or permission to bypass rechecks.
        self.call('/api/groups/'+owner['group_id']+'/settings',
            {'name':'Changed', 'favourites':[], 'restrict_venues':True}, token=owner['member_token'])
        self.call(path, selection, token=owner['member_token'], status=409)

    def test_mock_http_plan_and_stale_vote_approval(self):
        from fastapi.testclient import TestClient
        import app
        owner, members, game = self.ready()
        client = TestClient(app.create_app(self.store, rate_limit_secret='offline-test',
            planning_provider_factory=lambda: OpenAIResponsesProvider(transport=PlanningResponses(), approved_budget_usd=1)))
        try:
            result = client.post('/api/games/'+game+'/plan', headers={'Authorization':'Bearer '+owner['member_token']}).json()
            self.assertEqual(result['execution_mode'], 'mock_model')
            self.assertTrue(any(e['provenance']=='fixture' for e in result['activity_timeline']))
            self.assertTrue(any(e['decision_source']=='mock' for e in result['trace']))
            self.assertTrue(result['recommendations'])
            selection = result['recommendations'][0]['selection']
            response = client.post('/api/games/'+game+'/booking-handoff', json=selection,
                                   headers={'Authorization':'Bearer '+owner['member_token']})
            self.assertEqual(response.status_code, 200, response.text)
            player = self.call('/api/games/'+game+'/players', {}, token=members[1]['member_token'], status=201)
            self.call('/api/games/'+game+'/votes', {**player,'slot_ids':[]}, token=members[1]['member_token'])
            stale = client.post('/api/games/'+game+'/booking-handoff', json=selection,
                               headers={'Authorization':'Bearer '+owner['member_token']})
            self.assertEqual(stale.status_code, 409)
        finally:
            client.close()

    def test_timeout_only_reports_observed_activity(self):
        owner, _, game = self.ready()
        result = plan_game(self.store, game, owner['member_token'], provider=OpenAIResponsesProvider(
            transport=FakeResponses('slow'), approved_budget_usd=1))
        self.assertEqual(result['stop_reason'], 'decision_timeout')
        self.assertEqual(result['execution_status'], 'failed')
        self.assertEqual(result['recommendations'], [])
        self.assertEqual(len(result['activities']), 2)  # read + schedule, never a fabricated search
        self.assertFalse(any('Searched' in a['label'] for a in result['activities']))


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'TEST_DATABASE_URL not configured')
class PostgresErneWebTests(ErneWebTests):
    from test_postgres import PostgresTests as _PG
    setUp, tearDown, drop_test_schema = _PG.setUp, _PG.tearDown, _PG.drop_test_schema
