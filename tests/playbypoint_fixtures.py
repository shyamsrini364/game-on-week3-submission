"""Invented accessible pages, not scraped/reproduced provider markup."""
from html import escape


def booking_page(slot, *, category='Main courts', total='AUD 60.00', fees='AUD 0.00',
                 per_player='AUD 15.00', empty=False, wrong_time=False, wrong_category=False,
                 challenge=False, auth=False, missing=False, malicious=False):
    if challenge:
        return '<h1>Cloudflare security verification</h1>'
    if auth:
        return '<h1>Sign in</h1><label>Password<input type="password"></label>'
    if missing:
        return '<p>SIMULATED page still loading</p>'
    controls = '''<!doctype html><html><body><h1>SIMULATED booking fixture — no real availability</h1>
      <label>Court category<select aria-label="Court category"><option>Main courts</option><option>Individual courts</option><option>Training court</option></select></label>
      <label>Date<input type="date"></label><label>Start time<input type="time"></label>
      <label>Duration<select aria-label="Duration"><option>60</option><option>90</option><option>120</option></select></label>
      <label>Players<input type="number" value="1"></label>'''
    if empty:
        return controls+'<p>No courts available for this selection</p></body></html>'
    values = {'Court ID':'synthetic-court-1', 'Court name':'Synthetic Court 1',
              'Category':'Training court' if wrong_category else category,
              'Starts at': '2099-01-01T10:00:00+11:00' if wrong_time else slot.starts_at.isoformat(),
              'Ends at': slot.ends_at.isoformat(), 'Availability':'Available',
              'Total':total, 'Fees':fees, 'Tax':'AUD$0.00', 'Per player':per_player,
              'Pricing conditions':'Synthetic total only. Level Up player selector is not attendance capacity.'}
    fields = ''.join(f'<span aria-label="{k}">{escape(v)}</span>' for k,v in values.items())
    action = "location.href='https://unapproved.invalid/'" if malicious else "document.getElementById('review').hidden=false"
    return (controls+'<section role="group" aria-label="Court option">'+fields+
            f'<button type="button" onclick="{action}">Review checkout</button></section>'+
            '<h2 id="review" hidden>Checkout review — not reserved</h2>'+
            '<button onclick="throw Error(\'Forbidden reservation\')">Reserve and pay</button></body></html>')
