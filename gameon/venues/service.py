"""Bounded synchronous recommendations for ASGI; no graph/process/model imports."""
from contextlib import contextmanager
import hashlib
import json
from gameon.groups import AccessDenied
from gameon.validation import StorageError
from gameon.storage import GameStore
from gameon.erne.contracts import ReadInput, ScheduleInput, VenueSearchInput, RankVenuesInput, Evidence
from gameon.erne.tools import read_game, schedule_game, eligible_slots
from .directory import CuratedDirectory, search_pickleball_venues, rank_venues


class _SnapshotReader(GameStore):
    """Reuse existing store readers on one read-only transaction, including PostgreSQL."""
    def __init__(self, connection, player_order):
        self.connection, self.player_order = connection, player_order

    @contextmanager
    def _connect(self, write=False):
        if write:
            raise RuntimeError('Recommendation snapshots are read-only')
        yield self.connection


def recommend_for_game(store, game_id, member_token='', directory=None, *, require_owner=False,
                       selected_slot_id=None, search_scopes=None):
    directory = directory if directory is not None else CuratedDirectory()
    with store._connect() as connection:
        reader = _SnapshotReader(connection, store.player_order)
        group_id = reader.game_group_access(game_id, member_token)
        can_prepare = bool(group_id and reader._member(connection, group_id, member_token)['role'] == 'owner')
        if require_owner and not can_prepare:
            raise AccessDenied()
        preferences = reader.game_preferences(game_id, member_token)
        try:
            snapshot = read_game(reader, ReadInput(game_id=game_id), preferences, directory)
        except ValueError:
            raise StorageError('Game data is invalid. Ask the organiser to create a valid session.') from None
    # Stable across polls, but changes with votes, slots, duration, preferences or directory evidence.
    state = snapshot.model_dump(mode='json', exclude={'snapshot_id', 'observed_at'})
    state['directory'] = [v.model_dump(mode='json') for v in directory.venues]
    revision = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    schedule = schedule_game(snapshot, ScheduleInput(snapshot_id=snapshot.snapshot_id))
    result = {'snapshot_id': snapshot.snapshot_id, 'mode': 'deterministic_service', 'llm_used': False,
              'revision': revision, 'can_prepare_handoff': can_prepare,
              'player_count': 0, 'session_player_count': len(snapshot.players),
              'restrict_to_preferences': preferences.group.restrict_to_preferences,
              'availability_status': 'not_checked', 'recommendations': [], 'searches': [],
              'slot_id': None, 'slot_starts_at': None, 'slot_ends_at': None,
              'duration_minutes': snapshot.duration_minutes,
              'accuracy_note': 'Approximate straight-line distances between suburb reference points, not driving distances or exact venue entrances.'}
    if schedule.status != 'ready':
        return {**result, 'status': schedule.status, 'explanation': schedule.explanation}
    eligible = eligible_slots(snapshot, schedule)
    if not eligible:
        return {**result, 'status': 'no_future_time', 'explanation': 'Propose a new future time before looking for venues.'}
    if selected_slot_id is not None and selected_slot_id not in eligible:
        return {**result, 'status': 'no_future_time', 'explanation': 'The selected time is no longer eligible.'}
    slot = next(s for s in snapshot.slots if s.id == (selected_slot_id or eligible[0]))
    result.update(slot_id=slot.id, slot_starts_at=slot.starts_at.isoformat(), slot_ends_at=slot.ends_at.isoformat())
    attending = next(m.available_player_ids for m in schedule.matches if m.slot_id == slot.id)
    result['player_count'] = len(attending)
    unresolved = sorted({p.suburb for p in snapshot.venue_context.players if p.player_id in attending and p.location is None})
    if unresolved:
        return {**result, 'status': 'needs_suburbs', 'missing_suburbs': unresolved,
                'explanation': 'The directory has no location reference for: ' + ', '.join(unresolved) + '. Ask the organiser to extend the directory; no locations were guessed.'}
    observations = []
    scopes = search_scopes if search_scopes is not None else (
        ('preferred',) if preferences.group.restrict_to_preferences else ('preferred', 'expanded'))
    if (not 1 <= len(scopes) <= 2 or any(s not in ('preferred', 'local', 'expanded') for s in scopes)
            or (preferences.group.restrict_to_preferences and any(s != 'preferred' for s in scopes))):
        raise StorageError('The proposed search is no longer permitted.')
    for scope in scopes:
        args = VenueSearchInput(snapshot_id=snapshot.snapshot_id, slot_id=slot.id, scope=scope)
        value = search_pickleball_venues(directory, args, snapshot, schedule)
        observations.append(Evidence(evidence_id='web-'+scope, observed_at=snapshot.observed_at,
                                     source='search_pickleball_venues', payload=value))
        result['searches'].append({'scope': scope, 'venue_ids': [v.id for v in value.venues],
            'missing_preferred_ids': list(value.missing_preferred_ids), 'excluded_ids': list(value.excluded_ids)})
    ranking = rank_venues(directory, RankVenuesInput(snapshot_id=snapshot.snapshot_id, slot_id=slot.id),
                          snapshot, schedule, tuple(observations))
    # A slot may expire during processing; do not return a recommendation for it.
    if slot.id not in eligible_slots(snapshot, schedule):
        return {**result, 'status': 'no_future_time', 'explanation': 'The selected time has passed. Propose a new future time.'}
    result['recommendations'] = [r.model_dump(mode='json') for r in ranking.recommendations]
    return {**result, 'status': 'recommended' if ranking.recommendations else 'no_venues',
            'explanation': ('Venue suggestions for the best qualifying time. Court availability has not been checked.'
                if ranking.recommendations else 'No suitable venues in the limited directory. Ask the organiser to review preferences or extend coverage.')}
