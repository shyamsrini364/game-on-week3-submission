"""Read only an already-open, owner-approved real summary. Never click Next/pay.

Visible DOM only: no requests to private APIs, cookies, storage or page internals.
Missing amounts remain unknown. A displayed quote does not confirm inventory.
"""
from datetime import datetime, timezone
import re
import secrets
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
from .playbypoint_contracts import CourtOption, CourtObservation

HOSTS = {'pickleholic-clayton': 'book.pickleholic.com.au',
         'level-up-knox': 'www.leveluppickleball.com.au'}
NAMES = {'pickleholic-clayton': 'PICKLEHOLIC', 'level-up-knox': 'Level Up Pickleball Knox City'}


def parse_summary(text, venue_id, slot, player_count):
    from .playbypoint import money
    lines = [s.strip() for s in text.splitlines() if s.strip()]
    if 'Booking at '+NAMES[venue_id] not in lines or lines.count('Reservation Summary') != 1:
        raise ValueError('Unexpected venue or missing summary')
    summary = lines[lines.index('Reservation Summary')+1:]
    if 'Cancellation Policy' in summary:
        summary = summary[:summary.index('Cancellation Policy')]
    local = slot.starts_at.astimezone(ZoneInfo('Australia/Melbourne'))
    end = slot.ends_at.astimezone(ZoneInfo('Australia/Melbourne'))
    category = 'Main Courts' if venue_id == 'pickleholic-clayton' else 'Pickleball'
    # One explicitly supported individual court. Never infer an opaque provider ID.
    if summary[0] != category+' • Court 3':
        raise ValueError('Unsupported court')
    expected_date = local.strftime('%A, %b %d')
    minutes = int((slot.ends_at-slot.starts_at).total_seconds()/60)
    clock = lambda d: d.strftime('%I:%M').lstrip('0')
    expected_time = f'{clock(local)} - {clock(end)} {end.strftime("%p")} ({minutes} minutes)'
    if local.date() != end.date() or local.strftime('%p') != end.strftime('%p'):
        raise ValueError('Ambiguous cross-period time unsupported')
    if summary[1:3] != [expected_date, expected_time]:
        raise ValueError('Displayed date/time does not match approved slot')
    expected_players = player_count if venue_id == 'pickleholic-clayton' else 1
    if expected_players > 4 or f'{expected_players} player'+('s' if expected_players != 1 else '') not in summary:
        raise ValueError('Displayed player setting does not match')
    def amount(label):
        positions = [i for i, s in enumerate(summary) if s == label]
        if not positions:
            return None
        if len(positions) != 1 or positions[0]+1 >= len(summary):
            raise ValueError('Ambiguous price field')
        value = money(summary[positions[0]+1])
        if value is None:
            raise ValueError('Unrecognised displayed amount')
        return value
    total, fee, tax = amount('Total'), amount('Technology fee'), amount('Tax')
    if total is None:
        raise ValueError('Total not displayed')
    base, per_player = amount('Single reservation'), None
    if expected_players > 1:
        shares = [amount('Player 1 (you)')]+[amount(f'Player {i}') for i in range(2, expected_players+1)]
        if any(v is None for v in shares) or 'This club requires the booker to pay for every player.' not in lines:
            raise ValueError('Group payment coverage unclear')
        base = sum(shares)
        per_player = shares[0] if len(set(shares)) == 1 else None
    if base is None or base+(fee or 0)+(tax or 0) != total:
        raise ValueError('Displayed amounts do not reconcile')
    return CourtOption(court_id='court-3', court_name='Court 3', starts_at=slot.starts_at,
        ends_at=slot.ends_at, currency='AUD' if any('AUD' in s for s in summary) else None,
        base_minor=base, total_minor=total, fees_minor=fee, tax_minor=tax,
        per_player_minor=per_player, provider_players=expected_players,
        conditions='Displayed quote only; inventory/hold not guaranteed. No reservation confirmed. '
        + ('Booker pays all selected players.' if expected_players > 1 else 'Provider player setting is not attendance capacity.'))


def read_summary(page, venue_id, revision, request, slot, player_count, *, provider_players=1):
    if not 0 < (slot.starts_at-datetime.now(timezone.utc)).total_seconds() <= 14*86400:
        raise ValueError('Live summary date must be within the visible booking horizon')
    outer = urlsplit(page.url)
    if outer.scheme != 'https' or outer.hostname != HOSTS[venue_id] or outer.path != '/book':
        raise ValueError('Untrusted booking page')
    frames = [f for f in page.frames if (urlsplit(f.url).scheme, urlsplit(f.url).hostname,
              urlsplit(f.url).path) == ('https', 'greenfield.playbypoint.com', '/booking/checkout')]
    if len(frames) != 1:
        raise ValueError('Approved checkout summary is not open; supervise Next separately')
    frame = frames[0]
    # Do not read personal values, account content or signed-in profiles.
    if frame.get_by_role('button', name='Sign In', exact=True).count() != 1:
        raise ValueError('Fresh unauthenticated browser required')
    if frame.locator('input[type=text]:not([aria-label="Search countries"]),input[type=email],input[type=tel],textarea').evaluate_all(
            'els=>els.some(e=>Boolean(e.value))'):
        raise ValueError('Personal fields must remain blank')
    text = frame.locator('body').inner_text(timeout=3000)
    option = parse_summary(text, venue_id, slot, provider_players)
    return CourtObservation(observation_id=secrets.token_hex(16), snapshot_id=request.snapshot_id,
        slot_id=request.slot_id, venue_id=venue_id, revision=revision, player_count=player_count,
        observed_at=datetime.now(timezone.utc), source_url=f'https://{HOSTS[venue_id]}/book',
        simulated=False, source='live_browser_summary', status='results', options=(option,))
