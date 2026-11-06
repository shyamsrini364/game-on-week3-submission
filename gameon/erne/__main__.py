"""python -m gameon.erne --scenario empty_discovery (isolated synthetic SQLite)."""
import argparse
import json
from pathlib import Path
import tempfile

from .contracts import RunRequest
from .fixtures import create_scenario
from .graph import run_erne
from .tools import FixtureDiscovery
from gameon.storage import GameStore


def main():
    parser = argparse.ArgumentParser(description='Erne V0.2: local deterministic test mode, no LLM or live booking')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--scenario', choices=['missing_votes', 'no_common_time', 'common_time', 'empty_discovery'])
    source.add_argument('--game-id')
    database = parser.add_mutually_exclusive_group()
    database.add_argument('--db', type=Path, help='Existing local SQLite file; read-only tools')
    database.add_argument('--env-file', type=Path, help='Explicit ignored file containing DATABASE_URL (never printed)')
    parser.add_argument('--schema', help='Required with --env-file; no schema creation')
    parser.add_argument('--venue-directory', action='store_true', help='Source-verified Melbourne directory; no live availability')
    parser.add_argument('--group-profile', type=Path)
    parser.add_argument('--player-preferences', type=Path)
    parser.add_argument('--simulate-empty-discovery', action='store_true')
    parser.add_argument('--max-iterations', type=int, default=6)
    parser.add_argument('--tool-timeout', type=float, default=10.0)
    parser.add_argument('--deadline', type=float, default=30.0)
    args = parser.parse_args()
    if args.scenario and (args.db or args.env_file or args.schema):
        parser.error('Scenarios use temporary SQLite only; do not supply a database.')
    if args.game_id and not (args.db or args.env_file):
        parser.error('Choose an explicit --db or --env-file for an existing game.')
    if bool(args.env_file) != bool(args.schema):
        parser.error('--env-file and --schema must be supplied together.')
    if (args.group_profile or args.player_preferences) and not args.venue_directory:
        parser.error('Preference files require --venue-directory.')
    if args.venue_directory and (args.simulate_empty_discovery or args.scenario == 'empty_discovery'):
        parser.error('Choose either the real directory or simulated discovery.')
    try:
        with tempfile.TemporaryDirectory(prefix='erne-') as temporary:
            if args.scenario:
                store = GameStore(Path(temporary) / 'scenario.sqlite3')
                game_id = create_scenario(store, args.scenario)
            elif args.db:
                store, game_id = GameStore(args.db, initialise=False), args.game_id
            else:
                from dotenv import dotenv_values
                from gameon.postgres import PostgresStore
                url = dotenv_values(args.env_file).get('DATABASE_URL')
                if not url:
                    raise ValueError('Missing configuration')
                store, game_id = PostgresStore(url, args.schema), args.game_id
            request = {'game_id': game_id, 'max_iterations': args.max_iterations,
                       'tool_timeout_seconds': args.tool_timeout, 'deadline_seconds': args.deadline}
            discovery = FixtureDiscovery() if args.simulate_empty_discovery or args.scenario == 'empty_discovery' else None
            if args.venue_directory:
                from gameon.venues.directory import CuratedDirectory
                from gameon.venues.profiles import load_preferences
                request['venue_preferences'] = load_preferences(args.group_profile, args.player_preferences)
                discovery = CuratedDirectory()
            result = run_erne(request, store, discovery=discovery)
            print(result.model_dump_json(indent=2))
            return 1 if result.action == 'ERROR' or result.stop_reason != 'completed' else 0
    except Exception:
        print(json.dumps({'mode': 'deterministic_test', 'llm_used': False, 'action': 'ERROR',
                          'stop_reason': 'configuration_error', 'reason': 'Check the selected local database/configuration; private details suppressed.'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
