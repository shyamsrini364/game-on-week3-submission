"""Explicit local entry point. No public route or deployment configuration added.

Uses the same owner capture and signed receipts as existing planning. Approval
is a separate owner operation, never a model decision. Registry lives only for
this local session; restart invalidates all observations and permits.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import os
import threading

from gameon.validation import StorageError
from gameon.venues.directory import CuratedDirectory, suitable
from . import proposals
from .graph import run_erne
from .playbypoint import PlaybypointSkill, VENUES
from .playbypoint_contracts import CourtObservation, BrowserApproval
from .web_service import capture, PlanningConflict, PlanningUnavailable


def plan_live_game(store, game_id, token, venue_id, signing_secret):
    """Explicit opt-in to the existing local plan route; no hosted browser path."""
    from pathlib import Path
    from .web_service import public_activity
    from gameon.venues.service import recommend_for_game
    from gameon.pricing import cached_price
    if os.environ.get('VERCEL') or venue_id not in VENUES:
        raise PlanningUnavailable('Unsupported local browser venue.')
    executable=os.environ.get('GAMEON_PLAYWRIGHT_EXECUTABLE','')
    if not executable or not Path(executable).is_file():
        raise PlanningUnavailable('Configure the existing local Chromium executable before live planning.')
    frozen, preferences, revision=capture(store,game_id,token)
    session=LocalPlaybypointSession(store,signing_secret,executable,{},live_selection=True)
    result=session.get_court_options(game_id,token,venue_id)
    current=recommend_for_game(store,game_id,token,require_owner=True)
    recommendations=[]
    observed=next((e.payload for e in reversed(result.observations)
        if isinstance(e.payload,CourtObservation) and e.payload.status=='results' and e.payload.fresh()),None)
    if observed and result.outcome=='discovery_recommendation' and result.stop_reason=='completed':
        scopes=('preferred',) if preferences.group.restrict_to_preferences else ('preferred','expanded')
        checked=recommend_for_game(store,game_id,token,require_owner=True,
            selected_slot_id=observed.slot_id,search_scopes=scopes)
        ranked=next((r for r in checked['recommendations'] if r['venue']['id']==venue_id),None)
        # Existing booking handoff cache/brief is scoped to Court 3. Do not
        # silently substitute another court before exact-court handoff exists.
        court=next((o for o in observed.options if o.court_id=='court-3'),None)
        if ranked and court:
            venue=ranked['venue']
            price=cached_price(store,venue_id,court.starts_at,court.ends_at,observed.player_count)
            if price and price['court_name']!=court.court_name:price=None
            receipt=proposals.issue(signing_secret,game_id,checked['revision'],observed.slot_id,scopes,(venue_id,))
            recommendations.append(dict(venue_id=venue_id,name=venue['name'],suburb=venue['suburb'],venue=venue,
                reason='Offered on the live provider selection page; checkout requires revalidation.',
                average_distance_km=round(ranked['average_distance_km'],1),longest_distance_km=round(ranked['longest_distance_km'],1),
                player_count=observed.player_count,starts_at=court.starts_at.isoformat(),duration_minutes=frozen.game.duration_minutes,
                pricing=price,court_availability=dict(court_name=court.court_name,category=VENUES[venue_id].category,
                    status='offered_at_observation',observed_at=observed.observed_at.isoformat(),source_url=observed.source_url,
                    simulated=False,checkout_price_verified=False,provider_players=1,provider_players_verified=court.provider_players_verified),
                selection=dict(revision=checked['revision'],slot_id=observed.slot_id,venue_id=venue_id,proposal=receipt)))
    if capture(store,game_id,token)[2]!=revision:
        raise PlanningConflict('The game changed during live planning. Run again.')
    activity=public_activity(result)
    if observed and not recommendations:
        activity['planning_state']='needs_user_help'
    return dict(**activity,execution_mode='deterministic',execution_status='completed' if result.stop_reason=='completed' else 'failed',
        revision=current['revision'],activities=[],llm_used=False,llm_call_attempted=False,model=None,
        elapsed_seconds=round(result.elapsed_seconds,3),outcome=result.outcome,reason=result.reason,
        stop_reason=result.stop_reason,missing_information=[],availability_status='observed' if recommendations else 'unknown',
        trace=[],recommendations=recommendations)


class LocalPlaybypointSession:
    def __init__(self, store, signing_secret, executable_path, fixture_pages, *, live_selection=False):
        if os.environ.get('VERCEL') or not signing_secret:
            raise PlanningUnavailable('Browser skill requires an explicit local session.')
        self.store, self.secret, self.executable = store, signing_secret, executable_path
        self.pages = fixture_pages
        self.live_selection = live_selection
        self.observations, self.approvals = {}, {}
        self.lock = threading.Lock()

    def _capture(self, game_id, token):
        if os.environ.get('VERCEL'):
            raise PlanningUnavailable('Browser skill is local only.')
        return capture(self.store, game_id, token)

    def _adapter(self, frozen, revision, venue_id, preferences, approval=None):
        adapter = PlaybypointSkill(venue_id, revision, self.pages.get(venue_id, {}), self.executable, approval)
        adapter.live_selection = self.live_selection
        directory = CuratedDirectory()
        context = directory.context(frozen.game, preferences)
        venue = next(v for v in directory.venues if v.id == venue_id)
        allowed = []
        for slot in frozen.game.slots:
            players = tuple(p for p in context.players if slot.id in frozen.game.votes.get(p.player_id, ()))
            if (players and all(p.location is not None for p in players)
                    and suitable(venue, context, players, datetime.now(timezone.utc).date())):
                allowed.append(slot.id)
        adapter.allowed_slots = tuple(allowed)
        return adapter

    def _run(self, frozen, game_id, revision, venue_id, preferences, approval=None, live_observation=None):
        adapter = self._adapter(frozen, revision, venue_id, preferences, approval)
        if live_observation is not None:
            adapter.live_selection = False
            adapter.live_observations = (live_observation,)
            adapter.allowed_slots = tuple(s for s in adapter.allowed_slots if s == live_observation.slot_id)
        return run_erne({'game_id': game_id, 'deadline_seconds': 50.0, 'tool_timeout_seconds': 20.0},
                        frozen, discovery=adapter)

    def get_court_options(self, game_id, token, venue_id, *, live_page=None, slot_id=None, provider_players=1):
        if venue_id not in VENUES:
            raise StorageError('Unsupported venue')
        frozen, preferences, revision = self._capture(game_id, token)
        if not self.store.allow_request('pbp-read:'+game_id, 1, 55 if self.live_selection else 20):
            raise PlanningConflict('Wait before another browser run.')
        observation = None
        if live_page is not None:
            from .tools import read_game, schedule_game
            from .contracts import ReadInput, ScheduleInput, DiscoveryInput
            snapshot = read_game(frozen, ReadInput(game_id=game_id))
            schedule = schedule_game(snapshot, ScheduleInput(snapshot_id=snapshot.snapshot_id))
            adapter = self._adapter(frozen, revision, venue_id, preferences)
            observation = adapter.get_court_options(DiscoveryInput(snapshot_id=snapshot.snapshot_id, slot_id=slot_id),
                snapshot, schedule, live_page=live_page, provider_players=provider_players)
        result = self._run(frozen, game_id, revision, venue_id, preferences, live_observation=observation)
        if self._capture(game_id, token)[2] != revision:
            raise PlanningConflict('Game changed during browser execution.')
        with self.lock:
            for item in result.observations:
                if isinstance(item.payload, CourtObservation):
                    self.observations[item.payload.observation_id] = (game_id, item.payload)
        return result

    def refresh_price(self, game_id, token, venue_id, *, live_page, slot_id, provider_players=1):
        """Manual local refresh of an already-approved summary; never navigates.

        The supervisor leaves the provider selector at 1 unless venue requirements
        explicitly require otherwise. Group attendance is captured independently.
        """
        from gameon.pricing import save_observation, mark_refresh_failed
        from .tools import read_game, schedule_game
        from .contracts import ReadInput, ScheduleInput, DiscoveryInput
        if venue_id not in VENUES or type(provider_players) is not int or not 1 <= provider_players <= 4:
            raise StorageError('Unsupported venue or provider player setting')
        frozen, preferences, revision = self._capture(game_id, token)
        snapshot = read_game(frozen, ReadInput(game_id=game_id))
        schedule = schedule_game(snapshot, ScheduleInput(snapshot_id=snapshot.snapshot_id))
        adapter = self._adapter(frozen, revision, venue_id, preferences)
        request = DiscoveryInput(snapshot_id=snapshot.snapshot_id, slot_id=slot_id)
        slot, _ = adapter._scope(request, snapshot, schedule)
        if not self.store.allow_request('pbp-read:'+game_id, 1, 55 if self.live_selection else 20):
            raise PlanningConflict('Wait before another browser run.')
        try:
            observation = adapter.get_court_options(request, snapshot, schedule,
                live_page=live_page, provider_players=provider_players)
        except Exception:
            # Reauthorise before any write. Retain the old timestamp and amount.
            if self._capture(game_id, token)[2] == revision:
                mark_refresh_failed(self.store, venue_id, slot.starts_at, slot.ends_at)
            raise
        if self._capture(game_id, token)[2] != revision:
            raise PlanningConflict('Game changed during price refresh.')
        save_observation(self.store, observation)
        return observation

    def _selection(self, game_id, token, observation_id, court_id):
        frozen, preferences, revision = self._capture(game_id, token)
        stored = self.observations.get(observation_id)
        if not stored or stored[0] != game_id:
            raise StorageError('Unknown observation')
        observation = stored[1]
        if not observation.simulated:
            raise StorageError('Live quote is for organiser review only; supervise provider checkout separately')
        court = next((c for c in observation.options if c.court_id == court_id), None)
        if not observation.fresh() or observation.revision != revision or not court:
            raise StorageError('Stale or mismatched observation')
        if court.total_minor is None or court.fees_minor is None:
            raise StorageError('Price or fees unknown; review manually')
        binding = hashlib.sha256((observation.model_dump_json()+'|'+court_id).encode()).hexdigest()
        selection = {'revision': revision, 'slot_id': observation.slot_id, 'venue_id': observation.venue_id}
        return frozen, observation, binding, selection, preferences

    def approve_checkout(self, game_id, token, observation_id, court_id):
        """Call only for an explicit organiser confirmation; no automatic approval."""
        with self.lock:
            _, obs, binding, selection, _ = self._selection(game_id, token, observation_id, court_id)
            receipt = proposals.issue(self.secret, game_id, obs.revision, obs.slot_id, (), (obs.venue_id,), binding=binding)
            if binding in self.approvals:
                raise StorageError('Selection already approved; obtain a fresh observation')
            self.approvals[binding] = receipt
            return receipt

    def prepare_checkout(self, game_id, token, observation_id, court_id, approval_reference):
        with self.lock:
            frozen, obs, binding, selection, preferences = self._selection(game_id, token, observation_id, court_id)
            proposals.verify(self.secret, approval_reference, game_id, selection, binding=binding)
            if self.approvals.get(binding) != approval_reference:
                raise StorageError('Approval already consumed or not issued here')
            # Consume before execution, including failures. No provider mutation or retry.
            self.approvals[binding] = None
        permit = BrowserApproval(observation=obs, court_id=court_id,
            expires_at=min(obs.observed_at+timedelta(seconds=120), datetime.now(timezone.utc)+timedelta(seconds=60)))
        result = self._run(frozen, game_id, obs.revision, obs.venue_id, preferences, permit)
        if self._capture(game_id, token)[2] != obs.revision:
            raise PlanningConflict('Game changed during checkout review.')
        return result
