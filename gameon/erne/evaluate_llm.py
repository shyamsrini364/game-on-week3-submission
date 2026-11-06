"""Explicitly approved, bounded synthetic evaluation. Never touches production.

No call is enabled by importing this module. Budget defaults to zero.
"""
import argparse
import json
import tempfile
from pathlib import Path
from .choice_demo import create_choice_game, TwoSlotDiscovery
from .contracts import Candidate, DiscoveryObservation
from .fixtures import create_scenario
from .graph import run_erne
from .openai_provider import OpenAIResponsesProvider, MAX_CALL_USD, MODEL
from .tools import TransientToolError
from gameon.storage import GameStore


class FailingOnceDiscovery:
    def __init__(self):
        self.failed = False

    def search(self, request):
        if not self.failed:
            self.failed = True
            raise TransientToolError('Synthetic transient failure')
        return DiscoveryObservation(snapshot_id=request.snapshot_id, slot_id=request.slot_id,
            status='results', candidates=(Candidate(id='fixture', name='Simulated court; unverified'),))


def evaluate(env_file, approved_budget_usd, max_calls):
    if not 0 < approved_budget_usd <= 1 or not 1 <= max_calls <= 16:
        raise ValueError('Explicit budget up to USD 1 and 1..16 calls required')
    results, reserved, calls = [], 0.0, 0
    with tempfile.TemporaryDirectory(prefix='erne-llm-evaluation-') as directory:
        store = GameStore(Path(directory) / 'synthetic.sqlite3')
        for scenario in ('missing_votes', 'multiple_slots', 'empty_first', 'tool_failure'):
            available = min(4, max_calls - calls)
            remaining = approved_budget_usd - reserved
            if available < 1 or remaining < MAX_CALL_USD:
                break
            if scenario == 'missing_votes':
                game_id = create_scenario(store, scenario)
                preferred, discovery = None, None
            else:
                game_id, slots = create_choice_game(store)
                preferred = slots[1] if scenario == 'multiple_slots' else slots[0]
                discovery = FailingOnceDiscovery() if scenario == 'tool_failure' else TwoSlotDiscovery(slots[1])
            before = store.get_game(game_id)
            provider = OpenAIResponsesProvider(env_file=env_file, approved_budget_usd=remaining,
                max_calls=available, timeout_seconds=25.0)
            result = run_erne({'game_id': game_id, 'mode': 'llm', 'preferred_slot_id': preferred,
                              'tool_timeout_seconds': 30.0, 'deadline_seconds': 60.0}, store,
                              provider=provider, discovery=discovery)
            calls += len(result.inference_calls)
            reserved += sum(c.reserved_cost_usd for c in result.inference_calls)
            assert store.get_game(game_id) == before
            # Compare the same records and preference with a fresh fixture adapter;
            # the baseline makes no API calls and must not reuse failure counters.
            baseline_discovery = (None if scenario == 'missing_votes' else
                FailingOnceDiscovery() if scenario == 'tool_failure' else TwoSlotDiscovery(slots[1]))
            baseline = run_erne({'game_id': game_id, 'preferred_slot_id': preferred}, store,
                                discovery=baseline_discovery)
            assert store.get_game(game_id) == before
            results.append({'scenario': scenario, 'records_unchanged': True,
                            'result': result.model_dump(mode='json'),
                            'deterministic_baseline': baseline.model_dump(mode='json')})
    recorded_calls = [c for r in results for c in r['result']['inference_calls']]
    return {'model': MODEL, 'reasoning_effort': 'medium', 'simulated_discovery': True,
        'approved_budget_usd': approved_budget_usd, 'maximum_calls': max_calls,
        'calls_attempted': calls, 'reserved_cost_usd': reserved,
        'total_run_seconds': sum(r['result']['elapsed_seconds'] for r in results),
        'known_input_tokens': sum(c['input_tokens'] or 0 for c in recorded_calls),
        'known_output_tokens': sum(c['output_tokens'] or 0 for c in recorded_calls),
        'known_estimated_cost_usd': sum(c['estimated_cost_usd'] or 0 for c in recorded_calls),
        'calls_with_unknown_usage': sum(c['estimated_cost_usd'] is None for c in recorded_calls),
        'cost_note': 'Estimates use published standard text rates, not an invoice. Unknown usage is not zero cost.',
        'runs': results}


def main():
    parser = argparse.ArgumentParser(description='Paid synthetic evaluation; requires explicit budget approval')
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--approved-budget-usd', type=float, default=0.0)
    parser.add_argument('--max-calls', type=int, default=16)
    args = parser.parse_args()
    if not 0 < args.approved_budget_usd <= 1 or not 1 <= args.max_calls <= 16:
        parser.error('Obtain approval, then provide a budget up to USD 1 and at most 16 calls.')
    result = evaluate(args.env_file, args.approved_budget_usd, args.max_calls)
    print(json.dumps(result, indent=2))
    return 0 if len(result['runs']) == 4 and all(
        r['result']['stop_reason'] == 'completed' for r in result['runs']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
