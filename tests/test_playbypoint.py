"""Real local browser + graph, offline pages only. No provider network calls."""
from datetime import datetime, timedelta, timezone
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from gameon.storage import GameStore
from gameon.erne.choice_demo import create_choice_game
from gameon.erne.contracts import ReadInput, ScheduleInput, DiscoveryInput
from gameon.erne.tools import read_game, schedule_game
from gameon.erne.graph import run_erne
from gameon.erne.playbypoint import PlaybypointSkill, VENUES, money
from gameon.erne.playbypoint_contracts import BrowserApproval, CheckoutInput, CourtObservation, CheckoutObservation
from playbypoint_fixtures import booking_page

CHROME = os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or os.path.expandvars(r'%LOCALAPPDATA%/ms-playwright/chromium-1234/chrome-win64/chrome.exe')


class BrowserSkillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = GameStore(Path(self.tmp.name)/'fixture.sqlite3')
        self.game, self.slots = create_choice_game(self.store)
        self.snapshot = read_game(self.store, ReadInput(game_id=self.game))
        self.schedule = schedule_game(self.snapshot, ScheduleInput(snapshot_id=self.snapshot.snapshot_id))
        self.request = DiscoveryInput(snapshot_id=self.snapshot.snapshot_id, slot_id=self.slots[0])

    def tearDown(self):
        self.tmp.cleanup()

    def adapter(self, venue='pickleholic-clayton', **page_args):
        pages = {s.id:booking_page(s, category=VENUES[venue].category, **page_args) for s in self.snapshot.slots}
        return PlaybypointSkill(venue, 'test-revision', pages, CHROME)

    def observe(self, skill):
        return skill.get_court_options(self.request, self.snapshot, self.schedule)

    def approved(self, skill, obs):
        skill.approval = BrowserApproval(observation=obs, court_id=obs.options[0].court_id,
            expires_at=datetime.now(timezone.utc)+timedelta(seconds=60))
        return CheckoutInput(snapshot_id=self.snapshot.snapshot_id, slot_id=obs.slot_id,
                             observation_id=obs.observation_id, court_id=obs.options[0].court_id)

    def test_both_venues_price_category_players_and_unknown_fees(self):
        for venue, count in [('pickleholic-clayton',1), ('level-up-knox',1)]:
            with self.subTest(venue=venue):
                obs = self.observe(self.adapter(venue))
                self.assertEqual(obs.status, 'results')
                self.assertEqual(obs.player_count, 4)
                self.assertEqual(obs.options[0].provider_players, count)
                self.assertEqual(obs.options[0].total_minor, 6000)
                self.assertEqual(obs.options[0].per_player_minor, 1500)
                self.assertEqual(obs.options[0].starts_at, self.snapshot.slots[0].starts_at)
                self.assertTrue(obs.simulated)
        self.assertIsNone(self.observe(self.adapter(fees='Not displayed')).options[0].fees_minor)
        for value in ('USD 60.00', '$60 to $80', '', 'free', 'NaN', '$-2.00'):
            self.assertIsNone(money(value))
        self.assertEqual(money('AUD$45.45'), 4545)

    def test_matching_challenge_auth_missing_and_timeout(self):
        for kwargs, expected in [({'wrong_time':True},'unknown'), ({'wrong_category':True},'unknown'),
                                 ({'challenge':True},'challenge'), ({'auth':True},'auth_required'),
                                 ({'missing':True},'timeout'), ({'empty':True},'empty')]:
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.observe(self.adapter(**kwargs)).status, expected)
        bad = self.request.model_copy(update={'slot_id':'not-approved'})
        with self.assertRaises(ValueError):
            self.adapter().get_court_options(bad, self.snapshot, self.schedule)

    def test_checkout_revalidation_transfer_and_expiry(self):
        skill = self.adapter()
        obs = self.observe(skill)
        request = self.approved(skill, obs)
        result = skill.prepare_checkout(request, self.snapshot, self.schedule)
        self.assertEqual(result.status, 'handoff')
        self.assertEqual(result.booking_status, 'not_booked')
        self.assertFalse(result.checkout_transferred)
        self.assertIn('nothing is prefilled', result.instructions)
        skill.fixture_pages[self.slots[0]] = booking_page(self.snapshot.slots[0], total='AUD 65.00')
        self.assertEqual(skill.prepare_checkout(request, self.snapshot, self.schedule).status, 'changed')
        skill.approval = skill.approval.model_copy(update={'expires_at':datetime.now(timezone.utc)-timedelta(seconds=1)})
        with self.assertRaises(ValueError):
            skill.prepare_checkout(request, self.snapshot, self.schedule)
        old = obs.model_copy(update={'observed_at':datetime.now(timezone.utc)-timedelta(seconds=121)})
        self.assertFalse(old.fresh())
        skill.approval = None
        with self.assertRaises(ValueError):
            skill.prepare_checkout(request, self.snapshot, self.schedule)

    def test_blocked_navigation_and_no_unknown_fee_checkout(self):
        skill = self.adapter(malicious=True)
        obs = self.observe(skill)
        result = skill.prepare_checkout(self.approved(skill, obs), self.snapshot, self.schedule)
        self.assertNotEqual(result.status, 'handoff')
        skill = self.adapter(fees='Unknown')
        obs = self.observe(skill)
        self.assertEqual(skill.prepare_checkout(self.approved(skill, obs), self.snapshot, self.schedule).status, 'changed')

    def test_real_graph_replans_without_vote_mutation(self):
        before = self.store.get_game(self.game)
        for scenario in ('A','B','C'):
            skill = self.adapter()
            if scenario != 'A':
                skill.fixture_pages[self.slots[0]] = booking_page(self.snapshot.slots[0], empty=True)
            if scenario == 'C':
                skill.fixture_pages[self.slots[1]] = booking_page(self.snapshot.slots[1], empty=True)
            result = run_erne({'game_id':self.game, 'tool_timeout_seconds':20.0, 'deadline_seconds':50.0}, self.store, discovery=skill)
            self.assertEqual(result.outcome, 'search_exhausted' if scenario=='C' else 'discovery_recommendation', result)
            observations = [e.payload for e in result.observations if isinstance(e.payload, CourtObservation)]
            self.assertEqual(len(observations), 1 if scenario=='A' else 2)
            self.assertEqual(observations[-1].slot_id, self.slots[0] if scenario=='A' else self.slots[1])
            from gameon.erne.activity import project_activity
            activity = project_activity(result)
            self.assertEqual(activity[-1]['stage'], 'needs_user_help' if scenario=='C' else 'approval_required')
            replans = [e for e in activity if e['stage']=='replanning']
            self.assertEqual(len(replans), 0 if scenario=='A' else 1)
            if replans:
                self.assertEqual(replans[0]['provenance'],'fixture')
                self.assertTrue(any(e['status']=='started' and e['stage']=='reviewing_courts' for e in activity[replans[0]['order']:]))
            self.assertTrue(result.simulated)
            self.assertFalse(result.llm_used)
            self.assertEqual(self.store.get_game(self.game), before)
        with patch.dict(os.environ, {'VERCEL':'1'}):
            with self.assertRaises(ValueError):
                self.adapter()


if __name__ == '__main__':
    unittest.main()
