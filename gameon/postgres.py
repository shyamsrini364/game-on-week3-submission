"""Portable PostgreSQL adapter: standard SQL and psycopg, no provider SDK."""
from contextlib import contextmanager
from pathlib import Path
import re

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from .storage import DatabaseUnavailable, GameStore


class Connection:
    """Translate placeholders in our fixed SQL only; values stay parameterised."""
    def __init__(self, connection):
        self.connection = connection

    def execute(self, query, params=()):
        return self.connection.execute(query.replace('?', '%s'), params)

    def executemany(self, query, rows):
        with self.connection.cursor() as cursor:
            cursor.executemany(query.replace('?', '%s'), rows)


class PostgresStore(GameStore):
    player_order = "joined_order"
    vote_lock = " FOR UPDATE"

    def __init__(self, database_url, schema="gameon"):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
            raise ValueError("Invalid database schema name")
        # No connection, DDL or file writes during serverless module import.
        self.database_url = database_url
        self.schema = schema

    def _open(self):
        return psycopg.connect(self.database_url, connect_timeout=10,
                               row_factory=dict_row, prepare_threshold=None)

    @contextmanager
    def _connect(self, write=False):
        try:
            with self._open() as conn:
                # Consistent multi-query view of players, submissions and votes.
                if not write:
                    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                conn.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(self.schema)))
                conn.execute("SET LOCAL statement_timeout = '8000ms'")
                conn.execute("SET LOCAL lock_timeout = '5000ms'")
                yield Connection(conn)
        except psycopg.Error:
            raise DatabaseUnavailable("Database temporarily unavailable.") from None

    def initialise(self):
        """Explicit, repeatable setup; never called by HTTP request handling."""
        try:
            with self._open() as conn:
                conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema)))
                conn.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(self.schema)))
                script = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
                conn.execute(script, prepare=False)
                conn.execute((Path(__file__).parent / 'groups_schema.sql').read_text(encoding='utf-8'), prepare=False)
                conn.execute((Path(__file__).parent / 'pricing_schema.sql').read_text(encoding='utf-8'), prepare=False)
        except psycopg.Error:
            raise DatabaseUnavailable("Database schema setup failed.") from None
