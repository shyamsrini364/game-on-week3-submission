"""Shared validation/store operations with SQLite for local persistence.

PostgresStore overrides connections, setup and ordering for hosted storage.
"""
from __future__ import annotations

import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .models import GameSession, Player, TimeSlot
from .scheduling import find_common_slots

MELBOURNE = ZoneInfo("Australia/Melbourne")


from .validation import StorageError, DatabaseUnavailable, require_text, _new_id


from .groups import GroupOperations, avatar_seed


class GameStore(GroupOperations):
    player_order = "rowid"
    vote_lock = ""

    def __init__(self, db_path: str | Path, *, initialise=True):
        self.db_path = str(db_path)
        if initialise:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self.initialise()
        elif not Path(self.db_path).is_file():
            raise StorageError("Database file not found.")

    @contextmanager
    def _connect(self, write=False):
        conn = sqlite3.connect(self.db_path, timeout=8)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            # Serialize writes before reading credentials/votes.
            if write:
                conn.execute("BEGIN IMMEDIATE")
            else:
                conn.execute("BEGIN")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialise(self):
        with self._connect() as c:
            c.executescript("""
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS games (
                id TEXT PRIMARY KEY, title TEXT NOT NULL,
                min_players INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS slots (
                id TEXT PRIMARY KEY, game_id TEXT NOT NULL,
                starts_at TEXT NOT NULL, ends_at TEXT NOT NULL,
                FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS players (
                id TEXT PRIMARY KEY, game_id TEXT NOT NULL,
                name TEXT NOT NULL, suburb TEXT NOT NULL,
                token TEXT NOT NULL,
                FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS votes (
                player_id TEXT NOT NULL, slot_id TEXT NOT NULL,
                PRIMARY KEY(player_id, slot_id),
                FOREIGN KEY(player_id) REFERENCES players(id) ON DELETE CASCADE,
                FOREIGN KEY(slot_id) REFERENCES slots(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS vote_submissions (
                player_id TEXT PRIMARY KEY,
                submitted_at TEXT NOT NULL,
                FOREIGN KEY(player_id) REFERENCES players(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_slots_game ON slots(game_id);
            CREATE INDEX IF NOT EXISTS idx_players_game ON players(game_id);
            CREATE TABLE IF NOT EXISTS rate_limits (
                key TEXT PRIMARY KEY, hits INTEGER NOT NULL, expires_at INTEGER NOT NULL
            );
            """)

            c.executescript((Path(__file__).parent / 'groups_schema.sql').read_text(encoding='utf-8'))
            c.executescript((Path(__file__).parent / 'pricing_schema.sql').read_text(encoding='utf-8'))
        with self._connect(write=True) as c:
            columns = {r['name'] for r in c.execute('PRAGMA table_info(games)')}
            if 'duration_minutes' not in columns:
                c.execute('ALTER TABLE games ADD COLUMN duration_minutes INTEGER NOT NULL DEFAULT 90 CHECK(duration_minutes IN (60,90,120))')

    def create_game(self, title: str, start_values: list[str], min_players: int = 4, group_id=None, member_token=None, duration_minutes=90):
        if type(duration_minutes) is not int or duration_minutes not in (60, 90, 120):
            raise StorageError("Choose a duration of 60, 90 or 120 minutes.")
        title = require_text(title, "Game title")
        if title and not 2 <= len(title) <= 75:
            raise StorageError("Game title must be 2–75 characters.")
        if type(min_players) is not int or not 2 <= min_players <= 12:
            raise StorageError("Player minimum must be between 2 and 12.")
        if not isinstance(start_values, list) or not 2 <= len(start_values) <= 6:
            raise StorageError("Please provide 2–6 possible start times.")
        starts = []
        for value in start_values:
            try:
                if not isinstance(value, str) or len(value) != 16:
                    raise ValueError("Expected YYYY-MM-DDTHH:MM")
                naive = datetime.strptime(value, "%Y-%m-%dT%H:%M")
                start = naive.replace(tzinfo=MELBOURNE)
                if start <= datetime.now(MELBOURNE):
                    raise StorageError("Choose future time slots.")
                starts.append(start)
            except StorageError:
                raise
            except (TypeError, ValueError) as e:
                raise StorageError("Use a valid date and time for every slot.") from e
        if len(set(starts)) != len(starts):
            raise StorageError("Each proposed time must be different.")
        if not title:
            # Generate once, on creation. Existing titles are never rewritten.
            earliest = min(starts)
            weekday = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[earliest.weekday()]
            month = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")[earliest.month - 1]
            title = f"Pickleball · {weekday} {earliest.day} {month}"
        game_id = _new_id()
        with self._connect(write=True) as c:
            if group_id is not None:
                self._member(c, group_id, member_token, owner=True, lock=True)
            c.execute("INSERT INTO games (id,title,min_players,created_at,duration_minutes) VALUES (?,?,?,?,?)", (game_id, title, min_players, datetime.now(MELBOURNE).isoformat(), duration_minutes))
            for start in starts:
                c.execute("INSERT INTO slots VALUES (?,?,?,?)", (
                    _new_id(), game_id, start.isoformat(), (start + timedelta(minutes=duration_minutes)).isoformat()
                ))
            if group_id is not None:
                c.execute('INSERT INTO group_games VALUES (?,?)', (game_id, group_id))
        return game_id

    def add_player(self, game_id: str, name: str, suburb: str, member_token=None):
        player_id, token = _new_id(), secrets.token_urlsafe(24)
        with self._connect(write=True) as c:
            if not c.execute("SELECT 1 FROM games WHERE id=?", (game_id,)).fetchone():
                raise StorageError("Game not found.")
            group = c.execute('SELECT group_id FROM group_games WHERE game_id=?', (game_id,)).fetchone()
            member = self._member(c, group['group_id'], member_token, lock=True) if group else None
            if member:
                existing = c.execute('SELECT p.id,p.token FROM players p JOIN member_players mp ON mp.player_id=p.id WHERE mp.membership_id=? AND mp.game_id=?', (member['id'], game_id)).fetchone()
                if existing:
                    return {'player_id': existing['id'], 'player_token': existing['token']}
                name, suburb = member['name'], member['suburb']
            name, suburb = require_text(name, "Name"), require_text(suburb, "Suburb")
            if not (1 <= len(name) <= 45 and 2 <= len(suburb) <= 90):
                raise StorageError("Enter a name and suburb (not a street address).")
            c.execute("INSERT INTO players (id,game_id,name,suburb,token) VALUES (?,?,?,?,?)", (player_id, game_id, name, suburb, token))
            if member:
                c.execute('INSERT INTO member_players VALUES (?,?,?)', (member['id'], game_id, player_id))
        return {"player_id": player_id, "player_token": token}

    def submit_vote(self, game_id: str, player_id: str, token: str, slot_ids: list[str]):
        player_id, token = require_text(player_id, "Player id"), require_text(token, "Player token")
        if not isinstance(slot_ids, list) or not all(isinstance(s, str) for s in slot_ids):
            raise StorageError("Invalid availability selection.")
        if len(set(slot_ids)) != len(slot_ids):
            raise StorageError("A time slot can only be selected once.")
        with self._connect(write=True) as c:
            row = c.execute("SELECT 1 FROM players WHERE id=? AND game_id=? AND token=?" + self.vote_lock, (player_id, game_id, token)).fetchone()
            if not row:
                raise StorageError("Your player session is invalid. Use the same device or rejoin.")
            valid = {r["id"] for r in c.execute("SELECT id FROM slots WHERE game_id=?", (game_id,))}
            if not set(slot_ids) <= valid:
                raise StorageError("One or more time slots are not in this game.")
            c.execute("DELETE FROM votes WHERE player_id=?", (player_id,))
            c.executemany("INSERT INTO votes VALUES (?,?)", [(player_id, sid) for sid in slot_ids])
            c.execute("INSERT INTO vote_submissions VALUES (?,?) ON CONFLICT(player_id) DO UPDATE SET submitted_at=excluded.submitted_at", (player_id, datetime.now(MELBOURNE).isoformat()))

    def load_game_session(self, game_id: str):
        """Read one consistent domain snapshot and title, without player tokens."""
        with self._connect() as c:
            g = c.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()
            if not g:
                raise StorageError("Game not found.")
            slots = c.execute("SELECT * FROM slots WHERE game_id=? ORDER BY starts_at", (game_id,)).fetchall()
            players = c.execute("SELECT id,name,suburb FROM players WHERE game_id=? ORDER BY " + self.player_order, (game_id,)).fetchall()
            voted = {r["player_id"] for r in c.execute("SELECT player_id FROM vote_submissions WHERE player_id IN (SELECT id FROM players WHERE game_id=?)", (game_id,))}
            vote_rows = c.execute("SELECT player_id, slot_id FROM votes WHERE player_id IN (SELECT id FROM players WHERE game_id=?)", (game_id,)).fetchall()
        game = GameSession(id=g["id"], sport="pickleball", min_players=g["min_players"], duration_minutes=g["duration_minutes"] if "duration_minutes" in g.keys() else 90,
            slots=tuple(TimeSlot(r["id"], datetime.fromisoformat(r["starts_at"]), datetime.fromisoformat(r["ends_at"])) for r in slots))
        for r in players:
            game.add_player(Player(r["id"], r["name"], r["suburb"]))
        for r in players:
            if r["id"] in voted:
                game.submit_availability(r["id"], {v["slot_id"] for v in vote_rows if v["player_id"] == r["id"]})
        return game, g["title"]

    def get_game(self, game_id: str):
        game, title = self.load_game_session(game_id)
        with self._connect() as c:
            avatars = {r['player_id']: (avatar_seed(r['membership_id']), r['avatar_id']) for r in
                       c.execute("SELECT mp.player_id,mp.membership_id,COALESCE(a.avatar_id,'auto') AS avatar_id FROM member_players mp LEFT JOIN membership_avatars a ON a.membership_id=mp.membership_id WHERE mp.game_id=?", (game_id,)).fetchall()}
        voted = game.votes
        result = find_common_slots(game)
        return {
            "id": game.id,
            "title": title,
            "sport": "pickleball",
            "min_players": game.min_players,
            "duration_minutes": game.duration_minutes,
            "players": [{"id": p.id, "name": p.name, "suburb": p.suburb, "has_voted": p.id in voted,
                         "avatar_seed": avatars.get(p.id, (avatar_seed(p.id), 'auto'))[0],
                         "avatar_id": avatars.get(p.id, (avatar_seed(p.id), 'auto'))[1]} for p in game.players.values()],
            "slots": [{"id": s.id, "starts_at": s.starts_at.isoformat(), "ends_at": s.ends_at.isoformat()} for s in game.slots],
            "status": result.status,
            "explanation": result.explanation,
            "ranking": [{
                "slot_id": m.slot.id, "available_count": m.available_count,
                "available_names": [game.players[pid].name for pid in m.available_players],
                "available_player_ids": list(m.available_players),
                "pending_count": len(m.unanswered_players)
            } for m in result.matches],
        }

    def get_player_vote(self, game_id: str, player_id: str, token: str):
        token = require_text(token, "Player token")
        with self._connect() as c:
            row = c.execute("SELECT name FROM players WHERE id=? AND game_id=? AND token=?", (player_id, game_id, token)).fetchone()
            if not row:
                raise StorageError("Player session not found.")
            votes = c.execute("SELECT slot_id FROM votes WHERE player_id=?", (player_id,)).fetchall()
            submitted = c.execute("SELECT 1 FROM vote_submissions WHERE player_id=?", (player_id,)).fetchone()
        return {"name": row["name"], "slot_ids": [v["slot_id"] for v in votes], "has_voted": bool(submitted)}

    def check_health(self):
        with self._connect() as c:
            c.execute("SELECT 1 FROM games LIMIT 1").fetchone()

    def allow_request(self, key, limit, window_seconds):
        """Shared fixed-window rate limit. Key is an HMAC, never a raw IP/token."""
        now = int(time.time())
        with self._connect(write=True) as c:
            c.execute("DELETE FROM rate_limits WHERE expires_at <= ?", (now,))
            row = c.execute("""INSERT INTO rate_limits (key,hits,expires_at) VALUES (?,1,?)
                ON CONFLICT(key) DO UPDATE SET hits=rate_limits.hits+1 RETURNING hits""",
                (key, now + window_seconds)).fetchone()
        return row['hits'] <= limit
