"""Short-lived, signed evidence receipts; approval still requires current ownership.

Only server-observed graph results issue receipts. They confer no booking authority.
"""
import base64
import hashlib
import hmac
import json
import time

from gameon.validation import StorageError


def issue(secret, game_id, revision, slot_id, scopes, venue_ids, *, binding=None):
    claims = {'game': game_id, 'revision': revision, 'slot': slot_id,
        'scopes': list(scopes), 'venues': list(venue_ids), 'expires': int(time.time())+600}
    if binding is not None:
        claims['binding'] = binding
    body = json.dumps(claims,
        separators=(',', ':'), sort_keys=True).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(body).decode() + '.' + signature


def verify(secret, receipt, game_id, selection, *, binding=None):
    try:
        if not secret or not isinstance(receipt, str) or len(receipt) > 2000:
            raise ValueError()
        encoded, signature = receipt.split('.')
        body = base64.b64decode(encoded, altchars=b'-_', validate=True)
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError()
        data = json.loads(body)
        if (data['game'] != game_id or data['revision'] != selection.get('revision')
                or data['slot'] != selection.get('slot_id')
                or selection.get('venue_id') not in data['venues']
                or type(data['expires']) is not int or data['expires'] <= time.time()):
            raise ValueError()
        if data.get('binding') != binding:
            raise ValueError()
        return tuple(data['scopes'])
    except (ValueError, KeyError, TypeError):
        raise StorageError('This Ernie proposal is invalid or expired. Run a fresh plan.') from None
