"""Actual deterministic graph, synthetic players, source-verified venue directory."""
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from gameon.storage import GameStore
from gameon.venues.contracts import VenuePreferences, GroupProfile, PlayerPreference
from gameon.venues.directory import CuratedDirectory
from .contracts import RunRequest
from .graph import run_erne


def create_venue_game(store):
    start = datetime.now(ZoneInfo('Australia/Melbourne')) + timedelta(days=7)
    game_id = store.create_game('', [(start + timedelta(days=i)).replace(hour=10, minute=0).strftime('%Y-%m-%dT%H:%M') for i in range(2)])
    slot_id = store.get_game(game_id)['slots'][0]['id']
    preferences = []
    for index, suburb in enumerate(('Clayton', 'Springvale', 'Glen Waverley', 'Wantirna South')):
        player = store.add_player(game_id, f'Synthetic {index+1}', suburb)
        store.submit_vote(game_id, player['player_id'], player['player_token'], [slot_id])
        preferences.append(PlayerPreference(player_id=player['player_id'],
            venue_ids=('pickleholic-clayton',) if index < 2 else ('level-up-knox',)))
    return game_id, VenuePreferences(group=GroupProfile(group_id='synthetic-friends',
        preferred_venue_ids=('level-up-knox',)), players=tuple(preferences))


def main():
    with tempfile.TemporaryDirectory(prefix='erne-venues-') as temporary:
        store = GameStore(Path(temporary)/'synthetic.sqlite3')
        game_id, preferences = create_venue_game(store)
        before = store.get_game(game_id)
        result = run_erne(RunRequest(game_id=game_id, venue_preferences=preferences),
                          store, discovery=CuratedDirectory())
        assert result.outcome == 'discovery_recommendation', result.stop_reason
        assert len(result.recommendations) == 3
        assert store.get_game(game_id) == before
        print(json.dumps({'players': 'synthetic suburb-level profiles', 'records_unchanged': True,
            'venues': 'source-verified curated directory; availability not checked',
            'api_calls': 0, 'api_cost_usd': 0, 'result': result.model_dump(mode='json')}, indent=2))


if __name__ == '__main__':
    main()
