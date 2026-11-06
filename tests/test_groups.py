"""Group capability boundaries, exercised against both SQLite and PostgreSQL."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from fastapi.testclient import TestClient
from zoneinfo import ZoneInfo
from unittest.mock import patch
import app
from gameon.storage import GameStore


class GroupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = GameStore(Path(self.temp.name)/'groups.sqlite3')
        self.client = TestClient(app.create_app(self.store, rate_limit_secret='test-groups'))

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def call(self, path, data=None, token=None, status=200):
        headers = {'Authorization': 'Bearer '+token} if token else {}
        r = self.client.post(path, json=data, headers=headers) if data is not None else self.client.get(path, headers=headers)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def group(self, name='Friday friends'):
        return self.call('/api/groups', {'name': name, 'player_name': 'Alex', 'suburb': 'Clayton'}, status=201)

    def join(self, group, suburb='Springvale', favourites=None):
        return self.call('/api/invitations/join', {'code': group['code'], 'invitation_token': group['invitation_token'],
            'name': 'Sam', 'suburb': suburb, 'favourites': favourites or []}, status=201)

    def game(self, group=None, token=None):
        start = datetime.now(ZoneInfo('Australia/Melbourne'))+timedelta(days=7)
        data = {'starts': [(start+timedelta(days=i)).strftime('%Y-%m-%dT%H:%M') for i in range(2)]}
        if group:
            data['group_id'] = group['group_id']
        return self.call('/api/games', data, token=token or (group['member_token'] if group else None), status=201)['game_id']

    def test_two_groups_isolate_members_games_and_old_endpoints(self):
        a, b = self.group(), self.group('Sunday friends')
        member = self.join(a)
        ag, bg = self.game(a), self.game(b)
        for credentials in (None, b['member_token'], a['invitation_token'], a['code']):
            self.call('/api/groups/'+a['group_id'], token=credentials, status=403)
            self.call('/api/games/'+ag, token=credentials, status=403)
            self.call('/api/games/'+ag+'/players', {}, token=credentials, status=403)
            self.call('/api/games/'+ag+'/votes', {}, token=credentials, status=403)
            self.call('/api/games/'+ag+'/players/unknown', {}, token=credentials, status=403)
        visible = self.call('/api/groups/'+a['group_id'], token=member['member_token'])
        self.assertEqual([g['id'] for g in visible['games']], [ag])
        self.assertNotIn(bg, json.dumps(visible))
        self.assertEqual(len(visible['members']), 2)
        self.assertNotIn('suburb', visible['members'][0])
        self.assertNotIn('token', json.dumps(visible))

    def test_invitation_scope_revocation_expiry_and_no_role_escalation(self):
        a, b = self.group(), self.group('Other group')
        preview = self.call('/api/invitations/preview', {'code': a['code'], 'invitation_token': a['invitation_token']})
        self.assertEqual(set(preview), {'name','code'})
        self.call('/api/invitations/preview', {'code': b['code'], 'invitation_token': a['invitation_token']}, status=403)
        member = self.call('/api/invitations/join', {'code': a['code'], 'invitation_token': a['invitation_token'],
            'name': 'Sam', 'suburb': 'Clayton', 'role': 'owner'}, status=201)
        self.assertEqual(self.call('/api/groups/'+a['group_id'], token=member['member_token'])['me']['role'], 'member')
        path = '/api/groups/'+a['group_id']+'/invitation'
        self.call(path, {'action': 'revoke'}, token=member['member_token'], status=403)
        self.call(path, {'action': 'revoke'}, token=a['member_token'])
        self.call('/api/invitations/preview', {'code': a['code'], 'invitation_token': a['invitation_token']}, status=403)
        self.call('/api/groups/'+a['group_id'], token=member['member_token'])
        fresh = self.call(path, {'action': 'rotate'}, token=a['member_token'])['invitation_token']
        self.call('/api/invitations/preview', {'code': a['code'], 'invitation_token': fresh})
        with patch('gameon.groups.time.time', return_value=9999999999):
            self.call('/api/invitations/preview', {'code': a['code'], 'invitation_token': fresh}, status=403)

    def test_creator_settings_recovery_rotation_and_hashes(self):
        a = self.group()
        member = self.join(a)
        path = '/api/groups/'+a['group_id']
        settings = {'name': 'Renamed', 'favourites': ['level-up-knox'], 'restrict_venues': False}
        self.call(path+'/settings', settings, token=member['member_token'], status=403)
        self.call(path+'/settings', settings, token=a['member_token'])
        self.call('/api/groups/restore', {'code': a['code']}, token=a['code'], status=403)
        restored = self.call('/api/groups/restore', {'code': a['code']}, token=a['member_token'])
        self.assertEqual(restored['group_id'], a['group_id'])
        new = self.call(path+'/credential', {}, token=a['member_token'])['member_token']
        self.call(path, token=a['member_token'], status=403)
        self.assertEqual(self.call(path, token=new)['name'], 'Renamed')
        with self.store._connect() as c:
            rows = [dict(r) for r in c.execute('SELECT * FROM memberships')]
            groups = [dict(r) for r in c.execute('SELECT * FROM groups')]
        for secret in (a['member_token'], a['invitation_token'], new):
            self.assertNotIn(secret, json.dumps(rows+groups))

    def test_reusable_membership_and_profile_without_duplicate_game_players(self):
        a = self.group()
        member = self.join(a, favourites=['pickleholic-clayton'])
        games = [self.game(a), self.game(a)]
        for gid in games:
            one = self.call('/api/games/'+gid+'/players', {}, token=member['member_token'], status=201)
            two = self.call('/api/games/'+gid+'/players', {}, token=member['member_token'], status=201)
            self.assertEqual(one, two)
            game = self.call('/api/games/'+gid, token=member['member_token'])
            self.call('/api/games/'+gid+'/votes', {**one,'slot_ids': [game['slots'][0]['id']]}, token=member['member_token'])
            self.assertTrue(self.call('/api/games/'+gid, token=member['member_token'])['players'][0]['has_voted'])
            prefs = self.store.game_preferences(gid, member['member_token'])
            self.assertEqual(prefs.players[0].venue_ids, ('pickleholic-clayton',))
        self.call('/api/groups/'+a['group_id']+'/me', {'name':'Samuel', 'suburb':'Glen Waverley', 'favourites':['level-up-knox']}, token=member['member_token'])
        for gid in games:
            game = self.call('/api/games/'+gid, token=member['member_token'])
            self.assertEqual(game['players'][0]['name'], 'Samuel')
            self.assertTrue(game['players'][0]['has_voted'])
        self.store.initialise()
        self.assertEqual(len(self.call('/api/groups/'+a['group_id'], token=member['member_token'])['games']), 2)

    def test_only_owner_creates_group_games_and_standalone_still_works(self):
        a = self.group()
        member = self.join(a)
        gid = self.game()
        starts = [s['starts_at'][:16] for s in self.call('/api/games/'+gid)['slots']]
        self.call('/api/games', {'group_id': a['group_id'], 'starts': starts}, token=member['member_token'], status=403)
        p = self.call('/api/games/'+gid+'/players', {'name':'Independent','suburb':'Clayton'}, status=201)
        self.assertTrue(p['player_token'])
        self.assertEqual(len(self.call('/api/games/'+gid)['players']), 1)
        self.assertEqual(self.call('/api/groups/'+a['group_id'], token=a['member_token'])['games'], [])

    def test_same_person_can_join_multiple_groups_and_favourites_are_validated(self):
        a, b = self.group(), self.group('Second group')
        ma, mb = self.join(a), self.join(b)
        self.assertNotEqual(ma['membership_id'], mb['membership_id'])
        path = '/api/groups/'+a['group_id']+'/me'
        self.call(path, {'name':'Sam','suburb':'Clayton','favourites':['invented']}, token=ma['member_token'], status=400)
        self.call(path, {'name':'Sam','suburb':'Clayton','favourites':[]}, token=mb['member_token'], status=403)

    def test_multiple_old_and_new_favourites_survive_recurring_games(self):
        group = self.group()
        favourites = ['level-up-knox', 'state-pickleball-bayswater']
        member = self.join(group, favourites=favourites)
        path = '/api/groups/'+group['group_id']
        self.call(path+'/settings', {'name':'Friday friends', 'favourites':favourites,
                  'restrict_venues':False}, token=group['member_token'])
        for _ in range(2):
            gid = self.game(group)
            self.call('/api/games/'+gid+'/players', {}, token=member['member_token'], status=201)
            prefs = self.store.game_preferences(gid, member['member_token'])
            self.assertEqual(set(prefs.group.preferred_venue_ids), set(favourites))
            self.assertEqual(set(prefs.players[0].venue_ids), set(favourites))
        self.store.initialise()
        restored = self.call(path, token=member['member_token'])
        self.assertEqual(set(restored['me']['favourites']), set(favourites))
        self.assertEqual(set(restored['favourites']), set(favourites))

    def test_public_directory_contains_verified_bayswater_and_existing_ids(self):
        directory = self.call('/api/venues')
        venues = {v['id']:v for v in directory['venues']}
        self.assertEqual(venues['state-pickleball-bayswater']['suburb'], 'Bayswater')
        self.assertIn('level-up-knox', venues)
        self.assertIn('Bayswater', directory['suburbs'])


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'TEST_DATABASE_URL not configured')
class PostgresGroupTests(GroupTests):
    from test_postgres import PostgresTests as _PG
    setUp = _PG.setUp
    tearDown = _PG.tearDown
    drop_test_schema = _PG.drop_test_schema


if __name__ == '__main__':
    unittest.main()
