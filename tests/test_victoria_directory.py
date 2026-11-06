"""Canonical dataset integrity and expanded-directory compatibility, no external calls."""
import json
import unittest
from pathlib import Path
from pydantic import ValidationError
from gameon.venues.contracts import Venue, Point, VenueSearchResult
from gameon.venues.directory import CuratedDirectory, deduplicate
from gameon.venues.browse import browse_venues


class VictoriaDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = CuratedDirectory()

    def test_original_saved_identities_and_reference_coordinates_survive(self):
        expected = {'pickleholic-clayton': (-37.915,145.13), 'level-up-knox': (-37.881,145.224),
                    'picklezone-springvale': (-37.947,145.153), 'the-jar-maidstone': (-37.783,144.878),
                    'palace-pascoe-vale': (-37.727,144.942),
                    'the-rally-altona': (-37.868,144.83), 'state-pickleball-bayswater': (-37.843,145.268)}
        by_id={v.id:v for v in self.directory.venues}
        for id,point in expected.items():
            self.assertIn(id,by_id)
            self.assertEqual((by_id[id].location.latitude,by_id[id].location.longitude),point)

    def test_each_published_record_has_reviewed_provenance(self):
        audit=json.loads(Path('tests/fixtures/venue-provenance.json').read_text(encoding='utf8'))
        evidence={v['venue_id']:v for v in audit['records']}
        self.assertEqual(set(evidence), {v.id for v in self.directory.venues})
        self.assertEqual(len(evidence),29)
        for v in self.directory.venues:
            self.assertEqual(v.identity_status,'source_verified')
            self.assertTrue(v.pickleball)
            self.assertEqual(v.state,'VIC')
            self.assertRegex(v.postcode,r'^3\d{3}$')
            self.assertEqual(set(v.sources),set(evidence[v.id]['source_urls']))
            self.assertEqual(str(v.checked_on),evidence[v.id]['checked_on'])
            p=v.map_location or v.location
            self.assertEqual(p.accuracy,evidence[v.id]['coordinate_precision'])
            self.assertEqual(p.source_url,evidence[v.id]['coordinate_source'])

    def test_duplicate_formatting_is_not_a_second_venue(self):
        original=self.directory.venues[0]
        self.assertEqual(len(deduplicate([original,original])),1)
        duplicate=original.model_copy(update={'id':'different-id', 'address': original.address.replace('Street','St.').replace(',', '  ')})
        with self.assertRaisesRegex(ValueError,'address'): deduplicate([original,duplicate])
        with self.assertRaisesRegex(ValueError,'ID'): deduplicate([original, original.model_copy(update={'name':'Changed'})])

    def test_invalid_coordinates_status_and_links_rejected(self):
        original=self.directory.venues[0].model_dump(mode='json')
        for changes in [{'identity_status':'unverified'},{'state':'NSW'},{'postcode':'2000'},
                        {'enquiry_url':'javascript:alert(1)'}, {'location':{**original['location'],'latitude':-91.0}},
                        {'location':{**original['location'],'longitude':float('nan')}}]:
            with self.subTest(changes=changes),self.assertRaises(ValidationError):
                Venue.model_validate_json(json.dumps({**original,**changes}))

    def test_regional_suburb_search_and_coordinate_range(self):
        for suburb in ['Wangaratta','West Bendigo','Eaglehawk','Wendouree','Norlane']:
            result=browse_venues(suburb,self.directory)
            self.assertEqual(result['status'],'located')
            self.assertEqual(result['venues'][0]['venue']['suburb'],suburb)
            self.assertEqual(result['search_location']['accuracy'],'suburb_reference_point')
        self.assertIsNone(browse_venues('Unverified suburb',self.directory)['search_location'])

    def test_more_than_twenty_results_remain_valid_for_erne(self):
        result=VenueSearchResult(snapshot_id='synthetic',slot_id='synthetic',scope='expanded',status='results',venues=self.directory.venues)
        self.assertEqual(len(VenueSearchResult.model_validate_json(result.model_dump_json()).venues),29)

    def test_discovery_does_not_invent_booking_capability(self):
        venues={v.id:v for v in self.directory.venues}
        for id in ['carlson-reserve-clayton','patterson-lakes-community','bayside-hampton']:
            self.assertIsNone(venues[id].booking_url)
            self.assertTrue(venues[id].enquiry_url.startswith('https://'))
        self.assertEqual(venues['pickleplay-bentleigh-east'].booking_provider,'SportLogic')
