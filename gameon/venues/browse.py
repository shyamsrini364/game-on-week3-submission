"""Public, read-only catalogue exploration. Never a game recommendation or booking."""
from .directory import CuratedDirectory, distance


def browse_venues(suburb='', directory=None):
    directory = directory if directory is not None else CuratedDirectory()
    query = ' '.join(suburb.split())
    point = directory.points.get(query.casefold())
    status = 'located' if point else 'unknown_suburb' if query else 'needs_suburb'
    message = ('Venues ordered by approximate straight-line distance from your suburb reference point.' if point else
               'We do not have a geographic reference for that suburb yet. Try a listed suburb, or keep browsing all venues without distances.' if query else
               'Which suburb would you like to play near? Enter a supported Victorian suburb to compare approximate distances, or browse all venues below.')
    venues = [{'venue': v.model_dump(mode='json'),
               'approximate_distance_km': round(distance(point, v.location), 1) if point else None}
              for v in directory.venues]
    venues.sort(key=lambda r: (r['approximate_distance_km'] if point else 0, r['venue']['name']))
    return {'mode': 'general_browse', 'status': status, 'message': message,
            'venues': venues, 'suburbs': sorted(name.title() for name in directory.points),
            'location_source_url': point.source_url if point else None,
            'search_location': point.model_dump(mode='json') if point else None,
            'availability_status': 'not_checked', 'can_prepare_handoff': False,
            'coverage_note': 'Verified Victoria directory, not every court. Moving the map filters the list to that area. These are not group recommendations for an agreed time; availability and prices are not checked.'}
