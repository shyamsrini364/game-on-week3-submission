"""Shared local fixture workflow and explicitly supervised live summary reads.

Selectors describe labelled offline fixtures, NOT a verified provider DOM.
No private endpoints, cookies, logins, network writes or paid services.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import os
import re
import secrets
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .playbypoint_contracts import CourtOption, CourtObservation, CheckoutObservation
from .tools import eligible_slots


@dataclass(frozen=True)
class VenueConfig:
    url: str
    category: str
    group_players: bool


VENUES = {
    'pickleholic-clayton': VenueConfig('https://book.pickleholic.com.au/book/Pickleholic', 'Main courts', True),
    # Normal Book Now destination directly observed in the owner's walkthrough.
    'level-up-knox': VenueConfig('https://www.leveluppickleball.com.au/book', 'Individual courts', False),
}


def money(text):
    """Parse displayed AUD only; absent/ambiguous/other currency stays unknown."""
    match = re.fullmatch(r'(?:AUD\s*\$?\s*|A\$\s*|\$\s*)(\d+(?:,\d{3})*(?:\.\d{2})?)', text.strip())
    return int(Decimal(match[1].replace(',', ''))*100) if match else None


@dataclass
class PlaybypointSkill:
    venue_id: str
    revision: str
    # Server-supplied offline pages, keyed by approved slot ID. Never user HTTP input.
    fixture_pages: dict[str, str]
    executable_path: str
    approval: object = None
    timeout_ms: int = 1500
    allowed_slots: tuple[str, ...] | None = None
    live_observations: tuple[CourtObservation, ...] | None = None
    live_selection: bool = False

    def __post_init__(self):
        if self.venue_id not in VENUES or not 100 <= self.timeout_ms <= 3000:
            raise ValueError('Invalid browser configuration')
        if os.environ.get('VERCEL'):
            raise ValueError('Browser skill is local only')

    def _scope(self, request, snapshot, schedule):
        if os.environ.get('VERCEL'):
            raise ValueError('Browser skill is local only')
        if request.snapshot_id != snapshot.snapshot_id or request.slot_id not in eligible_slots(snapshot, schedule):
            raise ValueError('Ineligible browser request')
        if self.allowed_slots is not None and request.slot_id not in self.allowed_slots:
            raise ValueError('Venue excluded by group preferences')
        slot = next(s for s in snapshot.slots if s.id == request.slot_id)
        count = len(next(m.available_player_ids for m in schedule.matches if m.slot_id == slot.id))
        return slot, count

    def get_court_options(self, request, snapshot, schedule, *, live_page=None, provider_players=1):
        slot, count = self._scope(request, snapshot, schedule)
        if live_page is not None:
            from .playbypoint_live import read_summary
            return read_summary(live_page, self.venue_id, self.revision, request, slot, count, provider_players=provider_players)
        if self.live_selection:
            from .playbypoint_selection import browse_selection
            return browse_selection(self, request, slot, count)
        if self.live_observations is not None:
            observed = next((o for o in self.live_observations if o.slot_id == slot.id), None)
            if (observed is None or observed.simulated or not observed.fresh()
                    or observed.revision != self.revision or observed.venue_id != self.venue_id
                    or observed.player_count != count
                    or any(o.starts_at != slot.starts_at or o.ends_at != slot.ends_at for o in observed.options)):
                raise ValueError('Live observation no longer matches this game')
            return observed.model_copy(update={'snapshot_id': request.snapshot_id})
        return self._browse(request, snapshot, schedule)[0]

    def prepare_checkout(self, request, snapshot, schedule):
        approval = self.approval
        if approval and not approval.observation.simulated:
            raise ValueError('Live checkout navigation requires separate supervision')
        if (not approval or approval.expires_at <= datetime.now(timezone.utc)
                or not approval.observation.fresh() or approval.observation.revision != self.revision
                or request.observation_id != approval.observation.observation_id
                or request.slot_id != approval.observation.slot_id or request.court_id != approval.court_id):
            raise ValueError('Missing, stale or mismatched checkout approval')
        observed, reviewed = self._browse(request, snapshot, schedule, approval)
        return CheckoutObservation(snapshot_id=request.snapshot_id, slot_id=request.slot_id,
            observation_id=request.observation_id, court_id=request.court_id,
            status='handoff' if reviewed else 'changed' if observed.status == 'results' else 'unknown',
            source_url=VENUES[self.venue_id].url,
            instructions=('SIMULATED checkout reviewed. Session is not transferable. Open the official booking page and select the approved court/time again; nothing is prefilled or booked.'
                          if reviewed else 'Checkout stopped: details changed or could not be verified. Ask the organiser to review again.'))

    def _browse(self, request, snapshot, schedule, approval=None):
        from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout, Error as BrowserError
        slot, count = self._scope(request, snapshot, schedule)
        cfg = VENUES[self.venue_id]
        html = self.fixture_pages.get(slot.id)
        options, status, reviewed = (), 'unknown', False
        if html is None:
            return self._observation(request, count, status, options), reviewed
        # A fresh ephemeral context each time. Every request is intercepted; no live flag.
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=self.executable_path)
            try:
                context = browser.new_context(service_workers='block')
                blocked = []
                def route(r):
                    if r.request.method == 'GET' and r.request.url == cfg.url:
                        r.fulfill(content_type='text/html; charset=utf-8', body=html)
                    else:
                        blocked.append(True)
                        r.abort()
                context.route('**/*', route)
                page = context.new_page()
                page.set_default_timeout(self.timeout_ms)
                page.goto(cfg.url, timeout=self.timeout_ms)
                body = page.locator('body').inner_text()
                if re.search(r'captcha|cloudflare|security verification|verify you are human', body, re.I):
                    status = 'challenge'
                elif page.get_by_label('Password', exact=True).count() or page.get_by_role('heading', name='Sign in', exact=True).count():
                    status = 'auth_required'
                else:
                    page.get_by_label('Court category', exact=True).select_option(label=cfg.category)
                    local = slot.starts_at.astimezone(ZoneInfo('Australia/Melbourne'))
                    page.get_by_label('Date', exact=True).fill(local.strftime('%Y-%m-%d'))
                    page.get_by_label('Start time', exact=True).fill(local.strftime('%H:%M'))
                    page.get_by_label('Duration', exact=True).select_option(str(snapshot.duration_minutes))
                    player_field = page.get_by_label('Players', exact=True)
                    if cfg.group_players:
                        player_field.fill('1')
                    provider_players = int(player_field.input_value())
                    if provider_players != 1:
                        raise ValueError('Player-count mismatch')
                    found = []
                    for row in page.get_by_role('group', name='Court option', exact=True).all():
                        def value(label):
                            el = row.get_by_label(label, exact=True)
                            return el.inner_text().strip() if el.count() == 1 else ''
                        if value('Category') != cfg.category:
                            continue
                        start, end = datetime.fromisoformat(value('Starts at')), datetime.fromisoformat(value('Ends at'))
                        if start != slot.starts_at or end != slot.ends_at or value('Availability') != 'Available':
                            continue
                        found.append(CourtOption(court_id=value('Court ID'), court_name=value('Court name'),
                            starts_at=start, ends_at=end, provider_players=provider_players,
                            total_minor=money(value('Total')), fees_minor=money(value('Fees')),
                            tax_minor=money(value('Tax')),
                            per_player_minor=money(value('Per player')), conditions=value('Pricing conditions')))
                    options = tuple(found)
                    status = 'results' if options else 'empty' if page.get_by_text('No courts available for this selection', exact=True).count() else 'unknown'
                    if approval:
                        old = next((o for o in approval.observation.options if o.court_id == approval.court_id), None)
                        current = next((o for o in options if o.court_id == approval.court_id), None)
                        if (old and current == old and count == approval.observation.player_count
                                and approval.observation.fresh() and approval.expires_at > datetime.now(timezone.utc)
                                and current.total_minor is not None and current.fees_minor is not None):
                            row = page.get_by_role('group', name='Court option', exact=True).filter(has=page.get_by_label('Court ID', exact=True).filter(has_text=re.compile('^'+re.escape(current.court_id)+'$')))
                            row.get_by_role('button', name='Review checkout', exact=True).click()
                            page.get_by_role('heading', name='Checkout review — not reserved', exact=True).wait_for()
                            reviewed = True
                if blocked or urlsplit(page.url).netloc != urlsplit(cfg.url).netloc:
                    options, status, reviewed = (), 'unknown', False
            except BrowserTimeout:
                options, status, reviewed = (), 'timeout', False
            except (BrowserError, ValueError, TypeError):
                options, status, reviewed = (), 'unknown', False
            finally:
                browser.close()
        return self._observation(request, count, status, options), reviewed

    def _observation(self, request, count, status, options):
        return CourtObservation(observation_id=secrets.token_hex(16), snapshot_id=request.snapshot_id,
            slot_id=request.slot_id, venue_id=self.venue_id, revision=self.revision, player_count=count,
            observed_at=datetime.now(timezone.utc), source_url=VENUES[self.venue_id].url,
            status=status, options=options)
