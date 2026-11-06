"""Deterministic scheduling tool. Later, the LangGraph agent can call it."""
from dataclasses import dataclass
from typing import List, Literal
from .models import GameSession, TimeSlot

Status = Literal["ready", "waiting_for_responses", "no_common_time"]


@dataclass(frozen=True)
class SlotMatch:
    slot: TimeSlot
    available_players: tuple[str, ...]
    unavailable_players: tuple[str, ...]
    unanswered_players: tuple[str, ...]

    @property
    def available_count(self) -> int:
        return len(self.available_players)


@dataclass(frozen=True)
class SchedulingResult:
    status: Status
    matches: tuple[SlotMatch, ...]
    pending_player_ids: tuple[str, ...]
    explanation: str


def find_common_slots(game: GameSession) -> SchedulingResult:
    """Rank proposed times based on explicit votes, never guesses.

    'ready' requires every joined player to have responded AND at least
    min_players available for one slot. Before all votes arrive, results are
    provisional, even if enough people have already said yes.
    """
    pending = tuple(pid for pid in game.players if pid not in game.votes)
    matches: List[SlotMatch] = []
    for slot in game.slots:
        yes = tuple(pid for pid in game.players if slot.id in game.votes.get(pid, ()))
        no = tuple(pid for pid in game.players if pid in game.votes and slot.id not in game.votes[pid])
        matches.append(SlotMatch(slot=slot, available_players=yes,
                                 unavailable_players=no, unanswered_players=pending))
    matches.sort(key=lambda m: (-m.available_count, m.slot.starts_at))

    if len(game.players) < game.min_players:
        remaining = game.min_players - len(game.players)
        return SchedulingResult("waiting_for_responses", tuple(matches), pending,
                                f"Need {remaining} more player(s) to join before there can be a game.")
    if pending:
        return SchedulingResult("waiting_for_responses", tuple(matches), pending,
                                f"Waiting for {len(pending)} player(s) to vote; results are provisional.")
    eligible = [m for m in matches if m.available_count >= game.min_players]
    if eligible:
        return SchedulingResult("ready", tuple(matches), (),
                                f"{len(eligible)} option(s) meet the {game.min_players}-player minimum.")
    return SchedulingResult("no_common_time", tuple(matches), (),
                            "No proposed time has enough players. Ask everyone to vote on new options.")
