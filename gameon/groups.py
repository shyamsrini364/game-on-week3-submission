"""Small group-scoped capability system; group codes are not credentials."""
import hashlib
import json
import secrets
import time
from .validation import StorageError, require_text, _new_id
from .accounts import AccountOperations
from .auth import VerifiedIdentity

AVATAR_IDS = ('rally', 'ace', 'sunny', 'river', 'sky', 'blaze')


def validate_avatar(value):
    if value is not None and (not isinstance(value, str) or value not in ('auto', *AVATAR_IDS)):
        raise StorageError('Choose an avatar from the preset characters.')
    return value


class AccessDenied(ValueError):
    def __init__(self):
        super().__init__('Group access denied. Use your invitation or saved membership credential.')


def digest(token):
    if not isinstance(token, str) or not 32 <= len(token) <= 128:
        raise AccessDenied()
    return hashlib.sha256(token.encode()).hexdigest()


def avatar_seed(identity):
    """Public visual seed, never an access credential; stable for this membership."""
    return hashlib.sha256(identity.encode()).hexdigest()[:12]


def profile(name, suburb):
    name, suburb = require_text(name, 'Name'), require_text(suburb, 'Suburb')
    if not 1 <= len(name) <= 45 or not 2 <= len(suburb) <= 90:
        raise StorageError('Enter a first name and suburb, not a street address.')
    return name, suburb


def venue_ids(values):
    from .venues.directory import CuratedDirectory
    valid = {v.id for v in CuratedDirectory().venues}
    if not isinstance(values, list) or len(values) > 10 or any(not isinstance(v, str) for v in values):
        raise StorageError('Invalid venue preferences.')
    if len(set(values)) != len(values) or not set(values) <= valid:
        raise StorageError('Choose venues from the directory.')
    return json.dumps(values)


class GroupOperations(AccountOperations):
    def _save_avatar(self, c, member_id, avatar_id):
        if avatar_id is None:
            return  # Older clients must not erase an existing selection.
        if avatar_id == 'auto':
            c.execute('DELETE FROM membership_avatars WHERE membership_id=?', (member_id,))
        else:
            c.execute('INSERT INTO membership_avatars (membership_id,avatar_id) VALUES (?,?) ON CONFLICT(membership_id) DO UPDATE SET avatar_id=excluded.avatar_id', (member_id, avatar_id))

    def _member(self, c, group_id, token, owner=False, lock=False):
        if isinstance(token, VerifiedIdentity):
            row = c.execute('SELECT * FROM memberships WHERE group_id=? AND id IN (SELECT membership_id FROM account_memberships WHERE account_id=? AND group_id=?)' +
                            (self.vote_lock if lock else ''), (group_id, token.key, group_id)).fetchone()
        else:
            row = c.execute('SELECT * FROM memberships WHERE group_id=? AND token_hash=?' +
                            (self.vote_lock if lock else ''), (group_id, digest(token))).fetchone()
        if not row or (owner and row['role'] != 'owner'):
            raise AccessDenied()
        return row

    def create_group(self, name, player_name, suburb, favourites, avatar_id=None, identity=None):
        avatar_id = validate_avatar(avatar_id)
        name = require_text(name, 'Group name')
        if not 2 <= len(name) <= 75:
            raise StorageError('Group name must be 2–75 characters.')
        player_name, suburb = profile(player_name, suburb)
        favourites = venue_ids(favourites)
        token, invitation = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        member_id = _new_id()
        with self._connect(write=True) as c:
            if identity is not None:
                self._ensure_account(c, identity, player_name, suburb, avatar_id, favourites)
                p = self._account(c, identity)
                player_name, suburb, avatar_id, favourites = (p[k] for k in ('name','suburb','avatar_id','favourites'))
            for _ in range(4):
                group_id, code = _new_id(), 'PICKLE-' + secrets.token_hex(5).upper()
                row = c.execute('INSERT INTO groups (id,code,name,invite_hash,invite_expires) VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING RETURNING id',
                    (group_id, code, name, digest(invitation), int(time.time()) + 30*86400)).fetchone()
                if row:
                    break
            else:
                raise StorageError('Please try creating the group again.')
            c.execute('INSERT INTO memberships VALUES (?,?,?,?,?,?,?)',
                (member_id, group_id, player_name, suburb, 'owner', digest(token), favourites))
            self._save_avatar(c, member_id, avatar_id)
            if identity is not None:
                self._link_account(c, identity, member_id, group_id)
        return {'group_id': group_id, 'code': code, 'membership_id': member_id,
                **({'member_token': token} if identity is None else {}), 'invitation_token': invitation}

    def _invitation(self, c, code, token, lock=False):
        if not isinstance(code, str) or len(code) > 32:
            raise AccessDenied()
        row = c.execute('SELECT * FROM groups WHERE code=? AND invite_hash=? AND invite_expires>?' +
                        (self.vote_lock if lock else ''), (code.upper(), digest(token), int(time.time()))).fetchone()
        if not row:
            raise AccessDenied()
        return row

    def preview_invitation(self, code, token):
        with self._connect() as c:
            g = self._invitation(c, code, token)
            return {'name': g['name'], 'code': g['code']}

    def join_group(self, code, invitation, name, suburb, favourites, avatar_id=None, identity=None):
        avatar_id = validate_avatar(avatar_id)
        name, suburb = profile(name, suburb)
        favourites = venue_ids(favourites)
        member_id, token = _new_id(), secrets.token_urlsafe(32)
        with self._connect(write=True) as c:
            g = self._invitation(c, code, invitation, lock=True)
            if identity is not None:
                self._ensure_account(c, identity, name, suburb, avatar_id, favourites)
                existing = c.execute('SELECT membership_id FROM account_memberships WHERE account_id=? AND group_id=?', (identity.key,g['id'])).fetchone()
                if existing:
                    return {'group_id':g['id'],'code':g['code'],'membership_id':existing['membership_id']}
                p = self._account(c, identity)
                name, suburb, avatar_id, favourites = (p[k] for k in ('name','suburb','avatar_id','favourites'))
            c.execute('INSERT INTO memberships VALUES (?,?,?,?,?,?,?)',
                (member_id, g['id'], name, suburb, 'member', digest(token), favourites))
            self._save_avatar(c, member_id, avatar_id)
            if identity is not None:
                self._link_account(c, identity, member_id, g['id'])
        return {'group_id': g['id'], 'code': g['code'], 'membership_id': member_id, **({'member_token': token} if identity is None else {})}

    def get_group(self, group_id, token):
        with self._connect() as c:
            me = self._member(c, group_id, token)
            g = c.execute('SELECT id,code,name,favourites,restrict_venues FROM groups WHERE id=?', (group_id,)).fetchone()
            members = c.execute("SELECT m.id,m.name,m.role,COALESCE(a.avatar_id,'auto') AS avatar_id FROM memberships m LEFT JOIN membership_avatars a ON a.membership_id=m.id WHERE m.group_id=? ORDER BY m.name,m.id", (group_id,)).fetchall()
            my_avatar = next(m['avatar_id'] for m in members if m['id'] == me['id'])
            games = c.execute('SELECT games.id,games.title FROM games JOIN group_games ON games.id=group_games.game_id WHERE group_games.group_id=? ORDER BY games.created_at DESC', (group_id,)).fetchall()
        return {**dict(g), 'favourites': json.loads(g['favourites']), 'restrict_venues': bool(g['restrict_venues']),
                'members': [{**dict(m), 'avatar_seed': avatar_seed(m['id'])} for m in members], 'games': [dict(g) for g in games],
                'me': {'id': me['id'], 'name': me['name'], 'suburb': me['suburb'], 'role': me['role'], 'avatar_id': my_avatar, 'favourites': json.loads(me['favourites'])}}

    def restore_group(self, code, token):
        with self._connect() as c:
            g = c.execute('SELECT id FROM groups WHERE code=?', (require_text(code, 'Group code').upper(),)).fetchone()
            if not g:
                raise AccessDenied()
            me = self._member(c, g['id'], token)
            return {'group_id': g['id'], 'membership_id': me['id']}

    def update_group(self, group_id, token, name, favourites, restrict):
        name = require_text(name, 'Group name')
        if not 2 <= len(name) <= 75 or type(restrict) is not bool:
            raise StorageError('Invalid group settings.')
        favourites = venue_ids(favourites)
        with self._connect(write=True) as c:
            self._member(c, group_id, token, owner=True, lock=True)
            c.execute('UPDATE groups SET name=?,favourites=?,restrict_venues=? WHERE id=?', (name, favourites, int(restrict), group_id))

    def update_member(self, group_id, token, name, suburb, favourites, avatar_id=None):
        avatar_id = validate_avatar(avatar_id)
        name, suburb = profile(name, suburb)
        favourites = venue_ids(favourites)
        with self._connect(write=True) as c:
            me = self._member(c, group_id, token, lock=True)
            self._save_avatar(c, me['id'], avatar_id)
            c.execute('UPDATE memberships SET name=?,suburb=?,favourites=? WHERE id=?', (name, suburb, favourites, me['id']))
            # Existing session profiles follow the membership; votes are untouched.
            c.execute('UPDATE players SET name=?,suburb=? WHERE id IN (SELECT player_id FROM member_players WHERE membership_id=?)', (name, suburb, me['id']))

    def invitation_action(self, group_id, token, action):
        if action not in ('revoke', 'rotate'):
            raise StorageError('Invalid invitation action.')
        invitation = secrets.token_urlsafe(32) if action == 'rotate' else None
        with self._connect(write=True) as c:
            self._member(c, group_id, token, owner=True, lock=True)
            c.execute('UPDATE groups SET invite_hash=?,invite_expires=? WHERE id=?',
                (digest(invitation) if invitation else None, int(time.time()) + 30*86400 if invitation else 0, group_id))
        return {'invitation_token': invitation}

    def rotate_member_token(self, group_id, token):
        new_token = secrets.token_urlsafe(32)
        with self._connect(write=True) as c:
            me = self._member(c, group_id, token, lock=True)
            c.execute('UPDATE memberships SET token_hash=? WHERE id=?', (digest(new_token), me['id']))
        return {'member_token': new_token}

    def game_group_access(self, game_id, token):
        """All HTTP game routes gate group games; standalone links retain their contract."""
        with self._connect() as c:
            row = c.execute('SELECT group_id FROM group_games WHERE game_id=?', (game_id,)).fetchone()
            if row:
                self._member(c, row['group_id'], token)
                return row['group_id']
        return None

    def game_preferences(self, game_id, token):
        from .venues.contracts import VenuePreferences, GroupProfile, PlayerPreference
        with self._connect() as c:
            g = c.execute('SELECT groups.* FROM groups JOIN group_games ON groups.id=group_games.group_id WHERE game_id=?', (game_id,)).fetchone()
            if not g:
                return VenuePreferences()
            self._member(c, g['id'], token)
            members = c.execute('SELECT mp.player_id,m.favourites FROM member_players mp JOIN memberships m ON m.id=mp.membership_id WHERE mp.game_id=? AND m.group_id=?', (game_id, g['id'])).fetchall()
            return VenuePreferences(group=GroupProfile(group_id=g['id'], preferred_venue_ids=tuple(json.loads(g['favourites'])), restrict_to_preferences=bool(g['restrict_venues'])),
                players=tuple(PlayerPreference(player_id=m['player_id'], venue_ids=tuple(json.loads(m['favourites']))) for m in members))
