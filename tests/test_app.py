import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import app
from gameon.storage import GameStore


class GameOnTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = GameStore(Path(self.temp.name) / "test.sqlite3")
        self.application = app.create_app(self.store, rate_limit_secret="test-secret")
        self.client = TestClient(self.application)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def reopen_store(self):
        return GameStore(self.store.db_path)

    def request(self, path, data=None, expected=200):
        response = self.client.post(path, json=data) if data is not None else self.client.get(path)
        payload = response.json()
        self.assertEqual(response.status_code, expected, payload)
        return payload

    def make_game(self):
        start = (datetime.now(ZoneInfo("Australia/Melbourne")) + timedelta(days=5)).replace(hour=10, minute=0, second=0, microsecond=0)
        slots = [(start + timedelta(days=i)).strftime("%Y-%m-%dT%H:%M") for i in range(2)]
        result = self.request("/api/games", {"starts":slots}, 201)
        return result["game_id"], self.request("/api/games/" + result["game_id"])

    def test_create_and_get_game(self):
        game_id, result = self.make_game()
        self.assertTrue(game_id)
        self.assertEqual(result["status"], "waiting_for_responses")
        self.assertEqual(len(result["slots"]), 2)
        self.assertEqual(result["sport"], "pickleball")
        self.assertEqual(result["min_players"], 4)

    def test_automatic_title_uses_earliest_melbourne_date(self):
        # Freeze the clock so this readable example stays valid in future years.
        with patch('gameon.storage.datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 11, 2, tzinfo=ZoneInfo('Australia/Melbourne'))
            payload = {"starts": ["2026-11-08T09:00", "2026-11-07T00:30"]}
            first = self.request('/api/games', payload, 201)
            second = self.request('/api/games', payload, 201)
        game = self.request('/api/games/' + first['game_id'])
        self.assertEqual(game['title'], 'Pickleball · Sat 7 Nov')
        self.assertEqual(game['min_players'], 4)
        self.assertNotEqual(first['game_id'], second['game_id'])
        self.assertEqual(game['slots'][0]['starts_at'], '2026-11-07T00:30:00+11:00')
        self.assertEqual(game['slots'][0]['ends_at'], '2026-11-07T02:00:00+11:00')

    def test_blank_titles_generate_names_but_invalid_explicit_titles_are_rejected(self):
        _, game = self.make_game()
        starts = [slot['starts_at'][:16] for slot in game['slots']]
        for title in ('', '   '):
            with self.subTest(title=title):
                created = self.request('/api/games', {'title': title, 'starts': starts}, 201)
                stored = self.request('/api/games/' + created['game_id'])
                self.assertEqual(stored['title'], game['title'])
        for title in ('x', 'x' * 76):
            with self.subTest(title=title):
                self.request('/api/games', {'title': title, 'starts': starts}, 400)

    def test_six_players_can_join_vote_and_change_availability(self):
        game_id, game = self.make_game()
        slots = [slot['id'] for slot in game['slots']]
        users = [self.request(f'/api/games/{game_id}/players',
                             {'name': name, 'suburb': 'Clayton'}, 201)
                 for name in ('Alex', 'Jess', 'Priya', 'Sam', 'Taylor', 'Morgan')]
        for user in users[:4]:
            self.request(f'/api/games/{game_id}/votes', {**user, 'slot_ids': slots})
        waiting = self.request(f'/api/games/{game_id}')
        self.assertEqual(waiting['status'], 'waiting_for_responses')
        self.assertEqual(waiting['ranking'][0]['pending_count'], 2)
        for user in users[4:]:
            self.request(f'/api/games/{game_id}/votes', {**user, 'slot_ids': [slots[1]]})
        ready = self.request(f'/api/games/{game_id}')
        self.assertEqual(len(ready['players']), 6)
        self.assertEqual(ready['status'], 'ready')
        self.assertEqual([r['available_count'] for r in ready['ranking']], [6, 4])
        self.assertEqual(ready['ranking'][0]['slot_id'], slots[1])
        self.request(f'/api/games/{game_id}/votes', {**users[5], 'slot_ids': []})
        changed = self.request(f'/api/games/{game_id}')
        self.assertEqual(changed['status'], 'ready')
        self.assertEqual(changed['ranking'][0]['available_count'], 5)
        self.assertTrue(all(p['has_voted'] for p in changed['players']))

    def test_existing_named_game_and_votes_survive_store_reopen(self):
        _, sample = self.make_game()
        starts = [slot['starts_at'][:16] for slot in sample['slots']]
        # Use the V1 request shape, including a non-default player minimum.
        created = self.request('/api/games',
                               {'title': 'Our original game', 'min_players': 2, 'starts': starts}, 201)
        game_id = created['game_id']
        game = self.request(f'/api/games/{game_id}')
        users = [self.request(f'/api/games/{game_id}/players',
                             {'name': name, 'suburb': 'Berwick'}, 201)
                 for name in ('Alex', 'Sam')]
        for user in users:
            self.request(f'/api/games/{game_id}/votes', {**user, 'slot_ids': [game['slots'][0]['id']]})
        before = self.request(f'/api/games/{game_id}')
        # Reinitialise from the same database, as a restarted server would.
        self.application.state.store = self.reopen_store()
        self.make_game()
        after = self.request(f'/api/games/{game_id}')
        self.assertEqual(after, before)
        self.assertEqual(after['title'], 'Our original game')
        self.assertEqual(after['min_players'], 2)
        self.assertEqual(after['status'], 'ready')
        restored = self.request(f"/api/games/{game_id}/players/{users[0]['player_id']}",
                                {'player_token': users[0]['player_token']})
        self.assertTrue(restored['has_voted'])
        self.assertEqual(restored['slot_ids'], [game['slots'][0]['id']])

    def test_four_players_vote_then_revote(self):
        game_id, game = self.make_game()
        slots = [s["id"] for s in game["slots"]]
        users = []
        for name in ["Alex", "Jess", "Priya", "Sam"]:
            users.append(self.request(f"/api/games/{game_id}/players", {"name":name, "suburb":"Clayton"}, 201))
        for user in users[:3]:
            self.request(f"/api/games/{game_id}/votes", {**user,"slot_ids":[slots[0]]})
        state = self.request(f"/api/games/{game_id}")
        self.assertEqual(state["status"], "waiting_for_responses")
        self.assertEqual(state["ranking"][0]["available_count"], 3)
        self.request(f"/api/games/{game_id}/votes", {**users[3],"slot_ids":[slots[0]]})
        state = self.request(f"/api/games/{game_id}")
        self.assertEqual(state["status"], "ready")
        self.assertEqual(state["ranking"][0]["available_count"], 4)
        # A player can change a previous vote. It does not create duplicates.
        self.request(f"/api/games/{game_id}/votes", {**users[3],"slot_ids":[]})
        state = self.request(f"/api/games/{game_id}")
        self.assertEqual(state["status"], "no_common_time")
        self.assertEqual(sum(p["has_voted"] for p in state["players"]), 4)

    def test_cannot_edit_someone_elses_vote(self):
        game_id, game = self.make_game()
        person = self.request(f"/api/games/{game_id}/players", {"name":"Alex", "suburb":"Lyndhurst"}, 201)
        result = self.request(f"/api/games/{game_id}/votes", {"player_id":person["player_id"], "player_token":"wrong", "slot_ids":[]}, 400)
        self.assertIn("invalid", result["error"].lower())

    def test_rejects_unknown_slot_and_duplicate_times(self):
        game_id, game = self.make_game()
        person = self.request(f"/api/games/{game_id}/players", {"name":"Alex", "suburb":"Lyndhurst"}, 201)
        self.request(f"/api/games/{game_id}/votes", {**person,"slot_ids":["not-a-real-slot"]}, 400)
        start = (datetime.now(ZoneInfo("Australia/Melbourne")) + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")
        self.request('/api/games', {"title":"duplicates", "starts":[start, start], "min_players":4}, 400)

    def test_restores_player_session(self):
        game_id, _ = self.make_game()
        person = self.request(f"/api/games/{game_id}/players", {"name":"Alex", "suburb":"Berwick"}, 201)
        restored = self.request(f"/api/games/{game_id}/players/{person['player_id']}", {"player_token":person["player_token"]})
        self.assertEqual(restored["name"], "Alex")
        self.assertFalse(restored["has_voted"])

    def test_tokens_are_private_and_bound_to_player_and_game(self):
        game_id, game = self.make_game()
        other_id, other = self.make_game()
        alice = self.request(f'/api/games/{game_id}/players', {'name': 'Alice', 'suburb': 'Clayton'}, 201)
        bob = self.request(f'/api/games/{game_id}/players', {'name': 'Bob', 'suburb': 'Berwick'}, 201)
        slot = game['slots'][0]['id']
        self.request(f'/api/games/{game_id}/votes', {**alice, 'slot_ids': [slot]})
        self.request(f'/api/games/{game_id}/votes', {**alice, 'player_token': bob['player_token'], 'slot_ids': []}, 400)
        self.request(f'/api/games/{game_id}/players/{alice["player_id"]}', {'player_token': bob['player_token']}, 400)
        self.request(f'/api/games/{other_id}/votes', {**alice, 'slot_ids': []}, 400)
        self.request(f'/api/games/{game_id}/votes', {**alice, 'slot_ids': [other['slots'][0]['id']]}, 400)
        restored = self.request(f'/api/games/{game_id}/players/{alice["player_id"]}', {'player_token': alice['player_token']})
        self.assertEqual(restored['slot_ids'], [slot])
        public = json.dumps(self.request(f'/api/games/{game_id}'))
        for person in (alice, bob):
            self.assertNotIn(person['player_token'], public)

    def test_simultaneous_updates_replace_whole_vote(self):
        game_id, game = self.make_game()
        person = self.store.add_player(game_id, 'Alex', 'Clayton')
        slots = [s['id'] for s in game['slots']]
        def save(slot):
            self.reopen_store().submit_vote(game_id, person['player_id'], person['player_token'], [slot])
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(save, slots))
        restored = self.reopen_store().get_player_vote(game_id, person['player_id'], person['player_token'])
        self.assertIn(restored['slot_ids'], [[slots[0]], [slots[1]]])

    def test_bad_requests_and_frontend(self):
        for body in (b'bad json', b'[]', b'null'):
            response = self.client.post('/api/games', content=body)
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post('/api/games', content=b'x' * 10001).status_code, 413)
        for data in ({'title': None}, {'min_players': True}, {'starts': None}):
            self.request('/api/games', data, 400)
        self.request('/api/games/not-found', expected=404)
        self.request('/api/unknown', expected=404)
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertEqual(self.client.get('/app.js').status_code, 200)
        self.assertEqual(self.client.get('/styles.css').status_code, 200)
        self.assertEqual(self.client.get('/.env').status_code, 404)
        self.assertEqual(self.client.post('/api/games', json={}, headers={'Origin': 'https://untrusted.example'}).status_code, 403)
        response = self.client.get('/api/health')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_rate_limits_survive_new_store_and_expire(self):
        with patch('gameon.storage.time.time', return_value=1000):
            self.assertTrue(self.store.allow_request('test-key', 1, 60))
            self.assertFalse(self.reopen_store().allow_request('test-key', 1, 60))
        with patch('gameon.storage.time.time', return_value=1061):
            self.assertTrue(self.reopen_store().allow_request('test-key', 1, 60))
        with patch.object(self.store, 'allow_request', return_value=False):
            response = self.client.get('/api/health')
            self.assertEqual(response.status_code, 429)
            self.assertIn('Retry-After', response.headers)

    def test_database_errors_do_not_leak_details(self):
        with patch.object(self.store, 'allow_request', side_effect=RuntimeError('private-credential')):
            response = self.client.get('/api/health')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private-credential', response.text)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')


if __name__ == '__main__':
    unittest.main()
