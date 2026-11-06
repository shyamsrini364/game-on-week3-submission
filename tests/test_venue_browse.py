"""General exploration is public and does not bypass game booking eligibility."""
import json
import unittest
from unittest.mock import patch
import test_groups
from gameon.venues.browse import browse_venues
from gameon.venues.directory import CuratedDirectory


class VenueBrowseTests(unittest.TestCase):
    setUp, tearDown = test_groups.GroupTests.setUp, test_groups.GroupTests.tearDown
    call, group, join, game = test_groups.GroupTests.call, test_groups.GroupTests.group, test_groups.GroupTests.join, test_groups.GroupTests.game

    def test_public_empty_and_unknown_suburb_keep_full_catalogue(self):
        for query, expected in [('', 'needs_suburb'), ('Not a covered suburb', 'unknown_suburb')]:
            r=self.client.get('/api/venues/browse',params={'suburb':query})
            self.assertEqual(r.status_code,200)
            data=r.json();self.assertEqual(data['status'],expected)
            self.assertEqual(len(data['venues']),len(CuratedDirectory().venues))
            self.assertTrue(all(v['approximate_distance_km'] is None for v in data['venues']))
            self.assertFalse(data['can_prepare_handoff']);self.assertEqual(data['mode'],'general_browse')
            self.assertNotIn('token',json.dumps(data));self.assertTrue(data['message'])

    def test_verified_suburbs_sorted_distances_and_sources(self):
        for query in ['Clyde','  lyNDhurst  ']:
            data=self.client.get('/api/venues/browse',params={'suburb':query}).json()
            self.assertEqual(data['status'],'located')
            distances=[v['approximate_distance_km'] for v in data['venues']]
            self.assertEqual(distances,sorted(distances));self.assertGreater(distances[0],0)
            self.assertTrue(data['location_source_url'].startswith('https://en.wikipedia.org/'))
            for entry in data['venues']:self.assertTrue(entry['venue']['sources'])
        suburbs=self.call('/api/venues')['suburbs']
        self.assertIn('Clyde',suburbs);self.assertIn('Lyndhurst',suburbs)

    def test_browse_does_not_require_game_or_write_preferences(self):
        group=self.group();game=self.game(group)
        before=self.call('/api/groups/'+group['group_id'],token=group['member_token'])
        data=self.call('/api/venues/browse?suburb=Clyde');self.assertTrue(data['venues'])
        rec=self.call('/api/games/'+game+'/recommendations',token=group['member_token'])
        self.assertNotEqual(rec['status'],'recommended')
        self.call('/api/games/'+game+'/booking-handoff',{'revision':'fake','slot_id':'fake','venue_id':data['venues'][0]['venue']['id']},status=403)
        self.assertEqual(before,self.call('/api/groups/'+group['group_id'],token=group['member_token']))

    def test_new_suburbs_work_in_existing_qualified_group_ranking(self):
        group=self.group();members=[group,self.join(group,'Clyde'),self.join(group,'Lyndhurst'),self.join(group,'Clayton')]
        game=self.game(group);slot=self.call('/api/games/'+game,token=group['member_token'])['slots'][0]['id']
        for member in members:
            player=self.call('/api/games/'+game+'/players',{},token=member['member_token'],status=201)
            self.call('/api/games/'+game+'/votes',{**player,'slot_ids':[slot]},token=member['member_token'])
        r=self.call('/api/games/'+game+'/recommendations',token=group['member_token'])
        self.assertEqual(r['status'],'recommended');self.assertTrue(r['recommendations'])

    def test_bounded_query_and_empty_directory(self):
        self.assertEqual(self.client.get('/api/venues/browse',params={'suburb':'a'*101}).status_code,400)
        result=browse_venues('Clyde',CuratedDirectory(venues=[],points={}))
        self.assertEqual(result['venues'],[]);self.assertEqual(result['status'],'unknown_suburb')
