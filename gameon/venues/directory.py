"""Small offline directory: verified identities, never live court inventory."""
import json
import math
import re
from datetime import date
from pathlib import Path
from .contracts import (Venue, Point, VenueContext, PlayerLocation, VenueSearchResult,
                        VenueRanking, Recommendation, PlayerDistance)

DATA = Path(__file__).with_name('data')


def distance(a, b):
    """Haversine kilometres between coarse suburb reference points, not routes."""
    lat1, lat2 = math.radians(a.latitude), math.radians(b.latitude)
    dlat = lat2 - lat1
    dlon = math.radians(b.longitude - a.longitude)
    h = math.sin(dlat / 2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon / 2)**2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(h)))


def deduplicate(records):
    """Collapse exact repeats; conflicting IDs/addresses fail closed for review."""
    ids, addresses = {}, {}
    for raw in records:
        venue = Venue.model_validate(raw)
        # Common formatting differences must not create two identities for one site.
        aliases = {'rd':'road', 'st':'street', 'dr':'drive', 'ave':'avenue',
                   'ct':'court', 'cres':'crescent', 'blvd':'boulevard', 'pde':'parade', 'victoria':'vic'}
        address = ' '.join(aliases.get(word, word) for word in
                           re.findall(r'[a-z0-9]+', venue.address.casefold()))
        if venue.id in ids and ids[venue.id] != venue:
            raise ValueError('Conflicting venue ID')
        if address in addresses and addresses[address] != venue.id:
            raise ValueError('Conflicting venue address')
        ids[venue.id] = venue
        addresses[address] = venue.id
    return tuple(ids[k] for k in sorted(ids))


class CuratedDirectory:
    def __init__(self, venues=None, points=None):
        if venues is None:
            venues = [Venue.model_validate_json(json.dumps(v)) for v in
                      json.loads((DATA / 'melbourne.json').read_text(encoding='utf-8'))]
        self.venues = deduplicate(venues)
        if points is None:
            points = {name: Point.model_validate_json(json.dumps(value)) for name, value in
                      json.loads((DATA / 'suburbs.json').read_text(encoding='utf-8')).items()}
        self.points = {name.casefold().strip(): Point.model_validate(p) for name, p in points.items()}

    def context(self, game, preferences):
        supplied = {p.player_id: p.venue_ids for p in preferences.players}
        if not set(supplied) <= set(game.players):
            raise ValueError('Player preference belongs to a different game')
        return VenueContext(preferences=preferences.group, players=tuple(
            PlayerLocation(player_id=p.id, suburb=p.suburb,
                           location=self.points.get(p.suburb.casefold().strip()),
                           preferred_venue_ids=supplied.get(p.id, ())) for p in game.players.values()))

    def search(self, request, snapshot, schedule):
        return search_pickleball_venues(self, request, snapshot, schedule)


def attendees(snapshot, schedule, request):
    from gameon.erne.tools import eligible_slots
    if request.snapshot_id != snapshot.snapshot_id or request.slot_id not in eligible_slots(snapshot, schedule):
        raise ValueError('Ineligible search scope')
    if snapshot.venue_context is None:
        raise ValueError('Missing venue context')
    match = next(m for m in schedule.matches if m.slot_id == request.slot_id)
    players = tuple(p for p in snapshot.venue_context.players if p.player_id in match.available_player_ids)
    if len(players) != len(match.available_player_ids) or any(p.location is None for p in players):
        raise ValueError('Unresolved attendee suburb')
    return players


def favourites(context, players):
    return set(context.preferences.preferred_venue_ids).union(*(p.preferred_venue_ids for p in players))


def suitable(venue, context, players, today):
    prefs = context.preferences
    return (0 <= (today - venue.checked_on).days <= 90 and venue.pickleball
            and (not prefs.indoor_only or venue.indoor is True)
            and (not prefs.restrict_to_preferences or venue.id in favourites(context, players))
            and all(distance(p.location, venue.location) <= prefs.max_individual_distance_km for p in players))


def search_pickleball_venues(directory, request, snapshot, schedule):
    players = attendees(snapshot, schedule, request)
    context = snapshot.venue_context
    preferred = favourites(context, players)
    if context.preferences.restrict_to_preferences and request.scope != 'preferred':
        raise ValueError('Organiser restricted discovery to preferences')
    centre = Point(latitude=sum(p.location.latitude for p in players)/len(players),
                   longitude=sum(p.location.longitude for p in players)/len(players),
                   source_url='https://example.invalid/computed-group-reference')
    selected = tuple(v for v in directory.venues if
                     (v.id in preferred if request.scope == 'preferred' else
                      distance(centre, v.location) <= (15 if request.scope == 'local' else 50)))
    accepted = tuple(v for v in selected if suitable(v, context, players, snapshot.observed_at.date()))
    return VenueSearchResult(snapshot_id=request.snapshot_id, slot_id=request.slot_id,
        scope=request.scope, status='results' if accepted else 'empty', venues=accepted,
        missing_preferred_ids=tuple(sorted(preferred - {v.id for v in directory.venues})),
        excluded_ids=tuple(v.id for v in selected if v not in accepted),
        warnings=('Limited curated coverage; absence is not proof that a venue does not exist.',
                  'Suburb reference points are not venue entrances. Availability has not been checked.'))


def rank_venues(directory, request, snapshot, schedule, observations):
    players = attendees(snapshot, schedule, request)
    context = snapshot.venue_context
    records = []
    for evidence in observations:
        result = evidence.payload
        if isinstance(result, VenueSearchResult):
            if result.snapshot_id != request.snapshot_id or result.slot_id != request.slot_id:
                raise ValueError('Ranking evidence scope mismatch')
            records.extend(result.venues)
    recommendations = []
    for venue in deduplicate(records):
        if venue not in directory.venues:
            raise ValueError('Untrusted venue record')
        if not suitable(venue, context, players, snapshot.observed_at.date()):
            continue
        distances = tuple(PlayerDistance(player_id=p.player_id, suburb=p.suburb,
            approximate_km=round(distance(p.location, venue.location), 2)) for p in players)
        average = sum(d.approximate_km for d in distances)/len(distances)
        longest = max(d.approximate_km for d in distances)
        group = venue.id in context.preferences.preferred_venue_ids
        supporters = sum(venue.id in p.preferred_venue_ids for p in players)
        bonus = 2.0*group + 2.0*supporters/len(players)
        score = round((average + longest)/2 - bonus, 3)
        recommendations.append(Recommendation(venue=venue, preferred=bool(group or supporters),
            group_preferred=group, individual_preference_count=supporters, distances=distances,
            average_distance_km=round(average, 2), longest_distance_km=longest, ranking_score=score,
            reason=f'Approximate average {average:.1f} km; longest {longest:.1f} km; preference bonus {bonus:.1f}. '
                   'Lower score is better. Straight-line suburb distances, not driving or a booking.'))
    # Price only breaks equal distance/preference scores; unknown is never treated as free.
    recommendations.sort(key=lambda r: (r.ranking_score,
        r.venue.price.aud_per_court_hour if r.venue.price else float('inf'), r.venue.id))
    return VenueRanking(snapshot_id=request.snapshot_id, slot_id=request.slot_id,
                        recommendations=tuple(recommendations[:3]))
