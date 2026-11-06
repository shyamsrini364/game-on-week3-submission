"""Local browser boundaries. Fixture observations cannot be presented as live."""
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, model_validator


class BrowserRecord(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True, revalidate_instances='always')


class CourtOption(BrowserRecord):
    court_id: str = Field(min_length=1, max_length=100)
    court_name: str = Field(min_length=1, max_length=100)
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    currency: Literal['AUD'] | None = 'AUD'
    base_minor: int | None = Field(default=None, ge=0)
    total_minor: int | None = Field(default=None, ge=0)
    fees_minor: int | None = Field(default=None, ge=0)
    tax_minor: int | None = Field(default=None, ge=0)
    per_player_minor: int | None = Field(default=None, ge=0)
    provider_players: int = Field(ge=1, le=1000)
    provider_players_verified: bool = True
    conditions: str = Field(default='', max_length=600)

    @model_validator(mode='after')
    def ordered(self):
        if self.ends_at <= self.starts_at:
            raise ValueError('Invalid court interval')
        if self.total_minor is not None and any(v is not None and v > self.total_minor for v in (self.fees_minor, self.tax_minor)):
            raise ValueError('Inconsistent displayed price')
        return self


class CourtObservation(BrowserRecord):
    observation_id: str
    snapshot_id: str
    slot_id: str
    venue_id: Literal['pickleholic-clayton', 'level-up-knox']
    revision: str
    player_count: int = Field(ge=1, le=1000)
    observed_at: AwareDatetime
    source_url: str
    simulated: bool = True
    source: Literal['offline_browser_fixture', 'live_browser_summary', 'live_browser_selection'] = 'offline_browser_fixture'
    status: Literal['results', 'empty', 'unknown', 'challenge', 'auth_required', 'timeout']
    options: tuple[CourtOption, ...] = Field(default=(), max_length=20)

    @model_validator(mode='after')
    def consistent(self):
        if self.simulated != (self.source == 'offline_browser_fixture'):
            raise ValueError('Inconsistent browser provenance')
        if (self.status == 'results') != bool(self.options):
            raise ValueError('Inconsistent court observation')
        if len({o.court_id for o in self.options}) != len(self.options):
            raise ValueError('Duplicate court identity')
        return self

    def fresh(self):
        age = (datetime.now(timezone.utc)-self.observed_at).total_seconds()
        return 0 <= age <= 120


class CheckoutInput(BrowserRecord):
    snapshot_id: str
    slot_id: str
    observation_id: str
    court_id: str


class CheckoutObservation(BrowserRecord):
    snapshot_id: str
    slot_id: str
    observation_id: str
    court_id: str
    status: Literal['handoff', 'changed', 'unknown']
    booking_status: Literal['not_booked'] = 'not_booked'
    checkout_transferred: Literal[False] = False
    source_url: str
    instructions: str
    simulated: Literal[True] = True


class BrowserApproval(BrowserRecord):
    observation: CourtObservation
    court_id: str
    expires_at: AwareDatetime
