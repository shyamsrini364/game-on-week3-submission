"""Game On! HTTP API and vanilla frontend; local Uvicorn or Vercel ASGI."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from gameon.groups import AccessDenied
from gameon.auth import AuthenticationRequired, VerifiedIdentity, configured_verifier
from gameon.booking import StaleHandoff, prepare_handoff
from gameon.config import create_store
from gameon.map_config import map_config
from gameon.storage import DatabaseUnavailable, StorageError

ROOT = Path(__file__).parent
if not os.environ.get("VERCEL"):
    load_dotenv(ROOT / ".env", override=False)


class InvalidBody(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


async def read_json(request):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 10000:
            raise InvalidBody("Request too large.", 413)
    try:
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeDecodeError):
        raise InvalidBody("Invalid JSON request.") from None


def create_app(store=None, rate_limit_secret=None, *, planning_provider_factory=None, auth_verifier=None,
               planning_approval=None):
    from gameon.runtime_profile import public_mvp, validate_public_mvp
    public_runtime = public_mvp()
    if public_runtime or os.environ.get('VERCEL_ENV') == 'production':
        validate_public_mvp()
        if planning_provider_factory is not None or planning_approval is not None:
            raise RuntimeError('Public MVP permits deterministic planning only.')
    application = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    verifier = auth_verifier or configured_verifier()
    public_map = map_config()
    application.state.store = store
    lock = threading.Lock()
    secret = rate_limit_secret or os.environ.get("GAMEON_RATE_LIMIT_SECRET")
    if not secret and not os.environ.get("VERCEL"):
        secret = secrets.token_hex(32)

    def get_store():
        with lock:
            if application.state.store is None:
                application.state.store = create_store()
        return application.state.store

    def allow_request(key, limit, window):
        return get_store().allow_request(key, limit, window)

    @application.middleware("http")
    async def security(request: Request, call_next):
        response = None
        try:
            if request.url.path.startswith("/api/"):
                origin = request.headers.get("origin")
                if request.method == "POST" and origin and urlsplit(origin).netloc != request.url.netloc:
                    response = JSONResponse({"error": "Origin not allowed."}, status_code=403)
                elif not secret:
                    response = JSONResponse({"error": "Service configuration unavailable."}, status_code=503)
                else:
                    client = request.client.host if request.client else "unknown"
                    if os.environ.get("VERCEL"):
                        client = request.headers.get("x-vercel-forwarded-for", client).split(",")[0].strip()
                    action, limit, window = "read", 120, 60
                    if request.method == "POST":
                        action, limit = "write", 120
                        if request.url.path in ("/api/games", "/api/groups"):
                            action, limit, window = "create", 20, 3600
                        elif (request.url.path.endswith("/players") or request.url.path == "/api/invitations/join"):
                            action, limit, window = "join", 40, 3600
                    key = hmac.new(secret.encode(), f"{action}:{client}".encode(), hashlib.sha256).hexdigest()
                    if not await run_in_threadpool(allow_request, key, limit, window):
                        response = JSONResponse({"error": "Too many requests. Please try again later."},
                                                status_code=429, headers={"Retry-After": str(window)})
            if response is None:
                request.state.identity = None
                header = request.headers.get('authorization', '')
                if header.startswith('Bearer ') and '.' in header[7:]:
                    if verifier is None:
                        raise AuthenticationRequired()
                    request.state.identity = await run_in_threadpool(verifier.verify, header[7:])
                if public_runtime and request.url.path.startswith('/api/'):
                    public_paths = {'/api/health', '/api/auth/config', '/api/map-config',
                                    '/api/venues', '/api/venues/browse', '/api/invitations/preview'}
                    if request.url.path not in public_paths and request.state.identity is None:
                        raise AuthenticationRequired()
                    if request.url.path.startswith('/api/games/'):
                        game_id = request.url.path.split('/')[3]
                        group = await run_in_threadpool(get_store().game_group_access, game_id, request.state.identity)
                        if not group:
                            raise AccessDenied()
                response = await call_next(request)
        except AuthenticationRequired:
            response = JSONResponse({'error': 'Please sign in again.'}, status_code=401)
        except AccessDenied:
            response = JSONResponse({'error': 'Group access required.'}, status_code=403)
        except Exception:
            # Never log connection strings, request bodies or player tokens.
            response = JSONResponse({"error": "Service temporarily unavailable."}, status_code=503)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        map_origins = ' '.join(public_map['resource_origins'])
        auth_origin = verifier.issuer if verifier else ''
        connect_origins = ' '.join(filter(None, (map_origins, auth_origin)))
        response.headers["Content-Security-Policy"] = f"default-src 'self'; script-src 'self'; style-src 'self'; worker-src 'self'; connect-src 'self' {connect_origins}; img-src 'self' data: blob: {map_origins}; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(InvalidBody)
    async def invalid_body(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=exc.status)

    @application.get('/api/map-config')
    async def public_map_config():
        return public_map

    @application.exception_handler(AccessDenied)
    async def denied(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=403)

    def member_token(request):
        if getattr(request.state, 'identity', None):
            return request.state.identity
        header = request.headers.get('authorization', '')
        return header[7:] if header.startswith('Bearer ') else ''

    def account_identity(request):
        identity = getattr(request.state, 'identity', None)
        if not isinstance(identity, VerifiedIdentity):
            raise AuthenticationRequired()
        return identity

    @application.exception_handler(AuthenticationRequired)
    async def authentication_required(request, exc):
        return JSONResponse({'error':'Please sign in again.'}, status_code=401)

    @application.get('/api/auth/config')
    def auth_config():
        return {'enabled':bool(verifier), 'url':verifier.url if verifier else None,
                'email_otp':bool(verifier) and os.environ.get('NEON_AUTH_EMAIL_OTP') == 'true'}

    @application.get('/api/account')
    def account(request: Request):
        return get_store().account_summary(account_identity(request))

    @application.post('/api/account/profile')
    async def account_profile(request: Request):
        identity = account_identity(request)
        data = await read_json(request)
        return await run_in_threadpool(get_store().update_account, identity, data.get('name',''), data.get('suburb',''), data.get('avatar_id'), data.get('favourites',[]))

    @application.post('/api/account/link')
    async def link_account(request: Request):
        identity = account_identity(request)
        data = await read_json(request)
        return await run_in_threadpool(get_store().link_legacy_membership, identity, data.get('group_id',''), data.get('member_token',''))

    @application.get('/api/games/{game_id}/me')
    def account_game_player(game_id: str, request: Request):
        p = get_store().account_player(game_id, account_identity(request))
        if not p:
            return {'player_id':None}
        return {'player_id':p['id'], **get_store().get_player_vote(game_id,p['id'],p['token'])}

    @application.exception_handler(StorageError)
    async def invalid_input(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=400)

    @application.exception_handler(StaleHandoff)
    async def stale_handoff(request, exc):
        return JSONResponse({'error': str(exc)}, status_code=409)

    @application.exception_handler(DatabaseUnavailable)
    async def unavailable(request, exc):
        return JSONResponse({"error": "Database temporarily unavailable."}, status_code=503)

    @application.get("/api/health")
    def health():
        get_store().check_health()
        return {"ok": True, "app": "Game On!"}

    @application.get("/api/games/{game_id}")
    def get_game(game_id: str, request: Request):
        get_store().game_group_access(game_id, member_token(request))
        try:
            return get_store().get_game(game_id)
        except StorageError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @application.post("/api/games", status_code=201)
    async def create_game(request: Request):
        data = await read_json(request)
        if public_runtime and not data.get('group_id'):
            raise InvalidBody('Create a game inside your group so access stays private.')
        game_id = await run_in_threadpool(get_store().create_game, data.get("title", ""),
                                          data.get("starts", []), data.get("min_players", 4), data.get("group_id"), member_token(request), data.get("duration_minutes", 90))
        return {"game_id": game_id}

    @application.post("/api/games/{game_id}/players", status_code=201)
    async def add_player(game_id: str, request: Request):
        data = await read_json(request)
        result = await run_in_threadpool(get_store().add_player, game_id, data.get("name", ""), data.get("suburb", ""), member_token(request))
        if isinstance(member_token(request), VerifiedIdentity) and await run_in_threadpool(get_store().game_group_access, game_id, member_token(request)):
            result.pop('player_token', None)
        return result

    @application.post("/api/games/{game_id}/votes")
    async def vote(game_id: str, request: Request):
        await run_in_threadpool(get_store().game_group_access, game_id, member_token(request))
        data = await read_json(request)
        if isinstance(member_token(request), VerifiedIdentity) and await run_in_threadpool(get_store().game_group_access, game_id, member_token(request)):
            p = await run_in_threadpool(get_store().account_player, game_id, member_token(request))
            if not p or data.get('player_id') != p['id']:
                raise AccessDenied()
            data['player_token'] = p['token']
        await run_in_threadpool(get_store().submit_vote, game_id, data.get("player_id", ""),
                                 data.get("player_token", ""), data.get("slot_ids", []))
        return {"ok": True}

    @application.post("/api/games/{game_id}/players/{player_id}")
    async def restore(game_id: str, player_id: str, request: Request):
        await run_in_threadpool(get_store().game_group_access, game_id, member_token(request))
        data = await read_json(request)
        if isinstance(member_token(request), VerifiedIdentity) and await run_in_threadpool(get_store().game_group_access, game_id, member_token(request)):
            p = await run_in_threadpool(get_store().account_player, game_id, member_token(request))
            if not p or player_id != p['id']:
                raise AccessDenied()
            data['player_token'] = p['token']
        return await run_in_threadpool(get_store().get_player_vote, game_id, player_id, data.get("player_token", ""))

    @application.get('/api/games/{game_id}/recommendations')
    def recommendations(game_id: str, request: Request):
        from gameon.venues.service import recommend_for_game
        return recommend_for_game(get_store(), game_id, member_token(request))

    @application.post('/api/games/{game_id}/plan')
    def plan(game_id: str, request: Request):
        from gameon.erne.web_service import plan_game, PlanningUnavailable, PlanningConflict
        try:
            return plan_game(get_store(), game_id, member_token(request), signing_secret=secret,
                             provider=planning_provider_factory() if planning_provider_factory else None,
                             live_approval=planning_approval)
        except PlanningUnavailable as exc:
            return JSONResponse({'error': str(exc)}, status_code=503)
        except PlanningConflict as exc:
            return JSONResponse({'error': str(exc)}, status_code=409)

    @application.get('/api/venues/browse')
    def browse_venues(request: Request):
        from gameon.venues.browse import browse_venues
        suburb = request.query_params.get('suburb', '')
        if len(suburb) > 100:
            return JSONResponse({'error': 'Use a suburb name of 100 characters or fewer.'}, status_code=400)
        return browse_venues(suburb)

    @application.get('/api/venues')
    def venues():
        from gameon.venues.directory import CuratedDirectory
        directory = CuratedDirectory()
        return {'venues': [{'id': v.id, 'name': v.name, 'suburb': v.suburb} for v in directory.venues],
                'suburbs': sorted(name.title() for name in directory.points),
                'suburb_locations': {name: point.model_dump(mode='json') for name, point in directory.points.items()}}

    @application.post('/api/games/{game_id}/booking-handoff')
    async def booking_handoff(game_id: str, request: Request):
        data = await read_json(request)
        return await run_in_threadpool(prepare_handoff, get_store(), game_id, member_token(request), data,
                                      planning_secret=secret)

    @application.post('/api/groups', status_code=201)
    async def create_group(request: Request):
        data = await read_json(request)
        identity = account_identity(request) if verifier else None
        return await run_in_threadpool(get_store().create_group, data.get('name', ''), data.get('player_name', ''), data.get('suburb', ''), data.get('favourites', []), data.get('avatar_id'), identity)

    @application.post('/api/invitations/preview')
    async def preview_invitation(request: Request):
        data = await read_json(request)
        return await run_in_threadpool(get_store().preview_invitation, data.get('code', ''), data.get('invitation_token', ''))

    @application.post('/api/invitations/join', status_code=201)
    async def join_group(request: Request):
        data = await read_json(request)
        identity = account_identity(request) if verifier else None
        return await run_in_threadpool(get_store().join_group, data.get('code', ''), data.get('invitation_token', ''), data.get('name', ''), data.get('suburb', ''), data.get('favourites', []), data.get('avatar_id'), identity)

    @application.post('/api/groups/restore')
    async def restore_group(request: Request):
        data = await read_json(request)
        return await run_in_threadpool(get_store().restore_group, data.get('code', ''), member_token(request))

    @application.get('/api/groups/{group_id}')
    def get_group(group_id: str, request: Request):
        return get_store().get_group(group_id, member_token(request))

    @application.post('/api/groups/{group_id}/settings')
    async def settings(group_id: str, request: Request):
        data = await read_json(request)
        await run_in_threadpool(get_store().update_group, group_id, member_token(request), data.get('name', ''), data.get('favourites', []), data.get('restrict_venues', False))
        return {'ok': True}

    @application.post('/api/groups/{group_id}/me')
    async def member_profile(group_id: str, request: Request):
        data = await read_json(request)
        if isinstance(member_token(request), VerifiedIdentity):
            await run_in_threadpool(get_store().get_group, group_id, member_token(request))
            await run_in_threadpool(get_store().update_account, member_token(request), data.get('name',''), data.get('suburb',''), data.get('avatar_id'), data.get('favourites',[]))
        else:
            await run_in_threadpool(get_store().update_member, group_id, member_token(request), data.get('name', ''), data.get('suburb', ''), data.get('favourites', []), data.get('avatar_id'))
        return {'ok': True}

    @application.post('/api/groups/{group_id}/invitation')
    async def invitation(group_id: str, request: Request):
        data = await read_json(request)
        return await run_in_threadpool(get_store().invitation_action, group_id, member_token(request), data.get('action'))

    @application.post('/api/groups/{group_id}/credential')
    def rotate_credential(group_id: str, request: Request):
        if isinstance(member_token(request), VerifiedIdentity):
            raise AccessDenied()
        return get_store().rotate_member_token(group_id, member_token(request))

    @application.api_route("/api/{path:path}", methods=["GET", "POST"])
    def missing(path: str):
        return JSONResponse({"error": "Not found"}, status_code=404)

    application.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
    return application


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8000")), access_log=False)
