"""Local LangGraph execution with a killable worker and parent-owned deadlines.

Callable from a guarded Python main or CLI. Injected adapters must be picklable.
Only read-only tools are supported; this is not a hosted/background job runner.
"""
import multiprocessing
import os
import secrets
import time
from datetime import datetime, timezone
from contextlib import redirect_stdout, redirect_stderr

from pydantic import ValidationError
from langgraph.graph import StateGraph, START, END
from langgraph.errors import GraphRecursionError

from gameon.venues.directory import CuratedDirectory, search_pickleball_venues, rank_venues
from gameon.venues.contracts import VenueSearchResult, VenueRanking
from gameon.storage import DatabaseUnavailable, StorageError
from .contracts import (AgentState, RunRequest, Snapshot, DiscoveryObservation,
                        FinalResult, TraceEntry, DecisionRequest, DecisionResponse, Evidence, ToolFailure, InferenceCall)
from .providers import DeterministicTestProvider, select
from .openai_provider import OpenAIResponsesProvider, ProviderError
from .policy import permitted_options
from .playbypoint import PlaybypointSkill
from .playbypoint_contracts import CourtObservation, CheckoutObservation
from .tools import read_game, schedule_game, eligible_slots, validate_schedule, TransientToolError


def discovery_status(history, configured):
    results = [e.payload for e in history if e.source in ('discover_courts', 'search_pickleball_venues', 'get_court_options')]
    if any(isinstance(d, (DiscoveryObservation, VenueSearchResult, CourtObservation)) and d.status == 'results' for d in results):
        return 'results'
    if results:
        status = getattr(results[-1], 'status', None)
        return status if status in ('results', 'empty', 'unavailable') else 'error'
    return 'not_attempted' if configured else 'not_configured'


def _execute_graph(connection, request, store, provider, discovery, run_id, deadline=None):
    deadline = deadline if deadline is not None else time.monotonic() + request.deadline_seconds
    trace = []
    venue_mode = isinstance(discovery, CuratedDirectory)
    browser = discovery if isinstance(discovery, PlaybypointSkill) else None
    inference_calls = {}
    is_responses = isinstance(provider, OpenAIResponsesProvider)
    default_source = ('llm' if provider.transport is None else 'mock') if is_responses else (
        'deterministic' if type(provider) is DeterministicTestProvider else 'scripted')

    def report(item):
        inference_calls[item.call_id] = item
        connection.send(('inference', item))
    if is_responses:
        provider.report = report

    def record(state, phase, name, summary, simulated=False, **metadata):
        event = TraceEntry(event_id=f'event-{len(trace) + 1}', iteration=state['iteration'], phase=phase, name=name,
                           summary=summary, simulated=simulated, **metadata)
        trace.append(event)
        connection.send(('trace', event))
        return event.event_id

    def stage(name, iteration):
        connection.send(('stage', (name, iteration)))

    def decide(state):
        if state['iteration'] >= request.max_iterations:
            return {'error': 'iteration_limit'}
        state = {**state, 'iteration': state['iteration'] + 1}
        stage('decision', state['iteration'])
        snapshot = state['snapshot']
        context = DecisionRequest(decision_id=secrets.token_hex(16), run_id=run_id,
            game_id=request.game_id, snapshot_id=snapshot.snapshot_id if snapshot else None,
            preferred_slot_id=request.preferred_slot_id,
            observations=state['observations'], options=permitted_options(state, request, discovery is not None, venue_mode, browser),
            eligible_slot_ids=eligible_slots(snapshot, state['schedule']),
            remaining_decisions=request.max_iterations-state['iteration'],
            remaining_discovery_attempts=max(0, 2-state['attempts'].get('discover_courts', 0)-state['attempts'].get('search_pickleball_venues', 0)-state['attempts'].get('get_court_options', 0)),
            remaining_seconds=max(0.0, deadline-time.monotonic()))
        source = 'policy' if is_responses and len(context.options) == 1 else default_source
        try:
            selection = DecisionResponse.model_validate(select(context, context.options[0])
                if source == 'policy' else provider.decide(context))
            if selection.decision_id != context.decision_id or selection.snapshot_id != context.snapshot_id:
                raise ValueError('Stale decision scope')
            decision = next(o for o in context.options if o.option_id == selection.selected_option_id)
            if set(selection.evidence_ids) != set(decision.evidence_ids) or len(set(selection.evidence_ids)) != len(selection.evidence_ids):
                raise ValueError('Invalid evidence references')
            if selection.reason_code != decision.reason_code:
                raise ValueError('Reason code incompatible with option')
            if decision not in permitted_options(state, request, discovery is not None, venue_mode, browser):
                raise ValueError('State changed during decision')
        except Exception as error:
            code = str(error) if isinstance(error, ProviderError) else 'invalid_decision'
            record(state, 'decision', code, 'Provider failed or returned an invalid decision.',
                   offered_options=context.options, decision_source=source)
            stage(None, state['iteration'])
            return {'iteration': state['iteration'], 'error': code}
        chosen = decision.tool if decision.kind == 'tool' else decision.action
        record(state, 'decision', chosen, decision.reason, decision.simulated,
               option_id=decision.option_id, offered_options=context.options, selection=selection, decision_source=source)
        stage(None, state['iteration'])
        return {'iteration': state['iteration'], 'decision': decision}

    def execute(state):
        decision = state['decision']
        name = decision.tool
        if decision not in permitted_options(state, request, discovery is not None, venue_mode, browser):
            return {'error': 'state_changed'}
        attempts = {**state['attempts'], name: state['attempts'].get(name, 0) + 1}
        if attempts[name] > (2 if name in ('read_game', 'discover_courts', 'search_pickleball_venues', 'get_court_options') else 1):
            return {'error': 'retry_limit'}
        simulated_tool = name in ('discover_courts', 'prepare_checkout') or (name == 'get_court_options' and browser.live_observations is None and not browser.live_selection)
        record(state, 'tool', name, 'Execute validated read-only tool.', simulated_tool, option_id=decision.option_id)
        stage(name, state['iteration'])
        try:
            if name == 'read_game':
                value = (read_game(store, decision.arguments, request.venue_preferences, discovery)
                         if venue_mode else read_game(store, decision.arguments))
            elif name == 'schedule_game':
                value = schedule_game(state['snapshot'], decision.arguments)
            elif name in ('get_court_options', 'prepare_checkout'):
                value = getattr(browser, name)(decision.arguments, state['snapshot'], state['schedule'])
            elif name == 'search_pickleball_venues':
                value = discovery.search(decision.arguments, state['snapshot'], state['schedule'])
            elif name == 'rank_venues':
                value = rank_venues(discovery, decision.arguments, state['snapshot'], state['schedule'], state['observations'])
            else:
                value = discovery.search(decision.arguments)
            return {'attempts': attempts, 'tool_result': value}
        except (DatabaseUnavailable, TransientToolError):
            value = ToolFailure(tool=name,
                snapshot_id=state['snapshot'].snapshot_id if state['snapshot'] else None,
                slot_id=getattr(decision.arguments, 'slot_id', None), scope=getattr(decision.arguments, 'scope', None))
            return {'attempts': attempts, 'tool_result': value}
        except StorageError:
            record(state, 'observation', name, 'Game could not be read.')
            return {'error': 'game_not_found', 'tool_result': None}
        except Exception:
            record(state, 'observation', name, 'Tool failed; private error details suppressed.')
            return {'error': 'tool_error', 'tool_result': None}
        finally:
            stage(None, state['iteration'])

    def observe(state):
        if state['error']:
            return {}
        name = state['decision'].tool
        try:
            if isinstance(state['tool_result'], ToolFailure):
                value = ToolFailure.model_validate(state['tool_result'])
                args = state['decision'].arguments
                if (value.tool != name or value.snapshot_id != getattr(args, 'snapshot_id', None)
                        or value.slot_id != getattr(args, 'slot_id', None)
                        or value.scope != getattr(args, 'scope', None)):
                    raise ValueError('Failure scope mismatch')
                return evidence(state, name, value, 'Transient failure; remaining options determine retry or handoff.')
            if name == 'read_game':
                value = Snapshot.model_validate(state['tool_result'])
                if value.game_id != request.game_id:
                    raise ValueError('Game mismatch')
                if request.preferred_slot_id and request.preferred_slot_id not in {s.id for s in value.slots}:
                    raise ValueError('Invalid preference scope')
                responses = sum(p.selected_slot_ids is not None for p in value.players)
                return {**evidence(state, name, value, f'{len(value.players)} players; {responses} submitted votes; {len(value.slots)} proposed times.'), 'snapshot': value}
            if name == 'schedule_game':
                value = validate_schedule(state['tool_result'], state['snapshot'])
                return {**evidence(state, name, value, f'{value.status}: {value.explanation}'), 'schedule': value}
            if name in ('search_pickleball_venues', 'rank_venues'):
                args = state['decision'].arguments
                expected = (search_pickleball_venues(discovery, args, state['snapshot'], state['schedule'])
                    if name == 'search_pickleball_venues' else
                    rank_venues(discovery, args, state['snapshot'], state['schedule'], state['observations']))
                value = type(expected).model_validate(state['tool_result'])
                if value != expected:
                    raise ValueError('Untrusted venue output')
                count = len(value.venues) if name == 'search_pickleball_venues' else len(value.recommendations)
                return evidence(state, name, value, f'{count} source-verified venues; court availability not checked.')
            if name in ('get_court_options', 'prepare_checkout'):
                cls = CourtObservation if name == 'get_court_options' else CheckoutObservation
                value = cls.model_validate(state['tool_result'])
                args = state['decision'].arguments
                if value.snapshot_id != args.snapshot_id or value.slot_id != args.slot_id:
                    raise ValueError('Browser scope mismatch')
                if isinstance(value, CourtObservation):
                    slot = next(s for s in state['snapshot'].slots if s.id == args.slot_id)
                    if value.revision != browser.revision or value.venue_id != browser.venue_id or not value.fresh():
                        raise ValueError('Invalid browser observation')
                    if any(o.starts_at != slot.starts_at or o.ends_at != slot.ends_at for o in value.options):
                        raise ValueError('Court time mismatch')
                provenance = ('offline browser fixture' if value.simulated else
                    'live selection-page observation; not held' if value.source == 'live_browser_selection' else
                    'previously observed live browser quote; inventory not guaranteed')
                return evidence(state, name, value, f'{value.status}: {provenance}; not booked.')
            value = DiscoveryObservation.model_validate(state['tool_result'])
            args = state['decision'].arguments
            if value.snapshot_id != args.snapshot_id or value.slot_id != args.slot_id:
                raise ValueError('Discovery scope mismatch')
            return {**evidence(state, name, value, f'{value.status}: {len(value.candidates)} simulated candidates; live availability not checked.')}
        except (ValueError, TypeError):
            record(state, 'observation', name, 'Tool output failed contract validation.')
            return {'error': 'invalid_observation', 'tool_result': None}

    def evidence(state, name, value, summary):
        simulated = name == 'discover_courts' or (name in ('get_court_options', 'prepare_checkout') and value.simulated)
        evidence_id = record(state, 'observation', name, summary, simulated,
                             option_id=state['decision'].option_id)
        item = Evidence(evidence_id=evidence_id, observed_at=datetime.now(timezone.utc),
                        source=name, payload=value, simulated=simulated)
        connection.send(('evidence', item))
        return {'observations': state['observations'] + (item,), 'tool_result': None}

    def finalize(state):
        error = state['error']
        # Re-evaluate expiry/gates immediately before returning an action.
        if not error and state['decision'] not in permitted_options(state, request, discovery is not None, venue_mode, browser):
            error = 'state_changed'
        if error:
            action = 'REQUEST_HELP' if error in ('iteration_limit', 'retry_limit', 'tool_unavailable', 'state_changed') else 'ERROR'
            reason = f'Execution stopped: {error}. No booking or payment was attempted.'
        else:
            action, reason = state['decision'].action, state['decision'].reason
            if state['decision'].reason_code == 'budget_exhausted':
                error = 'iteration_limit' if state['iteration'] >= request.max_iterations else 'tool_unavailable'
        snapshot, schedule = state['snapshot'], state['schedule']
        missing = []
        if snapshot:
            deficit = max(0, snapshot.min_players - len(snapshot.players))
            if deficit:
                missing.append(f'{deficit} more players needed')
            missing.extend(f'Vote needed: {p.id}' for p in snapshot.players if p.selected_slot_ids is None)
            if snapshot.venue_context:
                unresolved = sum(p.location is None for p in snapshot.venue_context.players)
                if unresolved:
                    missing.append(f'Mapped suburb reference needed for {unresolved} players')
        eligible = eligible_slots(snapshot, schedule)
        if schedule and schedule.status == 'ready' and not eligible:
            missing.append('New future time proposals needed')
        simulated = state['attempts'].get('discover_courts', 0) > 0 or any(e.simulated for e in state['observations'])
        record(state, 'final', action, reason, simulated)
        return {'result': FinalResult(run_id=run_id, game_id=request.game_id, action=action,
            mode=request.mode, inference_calls=tuple(inference_calls.values()),
            llm_call_attempted=any(c.source == 'live' for c in inference_calls.values()),
            llm_used=any(e.decision_source == 'llm' and e.selection is not None for e in trace),
            reason=reason, stop_reason=error or 'completed', iteration_count=state['iteration'],
            outcome='error' if error and action == 'ERROR' else 'human_handoff' if error else state['decision'].outcome,
            observations=state['observations'],
            recommendations=next((e.payload.recommendations for e in reversed(state['observations'])
                if isinstance(e.payload, VenueRanking)), ()) if not error and state['decision'].outcome == 'discovery_recommendation' else (),
            snapshot_id=snapshot.snapshot_id if snapshot else None,
            missing_information=tuple(missing), eligible_slot_ids=eligible,
            discovery_status=discovery_status(state['observations'], discovery is not None),
            simulated=simulated, evidence_ids=tuple(e.event_id for e in trace if e.phase == 'observation'), trace=tuple(trace))}

    builder = StateGraph(AgentState)
    for name, node in [('decide', decide), ('tool', execute), ('observe', observe), ('finalize', finalize)]:
        builder.add_node(name, node)
    builder.add_edge(START, 'decide')
    builder.add_conditional_edges('decide', lambda s: 'finalize' if s['error'] or s['decision'].kind == 'finish' else 'tool')
    builder.add_edge('tool', 'observe')
    builder.add_conditional_edges('observe', lambda s: 'finalize' if s['error'] else 'decide')
    builder.add_edge('finalize', END)
    graph = builder.compile()
    initial = AgentState(iteration=0, snapshot=None, schedule=None, decision=None,
                         attempts={}, error=None, tool_result=None, result=None, observations=())
    return graph.invoke(initial, config={'recursion_limit': 32, 'callbacks': []})['result']


def _worker(connection, request, store, provider, discovery, run_id, deadline):
    # Never enable external tracing implicitly from a user's shell settings.
    os.environ['LANGSMITH_TRACING'] = 'false'
    os.environ['LANGCHAIN_TRACING_V2'] = 'false'
    with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
        try:
            result = _execute_graph(connection, request, store, provider, discovery, run_id, deadline)
            connection.send(('result', result))
        except GraphRecursionError:
            connection.send(('failure', 'recursion_limit'))
        except Exception:
            connection.send(('failure', 'execution_error'))
        finally:
            connection.close()


def run_erne(request, store, *, provider=None, discovery=None):
    """One event-ready invocation: explicit input -> isolated run -> structured result.

    No timer, polling, registration of triggers or durable agent checkpoint writes.
    Deadlines include worker startup; termination/OS cleanup may add small overhead.
    """
    started = time.monotonic()
    run_id = secrets.token_hex(16)
    inference_calls = {}
    safe_mode = 'deterministic_test'
    trace, iteration = [], 0
    observations = []
    safe_game_id = ''

    def stopped(code):
        help_needed = code in ('tool_timeout', 'decision_timeout', 'deadline_exceeded', 'recursion_limit')
        action = 'REQUEST_HELP' if help_needed else 'ERROR'
        reason = f'Execution stopped: {code}. No booking or payment was attempted.'
        simulated = any(e.simulated for e in trace)
        final_trace = tuple(trace) + (TraceEntry(event_id=f'event-{len(trace) + 1}', iteration=iteration, phase='final', name=action, summary=reason, simulated=simulated),)
        return FinalResult(run_id=run_id, game_id=safe_game_id, action=action, reason=reason,
                           mode=safe_mode, elapsed_seconds=time.monotonic()-started,
                           inference_calls=tuple(inference_calls.values()),
                           llm_call_attempted=any(c.source == 'live' for c in inference_calls.values()),
                           llm_used=any(e.decision_source == 'llm' and e.selection is not None for e in trace),
                           stop_reason=code, iteration_count=iteration, trace=final_trace, simulated=simulated,
                           outcome='human_handoff' if help_needed else 'error',
                           observations=tuple(observations),
                           discovery_status='error' if simulated else 'not_attempted',
                           evidence_ids=tuple(e.event_id for e in trace if e.phase == 'observation'))

    try:
        request = RunRequest.model_validate(request)
        safe_game_id = request.game_id
        safe_mode = request.mode
    except (ValidationError, TypeError):
        return stopped('invalid_request')
    if (request.venue_preferences is not None) != isinstance(discovery, CuratedDirectory):
        return stopped('invalid_venue_configuration')
    if isinstance(discovery, PlaybypointSkill) and os.environ.get('VERCEL'):
        return stopped('local_browser_only')
    provider = provider if provider is not None else DeterministicTestProvider()
    if request.mode == 'llm':
        if not isinstance(provider, OpenAIResponsesProvider):
            return stopped('provider_not_supported')
    elif getattr(provider, 'mode', None) != 'deterministic_test':
        return stopped('provider_not_supported')
    deadline = started + request.deadline_seconds
    stage_deadline, current_stage = deadline, None
    if os.environ.get('VERCEL'):
        # The approved hosted adapter validates its exact runtime profile before
        # reaching this graph. Avoid spawn re-importing Vercel's handler as main.
        from .subprocess_worker import prepare_worker
        receiver, sender, process = prepare_worker(
            (request, store, provider, discovery, run_id, deadline))
    else:
        ctx = multiprocessing.get_context('spawn')
        receiver, sender = ctx.Pipe(duplex=False)
        process = ctx.Process(target=_worker, args=(sender, request, store, provider, discovery, run_id, deadline), daemon=True)
    try:
        process.start()
        sender.close()
        while True:
            remaining = min(deadline, stage_deadline) - time.monotonic()
            if remaining <= 0:
                return stopped('deadline_exceeded' if time.monotonic() >= deadline else
                               'decision_timeout' if current_stage == 'decision' else 'tool_timeout')
            if not receiver.poll(remaining):
                continue
            kind, payload = receiver.recv()
            if kind == 'trace':
                trace.append(TraceEntry.model_validate(payload))
            elif kind == 'inference':
                item = InferenceCall.model_validate(payload)
                inference_calls[item.call_id] = item
            elif kind == 'evidence':
                observations.append(Evidence.model_validate(payload))
            elif kind == 'stage':
                current_stage, iteration = payload
                stage_deadline = time.monotonic() + request.tool_timeout_seconds if current_stage else deadline
            elif kind == 'result':
                return FinalResult.model_validate(payload).model_copy(update={'elapsed_seconds': time.monotonic()-started})
            elif kind == 'failure':
                return stopped(payload)
    except Exception:
        return stopped('worker_error')
    finally:
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=0.5)
            if process.is_alive():
                process.kill()
                process.join(timeout=0.5)
            if not process.is_alive():
                process.close()
        sender.close()
        receiver.close()
