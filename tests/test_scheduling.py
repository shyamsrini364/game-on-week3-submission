import unittest
from gameon.demo import create_example_game
from gameon.models import Player
from gameon.scheduling import find_common_slots


class SchedulingTests(unittest.TestCase):
    def test_four_players_agree(self):
        result = find_common_slots(create_example_game())
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.matches[0].slot.id, "sat-10")
        self.assertEqual(result.matches[0].available_count, 4)

    def test_pending_player_is_not_counted_as_no(self):
        game = create_example_game()
        del game.votes["p4"]
        result = find_common_slots(game)
        self.assertEqual(result.status, "waiting_for_responses")
        self.assertEqual(result.pending_player_ids, ("p4",))
        self.assertEqual(result.matches[0].available_count, 3)

    def test_no_common_time(self):
        game = create_example_game()
        game.submit_availability("p4", set())
        result = find_common_slots(game)
        self.assertEqual(result.status, "no_common_time")

    def test_player_can_change_vote(self):
        game = create_example_game()
        game.submit_availability("p4", {"sun-9"})
        result = find_common_slots(game)
        self.assertEqual(result.status, "no_common_time")

    def test_unknown_slot_is_rejected(self):
        game = create_example_game()
        with self.assertRaises(ValueError):
            game.submit_availability("p1", {"friday-7"})

    def test_unregistered_player_is_rejected(self):
        game = create_example_game()
        with self.assertRaises(ValueError):
            game.submit_availability("stranger", {"sat-10"})


if __name__ == "__main__":
    unittest.main()

class AdditionalSchedulingTests(unittest.TestCase):
    def test_equal_counts_rank_earlier_slot_first(self):
        game = create_example_game()
        for player_id in game.players:
            game.submit_availability(player_id, {'sat-10', 'sun-9'})
        game.slots = tuple(reversed(game.slots))
        result = find_common_slots(game)
        self.assertEqual(result.status, 'ready')
        self.assertEqual([match.slot.id for match in result.matches], ['sat-10', 'sun-9'])

    def test_not_enough_players_is_waiting_not_failure(self):
        game = create_example_game()
        for player_id in ('p3', 'p4'):
            del game.players[player_id]
            game.votes.pop(player_id, None)
        result = find_common_slots(game)
        self.assertEqual(result.status, 'waiting_for_responses')
        self.assertIn('2 more player', result.explanation)

    def test_explicit_empty_vote_is_not_pending(self):
        game = create_example_game()
        game.submit_availability('p1', set())
        result = find_common_slots(game)
        self.assertEqual(result.status, 'no_common_time')
        self.assertEqual(result.pending_player_ids, ())
