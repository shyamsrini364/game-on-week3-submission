"""Two real local graph runs on one synthetic game: python -m gameon.erne.choice_demo."""
import json
import tempfile
from pathlib import Path
from .contracts import Candidate, DiscoveryObservation
from .fixtures import create_scenario
from .graph import run_erne
from .providers import DeterministicTestProvider, select
from .tools import FixtureDiscovery
from gameon.storage import GameStore


class SlotChoiceProvider(DeterministicTestProvider):
    """Scripted test adapter. An explicit slot preference drives the first search."""
    def __init__(self, slot_id):
        self.slot_id = slot_id

    def decide(self, request):
        searches = [o for o in request.options if o.tool == 'discover_courts']
        if not any(e.source == 'discover_courts' for e in request.observations):
            choice = next((o for o in searches if o.arguments.slot_id == self.slot_id), None)
            if choice:
                return select(request, choice)
        return super().decide(request)


class TwoSlotDiscovery(FixtureDiscovery):
    def __init__(self, result_slot):
        self.result_slot = result_slot

    def search(self, request):
        found = request.slot_id == self.result_slot
        return DiscoveryObservation(snapshot_id=request.snapshot_id, slot_id=request.slot_id,
            status='results' if found else 'empty',
            candidates=(Candidate(id='fixture-court', name='Synthetic court - availability unverified'),) if found else ())


def create_choice_game(store):
    game_id = create_scenario(store, 'common_time')
    slots = [s['id'] for s in store.get_game(game_id)['slots']]
    with store._connect() as connection:
        players = connection.execute('SELECT id,token FROM players WHERE game_id=?', (game_id,)).fetchall()
    for player in players:
        store.submit_vote(game_id, player['id'], player['token'], slots)
    return game_id, slots


def main():
    with tempfile.TemporaryDirectory(prefix='erne-choices-') as temporary:
        store = GameStore(Path(temporary) / 'fixture.sqlite3')
        game_id, slots = create_choice_game(store)
        before = store.get_game(game_id)
        runs = [run_erne({'game_id': game_id}, store, provider=SlotChoiceProvider(slot),
                         discovery=TwoSlotDiscovery(slots[1])) for slot in slots]
        assert all(r.outcome == 'discovery_recommendation' for r in runs)
        assert store.get_game(game_id) == before
        print(json.dumps({'fixture': 'same game; four players voted for both future slots',
            'records_unchanged': True, 'first_choices': slots,
            'runs': [r.model_dump(mode='json') for r in runs]}, indent=2))


if __name__ == '__main__':
    main()
