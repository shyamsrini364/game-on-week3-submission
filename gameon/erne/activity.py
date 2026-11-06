"""Allowlisted public activity from executed graph traces, never model prose."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .contracts import Snapshot, ScheduleObservation, ToolFailure, DiscoveryObservation
from .playbypoint_contracts import CourtObservation, CheckoutObservation
from gameon.venues.contracts import VenueSearchResult, VenueRanking

Stage = Literal['checking_group', 'finding_times', 'searching_venues', 'comparing',
                'reviewing_courts', 'recommendation_ready', 'replanning',
                'waiting_for_group', 'needs_user_help', 'approval_required', 'completed', 'failed']


class ActivityEvent(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    run_id: str
    event_id: str
    order: int = Field(ge=1)
    trace_event_id: str
    observation_id: str | None = None
    stage: Stage
    title: str
    detail: str
    status: Literal['started', 'completed', 'failed', 'skipped']
    provenance: Literal['deterministic', 'model', 'fixture', 'policy']
    decision_source: Literal['deterministic', 'scripted', 'policy', 'llm', 'mock'] | None = None


TOOLS = {
    'read_game': ('checking_group', 'Checking your group'),
    'schedule_game': ('finding_times', 'Finding shared times'),
    'search_pickleball_venues': ('searching_venues', 'Searching nearby venues'),
    'discover_courts': ('searching_venues', 'Searching court options'),
    'rank_venues': ('comparing', 'Comparing convenience'),
    'get_court_options': ('reviewing_courts', 'Reviewing court information'),
    'prepare_checkout': ('reviewing_courts', 'Reviewing the handoff'),
}


def completion_state(result):
    if result.outcome == 'error':
        return 'failed'
    if result.outcome == 'waiting':
        return 'waiting_for_group'
    if result.stop_reason == 'completed' and result.outcome == 'discovery_recommendation' and any(
            isinstance(e.payload, CheckoutObservation) and e.payload.status == 'handoff' for e in result.observations):
        return 'completed'
    if result.outcome == 'discovery_recommendation' and result.stop_reason == 'completed':
        return 'approval_required'
    return 'needs_user_help'


def project_activity(result):
    """Each event references an executed trace; IDs are scoped to this run.

    Directory emptiness and tool failure never establish inventory availability.
    Fixture provenance overrides the selector's provenance for simulated evidence.
    """
    evidence = {e.evidence_id: e for e in result.observations}
    events = []
    source = None
    setback = None

    def add(trace, stage, title, detail, status='completed', observed=None):
        fixture = trace.simulated or source in ('mock', 'scripted') or bool(observed and observed.simulated)
        provenance = 'fixture' if fixture else 'model' if source == 'llm' else 'policy' if source == 'policy' else 'deterministic'
        events.append(ActivityEvent(run_id=result.run_id, event_id=f'{result.run_id}:{len(events)+1}',
            order=len(events)+1, trace_event_id=trace.event_id,
            observation_id=observed.evidence_id if observed else None,
            stage=stage, title=title, detail=detail, status=status,
            provenance=provenance, decision_source=source).model_dump())

    for trace in result.trace:
        if trace.phase == 'decision':
            source = trace.decision_source
            # A visible replan is emitted only when the graph actually chose a tool
            # after a validated setback, not merely because a retry could be offered.
            if setback and trace.selection and trace.name in TOOLS:
                add(trace, 'replanning', 'Trying another option', setback)
                setback = None
            continue
        if trace.phase == 'tool':
            if trace.name in TOOLS:
                stage, title = TOOLS[trace.name]
                add(trace, stage, title, 'Started this permitted read-only action.', 'started')
            continue
        if trace.phase == 'observation':
            observed = evidence.get(trace.event_id)
            stage, title = TOOLS.get(trace.name, ('reviewing_courts', 'Reviewing a tool result'))
            if observed is None:
                add(trace, stage, title, 'The result could not be safely validated. No availability or price was inferred.', 'failed')
                continue
            value = observed.payload
            status = 'completed'
            if isinstance(value, ToolFailure):
                detail = 'A temporary tool failure prevented this check. Court availability remains unknown.'
                setback = 'The previous tool failed temporarily; trying a permitted recovery action within the existing limit.'
                status = 'failed'
            elif isinstance(value, Snapshot):
                votes = sum(p.selected_slot_ids is not None for p in value.players)
                detail = f'Read {len(value.players)} participants and {votes} submitted responses.'
            elif isinstance(value, ScheduleObservation):
                detail = 'Checked the submitted times against the group minimum and game duration.'
            elif isinstance(value, VenueSearchResult):
                detail = f'Found {len(value.venues)} suitable directory venues in the {value.scope} search. Live court availability was not checked.'
                if value.excluded_ids:
                    detail += f' Excluded {len(value.excluded_ids)} records that did not meet the directory rules or group requirements.'
                if not value.venues:
                    setback = 'The previous directory search found no suitable candidates; choosing another permitted action. This does not mean courts are unavailable.'
            elif isinstance(value, VenueRanking):
                detail = f'Compared {len(value.recommendations)} venues using approximate suburb distances and saved preferences. Availability remains unchecked.'
                if value.recommendations:
                    stage, title = 'recommendation_ready', 'Recommendation ready'
            elif isinstance(value, CourtObservation):
                if value.status == 'results':
                    detail = (f'Observed {len(value.options)} offered individual courts for the exact selected time. No court or Next action was clicked; checkout price still requires verification.'
                              if value.source == 'live_browser_selection' else
                              'Read displayed court and price fields. A quote is not an inventory guarantee or booking.')
                elif value.status == 'empty':
                    detail = 'The validated court check returned no options for this time.'
                    if value.simulated:
                        detail += ' This is simulated inventory, not live availability.'
                    setback = 'The previous court check returned no options; checking another time already eligible for this group.'
                else:
                    detail = 'The provider could not supply a usable court observation. Availability is unknown; organiser assistance is needed.'
                    status = 'failed'
            elif isinstance(value, DiscoveryObservation):
                detail = f'The simulated discovery returned {len(value.candidates)} candidates. Live availability was not checked.'
                if not value.candidates:
                    setback = 'The simulated search returned no usable candidates; trying another permitted group time.'
            elif isinstance(value, CheckoutObservation):
                detail = 'Reviewed a simulated non-booking handoff. No reservation or payment was made.'
            else:
                detail = 'Validated a permitted tool observation. No booking was made.'
            add(trace, stage, title, detail, status, observed)
        elif trace.phase == 'final':
            state = completion_state(result)
            title, detail = {
                'approval_required': ('Waiting for your approval', 'Planning is complete. Review a court below; availability is not guaranteed and nothing is booked.'),
                'waiting_for_group': ('Waiting for your group', 'More participants or submitted availability are needed before planning can continue.'),
                'needs_user_help': ('Need your help', 'No further suitable action is available within this run. Review times, suburbs or venue preferences, or try again if a provider is temporarily unavailable.'),
                'failed': ('Planning stopped safely', 'This run could not safely finish. Your votes are unchanged; no reservation or payment was attempted.'),
                'completed': ('Handoff review complete', 'The permitted handoff review finished. This does not confirm a reservation or payment.'),
            }[state]
            add(trace, state, title, detail, 'failed' if state == 'failed' else 'completed')
    return events
