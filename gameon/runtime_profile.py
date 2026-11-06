"""Explicit public MVP profile, bound to the approved development database only."""
import os
from urllib.parse import urlsplit, parse_qs


def public_mvp():
    return os.environ.get('GAMEON_RUNTIME_PROFILE') == 'public-mvp-dev'


def validate_public_mvp():
    expected = {
        'VERCEL_ENV': 'production',
        'VERCEL_PROJECT_ID': 'prj_submission_unconfigured',
        'GAMEON_SCHEMA': 'gameon_auth_dev',
        'NEON_PROJECT_ID': 'submission-project-unconfigured',
        'NEON_BRANCH_ID': 'br-submission-unconfigured',
        'NEON_BRANCH_NAME': 'game-on-dev',
        'GAMEON_ERNE_MODE': 'deterministic',
    }
    try:
        db = urlsplit(os.environ.get('DATABASE_URL', ''))
        auth = urlsplit(os.environ.get('NEON_AUTH_URL', ''))
        valid = (public_mvp() and os.environ.get('VERCEL') == '1'
            and all(os.environ.get(k) == v for k, v in expected.items())
            and db.scheme in ('postgres', 'postgresql') and db.path == '/neondb'
            and db.username == 'gameon_mvp_app'
            and parse_qs(db.query).get('sslmode') in (['require'], ['verify-full'])
            and db.hostname in ('ep-submission-unconfigured.ap-southeast-2.aws.neon.tech',
                                'ep-submission-unconfigured-pooler.ap-southeast-2.aws.neon.tech')
            and auth.scheme == 'https'
            and auth.hostname == 'ep-submission-unconfigured.neonauth.ap-southeast-2.aws.neon.tech'
            and auth.path == '/neondb/auth' and not auth.query and not auth.fragment
            and not auth.username and not auth.password
            and bool(os.environ.get('GAMEON_RATE_LIMIT_SECRET')))
    except ValueError:
        valid = False
    if not valid:
        raise RuntimeError('Public MVP configuration does not match the approved isolated target.')
