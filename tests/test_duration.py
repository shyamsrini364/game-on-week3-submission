"""Duration persistence, old-client compatibility and non-destructive migration."""
import os
import sqlite3
import unittest
from datetime import datetime
from pathlib import Path
import test_app
from gameon.storage import GameStore


class DurationTests(unittest.TestCase):
    setUp, tearDown = test_app.GameOnTests.setUp, test_app.GameOnTests.tearDown
    request, make_game = test_app.GameOnTests.request, test_app.GameOnTests.make_game
    reopen_store = test_app.GameOnTests.reopen_store

    def test_choices_persist_and_are_in_snapshot_and_recommendation_handoff(self):
        from gameon.erne.tools import read_game
        from gameon.erne.contracts import ReadInput
        _, original = self.make_game()
        starts = [s['starts_at'][:16] for s in original['slots']]
        for duration in (60, 90, 120):
            created = self.request('/api/games', {'starts': starts, 'duration_minutes': duration}, 201)
            gid = created['game_id']
            game = self.reopen_store().get_game(gid)
            self.assertEqual(game['duration_minutes'], duration)
            for slot in game['slots']:
                self.assertEqual((datetime.fromisoformat(slot['ends_at'])-datetime.fromisoformat(slot['starts_at'])).total_seconds(), duration*60)
            self.assertEqual(read_game(self.store, ReadInput(game_id=gid)).duration_minutes, duration)
            result = self.request('/api/games/'+gid+'/recommendations')
            self.assertEqual(result['duration_minutes'], duration)

    def test_omitted_duration_defaults_but_invalid_explicit_values_fail(self):
        _, game = self.make_game()
        self.assertEqual(game['duration_minutes'], 90)
        starts = [s['starts_at'][:16] for s in game['slots']]
        for value in (None, '', '90', 'later', 0, 30, 75, 180, True, 90.0):
            self.request('/api/games', {'starts': starts, 'duration_minutes': value}, 400)

    def test_repeat_setup_preserves_existing_votes_names_and_times(self):
        gid, game = self.make_game()
        player = self.request('/api/games/'+gid+'/players', {'name':'Synthetic','suburb':'Clayton'}, 201)
        self.request('/api/games/'+gid+'/votes', {**player, 'slot_ids':[game['slots'][0]['id']]})
        before = self.store.get_game(gid)
        self.store.initialise()
        self.store.initialise()
        self.assertEqual(self.reopen_store().get_game(gid), before)


class SQLiteDurationMigrationTests(unittest.TestCase):
    def test_pre_duration_database_migrates_without_rewriting_slots(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'legacy.sqlite3'
            with sqlite3.connect(path) as c:
                c.executescript('''CREATE TABLE games (id TEXT PRIMARY KEY,title TEXT NOT NULL,min_players INTEGER NOT NULL,created_at TEXT NOT NULL);
                CREATE TABLE slots (id TEXT PRIMARY KEY,game_id TEXT NOT NULL,starts_at TEXT NOT NULL,ends_at TEXT NOT NULL);
                INSERT INTO games VALUES ('old','Original custom title',4,'2020-01-01');
                INSERT INTO slots VALUES ('slot','old','2020-01-01T10:00:00+11:00','2020-01-01T11:30:00+11:00');''')
            c.close()
            store = GameStore(path)
            game = store.get_game('old')
            self.assertEqual(game['duration_minutes'], 90)
            self.assertEqual(game['title'], 'Original custom title')
            self.assertEqual(game['slots'][0]['ends_at'], '2020-01-01T11:30:00+11:00')


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'TEST_DATABASE_URL not configured')
class PostgresDurationTests(DurationTests):
    from test_postgres import PostgresTests as _PG
    setUp, tearDown = _PG.setUp, _PG.tearDown
    drop_test_schema, reopen_store = _PG.drop_test_schema, _PG.reopen_store

    def test_pre_duration_schema_migrates_preserving_records(self):
        gid, before = self.make_game()
        with self.store._connect(write=True) as c:
            c.execute('ALTER TABLE games DROP COLUMN duration_minutes')
        self.store.initialise()
        self.assertEqual(self.store.get_game(gid), before)


if __name__ == '__main__':
    unittest.main()
