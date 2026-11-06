"""Provider seam: select a bound option, never invent tool arguments."""
from typing import Protocol
from .contracts import DecisionRequest, DecisionResponse


class DecisionProvider(Protocol):
    mode: str
    def decide(self, request: DecisionRequest) -> DecisionResponse: ...


def select(request, option):
    return DecisionResponse(decision_id=request.decision_id, snapshot_id=request.snapshot_id,
        selected_option_id=option.option_id, evidence_ids=option.evidence_ids,
        reason_code=option.reason_code, explanation=option.reason)


class DeterministicTestProvider:
    """Baseline v2 uses scheduler ranking, never option order or opaque IDs."""
    mode = 'deterministic_test'

    def decide(self, request):
        def rank(option):
            if option.tool in ('read_game', 'schedule_game'):
                return (0, 0)
            if option.outcome == 'discovery_recommendation':
                return (1, 0)
            if option.tool == 'search_pickleball_venues':
                return (2, {'preferred': 0, 'expanded': 1, 'local': 2}[option.arguments.scope],
                        request.eligible_slot_ids.index(option.arguments.slot_id))
            if option.tool == 'rank_venues':
                return (3, 0)
            if option.tool in ('discover_courts', 'get_court_options', 'prepare_checkout'):
                return (3 if option.reason_code == 'retry_transient' else 2,
                        request.eligible_slot_ids.index(option.arguments.slot_id))
            return (5 if option.outcome == 'human_handoff' else 4, 0)
        return select(request, min(request.options, key=rank))
