"""Real dedicated-worker execution; no network, database service or model calls."""
import os
import time
import unittest
from unittest.mock import patch

import test_recommendations
from test_public_mvp import ENV
from gameon.erne.contracts import RunRequest
from gameon.erne.graph import run_erne
from gameon.erne.web_service import capture, plan_game
from gameon.erne.subprocess_worker import prepare_worker
from gameon.venues.directory import CuratedDirectory


class HostedWorkerTests(unittest.TestCase):
    _base = test_recommendations.RecommendationTests
    setUp, tearDown = _base.setUp, _base.tearDown
    call, group, join, game, ready = _base.call, _base.group, _base.join, _base.game, _base.ready

    def test_real_hosted_launcher_preserves_graph_recommendations(self):
        owner, _, game = self.ready()
        before = self.store.get_game(game)
        with patch.dict(os.environ, ENV):
            result = plan_game(self.store, game, owner['member_token'], signing_secret='synthetic')
        self.assertEqual(result['execution_status'], 'completed', result)
        self.assertTrue(result['recommendations'])
        self.assertTrue(result['recommendations'][0]['selection']['proposal'])
        self.assertFalse(result['llm_call_attempted'])
        self.assertEqual(result['availability_status'], 'not_checked')
        self.assertEqual(before, self.store.get_game(game))
        self.assertIn('rank_venues', [x['action'] for x in result['trace']])

    def test_cold_worker_deadline_is_enforced_and_child_reaped(self):
        owner, _, game = self.ready()
        frozen, preferences, _ = capture(self.store, game, owner['member_token'])
        processes = []
        def prepare(args):
            channel = prepare_worker(args)
            processes.append(channel[2])
            return channel
        started = time.monotonic()
        with patch.dict(os.environ, {'VERCEL':'1'}), patch(
                'gameon.erne.subprocess_worker.prepare_worker', side_effect=prepare):
            result = run_erne(RunRequest(game_id=game, venue_preferences=preferences,
                deadline_seconds=0.01), frozen, discovery=CuratedDirectory())
        self.assertEqual(result.stop_reason, 'deadline_exceeded')
        self.assertLess(time.monotonic()-started, 3)
        self.assertTrue(processes)
        self.assertFalse(processes[0].is_alive())

    def test_worker_environment_excludes_credentials(self):
        owner, _, game = self.ready()
        frozen, preferences, _ = capture(self.store, game, owner['member_token'])
        import subprocess
        original = subprocess.Popen
        seen = []
        def launch(*args, **kwargs):
            seen.append(kwargs['env'])
            return original(*args, **kwargs)
        with patch.dict(os.environ, {'VERCEL':'1', 'OPENAI_API_KEY':'synthetic-secret',
                'DATABASE_URL':'synthetic-secret', 'NEON_AUTH_URL':'synthetic-secret'}), patch(
                'gameon.erne.subprocess_worker.subprocess.Popen', side_effect=launch):
            result = run_erne(RunRequest(game_id=game, venue_preferences=preferences),
                              frozen, discovery=CuratedDirectory())
        self.assertEqual(result.stop_reason, 'completed', result)
        for key in ('OPENAI_API_KEY','DATABASE_URL','NEON_AUTH_URL','VERCEL'):
            self.assertNotIn(key, seen[0])
        self.assertEqual(seen[0]['LANGSMITH_TRACING'], 'false')


if __name__ == '__main__':
    unittest.main()
