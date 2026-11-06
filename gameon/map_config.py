"""Public, keyless map configuration. Never export arbitrary environment values."""
import os
import re
from urllib.parse import urlsplit


def map_config():
    style = os.getenv('GAMEON_MAP_STYLE_URL', 'https://tiles.openfreemap.org/styles/liberty').strip()
    origins = os.getenv('GAMEON_MAP_RESOURCE_ORIGINS', 'https://tiles.openfreemap.org').split()
    def safe_origin(value):
        return bool(re.fullmatch(r'https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?', value))
    if not origins or not all(safe_origin(origin) for origin in origins):
        return {'enabled': False, 'style_url': '', 'resource_origins': []}
    try:
        url = urlsplit(style)
    except ValueError:
        return {'enabled': False, 'style_url': '', 'resource_origins': []}
    if (url.scheme != 'https' or url.username or url.password or url.query or url.fragment
            or f'{url.scheme}://{url.netloc}' not in origins):
        return {'enabled': False, 'style_url': '', 'resource_origins': []}
    return {'enabled': True, 'style_url': style, 'resource_origins': sorted(set(origins))}
