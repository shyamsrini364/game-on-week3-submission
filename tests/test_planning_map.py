"""Read-only map contracts preserve membership checks and scheduling semantics."""
import unittest
import test_recommendations
from gameon.venues.directory import CuratedDirectory


class PlanningMapTests(unittest.TestCase):
    _base = test_recommendations.RecommendationTests
    setUp, tearDown = _base.setUp, _base.tearDown
    call, group, join, game, ready = _base.call, _base.group, _base.join, _base.game, _base.ready

    def test_public_reference_points_are_directory_points_not_player_locations(self):
        data = self.call('/api/venues')['suburb_locations']
        directory = CuratedDirectory()
        self.assertEqual(data, {name: point.model_dump(mode='json') for name, point in directory.points.items()})
        self.assertNotIn('unknown suburb', data)
        self.assertTrue(all(p['accuracy'] == 'suburb_reference_point' for p in data.values()))

    def test_available_ids_match_votes_and_stay_group_private(self):
        owner, members, game = self.ready()
        path = '/api/games/'+game
        self.call(path, status=403)
        state = self.call(path, token=owner['member_token'])
        first, second = state['ranking']
        self.assertEqual(set(first['available_player_ids']), {p['id'] for p in state['players']})
        self.assertEqual(len(first['available_player_ids']), first['available_count'])
        self.assertEqual(second['available_player_ids'], [])
