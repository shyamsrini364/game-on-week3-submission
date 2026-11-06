"""Preview configuration must fail closed before opening any connection."""
import unittest
from unittest.mock import Mock, patch

from gameon.config import create_store


class PreviewConfigurationTests(unittest.TestCase):
    def test_preview_rejects_missing_url_or_unsafe_schema(self):
        configurations = [
            {},
            {'GAMEON_SCHEMA': 'gameon_preview'},
            {'DATABASE_URL': 'synthetic-url'},
        ] + [
            {'DATABASE_URL': 'synthetic-url', 'GAMEON_SCHEMA': schema}
            for schema in ['', 'gameon', 'public', 'other', 'gameon_preview_other;DROP',
                           'gameon_preview_', 'gameon_preview_' + 'a' * 50]
        ]
        for config in configurations:
            with self.subTest(config=config), patch.dict('os.environ', {'VERCEL_ENV': 'preview', **config}, clear=True), patch('gameon.postgres.PostgresStore') as postgres, patch('gameon.config.GameStore') as sqlite:
                with self.assertRaisesRegex(RuntimeError, 'explicit preview schema'):
                    create_store()
                postgres.assert_not_called()
                sqlite.assert_not_called()

    def test_preview_accepts_only_explicit_preview_namespaces(self):
        for schema in ['gameon_preview', 'gameon_preview_v2', 'gameon_preview_20261104']:
            with self.subTest(schema=schema), patch.dict('os.environ', {'VERCEL_ENV': 'preview', 'DATABASE_URL': 'synthetic-url', 'GAMEON_SCHEMA': schema}, clear=True), patch('gameon.postgres.PostgresStore') as postgres:
                self.assertIs(create_store(), postgres.return_value)
                postgres.assert_called_once_with('synthetic-url', schema)

    def test_production_without_explicit_mvp_profile_fails_closed(self):
        for config, expected in [({}, 'gameon'), ({'GAMEON_SCHEMA': 'other_schema'}, 'other_schema')]:
            with self.subTest(config=config), patch.dict('os.environ', {'VERCEL': '1', 'VERCEL_ENV': 'production', 'DATABASE_URL': 'synthetic-url', **config}, clear=True), patch('gameon.postgres.PostgresStore') as postgres:
                with self.assertRaisesRegex(RuntimeError, 'approved isolated target'):
                    create_store()
                postgres.assert_not_called()

    def test_hosted_missing_database_still_rejects_sqlite(self):
        with patch.dict('os.environ', {'VERCEL': '1'}, clear=True), patch('gameon.config.GameStore') as sqlite:
            with self.assertRaisesRegex(RuntimeError, 'DATABASE_URL'):
                create_store()
            sqlite.assert_not_called()

    def test_local_sqlite_still_uses_selected_path(self):
        with patch.dict('os.environ', {'GAMEON_DB': 'synthetic.sqlite3'}, clear=True), patch('gameon.config.GameStore') as sqlite:
            create_store()
            sqlite.assert_called_once_with('synthetic.sqlite3')

    def test_hosted_missing_rate_secret_fails_before_database_access(self):
        import app
        from fastapi.testclient import TestClient
        store = Mock()
        with patch.dict('os.environ', {'VERCEL': '1', 'VERCEL_ENV': 'preview'}, clear=True):
            with TestClient(app.create_app(store=store)) as client:
                response = client.get('/api/health')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'error': 'Service configuration unavailable.'})
        store.allow_request.assert_not_called()
        store.check_health.assert_not_called()
