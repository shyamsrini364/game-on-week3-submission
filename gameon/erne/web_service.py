"""Local, organiser-authorised adapter for the existing bounded graph.

Hosted execution fails closed; local live inference needs a one-run approval.
No credential or database connection is passed to the graph worker.
"""
from dataclasses import dataclass
import hashlib
import json
import os

from gameon.groups import AccessDenied
from gameon.models import Player
from gameon.venues.directory import CuratedDirectory
from gameon.venues.service import _SnapshotReader, recommend_for_game


class PlanningUnavailable(RuntimeError):
    pass


class PlanningConflict(RuntimeError):
    pass


@dataclass
class FrozenGame:
    game: object

    def load_game_session(self, game_id):
        if game_id != self.game.id:
            raise ValueError('Game scope mismatch')
        return self.game, 'Planning snapshot'


def capture(store, game_id, token):
    with store._connect() as connection:
        reader = _SnapshotReader(connection, store.player_order)
        group = reader.game_group_access(game_id, token)
        if not group or reader._member(connection, group, token)['role'] != 'owner':
            raise AccessDenied()
        preferences = reader.game_preferences(game_id, token)
        game, _ = reader.load_game_session(game_id)
    # Names have no planning purpose. Worker receives only this read-only value.
    game.players = {pid: Player(pid, 'Participant', p.suburb) for pid, p in game.players.items()}
    state = {'game': game.id, 'minimum': game.min_players, 'duration': game.duration_minutes,
             'slots': [(s.id, s.starts_at.isoformat(), s.ends_at.isoformat()) for s in game.slots],
             'players': sorted((p.id, p.suburb) for p in game.players.values()),
             'votes': {p: sorted(v) for p, v in game.votes.items()},
             'preferences': preferences.model_dump(mode='json')}
    revision = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
    return FrozenGame(game), preferences, revision


def plan_game(store, game_id, token, *, provider=None, signing_secret=None, live_approval=None):
    local_venue = os.environ.get('GAMEON_LOCAL_PLAYBYPOINT_VENUE')
    if local_venue:
        if os.environ.get('VERCEL') or provider is not None or live_approval is not None:
            raise PlanningUnavailable('Live browser planning is local only and does not enable model spending.')
        from .playbypoint_service import plan_live_game
        return plan_live_game(store, game_id, token, local_venue, signing_secret)
    frozen, preferences, revision = capture(store, game_id, token)
    if os.environ.get('VERCEL'):
        from gameon.runtime_profile import public_mvp, validate_public_mvp
        if not public_mvp() or provider is not None or live_approval is not None:
            raise PlanningUnavailable('Hosted planning allows only the approved deterministic MVP profile.')
        try:
            validate_public_mvp()
        except RuntimeError:
            raise PlanningUnavailable('Hosted planning configuration is unavailable.') from None
    from .openai_provider import MODEL, OpenAIResponsesProvider
    from .live_approval import LivePlanningApproval
    if provider is not None and type(provider) is not OpenAIResponsesProvider:
        raise PlanningUnavailable('Live model planning requires separate approval.')
    live = provider is not None and provider.transport is None
    if live:
        if type(live_approval) is not LivePlanningApproval:
            raise PlanningUnavailable('Live model planning requires separate approval.')
        try:
            live_approval.validate(game_id, provider)
        except ValueError as exc:
            raise PlanningUnavailable(str(exc)) from None
    # Shared database gate also covers multiple local web processes. This intentionally
    # keeps its cooldown after completion; 35s exceeds the 20s worker deadline + cleanup.
    key = 'erne-plan:' + hashlib.sha256(game_id.encode()).hexdigest()
    if not store.allow_request(key, 1, 35):
        raise PlanningConflict('A plan is running or was just completed. Wait 35 seconds before trying again.')
    try:
        from .graph import run_erne
    except ImportError:
        raise PlanningUnavailable('The local agent dependencies are not installed. Built-in help remains available.') from None
    from .contracts import RunRequest
    if live:
        try:
            live_approval.consume(store, game_id, provider)
        except ValueError as exc:
            raise PlanningUnavailable(str(exc)) from None
    result = run_erne(RunRequest(game_id=game_id, venue_preferences=preferences,
        mode='llm' if provider else 'deterministic_test', deadline_seconds=20.0,
        tool_timeout_seconds=5.0), frozen, discovery=CuratedDirectory(), provider=provider)
    # Reauthorise as well as checking votes/preferences after the worker returns.
    _, _, current_revision = capture(store, game_id, token)
    if current_revision != revision:
        raise PlanningConflict('The game changed while Erne was working. Review the latest votes and run a new plan.')
    # Explicit allowlist: no raw snapshots, options, model text, player IDs or usage payloads.
    missing_votes = sum(p.selected_slot_ids is None for e in result.observations
                        if e.source == 'read_game' and hasattr(e.payload, 'players') for p in e.payload.players)
    missing = [m for m in result.missing_information if not m.startswith('Vote needed:')]
    if missing_votes:
        missing.append(f'{missing_votes} players still need to vote')
    # Reproduce the actual observed search scopes with current authoritative data.
    # This is validation, not a second agent run, and does not appear as agent activity.
    from gameon.venues.contracts import VenueRanking, VenueSearchResult
    ranking = next((e.payload for e in reversed(result.observations) if isinstance(e.payload, VenueRanking)), None)
    proposals = []
    current = recommend_for_game(store, game_id, token, require_owner=True)
    if result.recommendations and ranking:
        scopes = tuple(e.payload.scope for e in result.observations if isinstance(e.payload, VenueSearchResult))
        checked = recommend_for_game(store, game_id, token, require_owner=True,
                                     selected_slot_id=ranking.slot_id, search_scopes=scopes)
        if (checked['revision'] != current['revision'] or checked['status'] != 'recommended'
                or checked['recommendations'] != [r.model_dump(mode='json') for r in result.recommendations]):
            raise PlanningConflict('The game or venue evidence changed. Run a fresh plan.')
        from .proposals import issue
        receipt = issue(signing_secret, game_id, checked['revision'], ranking.slot_id, scopes,
                        [r.venue.id for r in result.recommendations]) if signing_secret else None
        from gameon.pricing import cached_price
        for r in result.recommendations:
            proposals.append({'venue_id': r.venue.id, 'name': r.venue.name, 'suburb': r.venue.suburb,
                'venue': r.venue.model_dump(mode='json'),
                'reason': r.reason, 'average_distance_km': round(r.average_distance_km, 1),
                'longest_distance_km': round(r.longest_distance_km, 1),
                'player_count': checked['player_count'], 'starts_at': checked['slot_starts_at'],
                'duration_minutes': checked['duration_minutes'],
                'pricing': cached_price(store, r.venue.id, checked['slot_starts_at'], checked['slot_ends_at'], checked['player_count']),
                'selection': {'revision': checked['revision'], 'slot_id': ranking.slot_id,
                              'venue_id': r.venue.id, 'proposal': receipt} if receipt else None})
    # Cover changes during validation too. No stale proposal is intentionally issued.
    if capture(store, game_id, token)[2] != revision:
        raise PlanningConflict('The game changed while Ernie was working. Run a fresh plan.')
    names = {'read_game': 'Read the group’s submitted availability',
             'schedule_game': 'Checked qualifying future times',
             'search_pickleball_venues': 'Searched the venue directory',
             'rank_venues': 'Compared approximate suburb distances and preferences'}
    activities = []
    for event in result.trace:
        if event.phase != 'observation':
            continue
        observed = next((e for e in result.observations if e.evidence_id == event.event_id), None)
        label = names.get(event.name, 'Checked a tool result')
        if observed and isinstance(observed.payload, VenueSearchResult):
            label = f'Searched {observed.payload.scope} courts in the directory'
        activities.append({'label': label, 'detail': event.summary,
                           'status': 'completed' if observed and not hasattr(observed.payload, 'retryable') else 'failed'})
    return {'execution_mode': 'live_model' if live else ('mock_model' if provider else 'deterministic'),
        **public_activity(result),
        'execution_status': 'failed' if result.stop_reason != 'completed' else 'completed',
        'revision': current['revision'], 'activities': activities,
        'llm_used': result.llm_used, 'llm_call_attempted': result.llm_call_attempted,
        'model': MODEL if result.llm_call_attempted else None,
        'elapsed_seconds': round(result.elapsed_seconds, 3),
        'outcome': result.outcome, 'reason': result.reason, 'stop_reason': result.stop_reason,
        'missing_information': missing, 'availability_status': 'not_checked',
        'trace': [{'phase': e.phase, 'action': e.name, 'summary': e.summary,
                   'decision_source': e.decision_source, 'simulated': e.simulated}
                  for e in result.trace],
        'recommendations': proposals}


def public_activity(result):
    from .activity import project_activity, completion_state
    return {'run_id': result.run_id, 'planning_state': completion_state(result),
            'activity_timeline': project_activity(result), 'progress_delivery': 'completed_run'}
