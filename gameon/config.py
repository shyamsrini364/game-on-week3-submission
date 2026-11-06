"""Explicit backend selection, with no silent SQLite fallback on Vercel."""
import os
import re
from pathlib import Path
from .storage import GameStore


def create_store():
    from .runtime_profile import public_mvp, validate_public_mvp
    url = os.environ.get("DATABASE_URL")
    schema = os.environ.get("GAMEON_SCHEMA")
    if public_mvp() or os.environ.get('VERCEL_ENV') == 'production':
        validate_public_mvp()
    elif os.environ.get('NEON_AUTH_URL'):
        from urllib.parse import urlsplit
        if (os.environ.get('VERCEL') or os.environ.get('NEON_BRANCH_NAME') != 'game-on-dev'
                or schema != 'gameon_auth_dev' or not url or urlsplit(url).path != '/neondb'):
            raise RuntimeError('Auth development requires verified game-on-dev/neondb/gameon_auth_dev; hosted activation is not configured.')
    if os.environ.get("VERCEL_ENV") == "preview":
        # Preview credentials currently share a database with production. Do not
        # let a missing/mistyped preview setting silently select production.
        if not url or not schema or not re.fullmatch(r"gameon_preview(?:_[a-z0-9_]+)?", schema) or len(schema) > 63:
            raise RuntimeError("Preview requires DATABASE_URL and an explicit preview schema.")
    if url:
        from .postgres import PostgresStore
        return PostgresStore(url, schema if schema is not None else "gameon")
    if os.environ.get("VERCEL"):
        raise RuntimeError("DATABASE_URL must be configured on Vercel.")
    return GameStore(os.environ.get("GAMEON_DB", Path(__file__).resolve().parent.parent / "data/gameon.sqlite3"))
