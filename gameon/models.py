"""Phase 1: explicitly structured application data, not LLM memory."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, FrozenSet, Optional, Tuple


@dataclass(frozen=True)
class Player:
    id: str
    name: str
    suburb: str

    def __post_init__(self) -> None:
        if not all((self.id.strip(), self.name.strip(), self.suburb.strip())):
            raise ValueError("Player id, name and suburb must not be blank")


@dataclass(frozen=True)
class TimeSlot:
    id: str
    starts_at: datetime
    ends_at: datetime

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("A time slot needs an id")
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise ValueError("Time slots must include a time zone")
        if self.ends_at <= self.starts_at:
            raise ValueError("Slot end must be after slot start")


@dataclass(frozen=True)
class Venue:
    id: str
    name: str
    suburb: str
    booking_url: Optional[str] = None
    # Booking and live availability integrations come in a later phase.


@dataclass
class GameSession:
    id: str
    sport: str
    slots: Tuple[TimeSlot, ...]
    players: Dict[str, Player] = field(default_factory=dict)
    # A submitted vote may be empty: that means this player cannot make any offered slot.
    votes: Dict[str, FrozenSet[str]] = field(default_factory=dict)
    min_players: int = 4
    duration_minutes: int = 90

    def __post_init__(self) -> None:
        if type(self.duration_minutes) is not int or self.duration_minutes not in (60, 90, 120):
            raise ValueError("Duration must be 60, 90 or 120 minutes")
        if self.min_players < 2:
            raise ValueError("min_players must be at least 2")
        if len({slot.id for slot in self.slots}) != len(self.slots):
            raise ValueError("Time slot ids must be unique")
        if not self.slots:
            raise ValueError("A game needs at least one candidate time")

    def add_player(self, player: Player) -> None:
        if player.id in self.players:
            raise ValueError(f"Player {player.id!r} already joined")
        self.players[player.id] = player

    def submit_availability(self, player_id: str, available_slot_ids: set[str]) -> None:
        if player_id not in self.players:
            raise ValueError("Player must join before voting")
        unknown = available_slot_ids - {slot.id for slot in self.slots}
        if unknown:
            raise ValueError(f"Unknown slot ids: {', '.join(sorted(unknown))}")
        # Re-submitting updates a vote instead of duplicating a response.
        self.votes[player_id] = frozenset(available_slot_ids)
