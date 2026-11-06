import os
import unittest
import test_groups


class AvatarTests(unittest.TestCase):
    _G=test_groups.GroupTests
    setUp,tearDown=_G.setUp,_G.tearDown
    call,group,join,game=_G.call,_G.group,_G.join,_G.game

    def test_membership_avatar_survives_games_and_profile_edits(self):
        group=self.group()
        first=self.call('/api/groups/'+group['group_id'],token=group['member_token'])['members'][0]['avatar_seed']
        self.assertRegex(first,r'^[a-f0-9]{12}$')
        for _ in range(2):
            game=self.game(group)
            self.call('/api/games/'+game+'/players',{},token=group['member_token'],status=201)
            state=self.call('/api/games/'+game,token=group['member_token'])
            self.assertEqual(state['players'][0]['avatar_seed'],first)
            self.assertFalse(state['players'][0]['has_voted'])
            self.call('/api/groups/'+group['group_id']+'/me',{'name':'Synthetic renamed','suburb':'Springvale','favourites':[]},token=group['member_token'])
        self.assertNotEqual(first,group['member_token'])

    def test_standalone_avatar_is_stable_and_no_vote_is_invented(self):
        game=self.game()
        self.call('/api/games/'+game+'/players',{'name':'Synthetic','suburb':'Clayton'},status=201)
        state=self.call('/api/games/'+game)
        self.assertEqual(state['players'],self.call('/api/games/'+game)['players'])
        self.assertFalse(state['players'][0]['has_voted'])

    def test_selected_avatar_survives_games_votes_profile_and_recovery(self):
        group = self.call('/api/groups', {'name':'Avatar crew','player_name':'Alex',
                         'suburb':'Clayton','avatar_id':'rally'}, status=201)
        path = '/api/groups/'+group['group_id']
        self.assertEqual(self.call(path,token=group['member_token'])['me']['avatar_id'],'rally')
        member = self.call('/api/invitations/join', {'code':group['code'],
            'invitation_token':group['invitation_token'],'name':'Sam','suburb':'Clayton',
            'avatar_id':'ace'},status=201)
        games = [self.game(group), self.game(group)]
        for game in games:
            player = self.call('/api/games/'+game+'/players',{},token=member['member_token'],status=201)
            state = self.call('/api/games/'+game,token=member['member_token'])
            self.assertEqual(state['players'][0]['avatar_id'],'ace')
            self.call('/api/games/'+game+'/votes',{**player,'slot_ids':[state['slots'][0]['id']]},token=member['member_token'])
        profile = {'name':'Sam renamed','suburb':'Springvale','favourites':[]}
        self.call(path+'/me',{**profile,'avatar_id':'sky'},token=member['member_token'])
        # An older client omitting avatar_id must not reset the stored choice.
        self.call(path+'/me',profile,token=member['member_token'])
        restored = self.call('/api/groups/restore',{'code':group['code']},token=member['member_token'])
        self.assertEqual(restored['membership_id'],member['membership_id'])
        self.assertEqual(self.call(path,token=member['member_token'])['me']['avatar_id'],'sky')
        for game in games:
            person = self.call('/api/games/'+game,token=member['member_token'])['players'][0]
            self.assertEqual(person['avatar_id'],'sky')
            self.assertTrue(person['has_voted'])
        self.call(path+'/me',{**profile,'avatar_id':'auto'},token=member['member_token'])
        self.assertEqual(self.call(path,token=member['member_token'])['me']['avatar_id'],'auto')

    def test_avatar_choices_are_allowlisted_and_group_scoped(self):
        group, other = self.group(), self.group('Other crew')
        path = '/api/groups/'+group['group_id']+'/me'
        profile = {'name':'Alex','suburb':'Clayton','favourites':[]}
        for invalid in ['not-a-preset','https://example.com/image.svg',[],{},True,4]:
            self.call(path,{**profile,'avatar_id':invalid},token=group['member_token'],status=400)
        for token in [None,other['member_token'],group['invitation_token']]:
            self.call(path,{**profile,'avatar_id':'blaze'},token=token,status=403)
        self.assertEqual(self.call(path[:-3],token=group['member_token'])['me']['avatar_id'],'auto')
        # A member can only update themselves, even if another ID is supplied.
        member = self.join(group)
        self.call(path,{**profile,'avatar_id':'sunny','membership_id':group['membership_id']},token=member['member_token'])
        self.assertEqual(self.call(path[:-3],token=group['member_token'])['me']['avatar_id'],'auto')
        self.assertEqual(self.call(path[:-3],token=member['member_token'])['me']['avatar_id'],'sunny')

    def test_additive_avatar_setup_preserves_existing_memberships_and_votes(self):
        group = self.group()
        game = self.game(group)
        player = self.call('/api/games/'+game+'/players',{},token=group['member_token'],status=201)
        before = self.call('/api/games/'+game,token=group['member_token'])
        self.call('/api/games/'+game+'/votes',{**player,'slot_ids':[before['slots'][0]['id']]},token=group['member_token'])
        # Reproduce pre-V2 storage: existing users/votes with no avatar table.
        with self.store._connect(write=True) as c:
            c.execute('DROP TABLE membership_avatars')
        self.store.initialise()
        self.store.initialise()  # Repeatability matters for explicit deployment setup.
        person = self.call('/api/games/'+game,token=group['member_token'])['players'][0]
        self.assertEqual(person['avatar_id'],'auto')
        self.assertEqual(person['avatar_seed'],before['players'][0]['avatar_seed'])
        self.assertTrue(person['has_voted'])
        self.assertEqual(self.call('/api/groups/'+group['group_id'],token=group['member_token'])['me']['id'],group['membership_id'])


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'),'TEST_DATABASE_URL not configured')
class PostgresAvatarTests(AvatarTests):
    from test_postgres import PostgresTests as _PG
    setUp,tearDown=_PG.setUp,_PG.tearDown
    drop_test_schema=_PG.drop_test_schema
