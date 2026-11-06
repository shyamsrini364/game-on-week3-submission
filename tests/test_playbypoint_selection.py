"""Offline DOM tests of live selectors; these are NOT provider availability evidence."""
from datetime import datetime, timedelta
from time import monotonic
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import unittest

from gameon.erne.playbypoint import PlaybypointSkill, VENUES
from gameon.erne.playbypoint_selection import FRAME, TITLES, read_selection, segment
from test_playbypoint import CHROME


class SelectionTests(unittest.TestCase):
    def test_time_labels(self):
        self.assertEqual(segment('6:30-7 PM'), (1110,1140))
        self.assertEqual(segment('11:30-12 PM'), (690,720))
        self.assertIsNone(segment('Next'))

    def test_observed_controls_with_offline_network(self):
        from playwright.sync_api import sync_playwright
        start=(datetime.now(ZoneInfo('Australia/Melbourne'))+timedelta(days=1)).replace(hour=18,minute=30,second=0,microsecond=0)
        slot=SimpleNamespace(starts_at=start,ends_at=start+timedelta(hours=1))
        request=SimpleNamespace(snapshot_id='offline-test',slot_id='approved-slot')
        date=start.strftime('%a').upper()+f' {start.day} '+start.strftime('%b').upper()
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,executable_path=CHROME)
            try:
                for venue in VENUES:
                    for state in ('available','disabled','missing','wrong-duration','challenge'):
                        with self.subTest(venue=venue,state=state):
                            context=browser.new_context(service_workers='block')
                            # Delayed render catches premature reads immediately after clicks.
                            html=f'''<body><h1>{TITLES[venue]}</h1><button>Sign In</button>
                            <button aria-pressed="false" onclick="setTimeout(()=>this.setAttribute('aria-pressed','true'),50)">{date}</button>
                            <button aria-pressed="false" onclick="this.setAttribute('aria-pressed','true')">Main Courts</button>
                            <label for="payer">Number of players</label><select id="payer"><option>4</option><option>1</option></select>
                            <p>Reservation time</p>
                            <button aria-pressed="false" onclick="setTimeout(()=>{{this.setAttribute('aria-pressed','true');document.querySelector('#second').setAttribute('aria-pressed','true')}},50)">6:30-7 PM</button>
                            <button id="second" aria-pressed="false">7-7:30 PM</button>
                            <button {'disabled' if state=='disabled' else ''}>Court 3</button>
                            <button onclick="document.body.dataset.forbidden='yes'">Next</button></body>'''
                            if state=='missing':html=html.replace('6:30-7 PM','5:30-6 PM')
                            def route(r):
                                if r.request.url.startswith(FRAME):r.fulfill(body=html,content_type='text/html')
                                else:r.fulfill(status=403 if state=='challenge' else 200,body=f'<iframe src="{FRAME}"></iframe>',content_type='text/html')
                            context.route('**/*',route)
                            page=context.new_page()
                            adapter=PlaybypointSkill(venue,'revision',{},CHROME,live_selection=True)
                            target=SimpleNamespace(starts_at=start,ends_at=start+timedelta(minutes=90)) if state=='wrong-duration' else slot
                            obs=read_selection(page,adapter,request,target,4,deadline=monotonic()+5)
                            expected={'available':'results','disabled':'empty','missing':'unknown','wrong-duration':'unknown','challenge':'challenge'}[state]
                            self.assertEqual(obs.status,expected)
                            if obs.options:
                                court=obs.options[0]
                                self.assertEqual(court.provider_players,1)
                                self.assertTrue(court.provider_players_verified)
                                self.assertEqual(obs.player_count,4)
                                self.assertEqual(court.starts_at,start)
                                self.assertIsNone(court.total_minor)
                                self.assertTrue(obs.fresh())
                            for frame in page.frames:
                                self.assertNotEqual(frame.locator('body').get_attribute('data-forbidden'),'yes')
                            context.close()
            finally:browser.close()


if __name__=='__main__':unittest.main()
