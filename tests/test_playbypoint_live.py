"""Recorded real summary parsing plus scope/provenance tests; no network."""
import json
from pathlib import Path
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
import unittest
from unittest.mock import patch
from gameon.erne.contracts import Slot
from gameon.erne.playbypoint_live import parse_summary
from gameon.erne.playbypoint_contracts import CourtObservation
from gameon.erne.playbypoint import PlaybypointSkill
from gameon.erne.contracts import DiscoveryInput

ROOT=Path(__file__).resolve().parents[1]/'gameon/erne/data/recorded-pricing'
class LiveSummaryTests(unittest.TestCase):
 def data(self,name): return json.loads((ROOT/(name+'.json')).read_text())['summary_text']
 def slot(self,day=7):
  return Slot(id='time',starts_at=datetime(2026,11,day,18,30,tzinfo=ZoneInfo('Australia/Melbourne')),ends_at=datetime(2026,11,day,19,30,tzinfo=ZoneInfo('Australia/Melbourne')))
 def test_observed_pickleholic_player_count_and_fee_rounding(self):
  one=parse_summary(self.data('pickleholic-one-player'),'pickleholic-clayton',self.slot(),1)
  four=parse_summary(self.data('pickleholic'),'pickleholic-clayton',self.slot(),4)
  self.assertEqual((one.base_minor,one.fees_minor,one.total_minor),(5000,125,5125))
  self.assertEqual((four.base_minor,four.fees_minor,four.total_minor,four.per_player_minor),(5000,124,5124,1250))
  self.assertIsNone(four.tax_minor);self.assertIsNone(four.currency)
 def test_observed_level_up_tax_unknown_fee_not_capacity(self):
  quote=parse_summary(self.data('level-up'),'level-up-knox',self.slot(8),4)
  self.assertEqual((quote.base_minor,quote.tax_minor,quote.total_minor,quote.provider_players),(4545,455,5000,1))
  self.assertEqual(quote.currency,'AUD');self.assertIsNone(quote.fees_minor)
 def test_wrong_venue_date_time_count_court_fail_closed(self):
  text=self.data('pickleholic')
  for bad in [text.replace('Court 3','Court 4'),text.replace('Saturday, Nov 07','Sunday, Nov 08'),text.replace('6:30 - 7:30 PM','7:30 - 8:30 PM'),text.replace('4 players','3 players'),text.replace('Booking at PICKLEHOLIC','Booking at Other')]:
   with self.assertRaises(ValueError):parse_summary(bad,'pickleholic-clayton',self.slot(),4)
 def test_missing_malformed_duplicate_or_inconsistent_amount_fails(self):
  text=self.data('pickleholic')
  for bad in [text.replace('Total\n$51.24',''),text.replace('$51.24','free'),text.replace('Total\n$51.24','Total\n$51.24\nTotal\n$51.24'),text.replace('$51.24','$51.25'),text.replace('This club requires the booker to pay for every player.','')]:
   with self.assertRaises(ValueError):parse_summary(bad,'pickleholic-clayton',self.slot(),4)
 def test_live_and_fixture_provenance_cannot_be_mixed(self):
  fields=dict(observation_id='obs',snapshot_id='snap',slot_id='time',venue_id='pickleholic-clayton',revision='rev',player_count=4,observed_at=datetime.now(timezone.utc),source_url='https://book.pickleholic.com.au/book',status='empty')
  for simulated,source in [(True,'live_browser_summary'),(False,'offline_browser_fixture')]:
   with self.assertRaises(ValueError):CourtObservation(**fields,simulated=simulated,source=source)

 def test_live_quote_rebinding_expiry_revision_and_time(self):
  # Synthetic boundary test, not provider evidence. _scope has its own fixture/service tests.
  original=CourtObservation.model_validate_json((ROOT/'level-up-live-tool.json').read_text())
  start=datetime.now(timezone.utc)+timedelta(days=1)
  slot=Slot(id=original.slot_id,starts_at=start,ends_at=start+timedelta(hours=1))
  option=original.options[0].model_copy(update={'starts_at':slot.starts_at,'ends_at':slot.ends_at})
  obs=original.model_copy(update={'observed_at':datetime.now(timezone.utc),'options':(option,)})
  skill=PlaybypointSkill(obs.venue_id,obs.revision,{},'',live_observations=(obs,))
  request=DiscoveryInput(snapshot_id='new-snapshot',slot_id=slot.id)
  with patch.object(skill,'_scope',return_value=(slot,obs.player_count)):
   self.assertEqual(skill.get_court_options(request,None,None).snapshot_id,'new-snapshot')
   invalid=[obs.model_copy(update={'observed_at':datetime.now(timezone.utc)-timedelta(seconds=121)}),
            obs.model_copy(update={'revision':'changed'}),obs.model_copy(update={'player_count':999}),
            obs.model_copy(update={'options':original.options})]
   for bad in invalid:
    skill.live_observations=(bad,)
    with self.assertRaises(ValueError):skill.get_court_options(request,None,None)

if __name__=='__main__':unittest.main()
