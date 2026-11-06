"""Local visible, anonymous selection-page reads. Never clicks Next or a court.

Uses observed accessible controls, not provider APIs. Missing evidence is unknown.
Only the verified one-hour interaction is supported; other durations fail closed.
"""
from datetime import datetime, timezone, timedelta
from time import monotonic
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
import re
import secrets

from .playbypoint_contracts import CourtObservation, CourtOption

FRAME = 'https://greenfield.playbypoint.com/booking/select'
TITLES = {'pickleholic-clayton': 'Welcome to PICKLEHOLIC.',
          'level-up-knox': 'Welcome to Level Up Pickleball Knox City.'}

def segment(text):
    match = re.fullmatch(r'(\d{1,2})(?::(\d{2}))?-(\d{1,2})(?::(\d{2}))? (AM|PM)', text)
    if not match:
        return None
    h,m,eh,em,period=match.groups()
    end=(int(eh)%12 + (12 if period=='PM' else 0))*60+int(em or 0)
    start=(int(h)%12 + (12 if period=='PM' else 0))*60+int(m or 0)
    if start>=end: start-=12*60
    return start,end

def observation(adapter, request, count, status, options=()):
    from .playbypoint import VENUES
    return CourtObservation(observation_id=secrets.token_hex(16),snapshot_id=request.snapshot_id,
        slot_id=request.slot_id,venue_id=adapter.venue_id,revision=adapter.revision,
        player_count=count,observed_at=datetime.now(timezone.utc),source_url=VENUES[adapter.venue_id].url,
        source='live_browser_selection',simulated=False,status=status,options=tuple(options))

def read_selection(page, adapter, request, slot, count, *, deadline):
    """Navigate only public date/category/start controls within an absolute budget."""
    from .playbypoint import VENUES
    from playwright.sync_api import TimeoutError as BrowserTimeout, expect
    cfg=VENUES[adapter.venue_id]
    local=slot.starts_at.astimezone(ZoneInfo('Australia/Melbourne'))
    today=datetime.now(ZoneInfo('Australia/Melbourne')).date()
    if (slot.starts_at<=datetime.now(timezone.utc) or slot.ends_at-slot.starts_at!=timedelta(hours=1)
            or not 0 <= (local.date()-today).days < 14 or local.minute not in (0,30)):
        return observation(adapter,request,count,'unknown')
    def budget():
        remaining=int((deadline-monotonic())*1000)
        if remaining<=0: raise BrowserTimeout('Read deadline')
        page.set_default_timeout(min(remaining,1500))
        return remaining
    response=page.goto(cfg.url,wait_until='domcontentloaded',timeout=budget())
    if response and response.status in (401,403,429):
        return observation(adapter,request,count,'challenge')
    if urlsplit(page.url).hostname!=urlsplit(cfg.url).hostname:
        return observation(adapter,request,count,'unknown')
    frame=None
    while frame is None:
        budget()
        frame=next((f for f in page.frames if f.url.split('?')[0]==FRAME),None)
        if frame is None:page.wait_for_timeout(min(50,budget()))
    frame.get_by_text(TITLES[adapter.venue_id],exact=True).wait_for(timeout=budget())
    body=frame.locator('body').inner_text(timeout=budget())
    if re.search(r'captcha|verify you are human|security challenge',body,re.I):
        return observation(adapter,request,count,'challenge')
    if not frame.get_by_role('button',name='Sign In',exact=True).is_visible():
        return observation(adapter,request,count,'auth_required')
    date_name=local.strftime('%a').upper()+f' {local.day} '+local.strftime('%b').upper()
    date=frame.get_by_role('button',name=date_name,exact=True)
    date.click(timeout=budget())
    expect(date).to_have_attribute('aria-pressed','true',timeout=budget())
    if adapter.venue_id=='pickleholic-clayton':
        category=frame.get_by_role('button',name='Main Courts',exact=True)
        category.click(timeout=budget())
        expect(category).to_have_attribute('aria-pressed','true',timeout=budget())
    # The selection page normally has no payer selector; if present, enforce one.
    payer=frame.get_by_label('Number of players',exact=True)
    payer_verified=False
    if payer.count():
        payer.select_option('1',timeout=budget())
        if payer.input_value()!='1':return observation(adapter,request,count,'unknown')
        payer_verified=True
    start_minute=local.hour*60+local.minute
    frame.get_by_text('Reservation time',exact=True).wait_for(timeout=budget())
    buttons=frame.get_by_role('button')
    first=None
    # The heading renders before the asynchronously loaded time buttons.
    time_deadline=min(deadline,monotonic()+3)
    while first is None and monotonic()<time_deadline:
        budget()
        times=[(b.inner_text().strip(),b) for b in buttons.all() if segment(b.inner_text().strip())]
        first=next((b for text,b in times if segment(text)[0]==start_minute),None)
        if first is None:page.wait_for_timeout(min(50,budget()))
    if first is None:return observation(adapter,request,count,'unknown')
    if first.is_disabled():return observation(adapter,request,count,'empty')
    first.click(timeout=budget())
    expect(first).to_have_attribute('aria-pressed','true',timeout=budget())
    second=next((b for text,b in times if segment(text)==(start_minute+30,start_minute+60)),None)
    if second is None:return observation(adapter,request,count,'unknown')
    expect(second).to_have_attribute('aria-pressed','true',timeout=budget())
    # Validate the complete selected interval, not just a clicked start label.
    chosen=[]
    for text,b in times:
        if b.get_attribute('aria-pressed')=='true':chosen.append(segment(text))
    if sorted(chosen)!=[(start_minute,start_minute+30),(start_minute+30,start_minute+60)]:
        return observation(adapter,request,count,'unknown')
    courts=frame.get_by_role('button',name=re.compile(r'^Court [1-9][0-9]?$'))
    while courts.count()==0:
        budget()
        if frame.get_by_text('No courts available for this selection',exact=True).count():
            return observation(adapter,request,count,'empty')
        page.wait_for_timeout(min(50,budget()))
    available=[b.inner_text().strip() for b in courts.all() if b.is_visible() and b.is_enabled()]
    options=[CourtOption(court_id='court-'+name.split()[-1],court_name=name,
        starts_at=slot.starts_at,ends_at=slot.ends_at,provider_players=1,
        provider_players_verified=payer_verified,currency=None,
        conditions='Offered on provider selection page at observation time; not held. Checkout price requires verification.') for name in available]
    return observation(adapter,request,count,'results' if options else 'empty',options)

def browse_selection(adapter, request, slot, count):
    from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout, Error as BrowserError
    deadline=monotonic()+16
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=False,executable_path=adapter.executable_path,timeout=4000)
            try:
                context=browser.new_context(service_workers='block')
                page=context.new_page()
                return read_selection(page,adapter,request,slot,count,deadline=deadline)
            finally:
                browser.close()
    except BrowserTimeout:
        return observation(adapter,request,count,'timeout')
    except (BrowserError,ValueError,TypeError,AssertionError):
        return observation(adapter,request,count,'unknown')
