"""Public runtime security gates; synthetic config/JWTs, no remote calls."""
import os
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import create_app
from gameon.config import create_store
from gameon.runtime_profile import validate_public_mvp
from test_auth import AccountTests, MockVerifier


ENV = {
    'VERCEL':'1', 'VERCEL_ENV':'production',
    'VERCEL_PROJECT_ID':'prj_submission_unconfigured',
    'GAMEON_RUNTIME_PROFILE':'public-mvp-dev', 'GAMEON_ERNE_MODE':'deterministic',
    'NEON_PROJECT_ID':'submission-project-unconfigured',
    'NEON_BRANCH_ID':'br-submission-unconfigured', 'NEON_BRANCH_NAME':'game-on-dev',
    'GAMEON_SCHEMA':'gameon_auth_dev', 'GAMEON_RATE_LIMIT_SECRET':'synthetic-only',
    'DATABASE_URL':'postgresql://gameon_mvp_app:synthetic@ep-submission-unconfigured-pooler.ap-southeast-2.aws.neon.tech/neondb?sslmode=require',
    'NEON_AUTH_URL':'https://ep-submission-unconfigured.neonauth.ap-southeast-2.aws.neon.tech/neondb/auth',
}


class PublicProfileTests(unittest.TestCase):
    def test_exact_target_only_and_no_database_connection_on_mismatch(self):
        with patch.dict(os.environ, ENV, clear=True), patch('gameon.postgres.PostgresStore') as store:
            validate_public_mvp();create_store()
            store.assert_called_once_with(ENV['DATABASE_URL'],'gameon_auth_dev')
        for key in ENV:
            with self.subTest(key=key), patch.dict(os.environ,{**ENV,key:''},clear=True), patch('gameon.postgres.PostgresStore') as store:
                with self.assertRaises(RuntimeError):validate_public_mvp()
                store.assert_not_called()


class PublicAccountTests(AccountTests):
    def setUp(self):
        super().setUp()
        self.client.close()
        self.env=patch.dict(os.environ,ENV,clear=True);self.env.start()
        self.addCleanup(self.env.stop)
        self.client=TestClient(create_app(self.store,auth_verifier=MockVerifier()))

    def test_public_home_private_api_and_no_legacy_bypass(self):
        self.assertEqual(self.client.get('/').status_code,200)
        for route in ['/api/account','/api/games/synthetic','/api/groups/synthetic']:
            for token in ['', 'legacy-private-credential']:
                response=self.client.get(route,headers={'Authorization':'Bearer '+token})
                self.assertEqual(response.status_code,401)
        self.call('/api/games',{'starts':[]},status=400)
        self.call('/api/games/synthetic',status=403)

    def test_no_hosted_model_factory(self):
        with self.assertRaisesRegex(RuntimeError,'deterministic'):
            create_app(self.store,auth_verifier=MockVerifier(),planning_provider_factory=lambda:None)
