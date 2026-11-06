"""Synthetic JWTs and disposable SQLite only; no Neon requests or email."""
import time
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import jwt
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi.testclient import TestClient
from app import create_app
from gameon.auth import NeonVerifier, VerifiedIdentity, AuthenticationRequired
from gameon.storage import GameStore

URL = 'https://ep-synthetic.neonauth.ap-southeast-2.aws.neon.tech/neondb/auth'


class JWTTests(unittest.TestCase):
    def setUp(self):
        self.key = ed25519.Ed25519PrivateKey.generate()
        keys = Mock()
        keys.get_signing_key_from_jwt.return_value = SimpleNamespace(key=self.key.public_key())
        self.verifier = NeonVerifier(URL, jwks_client=keys)

    def token(self, **changes):
        claims = dict(sub='synthetic-owner',iss=self.verifier.issuer,aud=self.verifier.audience,
                      iat=int(time.time()),exp=int(time.time())+60,emailVerified=True,role='authenticated')
        claims.update(changes)
        return jwt.encode(claims,self.key,algorithm='EdDSA')

    def test_valid_signature_and_stable_identity(self):
        self.assertEqual(self.verifier.verify(self.token()), VerifiedIdentity(self.verifier.issuer,'synthetic-owner'))

    def test_forged_expired_wrong_issuer_audience_and_unverified(self):
        for changes in ({'exp':0},{'iss':'https://other.example'},{'aud':'other'},
                        {'emailVerified':False},{'role':'admin'},{'sub':''},{'banned':True}):
            with self.subTest(changes=changes), self.assertRaises(AuthenticationRequired):
                self.verifier.verify(self.token(**changes))
        other = ed25519.Ed25519PrivateKey.generate()
        forged = jwt.encode(jwt.decode(self.token(), options={'verify_signature':False}), other, algorithm='EdDSA')
        with self.assertRaises(AuthenticationRequired):
            self.verifier.verify(forged)
        with self.assertRaises(AuthenticationRequired):
            self.verifier.verify('not.a.jwt')


class MockVerifier:
    url = URL
    issuer = 'https://ep-synthetic.neonauth.ap-southeast-2.aws.neon.tech'
    def verify(self, token):
        if token not in ('owner.mock.jwt','member.mock.jwt','stranger.mock.jwt'):
            raise AuthenticationRequired()
        return VerifiedIdentity(self.issuer,token.split('.')[0])


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = GameStore(Path(self.temp.name)/'auth.sqlite3')
        # Explicit schema setup exclusively in the disposable test file.
        with self.store._connect(write=True) as c:
            c.executescript(Path('gameon/accounts_schema.sql').read_text())
        self.client = TestClient(create_app(self.store,rate_limit_secret='synthetic-auth',auth_verifier=MockVerifier()))

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def call(self, path, data=None, user='owner', status=200):
        headers = {'Authorization':f'Bearer {user}.mock.jwt'} if user else {}
        r = self.client.get(path,headers=headers) if data is None else self.client.post(path,json=data,headers=headers)
        self.assertEqual(r.status_code,status,r.text)
        return r.json()

    def group(self, name='Synthetic crew'):
        result = self.call('/api/groups',dict(name=name,player_name='Alex',suburb='Clayton',avatar_id='ace'),status=201)
        self.assertNotIn('member_token',result)
        return result

    def test_missing_invalid_auth_and_no_body_identity(self):
        self.call('/api/account',user=None,status=401)
        self.call('/api/account',user='forged',status=401)
        self.call('/api/groups',dict(name='Synthetic crew',user_id='owner'),user=None,status=401)

    def test_multiple_groups_profile_restore_and_isolation(self):
        a,b = self.group(),self.group('Second crew')
        result = self.call('/api/account')
        self.assertEqual(len(result['groups']),2)
        self.assertEqual(result['profile']['avatar_id'],'ace')
        self.call('/api/groups/'+a['group_id'],user='stranger',status=403)
        self.call('/api/account/profile',dict(name='Updated',suburb='Richmond',avatar_id='river',favourites=[]))
        for g in (a,b):
            self.assertEqual(self.call('/api/groups/'+g['group_id'])['me']['name'],'Updated')
        # Independent HTTP client, no cookies or localStorage: verified identity is sufficient.
        with TestClient(create_app(self.store,rate_limit_secret='synthetic-auth',auth_verifier=MockVerifier())) as second:
            restored = second.get('/api/account',headers={'Authorization':'Bearer owner.mock.jwt'})
            self.assertEqual(len(restored.json()['groups']),2)

    def test_invitation_idempotency_revocation_and_roles(self):
        g = self.group()
        invitation = dict(code=g['code'],invitation_token=g['invitation_token'],name='Sam',suburb='Richmond',role='owner')
        a = self.call('/api/invitations/join',invitation,'member',201)
        b = self.call('/api/invitations/join',invitation,'member',201)
        self.assertEqual(a,b)
        self.assertEqual(len(self.call('/api/groups/'+g['group_id'])['members']),2)
        self.call('/api/groups/'+g['group_id']+'/invitation',{'action':'rotate'},'member',403)
        self.call('/api/groups/'+g['group_id']+'/invitation',{'action':'revoke'})
        self.call('/api/invitations/join',invitation,'stranger',403)

    def test_explicit_legacy_link_and_takeover_prevention(self):
        g = self.store.create_group('Legacy crew','Alex','Clayton',[], 'ace')
        link = dict(group_id=g['group_id'],member_token=g['member_token'])
        self.call('/api/account/link',{**link,'member_token':'x'*43},status=403)
        self.call('/api/account/link',link)
        self.call('/api/account/link',link) # idempotent
        self.call('/api/account/link',link,'stranger',403)
        self.assertEqual(self.call('/api/groups/'+g['group_id'])['me']['role'],'owner')
        self.assertEqual(self.store.get_group(g['group_id'],g['member_token'])['me']['role'],'owner')

    def test_votes_restore_and_owner_permissions(self):
        g = self.group()
        self.call('/api/invitations/join',dict(code=g['code'],invitation_token=g['invitation_token'],name='Sam',suburb='Richmond'),'member',201)
        start = datetime.now(ZoneInfo('Australia/Melbourne'))+timedelta(days=7)
        game = self.call('/api/games',dict(group_id=g['group_id'],min_players=2,starts=[(start+timedelta(days=i)).strftime('%Y-%m-%dT%H:%M') for i in range(2)]),status=201)['game_id']
        base = '/api/games/'+game
        p = self.call(base+'/players',dict(name='Alex',suburb='Clayton'),status=201)
        self.assertNotIn('player_token',p)
        slot = self.call(base)['slots'][0]['id']
        self.call(base+'/votes',dict(player_id=p['player_id'],slot_ids=[slot]))
        self.call(base+'/votes',dict(player_id=p['player_id'],slot_ids=[]),'member',403)
        self.assertEqual(self.call(base+'/me')['slot_ids'],[slot])
        self.call(base+'/plan',{},'member',403)
        self.call(base+'/booking-handoff',{},'member',403)
        self.call(base,user='stranger',status=403)

    def test_one_account_cannot_link_two_memberships_in_same_group(self):
        g = self.store.create_group('Legacy crew','Alex','Clayton',[])
        m = self.store.join_group(g['code'],g['invitation_token'],'Sam','Richmond',[])
        self.call('/api/account/link',dict(group_id=g['group_id'],member_token=m['member_token']))
        self.call('/api/account/link',dict(group_id=g['group_id'],member_token=g['member_token']),status=403)
        self.assertEqual(self.call('/api/groups/'+g['group_id'])['me']['role'],'member')

    def test_expired_invitation_rejects_even_existing_account(self):
        g = self.group()
        with self.store._connect(write=True) as c:
            c.execute('UPDATE groups SET invite_expires=0 WHERE id=?',(g['group_id'],))
        self.call('/api/invitations/join',dict(code=g['code'],invitation_token=g['invitation_token'],name='Alex',suburb='Clayton'),status=403)

    def test_existing_votes_survive_legacy_link(self):
        g = self.store.create_group('Legacy crew','Alex','Clayton',[])
        start = datetime.now(ZoneInfo('Australia/Melbourne'))+timedelta(days=7)
        game = self.store.create_game('',[(start+timedelta(days=i)).strftime('%Y-%m-%dT%H:%M') for i in range(2)],2,g['group_id'],g['member_token'])
        p = self.store.add_player(game,'Alex','Clayton',g['member_token'])
        slot = self.store.get_game(game)['slots'][0]['id']
        self.store.submit_vote(game,p['player_id'],p['player_token'],[slot])
        self.call('/api/account/link',dict(group_id=g['group_id'],member_token=g['member_token']))
        self.assertEqual(self.call('/api/games/'+game+'/me')['slot_ids'],[slot])


class IsolationTests(unittest.TestCase):
    def test_auth_configuration_rejects_other_schemas_and_hosted(self):
        from gameon.config import create_store
        env={'NEON_AUTH_URL':URL,'DATABASE_URL':'postgresql://synthetic:unused@localhost/neondb','NEON_BRANCH_NAME':'game-on-dev'}
        for schema in ('gameon','gameon_preview','neon_auth',''):
            with patch.dict('os.environ',{**env,'GAMEON_SCHEMA':schema},clear=True),self.assertRaises(RuntimeError):
                create_store()
        with patch.dict('os.environ',{**env,'GAMEON_SCHEMA':'gameon_auth_dev','VERCEL':'1'},clear=True),self.assertRaises(RuntimeError):
            create_store()


if __name__ == '__main__':
    unittest.main()
