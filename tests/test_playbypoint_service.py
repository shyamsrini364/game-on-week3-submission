"""Owner-bound local service, signed exact-selection approval and replay denial."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import unittest
import test_recommendations
from gameon.erne.playbypoint_service import LocalPlaybypointSession
from gameon.erne.playbypoint_contracts import CourtObservation, CheckoutObservation, CourtOption
from gameon.erne.web_service import capture
from gameon.groups import AccessDenied
from gameon.validation import StorageError
from gameon.erne import proposals
from playbypoint_fixtures import booking_page
from test_playbypoint import CHROME


class BrowserServiceTests(unittest.TestCase):
    _base = test_recommendations.RecommendationTests
    setUp, tearDown = _base.setUp, _base.tearDown
    call, group, join, game, ready = _base.call, _base.group, _base.join, _base.game, _base.ready

    def session(self):
        owner, members, game = self.ready()
        frozen, _, _ = capture(self.store, game, owner['member_token'])
        pages = {'pickleholic-clayton':{s.id:booking_page(s) for s in frozen.game.slots}}
        return owner, members, game, LocalPlaybypointSession(self.store, 'synthetic-only', CHROME, pages)

    def test_owner_approval_exact_selection_and_no_replay(self):
        owner, members, game, service = self.session()
        for token in ('', members[1]['member_token'], owner['invitation_token']):
            with self.assertRaises(AccessDenied):
                service.get_court_options(game, token, 'pickleholic-clayton')
        before = self.store.get_game(game)
        result = service.get_court_options(game, owner['member_token'], 'pickleholic-clayton')
        obs = next(e.payload for e in result.observations if isinstance(e.payload, CourtObservation))
        court = obs.options[0].court_id
        with self.assertRaises(StorageError):
            service.approve_checkout(game, owner['member_token'], obs.observation_id, 'other-court')
        receipt = service.approve_checkout(game, owner['member_token'], obs.observation_id, court)
        with self.assertRaises(StorageError):
            service.prepare_checkout(game, owner['member_token'], obs.observation_id, court, receipt+'tampered')
        result = service.prepare_checkout(game, owner['member_token'], obs.observation_id, court, receipt)
        checkout = next(e.payload for e in result.observations if isinstance(e.payload, CheckoutObservation))
        self.assertEqual(checkout.status, 'handoff', result)
        self.assertFalse(checkout.checkout_transferred)
        with self.assertRaises(StorageError):
            service.prepare_checkout(game, owner['member_token'], obs.observation_id, court, receipt)
        self.assertEqual(self.store.get_game(game), before)

    def test_expired_receipt_stale_observation_and_revision(self):
        owner, _, game, service = self.session()
        result = service.get_court_options(game, owner['member_token'], 'pickleholic-clayton')
        obs = next(e.payload for e in result.observations if isinstance(e.payload, CourtObservation))
        court = obs.options[0].court_id
        receipt = service.approve_checkout(game, owner['member_token'], obs.observation_id, court)
        with patch('gameon.erne.proposals.time.time', return_value=datetime.now(timezone.utc).timestamp()+601):
            with self.assertRaises(StorageError):
                service.prepare_checkout(game, owner['member_token'], obs.observation_id, court, receipt)
        service.observations[obs.observation_id] = (game, obs.model_copy(update={'observed_at':datetime.now(timezone.utc)-timedelta(seconds=121)}))
        with self.assertRaises(StorageError):
            service.prepare_checkout(game, owner['member_token'], obs.observation_id, court, receipt)

        service.observations[obs.observation_id] = (game, obs)
        self.call('/api/groups/'+owner['group_id']+'/settings', {'name':'Changed','favourites':[], 'restrict_venues':True}, token=owner['member_token'])
        with self.assertRaises(StorageError):
            service.prepare_checkout(game, owner['member_token'], obs.observation_id, court, receipt)

    def test_existing_preference_restriction_is_preserved(self):
        owner, _, game, service = self.session()
        # Group and member favourites include Pickleholic, so use a tight existing distance rule.
        frozen, preferences, revision = capture(self.store, game, owner['member_token'])
        preferences = preferences.model_copy(update={'group': preferences.group.model_copy(update={'max_individual_distance_km':0.01})})
        result = service._run(frozen, game, revision, 'pickleholic-clayton', preferences)
        self.assertEqual(result.outcome, 'human_handoff')
        self.assertFalse(any(e.source=='get_court_options' for e in result.observations))

    def test_live_read_auth_graph_provenance_and_checkout_denial(self):
        owner, members, game, service = self.session()
        frozen, _, _ = capture(self.store, game, owner['member_token'])
        slot_id = frozen.game.slots[0].id
        def simulated_live_read(page, venue, revision, request, slot, count, *, provider_players=1):
            # Test double, never evidence of real provider access.
            return CourtObservation(observation_id='mock-live', snapshot_id=request.snapshot_id,
                slot_id=slot.id, venue_id=venue, revision=revision, player_count=count,
                observed_at=datetime.now(timezone.utc), source_url='https://book.pickleholic.com.au/book',
                simulated=False, source='live_browser_summary', status='results', options=(
                    CourtOption(court_id='court-3',court_name='Court 3',starts_at=slot.starts_at,
                        ends_at=slot.ends_at,total_minor=100,fees_minor=0,provider_players=count),))
        with patch('gameon.erne.playbypoint_live.read_summary', side_effect=simulated_live_read) as reader:
            with self.assertRaises(AccessDenied):
                service.get_court_options(game, members[1]['member_token'], 'pickleholic-clayton',live_page=object(),slot_id=slot_id)
            reader.assert_not_called()
            result = service.get_court_options(game, owner['member_token'], 'pickleholic-clayton',live_page=object(),slot_id=slot_id)
            reader.assert_called_once()
        self.assertEqual(result.outcome, 'discovery_recommendation')
        self.assertFalse(result.simulated)
        self.assertFalse(result.llm_used)
        obs = next(e.payload for e in result.observations if isinstance(e.payload,CourtObservation))
        with self.assertRaises(StorageError):
            service.approve_checkout(game,owner['member_token'],obs.observation_id,'court-3')


if __name__ == '__main__':
    unittest.main()
