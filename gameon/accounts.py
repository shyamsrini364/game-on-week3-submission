"""Additive identity-to-membership links; no matching on names or email addresses."""
import json
from .auth import VerifiedIdentity, AuthenticationRequired


class AccountOperations:
    def _account(self, c, identity):
        if not isinstance(identity, VerifiedIdentity):
            raise AuthenticationRequired()
        return c.execute('SELECT * FROM account_profiles WHERE id=?', (identity.key,)).fetchone()

    def _ensure_account(self, c, identity, name, suburb, avatar, favourites):
        if not isinstance(identity, VerifiedIdentity):
            raise AuthenticationRequired()
        c.execute('INSERT INTO account_profiles (id,issuer,subject,name,suburb,avatar_id,favourites) VALUES (?,?,?,?,?,?,?) ON CONFLICT DO NOTHING',
                  (identity.key,identity.issuer,identity.subject,name,suburb,avatar or 'auto',favourites))

    def _link_account(self, c, identity, membership_id, group_id):
        from .groups import AccessDenied
        c.execute('INSERT INTO account_memberships (account_id,membership_id,group_id) VALUES (?,?,?) ON CONFLICT DO NOTHING',
                  (identity.key,membership_id,group_id))
        row = c.execute('SELECT account_id,membership_id FROM account_memberships WHERE membership_id=?', (membership_id,)).fetchone()
        if not row or row['account_id'] != identity.key:
            raise AccessDenied()

    def account_summary(self, identity):
        with self._connect() as c:
            account = self._account(c, identity)
            if not account:
                return {'profile':None,'groups':[]}
            groups = c.execute('SELECT g.id,g.name,m.role FROM account_memberships a JOIN memberships m ON m.id=a.membership_id JOIN groups g ON g.id=m.group_id WHERE a.account_id=? ORDER BY g.name,g.id', (identity.key,)).fetchall()
        return {'profile':{k:json.loads(account[k]) if k=='favourites' else account[k] for k in ('name','suburb','avatar_id','favourites')},
                'groups':[dict(g) for g in groups]}

    def update_account(self, identity, name, suburb, avatar, favourites):
        from .groups import profile, validate_avatar, venue_ids
        name, suburb = profile(name, suburb)
        avatar = validate_avatar(avatar) or 'auto'
        favourites = venue_ids(favourites)
        with self._connect(write=True) as c:
            self._ensure_account(c, identity, name, suburb, avatar, favourites)
            c.execute('UPDATE account_profiles SET name=?,suburb=?,avatar_id=?,favourites=? WHERE id=?', (name,suburb,avatar,favourites,identity.key))
            members = c.execute('SELECT membership_id FROM account_memberships WHERE account_id=?', (identity.key,)).fetchall()
            for m in members:
                self._save_avatar(c,m['membership_id'],avatar)
                c.execute('UPDATE memberships SET name=?,suburb=?,favourites=? WHERE id=?',(name,suburb,favourites,m['membership_id']))
                c.execute('UPDATE players SET name=?,suburb=? WHERE id IN (SELECT player_id FROM member_players WHERE membership_id=?)',(name,suburb,m['membership_id']))
        return self.account_summary(identity)

    def link_legacy_membership(self, identity, group_id, credential):
        with self._connect(write=True) as c:
            # Proof of possession and current role are checked under the write lock.
            member = self._member(c,group_id,credential,lock=True)
            avatar = c.execute('SELECT avatar_id FROM membership_avatars WHERE membership_id=?',(member['id'],)).fetchone()
            self._ensure_account(c,identity,member['name'],member['suburb'],avatar['avatar_id'] if avatar else 'auto',member['favourites'])
            self._link_account(c,identity,member['id'],group_id)
        return {'ok':True,'group_id':group_id}

    def account_player(self, game_id, identity):
        from .groups import AccessDenied
        with self._connect() as c:
            g = c.execute('SELECT group_id FROM group_games WHERE game_id=?',(game_id,)).fetchone()
            if not g:
                raise AccessDenied()
            member = self._member(c,g['group_id'],identity)
            p = c.execute('SELECT p.* FROM players p JOIN member_players mp ON p.id=mp.player_id WHERE mp.game_id=? AND mp.membership_id=?',(game_id,member['id'])).fetchone()
            if not p:
                return None
            return dict(p)  # Internal only: HTTP handlers never expose the legacy player token.
