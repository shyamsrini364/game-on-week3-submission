"""Synthetic observations only: verify local route projection and access gates."""
from datetime import datetime,timezone
import os
import unittest
from unittest.mock import patch
from test_playbypoint_service import BrowserServiceTests
from test_playbypoint import CHROME
from gameon.erne.web_service import capture,plan_game,PlanningUnavailable
from gameon.erne.playbypoint_service import LocalPlaybypointSession
from gameon.erne.playbypoint_contracts import CourtObservation,CourtOption
from gameon.erne.tools import read_game
from gameon.erne.contracts import ReadInput
from gameon.groups import AccessDenied


class ProjectionTests(unittest.TestCase):
    setUp,tearDown=BrowserServiceTests.setUp,BrowserServiceTests.tearDown
    call,group,join,game,ready=(BrowserServiceTests.call,BrowserServiceTests.group,
        BrowserServiceTests.join,BrowserServiceTests.game,BrowserServiceTests.ready)

    def test_existing_plan_route_and_signed_selection(self):
        owner,members,game=self.ready()
        frozen,preferences,revision=capture(self.store,game,owner['member_token'])
        snapshot=read_game(frozen,ReadInput(game_id=game));slot=snapshot.slots[0]
        obs=CourtObservation(observation_id='test-double',snapshot_id=snapshot.snapshot_id,
            slot_id=slot.id,venue_id='pickleholic-clayton',revision=revision,player_count=4,
            observed_at=datetime.now(timezone.utc),source_url='https://book.pickleholic.com.au/book',
            source='live_browser_selection',simulated=False,status='results',options=(CourtOption(
                court_id='court-3',court_name='Court 3',starts_at=slot.starts_at,ends_at=slot.ends_at,
                provider_players=1,provider_players_verified=False,currency=None),))
        session=LocalPlaybypointSession(self.store,'synthetic-secret',CHROME,{})
        result=session._run(frozen,game,revision,'pickleholic-clayton',preferences,live_observation=obs)
        self.assertEqual(result.outcome,'discovery_recommendation')
        with patch.dict(os.environ,{'GAMEON_LOCAL_PLAYBYPOINT_VENUE':'pickleholic-clayton','GAMEON_PLAYWRIGHT_EXECUTABLE':CHROME}):
            with patch.object(LocalPlaybypointSession,'get_court_options',return_value=result) as reader:
                with self.assertRaises(AccessDenied):plan_game(self.store,game,members[1]['member_token'],signing_secret='synthetic-secret')
                reader.assert_not_called()
                projected=plan_game(self.store,game,owner['member_token'],signing_secret='synthetic-secret')
            self.assertEqual(len(projected['recommendations']),1)
            card=projected['recommendations'][0]
            self.assertEqual(card['court_availability']['court_name'],'Court 3')
            self.assertFalse(card['court_availability']['checkout_price_verified'])
            self.assertIsNone(card['pricing'])
            self.assertTrue(card['selection']['proposal'])
            with patch.dict(os.environ,{'VERCEL':'1'}):
                with self.assertRaises(PlanningUnavailable):plan_game(self.store,game,owner['member_token'],signing_secret='synthetic-secret')


del BrowserServiceTests
if __name__=='__main__':unittest.main()
