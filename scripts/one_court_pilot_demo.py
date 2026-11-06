"""Offline feasibility demo using the existing LangGraph. Never calls a provider."""
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gameon.erne.choice_demo import create_choice_game, SlotChoiceProvider, TwoSlotDiscovery
from gameon.erne.graph import run_erne
from gameon.storage import GameStore


def demonstrate():
    rows = []
    with tempfile.TemporaryDirectory(prefix='one-court-pilot-') as tmp:
        store = GameStore(Path(tmp) / 'synthetic.sqlite3')
        game_id, slots = create_choice_game(store)
        before = store.get_game(game_id)
        for name, available_slot in [('A', slots[0]), ('B', slots[1]), ('C', None)]:
            result = run_erne({'game_id': game_id, 'preferred_slot_id': slots[0]}, store,
                             provider=SlotChoiceProvider(slots[0]),
                             discovery=TwoSlotDiscovery(available_slot))
            observations = [e.payload for e in result.observations if e.source == 'discover_courts']
            observed = [d.slot_id for d in observations]
            assert observed == (slots[:1] if name == 'A' else slots)
            assert all(d.slot_id in result.eligible_slot_ids and d.simulated for d in observations)
            assert not result.llm_used and not result.llm_call_attempted
            assert result.iteration_count <= 6
            assert store.get_game(game_id) == before
            expected = 'search_exhausted' if name == 'C' else 'discovery_recommendation'
            assert result.outcome == expected
            if name == 'C':
                assert result.action == 'REQUEST_HELP'
            rows.append({'scenario': name, 'outcome': result.outcome,
                'searched_eligible_time_numbers': [slots.index(s)+1 for s in observed],
                'proposed_time_number': slots.index(available_slot)+1 if available_slot else None,
                'court_id': 'fixture-court' if available_slot else None,
                # Sidecar fixture only: existing graph has no live-price contract.
                'price_fixture': {'currency': 'AUD', 'base_minor': 4500, 'fees_minor': 0,
                                  'total_minor': 4500, 'source': 'invented test fixture',
                                  'simulated': True} if available_slot else None,
                'requires_organiser_review': True, 'records_unchanged': True,
                'elapsed_seconds': result.elapsed_seconds})
    return {'observed_at': datetime.now(timezone.utc).isoformat(),
            'mode': 'offline scripted decisions through actual existing LangGraph',
            'live_provider_calls': 0, 'paid_model_calls': 0, 'writes_to_provider': 0,
            'limitation': 'Price is an explicit sidecar fixture, not a graph tool result or real quote. New tool names are proposed only.',
            'scenarios': rows}


if __name__ == '__main__':
    result = demonstrate()
    Path('replanning-results.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
