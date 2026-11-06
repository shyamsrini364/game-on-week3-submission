"""Permissions derived from validated observations and remaining budgets."""
import hashlib
from .contracts import ActionOption, ReadInput, ScheduleInput, DiscoveryInput, DiscoveryObservation, ToolFailure
from .tools import eligible_slots


def permitted_options(state, request, discovery_configured, venue_mode=False, browser=None):
    snapshot, schedule = state['snapshot'], state['schedule']
    history = state['observations']
    refs = tuple(e.evidence_id for e in history)
    options = []

    def add(*, tool=None, arguments=None, action=None, outcome=None, code, reason):
        identity = f'{request.game_id}:{snapshot.snapshot_id if snapshot else "none"}:{tool}:{arguments}:{outcome}'
        options.append(ActionOption(option_id='o-' + hashlib.sha256(identity.encode()).hexdigest()[:24],
            kind='tool' if tool else 'finish', tool=tool, arguments=arguments, action=action,
            reason=reason, reason_code=code, outcome=outcome, evidence_ids=refs,
            effect='read_only' if tool else 'return_to_caller',
            simulated=tool in ('discover_courts', 'prepare_checkout') or (tool == 'get_court_options' and (browser is None or browser.live_observations is None and not browser.live_selection)) or any(e.simulated for e in history)))

    def help_option(code='human_help'):
        add(action='REQUEST_HELP', outcome='human_handoff', code=code,
            reason='Return to the organiser for help. No message, booking or payment was sent.')

    can_call = state['iteration'] < request.max_iterations
    if snapshot is None:
        if can_call and state['attempts'].get('read_game', 0) < 2:
            add(tool='read_game', arguments=ReadInput(game_id=request.game_id), code='prerequisite', reason='Read the current game records.')
        else:
            help_option('budget_exhausted')
        return tuple(options)
    if schedule is None:
        if can_call and not state['attempts'].get('schedule_game'):
            add(tool='schedule_game', arguments=ScheduleInput(snapshot_id=snapshot.snapshot_id), code='prerequisite', reason='Calculate shared times using the existing scheduling function.')
        else:
            help_option('budget_exhausted')
        return tuple(options)
    eligible = eligible_slots(snapshot, schedule)
    if schedule.status == 'waiting_for_responses':
        add(action='WAIT', outcome='waiting', code='missing_information', reason='More players or availability responses are needed. Wait for an updated game.')
    elif not eligible:
        add(action='REQUEST_ALTERNATIVES', outcome='alternatives_requested', code='no_eligible_time', reason='No future proposed time is eligible. Request alternative times.')
    elif not discovery_configured:
        add(action='PROCEED_TO_DISCOVERY', outcome='discovery_ready', code='eligible_time', reason='An eligible time exists; no discovery adapter is configured. No live availability was checked.')
    elif browser is not None:
        from .playbypoint_policy import browser_options
        browser_options(state, request, eligible, browser, add, help_option)
    elif venue_mode:
        from .venue_policy import venue_options
        venue_options(state, request, eligible, add, help_option)
    else:
        discoveries = [e.payload for e in history if e.source == 'discover_courts']
        results = [d for d in discoveries if isinstance(d, DiscoveryObservation) and d.status == 'results' and d.slot_id in eligible]
        if results:
            add(action='PROCEED_TO_DISCOVERY', outcome='discovery_recommendation', code='candidates_found', reason='Simulated candidates found for an eligible time. This is a recommendation, not a booking or verified availability.')
        count = state['attempts'].get('discover_courts', 0)
        if can_call and count < 2:
            for slot_id in eligible:
                previous = [d for d in discoveries if d.slot_id == slot_id]
                if previous and not isinstance(previous[-1], ToolFailure):
                    continue
                code = 'retry_transient' if previous else 'alternative_slot' if discoveries else 'eligible_time'
                add(tool='discover_courts', arguments=DiscoveryInput(snapshot_id=snapshot.snapshot_id, slot_id=slot_id),
                    code=code, reason='Search this eligible slot using the simulated discovery adapter.')
        if not results and not any(o.kind == 'tool' for o in options):
            add(action='REQUEST_HELP', outcome='search_exhausted', code='search_exhausted',
                reason='No further simulated searches are permitted in this run. Live inventory remains unknown; no booking was made.')
    help_option()
    return tuple(options)
