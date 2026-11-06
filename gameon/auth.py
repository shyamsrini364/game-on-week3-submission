"""Neon JWT verification only; membership authorisation remains in the database."""
from dataclasses import dataclass
import hashlib
import os
from urllib.parse import urlsplit
import jwt


class AuthenticationRequired(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedIdentity:
    issuer: str
    subject: str

    @property
    def key(self):
        return hashlib.sha256((self.issuer+'\0'+self.subject).encode()).hexdigest()


class NeonVerifier:
    def __init__(self, url, *, jwks_client=None):
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or not parsed.hostname.endswith('.neon.tech')
                or '.neonauth.' not in parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or not parsed.path.endswith('/auth')):
            raise ValueError('Use the branch-specific Neon Auth URL.')
        self.url = url.rstrip('/')
        self.issuer = f'https://{parsed.netloc}'
        self.audience = self.issuer
        self.keys = jwks_client or jwt.PyJWKClient(self.url+'/.well-known/jwks.json',
                                                  lifespan=60, timeout=5)

    def verify(self, token):
        if not isinstance(token, str) or len(token) > 16000:
            raise AuthenticationRequired()
        try:
            key = self.keys.get_signing_key_from_jwt(token)
            claims = jwt.decode(token, key.key, algorithms=['EdDSA','RS256','ES256'],
                                issuer=self.issuer, audience=self.audience,
                                options={'require':['exp','iat','iss','aud','sub']}, leeway=5)
            if (not isinstance(claims['sub'], str) or not 1 <= len(claims['sub']) <= 200
                    or claims.get('emailVerified') is not True or claims.get('banned') is True
                    or claims.get('role') != 'authenticated'):
                raise AuthenticationRequired()
            return VerifiedIdentity(self.issuer, claims['sub'])
        except Exception:
            # Never include tokens, upstream responses or account details in errors.
            raise AuthenticationRequired() from None


def configured_verifier():
    from .runtime_profile import public_mvp, validate_public_mvp
    url = os.environ.get('NEON_AUTH_URL', '').strip()
    if public_mvp() or os.environ.get('VERCEL_ENV') == 'production':
        validate_public_mvp()
        return NeonVerifier(url)
    if not url:
        return None
    if os.environ.get('NEON_BRANCH_NAME') != 'game-on-dev':
        raise ValueError('Neon Auth V1 requires the explicitly verified game-on-dev branch.')
    return NeonVerifier(url)
