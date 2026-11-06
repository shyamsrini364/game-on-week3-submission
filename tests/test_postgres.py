"""Run the same API contract on real PostgreSQL in disposable, isolated schemas."""
import os
import secrets
import unittest

import psycopg
from psycopg import sql
from fastapi.testclient import TestClient

import app
import test_app
from gameon.postgres import PostgresStore


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'TEST_DATABASE_URL not configured')
class PostgresTests(test_app.GameOnTests):
    def setUp(self):
        self.schema = 'test_' + secrets.token_hex(12)
        self.store = PostgresStore(os.environ['TEST_DATABASE_URL'], self.schema)
        self.addCleanup(self.drop_test_schema)
        self.store.initialise()
        self.store.initialise()  # repeatable without clearing stored records
        self.application = app.create_app(self.store, rate_limit_secret='test-secret')
        self.client = TestClient(self.application)

    def tearDown(self):
        self.client.close()

    def reopen_store(self):
        return PostgresStore(self.store.database_url, self.schema)

    def drop_test_schema(self):
        # Only our freshly generated schema, never the app schemas or database.
        assert self.schema.startswith('test_') and len(self.schema) == 29
        with psycopg.connect(self.store.database_url, connect_timeout=10) as conn:
            conn.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(self.schema)))

    def test_repeated_schema_setup_preserves_records(self):
        game_id, before = self.make_game()
        self.store.initialise()
        self.assertEqual(self.reopen_store().get_game(game_id), before)

    def test_erne_reads_real_postgres_without_changing_votes(self):
        import importlib.util
        if importlib.util.find_spec('langgraph') is None:
            self.skipTest('Optional agent dependencies not installed')
        from gameon.erne.contracts import RunRequest
        from gameon.erne.graph import run_erne
        game_id, _ = self.make_game()
        before = self.store.get_game(game_id)
        result = run_erne(RunRequest(game_id=game_id), self.store)
        self.assertEqual(result.action, 'WAIT', result)
        self.assertEqual(result.stop_reason, 'completed')
        self.assertFalse(result.llm_used)
        self.assertEqual(self.store.get_game(game_id), before)


if __name__ == '__main__':
    unittest.main()
