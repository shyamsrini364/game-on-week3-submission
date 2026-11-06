"""Booking handoff: real store/API checks, synthetic players, no provider requests."""
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from gameon.booking import prepare_handoff, StaleHandoff
from gameon.venues.directory import CuratedDirectory
import test_recommendations


class BookingTests(unittest.TestCase):
    _R = test_recommendations.RecommendationTests
    setUp, tearDown = _R.setUp, _R.tearDown
    call, group, join, game = _R.call, _R.group, _R.join, _R.game
    ready, recommendations = _R.ready, _R.recommendations

    def selection(self, group, game):
        r = self.recommendations(group, game)
        return {'revision': r['revision'], 'slot_id': r['slot_id'], 'venue_id': r['recommendations'][0]['venue']['id']}

    def handoff(self, group, game, selection, status=200):
        return self.call('/api/games/'+game+'/booking-handoff', selection, token=group['member_token'], status=status)

    def test_deterministic_private_brief_and_no_booking_mutation(self):
        group, members, game = self.ready()
        selection = self.selection(group, game)
        before = self.call('/api/games/'+game, token=group['member_token'])
        first = self.handoff(group, game, selection)
        self.assertEqual(first, self.handoff(group, game, selection))
        self.assertEqual(before, self.call('/api/games/'+game, token=group['member_token']))
        self.assertEqual(first['booking_status'], 'not_confirmed')
        self.assertEqual(first['availability_status'], 'not_checked')
        self.assertEqual(first['player_count'], 4)
        venue = self.recommendations(group, game)['recommendations'][0]['venue']
        self.assertEqual(first['venue_address'], venue['address'])
        self.assertEqual(first['venue_suburb'], venue['suburb'])
        self.assertIn('Australia/Melbourne', first['brief'])
        for required in ('UNVERIFIED', 'including fees', 'cancellation', 'explicit approval', 'before making a reservation', 'provider confirmation'):
            self.assertIn(required, first['brief'])
        for private in ('Alex', 'Sam', group['invitation_token'], group['member_token'], game, 'player_id', 'member_token', '?group=', '#invite'):
            self.assertNotIn(private, first['brief'])
        self.assertNotIn('token', json.dumps(first))
        self.assertTrue(first['booking_url'].startswith('https://'))

    def test_only_owner_of_this_group_and_standalone_unchanged(self):
        group, members, game = self.ready()
        selection = self.selection(group, game)
        other = self.group('Another group')
        path = '/api/games/'+game+'/booking-handoff'
        for token in (None, members[1]['member_token'], other['member_token'], group['invitation_token'], group['code']):
            self.call(path, selection, token=token, status=403)
        self.assertFalse(self.recommendations(members[1], game)['can_prepare_handoff'])
        standalone = self.game()
        self.call('/api/games/'+standalone)
        self.call('/api/games/'+standalone+'/booking-handoff', selection, token=group['member_token'], status=403)
        rotated = self.call('/api/groups/'+group['group_id']+'/credential', {}, token=group['member_token'])
        self.handoff(group, game, selection, 403)
        self.handoff({**group, **rotated}, game, selection)

    def test_stale_votes_pending_players_and_recommendations(self):
        group, members, game = self.ready()
        selection = self.selection(group, game)
        self.assertEqual(selection, self.selection(group, game))
        self.call('/api/groups/'+group['group_id']+'/settings', {'name':'Synthetic', 'favourites':['picklezone-springvale'], 'restrict_venues':True}, token=group['member_token'])
        self.handoff(group, game, selection, 409)
        fresh = self.selection(group, game)
        self.assertIn('Stay within the saved preferred venues', self.handoff(group, game, fresh)['brief'])
        extra = self.join(group)
        self.call('/api/games/'+game+'/players', {}, token=extra['member_token'], status=201)
        self.handoff(group, game, fresh, 409)
        group, members, game = self.ready()
        selection = self.selection(group, game)
        player = self.call('/api/games/'+game+'/players', {}, token=members[1]['member_token'], status=201)
        self.call('/api/games/'+game+'/votes', {**player,'slot_ids':[]}, token=members[1]['member_token'])
        self.handoff(group, game, selection, 409)

    def test_forged_selection_and_untrusted_extra_data(self):
        group, _, game = self.ready()
        selection = self.selection(group, game)
        for changes in ({'venue_id':'not-in-directory'}, {'slot_id':'another-slot'}, {'revision':'old'}):
            self.handoff(group, game, {**selection, **changes}, 409)
        for changes in ({'booking_url':'https://attacker.invalid'}, {'duration_minutes':120}, {'constraints':'Private home address'}, {'venue_id':[]}):
            self.handoff(group, game, {**selection, **changes}, 400)
        self.handoff(group, game, {}, 400)

    def test_expired_slot_and_missing_votes(self):
        group, _, game = self.ready()
        selection = self.selection(group, game)
        with patch('gameon.venues.service.eligible_slots', return_value=()):
            self.handoff(group, game, selection, 409)
        # The last check also prevents a start expiring while preparing the brief.
        with patch('gameon.booking.datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime.now(timezone.utc) + timedelta(days=30)
            self.handoff(group, game, selection, 409)
        pending = self.game(group)
        self.handoff(group, pending, selection, 409)

    def test_directory_change_and_missing_booking_url(self):
        group, _, game = self.ready()
        selection = self.selection(group, game)
        directory = CuratedDirectory()
        directory.venues = tuple(v.model_copy(update={'booking_url':None}) if v.id == selection['venue_id'] else v for v in directory.venues)
        with self.assertRaises(StaleHandoff):
            prepare_handoff(self.store, game, group['member_token'], selection, directory)
        from gameon.venues.service import recommend_for_game
        r = recommend_for_game(self.store, game, group['member_token'], directory)
        value = prepare_handoff(self.store, game, group['member_token'], {**selection, 'revision':r['revision']}, directory)
        self.assertIsNone(value['booking_url'])
        self.assertIn('Not listed; contact the venue', value['brief'])
        self.assertIn(value['website_url'], value['brief'])

    def test_duration_dates_player_count_and_snapshot_transaction(self):
        group, members, game = self.ready()
        # A fifth member votes for another time: the brief must say 4 attending, 5 joined.
        extra = self.join(group)
        player = self.call('/api/games/'+game+'/players', {}, token=extra['member_token'], status=201)
        second = self.call('/api/games/'+game, token=group['member_token'])['slots'][1]['id']
        self.call('/api/games/'+game+'/votes', {**player,'slot_ids':[second]}, token=extra['member_token'])
        for duration, start in ((60,'2030-01-10T23:30:00+11:00'), (90,'2030-07-10T23:30:00+10:00'), (120,'2030-01-10T23:30:00+11:00')):
            old = self.selection(group, game)
            with self.store._connect(write=True) as c:
                c.execute('UPDATE games SET duration_minutes=? WHERE id=?', (duration, game))
                begin = datetime.fromisoformat(start)
                c.execute('UPDATE slots SET starts_at=?,ends_at=? WHERE id=?', (begin.isoformat(), (begin+timedelta(minutes=duration)).isoformat(), old['slot_id']))
                c.execute('UPDATE slots SET starts_at=?,ends_at=? WHERE id=?', ((begin+timedelta(days=1)).isoformat(), (begin+timedelta(days=1,minutes=duration)).isoformat(), second))
            self.handoff(group, game, old, 409)
            selection = self.selection(group, game)
            with patch.object(self.store, '_connect', wraps=self.store._connect) as connect:
                value = prepare_handoff(self.store, game, group['member_token'], selection)
                # One consistent game snapshot plus a separate read-only historical price lookup.
            self.assertEqual(connect.call_count, 2)
            self.assertEqual(value['duration_minutes'], duration)
            self.assertEqual((datetime.fromisoformat(value['ends_at'])-datetime.fromisoformat(value['starts_at'])).total_seconds(), duration*60)
            self.assertEqual(value['player_count'], 4)
            self.assertEqual(value['session_player_count'], 5)
            self.assertIn('2030-01-11' if duration != 90 else '2030-07-11', value['brief'])
            self.assertIn('AEDT' if duration != 90 else 'AEST', value['brief'])

    def test_inconsistent_duration_fails_closed(self):
        group, _, game = self.ready()
        selection = self.selection(group, game)
        with self.store._connect(write=True) as c:
            c.execute('UPDATE games SET duration_minutes=60 WHERE id=?', (game,))
        selection = self.selection(group, game)
        self.handoff(group, game, selection, 400)


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'TEST_DATABASE_URL not configured')
class PostgresBookingTests(BookingTests):
    from test_postgres import PostgresTests as _PG
    setUp, tearDown = _PG.setUp, _PG.tearDown
    drop_test_schema = _PG.drop_test_schema


if __name__ == '__main__':
    unittest.main()
