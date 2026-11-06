"""Historical court prices, never availability or booking authority.

Uses the existing SQLite/PostgreSQL connection abstraction. No public write API,
provider navigation, scheduler, or session data. Exact slot matching deliberately
does not extrapolate a weekend/evening rate to another date.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import os
from pathlib import Path

BOOKING_URLS = {
    'pickleholic-clayton': 'https://book.pickleholic.com.au/book/Pickleholic',
    'level-up-knox': 'https://www.leveluppickleball.com.au/book',
}
CATEGORIES = {'pickleholic-clayton': 'Main Courts', 'level-up-knox': 'Pickleball'}


def instant(value):
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    if value.tzinfo is None:
        raise ValueError('Price timestamps must include a timezone')
    return value.astimezone(timezone.utc)


def setup(store):
    """Explicit additive setup, never run during HTTP requests."""
    with store._connect(write=True) as c:
        script = '\n'.join(line for line in Path(__file__).with_name('pricing_schema.sql').read_text().splitlines()
                           if not line.lstrip().startswith('--'))
        for statement in script.split(';'):
            if statement.strip():
                c.execute(statement)


def save_observation(store, observation):
    if observation.simulated or observation.source != 'live_browser_summary' or observation.venue_id not in CATEGORIES:
        raise ValueError('Only supported real summary observations may enter the cache')
    observed = instant(observation.observed_at)
    if observed > datetime.now(timezone.utc):
        raise ValueError('Future observation timestamp')
    with store._connect(write=True) as c:
        for option in observation.options:
            if option.court_id != 'court-3' or option.total_minor is None:
                raise ValueError('Unsupported court or unknown total')
            start, end = instant(option.starts_at), instant(option.ends_at)
            minutes = int((end-start).total_seconds()/60)
            if minutes not in (60, 90, 120):
                raise ValueError('Unsupported duration')
            values = (observation.observation_id+':'+option.court_id, observation.venue_id,
                CATEGORIES[observation.venue_id], option.court_name, start.isoformat(), end.isoformat(),
                minutes, option.provider_players, option.total_minor, option.currency,
                option.fees_minor, option.tax_minor, observed.isoformat(), observation.source,
                BOOKING_URLS[observation.venue_id], 'observed')
            c.execute('INSERT INTO court_prices VALUES ('+','.join('?' for _ in values)+') '
                      'ON CONFLICT(observation_id) DO NOTHING', values)


def mark_refresh_failed(store, venue_id, starts_at, ends_at):
    with store._connect(write=True) as c:
        c.execute("UPDATE court_prices SET refresh_status='failed' WHERE venue_id=? AND starts_at=? AND ends_at=?",
                  (venue_id, instant(starts_at).isoformat(), instant(ends_at).isoformat()))


def cached_price(store, venue_id, starts_at, ends_at, participants, *, category=None,
                 court_name='Court 3', provider_players=1, now=None):
    if venue_id not in CATEGORIES:
        return None
    if type(participants) is not int or participants < 0:
        raise ValueError('Participating count must be nonnegative')
    with store._connect() as c:
        row = c.execute('SELECT * FROM court_prices WHERE venue_id=? AND starts_at=? AND ends_at=? '
            'AND category=? AND court_name=? AND provider_players=? ORDER BY observed_at DESC LIMIT 1',
            (venue_id, instant(starts_at).isoformat(), instant(ends_at).isoformat(),
             category or CATEGORIES[venue_id], court_name, provider_players)).fetchone()
    if not row:
        return None
    result = dict(row)
    cadence = int(os.environ.get('GAMEON_PRICE_REFRESH_DAYS', '7'))
    if not 1 <= cadence <= 14:
        raise ValueError('GAMEON_PRICE_REFRESH_DAYS must be between 1 and 14')
    age = (instant(now or datetime.now(timezone.utc))-instant(result['observed_at'])).total_seconds()
    result.update(stale=age > 14*86400, refresh_due=age >= cadence*86400,
                  refresh_cadence_days=cadence, participant_count=participants,
                  estimated_per_player_minor=int((Decimal(result['total_minor'])/participants).quantize(Decimal('1'), rounding=ROUND_HALF_UP)) if participants else None,
                  estimate=True, availability_status='not_checked')
    return result


def amount(minor, currency):
    return (currency+' ' if currency else '$')+f'{Decimal(minor)/100:.2f}'+(' (currency unverified)' if not currency else '')


def brief_lines(price):
    if not price:
        return ['Court/category and price: to confirm with the venue.']
    return [f"Court/category: {price['category']} / {price['court_name']}",
        f"Last observed total payable: {amount(price['total_minor'], price['currency'])} (cached estimate, not guaranteed)",
        f"Observed at: {price['observed_at']}"+(' — STALE' if price['stale'] else ''),
        f"Estimated per-person share: {amount(price['estimated_per_player_minor'], price['currency'])} ({price['participant_count']} participating players; calculated split, rounded)" if price['estimated_per_player_minor'] is not None else 'Per-person share unavailable: no participating players.',
        f"Provider player setting: {price['provider_players']} (separate from group participation)",
        'Fees: '+(amount(price['fees_minor'], price['currency']) if price['fees_minor'] is not None else 'not separately observed')+
        '; Tax: '+(amount(price['tax_minor'], price['currency']) if price['tax_minor'] is not None else 'not separately observed'),
        f"Refresh status: {price['refresh_status']}. This observation applies only to the specified court and time."]


def seed_i058(store):
    """Import immutable recorded evidence, preserving actual timestamps. Idempotent."""
    import json
    from .erne.contracts import Slot
    from .erne.playbypoint_live import parse_summary
    from .erne.playbypoint_contracts import CourtObservation
    root = Path(__file__).resolve().parents[1]/'gameon/erne/data/recorded-pricing'
    for filename, venue, day, count in [('pickleholic-one-player', 'pickleholic-clayton', 7, 1),
                                       ('pickleholic', 'pickleholic-clayton', 7, 4),
                                       ('level-up', 'level-up-knox', 8, 1)]:
        data = json.loads((root/(filename+'.json')).read_text())
        slot = Slot(id='historical', starts_at=datetime.fromisoformat(f'2026-11-{day:02}T18:30:00+11:00'), ends_at=datetime.fromisoformat(f'2026-11-{day:02}T19:30:00+11:00'))
        option = parse_summary(data['summary_text'], venue, slot, count)
        save_observation(store, CourtObservation(observation_id='i058-'+filename, snapshot_id='historical',
            slot_id=slot.id, venue_id=venue, revision='historical', player_count=count,
            observed_at=instant(data['observed_at']), source_url=BOOKING_URLS[venue], simulated=False,
            source='live_browser_summary', status='results', options=(option,)))
