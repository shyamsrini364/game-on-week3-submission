"""Read-only, organiser-authorised booking brief. No provider or model calls."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .validation import StorageError
from .venues.service import recommend_for_game


class StaleHandoff(StorageError):
    pass


def prepare_handoff(store, game_id, member_token, selection, directory=None, *, planning_secret=None):
    # Authorisation and all game reads share the recommendation transaction.
    result = recommend_for_game(store, game_id, member_token, directory, require_owner=True)
    original_selection = selection
    if 'proposal' in selection:
        from .erne.proposals import verify
        scopes = verify(planning_secret, selection['proposal'], game_id, selection)
        selection = {k: v for k, v in selection.items() if k != 'proposal'}
        if selection.get('revision') != result['revision']:
            raise StaleHandoff('The game or suggestions changed. Run a fresh Ernie plan.')
        result = recommend_for_game(store, game_id, member_token, directory, require_owner=True,
                                   selected_slot_id=selection.get('slot_id'), search_scopes=scopes)
    if set(selection) != {'revision', 'slot_id', 'venue_id'} or any(
            not isinstance(v, str) or not 1 <= len(v) <= 128 for v in selection.values()):
        raise StorageError('Choose a suggested venue to prepare a booking handoff.')
    if (result['status'] != 'recommended' or selection['revision'] != result['revision']
            or selection['slot_id'] != result['slot_id']):
        raise StaleHandoff('The game or suggestions changed. Refresh and select a venue again.')
    venue = next((r['venue'] for r in result['recommendations'] if r['venue']['id'] == selection['venue_id']), None)
    if venue is None:
        raise StaleHandoff('This venue is no longer recommended. Refresh and select again.')
    duration = result['duration_minutes']
    if type(duration) is not int or duration not in (60, 90, 120):
        raise StorageError('Choose a duration of 60, 90 or 120 minutes.')
    start = datetime.fromisoformat(result['slot_starts_at']).astimezone(timezone.utc)
    end = start + timedelta(minutes=duration)
    if start <= datetime.now(timezone.utc):
        raise StaleHandoff('The selected time has passed. Propose a new future time.')
    if end != datetime.fromisoformat(result['slot_ends_at']):
        raise StorageError('The saved time and duration disagree. Create a valid session.')
    local_start, local_end = (t.astimezone(ZoneInfo('Australia/Melbourne')) for t in (start, end))
    time_format = '%Y-%m-%d %H:%M %Z (UTC%z)'
    from .pricing import cached_price, brief_lines
    price = cached_price(store, venue['id'], start, end, result['player_count'])
    constraints = ('Stay within the saved preferred venues; ask me before changing this selected venue.'
        if result['restrict_to_preferences'] else 'This selected venue is approved for enquiry; ask me before changing venue.')
    brief = '\n'.join([
        'GAME ON! — BOOKING HANDOFF (not a reservation)',
        'Please help me enquire about a pickleball court booking.',
        f"Selected venue: {venue['name']} — {venue['suburb']}",
        f"Official venue website: {venue['website_url']}",
        f"Trusted booking page: {venue['booking_url'] or 'Not listed; contact the venue through its official website.'}",
        *brief_lines(price),
        'Booking page only: court/time are not prefilled. Reselect and verify current availability and price.',
        'Time zone: Australia/Melbourne',
        f'Start: {local_start.strftime(time_format)}',
        f'End: {local_end.strftime(time_format)}',
        f'Duration: {duration} minutes',
        f"Players available at this time: {result['player_count']} ({result['session_player_count']} joined this session)",
        'Organiser-approved constraints: ' + constraints,
        'Keep the selected date, start time and duration. Ask me before any changes.',
        'Court availability: UNVERIFIED. Total price: UNVERIFIED.',
        'First verify court availability, total price including fees, and booking/cancellation conditions with the provider.',
        'Confirm the required number of courts and player capacity with me; do not assume one court fits everyone.',
        'Ask for my explicit approval before making a reservation, paying, accepting charges or changing the plan.',
        'If unavailable, report alternatives and ask me; do not substitute automatically.',
        'Report a booking as confirmed and provide a booking reference only after verifying provider confirmation.',
        'Opening a link or copying these instructions is not confirmation. No reservation or payment has been made by Game On!.',
    ])
    return {'status': 'suggested_venue', 'availability_status': 'not_checked',
            'price_status': 'cached_estimate' if price else 'not_checked', 'pricing': price, 'handoff_kind': 'booking_page_only', 'booking_status': 'not_confirmed',
            'selection': original_selection, 'venue_name': venue['name'], 'booking_url': venue['booking_url'],
            'venue_address': venue.get('address'), 'venue_suburb': venue['suburb'],
            'website_url': venue['website_url'], 'starts_at': local_start.isoformat(),
            'ends_at': local_end.isoformat(), 'duration_minutes': duration,
            'player_count': result['player_count'], 'session_player_count': result['session_player_count'],
            'brief': brief}
