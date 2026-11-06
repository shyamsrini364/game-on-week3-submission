"""Read-only tool wrappers. Scheduling mathematics live in gameon.scheduling."""
from datetime import datetime, timezone
import secrets

from gameon.models import GameSession, Player, TimeSlot
from gameon import scheduling
from .contracts import (Snapshot, Slot, Participant, ReadInput, ScheduleInput,
                        DiscoveryInput, ScheduleObservation, Match, DiscoveryObservation)


class TransientToolError(RuntimeError):
    """An explicitly retryable read-only adapter failure; details are not logged."""


class FixtureDiscovery:
    """Simulated empty search by default; never a live availability check."""
    def search(self, request: DiscoveryInput) -> DiscoveryObservation:
        return DiscoveryObservation(snapshot_id=request.snapshot_id, slot_id=request.slot_id, status='empty')


def read_game(store, request: ReadInput, venue_preferences=None, directory=None) -> Snapshot:
    game, _title = store.load_game_session(request.game_id)
    if game.id != request.game_id:
        raise ValueError('Game scope mismatch')
    return Snapshot(snapshot_id=secrets.token_hex(16), game_id=game.id,
                    observed_at=datetime.now(timezone.utc), min_players=game.min_players, duration_minutes=game.duration_minutes,
                    venue_context=directory.context(game, venue_preferences) if venue_preferences is not None else None,
                    slots=tuple(Slot(id=s.id, starts_at=s.starts_at, ends_at=s.ends_at) for s in game.slots),
                    players=tuple(Participant(id=p.id, selected_slot_ids=tuple(sorted(game.votes[p.id]))
                                             if p.id in game.votes else None) for p in game.players.values()))


def schedule_game(snapshot: Snapshot, request: ScheduleInput) -> ScheduleObservation:
    if snapshot.snapshot_id != request.snapshot_id:
        raise ValueError('Snapshot mismatch')
    # Reconstruct the existing domain model. Names/suburbs are not needed for counting.
    game = GameSession(id=snapshot.game_id, sport='pickleball', min_players=snapshot.min_players, duration_minutes=snapshot.duration_minutes,
                       slots=tuple(TimeSlot(s.id, s.starts_at, s.ends_at) for s in snapshot.slots))
    for player in snapshot.players:
        game.add_player(Player(player.id, player.id, 'Not needed for scheduling'))
        if player.selected_slot_ids is not None:
            game.submit_availability(player.id, set(player.selected_slot_ids))
    result = scheduling.find_common_slots(game)
    return ScheduleObservation(snapshot_id=snapshot.snapshot_id, status=result.status,
        matches=tuple(Match(slot_id=m.slot.id, available_player_ids=m.available_players) for m in result.matches),
        pending_player_ids=result.pending_player_ids, explanation=result.explanation)


def eligible_slots(snapshot, schedule):
    """Use scheduler counts; exclude expired starts only at the agent boundary."""
    if schedule is None or schedule.status != 'ready':
        return ()
    future = {s.id for s in snapshot.slots if s.starts_at > datetime.now(timezone.utc)}
    return tuple(m.slot_id for m in schedule.matches
                 if len(m.available_player_ids) >= snapshot.min_players and m.slot_id in future)


def validate_schedule(result, snapshot):
    result = ScheduleObservation.model_validate(result)
    ids = {p.id for p in snapshot.players}
    slots = {s.id for s in snapshot.slots}
    if result.snapshot_id != snapshot.snapshot_id or {m.slot_id for m in result.matches} != slots:
        raise ValueError('Invalid schedule scope')
    if len(result.matches) != len(slots) or not set(result.pending_player_ids) <= ids:
        raise ValueError('Invalid schedule references')
    for match in result.matches:
        if not set(match.available_player_ids) <= ids or len(set(match.available_player_ids)) != len(match.available_player_ids):
            raise ValueError('Invalid participant references')
    return result
