"""Historical evidence, synthetic stores and mocked refresh; no provider calls."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from gameon.storage import GameStore
from gameon.pricing import cached_price, seed_i058, mark_refresh_failed, brief_lines, save_observation, BOOKING_URLS
from gameon.erne.playbypoint_contracts import CourtObservation
from gameon.erne.playbypoint import PlaybypointSkill


class PricingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = GameStore(Path(self.temp.name)/'prices.db')
        seed_i058(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def price(self, count=4, **kwargs):
        return cached_price(self.store, 'pickleholic-clayton', '2026-11-07T18:30:00+11:00',
                            '2026-11-07T19:30:00+11:00', count, **kwargs)

    def test_real_seed_totals_timestamps_split_and_idempotence(self):
        seed_i058(self.store)
        for count, split in [(2,2563),(3,1708),(4,1281),(5,1025)]:
            quote = self.price(count)
            self.assertEqual((quote['total_minor'],quote['fees_minor'],quote['estimated_per_player_minor']), (5125,125,split))
            self.assertEqual(quote['provider_players'],1)
            self.assertEqual(quote['observed_at'],'2026-11-05T07:01:08.157266+00:00')
            self.assertIsNone(quote['currency']); self.assertIsNone(quote['tax_minor'])
        with self.store._connect() as c:
            self.assertEqual(c.execute('SELECT count(*) AS n FROM court_prices').fetchone()['n'],3)
        self.assertEqual(self.price(provider_players=4)['total_minor'],5124)
        self.assertIsNone(self.price(0)['estimated_per_player_minor'])
        level=cached_price(self.store,'level-up-knox','2026-11-08T18:30:00+11:00','2026-11-08T19:30:00+11:00',4)
        self.assertEqual((level['total_minor'],level['tax_minor'],level['currency']), (5000,455,'AUD'))
        self.assertIsNone(level['fees_minor'])

    def test_exact_scope_and_missing(self):
        for start,end in [('2026-11-14T18:30:00+11:00','2026-11-14T19:30:00+11:00'),
                          ('2026-11-07T19:30:00+11:00','2026-11-07T20:30:00+11:00'),
                          ('2026-11-07T18:30:00+11:00','2026-11-07T20:00:00+11:00')]:
            self.assertIsNone(cached_price(self.store,'pickleholic-clayton',start,end,4))
        self.assertIsNone(self.price(category='Other'));self.assertIsNone(self.price(court_name='Court 4'))

    def test_freshness_failed_refresh_and_config(self):
        observed=datetime.fromisoformat(self.price()['observed_at'])
        self.assertFalse(self.price(now=observed+timedelta(days=6))['refresh_due'])
        self.assertTrue(self.price(now=observed+timedelta(days=7))['refresh_due'])
        self.assertFalse(self.price(now=observed+timedelta(days=14))['stale'])
        self.assertTrue(self.price(now=observed+timedelta(days=14,seconds=1))['stale'])
        with patch.dict(os.environ,{'GAMEON_PRICE_REFRESH_DAYS':'3'}):
            self.assertTrue(self.price(now=observed+timedelta(days=4))['refresh_due'])
        before=self.price()
        mark_refresh_failed(self.store,'pickleholic-clayton',before['starts_at'],before['ends_at'])
        after=self.price()
        self.assertEqual(after['refresh_status'],'failed')
        self.assertEqual((after['observed_at'],after['total_minor']),(before['observed_at'],before['total_minor']))

    def test_brief_privacy_urls_and_fixture_rejection(self):
        text='\n'.join(brief_lines(self.price()))
        for required in ('51.25','12.81','2026-11-05','currency unverified','cached estimate'):
            self.assertIn(required,text)
        for private in ('token','cookie','group_id','member','snapshot','revision'):
            self.assertNotIn(private,text)
        self.assertEqual(BOOKING_URLS['level-up-knox'],'https://www.leveluppickleball.com.au/book')
        self.assertEqual(BOOKING_URLS['pickleholic-clayton'],'https://book.pickleholic.com.au/book/Pickleholic')
        obs=CourtObservation.model_validate_json(Path('gameon/erne/data/recorded-pricing/level-up-live-tool.json').read_text())
        with self.assertRaises(ValueError):save_observation(self.store,obs.model_copy(update={'simulated':True}))

    def test_live_reader_defaults_provider_one_separate_from_group(self):
        skill=PlaybypointSkill('pickleholic-clayton','rev',{},'')
        with patch.object(skill,'_scope',return_value=('slot',4)), patch('gameon.erne.playbypoint_live.read_summary') as reader:
            skill.get_court_options('request',None,None,live_page='approved-page')
            self.assertEqual(reader.call_args.args[-1],4)
            self.assertEqual(reader.call_args.kwargs['provider_players'],1)

    def test_manual_refresh_retains_real_timestamp_and_failure_status(self):
        from gameon.erne.playbypoint_service import LocalPlaybypointSession, PlanningConflict
        obs=CourtObservation.model_validate_json(Path('gameon/erne/data/recorded-pricing/level-up-live-tool.json').read_text())
        session=LocalPlaybypointSession(self.store,'test-secret','',{})
        adapter=MagicMock(); adapter._scope.return_value=(obs.options[0],2)
        adapter.get_court_options.return_value=obs
        with patch.object(session,'_capture',return_value=(None,None,'rev')), patch.object(session,'_adapter',return_value=adapter), patch('gameon.erne.tools.read_game') as read, patch('gameon.erne.tools.schedule_game'), patch.object(self.store,'allow_request',return_value=True):
            read.return_value.snapshot_id='snap'
            returned=session.refresh_price('game','owner','level-up-knox',live_page='approved',slot_id='slot')
            self.assertEqual(returned.observed_at,obs.observed_at)
            adapter.get_court_options.side_effect=ValueError('summary unavailable')
            with self.assertRaises(ValueError):session.refresh_price('game','owner','level-up-knox',live_page='approved',slot_id='slot')
            quote=cached_price(self.store,'level-up-knox',obs.options[0].starts_at,obs.options[0].ends_at,2)
            self.assertEqual(quote['refresh_status'],'failed')

    def test_refresh_denied_or_changed_game_cannot_save(self):
        from gameon.erne.playbypoint_service import LocalPlaybypointSession, PlanningConflict
        from gameon.groups import AccessDenied
        session=LocalPlaybypointSession(self.store,'test-secret','',{})
        with patch.object(session,'_capture',side_effect=AccessDenied()), patch('gameon.pricing.save_observation') as save:
            with self.assertRaises(AccessDenied):
                session.refresh_price('game','other','level-up-knox',live_page='page',slot_id='slot')
            save.assert_not_called()
        adapter=MagicMock(); adapter._scope.return_value=(MagicMock(),2)
        with patch.object(session,'_capture',side_effect=[(None,None,'rev'),(None,None,'changed')]), patch.object(session,'_adapter',return_value=adapter), patch('gameon.erne.tools.read_game') as read, patch('gameon.erne.tools.schedule_game'), patch.object(self.store,'allow_request',return_value=True), patch('gameon.pricing.save_observation') as save:
            read.return_value.snapshot_id='snap'
            with self.assertRaises(PlanningConflict):
                session.refresh_price('game','owner','level-up-knox',live_page='page',slot_id='slot')
            save.assert_not_called()


if __name__=='__main__':unittest.main()
