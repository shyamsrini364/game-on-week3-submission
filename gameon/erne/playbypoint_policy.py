"""Offers only bound eligible slots; checkout requires a parent-verified permit."""
from datetime import datetime, timezone
from .contracts import DiscoveryInput
from .playbypoint_contracts import CourtObservation, CheckoutObservation, CheckoutInput


def browser_options(state, request, eligible, browser, add, help_option):
    if browser.allowed_slots is not None:
        eligible = tuple(s for s in eligible if s in browser.allowed_slots)
        if not eligible:
            help_option(); return
    history = [e.payload for e in state['observations'] if isinstance(e.payload, (CourtObservation, CheckoutObservation))]
    approval = browser.approval
    if approval:
        if (not approval.observation.fresh() or approval.expires_at <= datetime.now(timezone.utc)
                or approval.observation.slot_id not in eligible):
            help_option(); return
        if history:
            last = history[-1]
            if isinstance(last, CheckoutObservation) and last.status == 'handoff':
                add(action='PROCEED_TO_DISCOVERY', outcome='discovery_recommendation', code='candidates_found',
                    reason='Simulated checkout reviewed. Use the official page; nothing is booked or prefilled.')
            else:
                help_option()
        elif state['iteration'] < request.max_iterations and not state['attempts'].get('prepare_checkout'):
            add(tool='prepare_checkout', arguments=CheckoutInput(snapshot_id=state['snapshot'].snapshot_id,
                slot_id=approval.observation.slot_id, observation_id=approval.observation.observation_id,
                court_id=approval.court_id), code='eligible_time', reason='Revalidate the explicitly approved fixture selection before a non-booking handoff.')
        else:
            help_option()
        return
    results = [d for d in history if isinstance(d, CourtObservation) and d.status == 'results' and d.fresh()]
    if results:
        add(action='PROCEED_TO_DISCOVERY', outcome='discovery_recommendation', code='candidates_found',
            reason=('Observed simulated court options and displayed price fields.' if results[0].simulated else 'Read a fresh live provider observation; availability and checkout price can change.')+' Organiser review is required; no booking.')
    if any(d.status not in ('results', 'empty') for d in history):
        help_option(); return
    count = state['attempts'].get('get_court_options', 0)
    if count < 2 and state['iteration'] < request.max_iterations:
        for slot_id in eligible:
            if any(d.slot_id == slot_id for d in history):
                continue
            add(tool='get_court_options', arguments=DiscoveryInput(snapshot_id=state['snapshot'].snapshot_id, slot_id=slot_id),
                code='alternative_slot' if history else 'eligible_time',
                reason='Read one group-approved time using the local browser skill.')
    elif not results:
        add(action='REQUEST_HELP', outcome='search_exhausted', code='search_exhausted',
            reason='No further eligible court checks in this run. Ask the organiser; votes are unchanged.')
