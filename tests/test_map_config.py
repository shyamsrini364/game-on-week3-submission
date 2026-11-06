import os
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import create_app
from gameon.map_config import map_config
from gameon.venues.directory import CuratedDirectory


class MapConfigTests(unittest.TestCase):
    def test_public_config_only_exposes_keyless_map_settings(self):
        with patch.dict(os.environ, {'GAMEON_MAP_STYLE_URL': 'https://tiles.example.test/style.json',
                                     'GAMEON_MAP_RESOURCE_ORIGINS': 'https://tiles.example.test',
                                     'PRIVATE_API_KEY': 'never-export-this'}, clear=True):
            self.assertEqual(map_config(), {'enabled': True, 'style_url': 'https://tiles.example.test/style.json',
                                            'resource_origins': ['https://tiles.example.test']})

    def test_credentials_and_csp_injection_disable_only_map(self):
        for style, origins in [
            ('https://tiles.example.test/style?key=secret', 'https://tiles.example.test'),
            ('https://user:secret@tiles.example.test/style', 'https://tiles.example.test'),
            ('http://tiles.example.test/style', 'https://tiles.example.test'),
            ('https://tiles.example.test/style', "https://tiles.example.test; script-src *"),
            ('https://other.example.test/style', 'https://tiles.example.test'),
        ]:
            with self.subTest(style=style), patch.dict(os.environ, {'GAMEON_MAP_STYLE_URL': style,
                                                                  'GAMEON_MAP_RESOURCE_ORIGINS': origins}):
                self.assertEqual(map_config(), {'enabled': False, 'style_url': '', 'resource_origins': []})

    def test_csp_keeps_scripts_and_workers_local(self):
        with patch.dict(os.environ, {}, clear=True), TestClient(create_app()) as client:
            response = client.get('/')
            csp = response.headers['Content-Security-Policy']
            self.assertIn("script-src 'self';", csp)
            self.assertIn("worker-src 'self';", csp)
            self.assertIn("connect-src 'self' https://tiles.openfreemap.org;", csp)
            self.assertNotIn('unsafe-eval', csp)
            self.assertNotIn('unsafe-inline', csp)

    def test_map_coordinates_do_not_replace_distance_reference_points(self):
        venues = CuratedDirectory().venues
        precise = [v for v in venues if v.map_location]
        self.assertTrue({'pickleholic-clayton', 'picklezone-springvale', 'the-jar-maidstone'} <= {v.id for v in precise})
        self.assertTrue(all(v.location.accuracy == 'suburb_reference_point' for v in venues))
        self.assertTrue(all(v.map_location.accuracy == 'verified_venue' for v in precise))
        self.assertTrue(any(not v.map_location for v in venues))
