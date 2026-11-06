"""Paid network is never invoked: real DB gates and a simulated graph boundary."""
from dataclasses import replace
import time
import unittest
from unittest.mock import patch

import test_recommendations
from gameon.erne.live_approval import LivePlanningApproval
from gameon.erne.openai_provider import OpenAIResponsesProvider
from gameon.erne.web_service import plan_game, capture, PlanningUnavailable
from gameon.erne.graph import run_erne
from gameon.erne.contracts import RunRequest
from gameon.storage import GameStore


class LiveApprovalTests(unittest.TestCase):
    _base = test_recommendations.RecommendationTests
    setUp, tearDown = _base.setUp, _base.tearDown
    call, group, join, game, ready = _base.call, _base.group, _base.join, _base.game, _base.ready

    def permit(self, game):
        return LivePlanningApproval('synthetic-one-run', game, time.time()+120, 4, .17)

    def provider(self):
        return OpenAIResponsesProvider(approved_budget_usd=.17)

    def test_invalid_approval_never_reaches_graph(self):
        owner, _, game = self.ready()
        grant = self.permit(game)
        bad = [None, replace(grant, game_id='other'), replace(grant, expires_at=0),
               replace(grant, expires_at=float('nan')), replace(grant, max_calls=5),
               replace(grant, budget_usd=.18), replace(grant, budget_usd=float('nan')),
               replace(grant, budget_usd=.16), replace(grant, model='other')]
        with patch('gameon.erne.graph.run_erne') as graph:
            for approval in bad:
                with self.subTest(approval=approval), self.assertRaises(PlanningUnavailable):
                    plan_game(self.store, game, owner['member_token'], provider=self.provider(),
                              live_approval=approval)
            graph.assert_not_called()

    def test_receipt_persists_and_expires_after_approval(self):
        grant = self.permit('synthetic-game')
        grant.consume(self.store, grant.game_id, self.provider())
        # A fresh store object shares the receipt; a restarted app cannot refund it.
        with self.assertRaises(ValueError):
            grant.consume(GameStore(self.store.db_path), grant.game_id, self.provider())
        with patch('gameon.erne.live_approval.time.time', return_value=grant.expires_at+2):
            with self.assertRaises(ValueError):
                grant.consume(self.store, grant.game_id, self.provider())

    def test_authorized_path_metadata_and_no_refund_after_failure(self):
        owner, _, game = self.ready()
        frozen, preferences, _ = capture(self.store, game, owner['member_token'])
        baseline = run_erne(RunRequest(game_id=game, venue_preferences=preferences,
                            mode='deterministic_test'), frozen)
        # This ONLY simulates live metadata. No model request is made.
        simulated = baseline.model_copy(update={'llm_used':True, 'llm_call_attempted':True})
        grant = self.permit(game)
        with patch('gameon.erne.graph.run_erne', return_value=simulated):
            result = plan_game(self.store, game, owner['member_token'], provider=self.provider(),
                               live_approval=grant)
        self.assertEqual(result['execution_mode'], 'live_model')
        self.assertTrue(result['llm_used'])
        self.assertEqual(result['model'], 'gpt-6.1-terra')
        self.assertEqual(result['availability_status'], 'not_checked')
        with self.assertRaises(ValueError):
            grant.consume(self.store, game, self.provider())

        owner, _, game = self.ready()
        grant = replace(self.permit(game), approval_id='synthetic-failure')
        with patch('gameon.erne.graph.run_erne', side_effect=RuntimeError('synthetic')):
            with self.assertRaises(RuntimeError):
                plan_game(self.store, game, owner['member_token'], provider=self.provider(), live_approval=grant)
        with self.assertRaises(ValueError):
            grant.consume(self.store, game, self.provider())

    def test_permission_is_checked_before_spending(self):
        from gameon.groups import AccessDenied
        owner, members, game = self.ready()
        grant = self.permit(game)
        with patch('gameon.erne.graph.run_erne') as graph:
            with self.assertRaises(AccessDenied):
                plan_game(self.store, game, members[1]['member_token'], provider=self.provider(), live_approval=grant)
            graph.assert_not_called()
        grant.consume(self.store, game, self.provider())  # not consumed by denied request

    def test_http_body_cannot_authorize_live_spending(self):
        from app import create_app
        from fastapi.testclient import TestClient
        owner, _, game = self.ready()
        with TestClient(create_app(self.store, planning_provider_factory=self.provider)) as client:
            with patch('gameon.erne.graph.run_erne') as graph:
                response = client.post('/api/games/'+game+'/plan',
                    headers={'Authorization':'Bearer '+owner['member_token']},
                    json={'planning_approval':self.permit(game).__dict__})
                self.assertEqual(response.status_code, 503)
                graph.assert_not_called()
