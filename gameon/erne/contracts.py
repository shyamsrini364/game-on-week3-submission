"""Validated, immutable boundaries. No credentials or database handles here."""
from datetime import datetime
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, model_validator

from gameon.venues.contracts import VenuePreferences, VenueContext, VenueSearchResult, VenueRanking, Recommendation
from .playbypoint_contracts import CourtObservation, CheckoutObservation, CheckoutInput

Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')]
Text = Annotated[str, Field(min_length=1, max_length=600)]
Action = Literal['WAIT', 'REQUEST_ALTERNATIVES', 'PROCEED_TO_DISCOVERY', 'REQUEST_HELP', 'ERROR']
ToolName = Literal['read_game', 'schedule_game', 'discover_courts', 'search_pickleball_venues', 'rank_venues', 'get_court_options', 'prepare_checkout']


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True, revalidate_instances='always')


class RunRequest(Contract):
    game_id: Identifier
    mode: Literal['deterministic_test', 'llm'] = 'deterministic_test'
    venue_preferences: VenuePreferences | None = None
    preferred_slot_id: Identifier | None = None
    max_iterations: int = Field(default=6, ge=1, le=6)
    tool_timeout_seconds: float = Field(default=10.0, gt=0, le=30)
    deadline_seconds: float = Field(default=30.0, gt=0, le=60)


class Slot(Contract):
    id: Identifier
    starts_at: AwareDatetime
    ends_at: AwareDatetime

    @model_validator(mode='after')
    def ordered(self):
        if self.ends_at <= self.starts_at:
            raise ValueError('Invalid slot interval')
        return self


class Participant(Contract):
    id: Identifier
    # None = not answered, () = answered no times.
    selected_slot_ids: tuple[Identifier, ...] | None = Field(default=None, max_length=6)


class Snapshot(Contract):
    snapshot_id: Identifier
    game_id: Identifier
    observed_at: AwareDatetime
    min_players: int = Field(ge=2, le=12)
    duration_minutes: Literal[60, 90, 120] = 90
    slots: tuple[Slot, ...] = Field(min_length=1, max_length=6)
    players: tuple[Participant, ...] = Field(max_length=1000)
    venue_context: VenueContext | None = None

    @model_validator(mode='after')
    def references(self):
        slots = {s.id for s in self.slots}
        if len(slots) != len(self.slots) or len({p.id for p in self.players}) != len(self.players):
            raise ValueError('Duplicate record ID')
        if self.venue_context is not None:
            context_ids = [p.player_id for p in self.venue_context.players]
            if len(set(context_ids)) != len(context_ids) or set(context_ids) != {p.id for p in self.players}:
                raise ValueError('Venue context player scope mismatch')
        for player in self.players:
            votes = player.selected_slot_ids
            if votes is not None and (len(set(votes)) != len(votes) or not set(votes) <= slots):
                raise ValueError('Invalid vote references')
        return self


class ReadInput(Contract):
    game_id: Identifier


class ScheduleInput(Contract):
    snapshot_id: Identifier


class DiscoveryInput(ScheduleInput):
    slot_id: Identifier


class VenueSearchInput(DiscoveryInput):
    scope: Literal['preferred', 'local', 'expanded']


class RankVenuesInput(Contract):
    snapshot_id: Identifier
    slot_id: Identifier
    operation: Literal['rank'] = 'rank'


class Match(Contract):
    slot_id: Identifier
    available_player_ids: tuple[Identifier, ...] = Field(max_length=1000)


class ScheduleObservation(Contract):
    snapshot_id: Identifier
    status: Literal['ready', 'waiting_for_responses', 'no_common_time']
    matches: tuple[Match, ...] = Field(min_length=1, max_length=6)
    pending_player_ids: tuple[Identifier, ...] = Field(max_length=1000)
    explanation: Text


class Candidate(Contract):
    id: Identifier
    name: Annotated[str, Field(min_length=1, max_length=100)]


class DiscoveryObservation(Contract):
    snapshot_id: Identifier
    slot_id: Identifier
    status: Literal['results', 'empty', 'unavailable']
    candidates: tuple[Candidate, ...] = Field(default=(), max_length=10)
    simulated: Literal[True] = True
    source: Literal['fixture'] = 'fixture'

    @model_validator(mode='after')
    def consistent(self):
        if (self.status == 'results') != bool(self.candidates):
            raise ValueError('Inconsistent discovery result')
        return self


class Decision(Contract):
    kind: Literal['tool', 'finish']
    reason: Text
    tool: ToolName | None = None
    arguments: ReadInput | ScheduleInput | DiscoveryInput | VenueSearchInput | RankVenuesInput | CheckoutInput | None = None
    action: Action | None = None

    @model_validator(mode='after')
    def shape(self):
        if self.kind == 'tool':
            expected = {'read_game': ReadInput, 'schedule_game': ScheduleInput, 'discover_courts': DiscoveryInput,
                        'search_pickleball_venues': VenueSearchInput, 'rank_venues': RankVenuesInput,
                        'get_court_options': DiscoveryInput, 'prepare_checkout': CheckoutInput}
            if self.tool not in expected or type(self.arguments) is not expected[self.tool] or self.action is not None:
                raise ValueError('Invalid tool decision')
        elif self.action is None or self.tool is not None or self.arguments is not None:
            raise ValueError('Invalid final decision')
        return self


class TraceEntry(Contract):
    event_id: Identifier
    iteration: int = Field(ge=0, le=6)
    phase: Literal['decision', 'tool', 'observation', 'final']
    name: str = Field(max_length=80)
    summary: Text
    simulated: bool = False
    option_id: Identifier | None = None
    decision_source: Literal['deterministic', 'scripted', 'policy', 'llm', 'mock'] | None = None
    offered_options: tuple['ActionOption', ...] = ()
    selection: 'DecisionResponse | None' = None


class FinalResult(Contract):
    run_id: Identifier
    game_id: str = Field(max_length=128)
    mode: Literal['deterministic_test', 'llm'] = 'deterministic_test'
    llm_used: bool = False
    llm_call_attempted: bool = False
    elapsed_seconds: float = Field(default=0.0, ge=0)
    inference_calls: tuple['InferenceCall', ...] = ()
    outcome: Literal['waiting', 'alternatives_requested', 'discovery_ready', 'discovery_recommendation', 'human_handoff', 'search_exhausted', 'error'] = 'error'
    recommendations: tuple[Recommendation, ...] = Field(default=(), max_length=3)
    observations: tuple['Evidence', ...] = ()
    action: Action
    reason: Text
    stop_reason: str = Field(max_length=80)
    iteration_count: int = Field(ge=0, le=6)
    snapshot_id: Identifier | None = None
    missing_information: tuple[str, ...] = Field(default=(), max_length=1002)
    eligible_slot_ids: tuple[Identifier, ...] = Field(default=(), max_length=6)
    discovery_status: Literal['not_configured', 'not_attempted', 'results', 'empty', 'unavailable', 'error'] = 'not_attempted'
    simulated: bool = False
    evidence_ids: tuple[Identifier, ...] = Field(default=(), max_length=12)
    trace: tuple[TraceEntry, ...] = Field(default=(), max_length=25)


class AgentState(TypedDict):
    iteration: int
    snapshot: Snapshot | None
    schedule: ScheduleObservation | None
    decision: 'ActionOption | None'
    attempts: dict[str, int]
    error: str | None
    tool_result: object
    result: FinalResult | None
    observations: tuple['Evidence', ...]


ReasonCode = Literal['prerequisite', 'missing_information', 'no_eligible_time', 'eligible_time', 'retry_transient', 'alternative_slot', 'candidates_found', 'search_exhausted', 'human_help', 'budget_exhausted']


class ToolFailure(Contract):
    tool: ToolName
    snapshot_id: Identifier | None = None
    slot_id: Identifier | None = None
    scope: Literal['preferred', 'local', 'expanded'] | None = None
    retryable: Literal[True] = True
    code: Literal['transient_failure'] = 'transient_failure'


class Evidence(Contract):
    evidence_id: Identifier
    observed_at: AwareDatetime
    source: ToolName
    payload: Snapshot | ScheduleObservation | DiscoveryObservation | ToolFailure | VenueSearchResult | VenueRanking | CourtObservation | CheckoutObservation
    simulated: bool = False


class ActionOption(Decision):
    option_id: Identifier
    evidence_ids: tuple[Identifier, ...]
    reason_code: ReasonCode
    outcome: Literal['waiting', 'alternatives_requested', 'discovery_ready', 'discovery_recommendation', 'human_handoff', 'search_exhausted', 'error'] | None = None
    effect: Literal['read_only', 'return_to_caller']
    simulated: bool = False

    @model_validator(mode='after')
    def bound_effect(self):
        if self.kind == 'tool' and (self.effect != 'read_only' or self.outcome is not None):
            raise ValueError('Invalid tool effect')
        if self.kind == 'finish' and (self.effect != 'return_to_caller' or self.outcome is None):
            raise ValueError('Invalid terminal effect')
        return self


class DecisionRequest(Contract):
    schema_version: Literal['erne-0.2'] = 'erne-0.2'
    decision_id: Identifier
    run_id: Identifier
    game_id: Identifier
    snapshot_id: Identifier | None
    preferred_slot_id: Identifier | None = None
    observations: tuple[Evidence, ...]
    options: tuple[ActionOption, ...] = Field(min_length=1)
    eligible_slot_ids: tuple[Identifier, ...]
    remaining_decisions: int = Field(ge=0, le=5)
    remaining_discovery_attempts: int = Field(ge=0, le=2)
    remaining_seconds: float = Field(ge=0)


class DecisionResponse(Contract):
    decision_id: Identifier
    snapshot_id: Identifier | None
    selected_option_id: Identifier
    evidence_ids: tuple[Identifier, ...]
    reason_code: ReasonCode
    explanation: Text


class InferenceCall(Contract):
    call_id: Identifier
    decision_id: Identifier
    model: Literal['gpt-6.1-terra'] = 'gpt-6.1-terra'
    reasoning_effort: Literal['medium'] = 'medium'
    prompt_version: Literal['erne-selection-1', 'erne-selection-2'] = 'erne-selection-2'
    source: Literal['live', 'mock']
    status: Literal['started', 'completed', 'refused', 'incomplete', 'error', 'invalid_response']
    latency_seconds: float = Field(default=0.0, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    reserved_cost_usd: float = Field(ge=0)
    error_code: str | None = None


TraceEntry.model_rebuild()
FinalResult.model_rebuild()
