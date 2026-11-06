"""Synthetic scenarios written through the existing store, only on explicit request."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def create_scenario(store, scenario):
    start = (datetime.now(ZoneInfo('Australia/Melbourne')) + timedelta(days=7)).replace(hour=10, minute=0, second=0, microsecond=0)
    game_id = store.create_game('', [(start + timedelta(days=i)).strftime('%Y-%m-%dT%H:%M') for i in range(2)])
    game = store.get_game(game_id)
    slots = [s['id'] for s in game['slots']]
    for i in range(4):
        player = store.add_player(game_id, f'Synthetic {i + 1}', 'Clayton')
        if scenario == 'missing_votes' and i == 3:
            continue
        chosen = slots[i % 2] if scenario == 'no_common_time' else slots[0]
        store.submit_vote(game_id, player['player_id'], player['player_token'], [chosen])
    return game_id
