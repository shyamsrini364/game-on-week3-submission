"""Venue choices: preferred first; expansion is optional, ranking precedes return."""
from gameon.venues.contracts import VenueSearchResult, VenueRanking
from .contracts import VenueSearchInput, RankVenuesInput, ToolFailure


def venue_options(state, request, eligible, add, help_option):
    snapshot = state['snapshot']
    context = snapshot.venue_context
    if context is None:
        help_option('missing_information')
        return
    histories = [e.payload for e in state['observations'] if e.source == 'search_pickleball_venues']
    rankings = [e.payload for e in state['observations'] if isinstance(e.payload, VenueRanking)]
    if rankings:
        if rankings[-1].slot_id in eligible and rankings[-1].recommendations:
            add(action='PROCEED_TO_DISCOVERY', outcome='discovery_recommendation', code='candidates_found',
                reason='Source-verified venues ranked using approximate suburb distances. Court availability is not checked; no booking was made.')
        else:
            add(action='REQUEST_HELP', outcome='search_exhausted', code='search_exhausted',
                reason='No suitable recommendations remain. Directory coverage is limited; ask the organiser for help.')
        return
    slots = (histories[0].slot_id,) if histories else eligible
    locations = {p.player_id: p.location for p in context.players}
    results = [d for d in histories if isinstance(d, VenueSearchResult) and d.venues]
    # Reserve one final decision after ranking, and one ranking + final after searching.
    can_search = state['iteration'] <= request.max_iterations - 2 and state['attempts'].get('search_pickleball_venues', 0) < 2
    for slot in slots:
        if slot not in eligible:
            continue
        match = next(m for m in state['schedule'].matches if m.slot_id == slot)
        if any(locations[p] is None for p in match.available_player_ids):
            continue
        if can_search:
            if not histories:
                scopes = ('preferred',) if context.preferences.preferred_venue_ids or any(
                    p.preferred_venue_ids for p in context.players if p.player_id in match.available_player_ids
                ) or context.preferences.restrict_to_preferences else ('local', 'expanded')
            elif isinstance(histories[-1], ToolFailure):
                scopes = (histories[-1].scope,)
            else:
                scopes = (() if context.preferences.restrict_to_preferences or histories[-1].scope == 'expanded'
                          else ('expanded',) if histories[-1].scope == 'local' else ('local', 'expanded'))
                scopes = tuple(s for s in scopes if s not in {d.scope for d in histories})
            for scope in scopes:
                add(tool='search_pickleball_venues', arguments=VenueSearchInput(
                    snapshot_id=snapshot.snapshot_id, slot_id=slot, scope=scope),
                    code='retry_transient' if histories and isinstance(histories[-1], ToolFailure) else 'eligible_time',
                    reason=f'Search {scope} venues in the curated directory. Identity evidence does not verify court availability.')
        if results and state['iteration'] < request.max_iterations and not state['attempts'].get('rank_venues'):
            add(tool='rank_venues', arguments=RankVenuesInput(snapshot_id=snapshot.snapshot_id, slot_id=slot),
                code='candidates_found', reason='Rank the observed suitable venues using Python distance and preference calculations.')
    if histories and not results and (not can_search or context.preferences.restrict_to_preferences):
        add(action='REQUEST_HELP', outcome='search_exhausted', code='search_exhausted',
            reason='The bounded directory search found no suitable venues. No live availability was checked.')
