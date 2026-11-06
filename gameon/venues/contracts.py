"""Local venue/profile contracts. No availability or booking claims from a directory."""
from datetime import date
from typing import Annotated, Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, model_validator

Id = Annotated[str, Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')]
Name = Annotated[str, Field(min_length=1, max_length=160)]


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True, revalidate_instances='always', allow_inf_nan=False)


class Point(Record):
    # Victoria envelope, including regional centres. Precision is explicit below.
    latitude: float = Field(ge=-39.3, le=-34)
    longitude: float = Field(ge=140.9, le=150.1)
    accuracy: Literal['suburb_reference_point'] = 'suburb_reference_point'
    source_url: str


class VenueMapPoint(Point):
    accuracy: Literal['verified_venue'] = 'verified_venue'
    checked_on: date


class PlayerPreference(Record):
    player_id: Id
    venue_ids: tuple[Id, ...] = Field(default=(), max_length=10)


class GroupProfile(Record):
    group_id: Id = 'local-group'
    preferred_venue_ids: tuple[Id, ...] = Field(default=(), max_length=10)
    restrict_to_preferences: bool = False
    indoor_only: bool = False
    max_individual_distance_km: float = Field(default=80.0, gt=0, le=100)


class VenuePreferences(Record):
    group: GroupProfile = Field(default_factory=GroupProfile)
    players: tuple[PlayerPreference, ...] = Field(default=(), max_length=1000)

    @model_validator(mode='after')
    def unique_players(self):
        if len({p.player_id for p in self.players}) != len(self.players):
            raise ValueError('Duplicate player preferences')
        return self


class PlayerLocation(Record):
    player_id: Id
    suburb: Name
    location: Point | None
    preferred_venue_ids: tuple[Id, ...] = ()


class VenueContext(Record):
    preferences: GroupProfile
    players: tuple[PlayerLocation, ...]


class Price(Record):
    aud_per_court_hour: float = Field(gt=0, le=1000)
    basis: Literal['advertised_nonmember_rate'] = 'advertised_nonmember_rate'
    note: Name
    source_url: str


class Venue(Record):
    id: Id
    name: Name
    suburb: Name
    address: Name
    state: Literal['VIC'] = 'VIC'
    postcode: Annotated[str, Field(pattern=r'^3[0-9]{3}$')] | None = None
    venue_type: Literal['dedicated_centre', 'shared_facility', 'club', 'other'] | None = None
    location: Point
    map_location: VenueMapPoint | None = None
    website_url: str
    booking_url: str | None = None
    enquiry_url: str | None = None
    booking_provider: Literal['Playbypoint', 'SportLogic'] | None = None
    provider_evidence_url: str | None = None
    sources: tuple[str, ...] = Field(min_length=1, max_length=5)
    checked_on: date
    identity_status: Literal['source_verified'] = 'source_verified'
    pickleball: bool = True
    indoor: bool | None = None
    outdoor: bool | None = None
    price: Price | None = None
    notes: tuple[str, ...] = ()

    @model_validator(mode='after')
    def trusted_links(self):
        urls = (self.website_url, self.booking_url, self.enquiry_url, self.provider_evidence_url,
                self.location.source_url, self.map_location.source_url if self.map_location else None,
                *self.sources, self.price.source_url if self.price else None)
        for url in urls:
            if url is None:
                continue
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('Invalid venue URL')
        if self.booking_provider and not self.provider_evidence_url:
            raise ValueError('Provider identification needs evidence')
        return self


class VenueSearchResult(Record):
    snapshot_id: Id
    slot_id: Id
    scope: Literal['preferred', 'local', 'expanded']
    status: Literal['results', 'empty']
    venues: tuple[Venue, ...] = Field(default=(), max_length=100)
    missing_preferred_ids: tuple[Id, ...] = ()
    excluded_ids: tuple[Id, ...] = ()
    warnings: tuple[str, ...] = ()
    source: Literal['curated_directory'] = 'curated_directory'
    simulated: Literal[False] = False
    availability_status: Literal['not_checked'] = 'not_checked'

    @model_validator(mode='after')
    def consistent(self):
        if (self.status == 'results') != bool(self.venues) or len({v.id for v in self.venues}) != len(self.venues):
            raise ValueError('Inconsistent directory results')
        return self


class PlayerDistance(Record):
    player_id: Id
    suburb: Name
    approximate_km: float = Field(ge=0)


class Recommendation(Record):
    venue: Venue
    preferred: bool
    group_preferred: bool
    individual_preference_count: int = Field(ge=0)
    distances: tuple[PlayerDistance, ...]
    average_distance_km: float = Field(ge=0)
    longest_distance_km: float = Field(ge=0)
    distance_accuracy: Literal['approximate_straight_line_between_suburb_reference_points_not_driving'] = 'approximate_straight_line_between_suburb_reference_points_not_driving'
    ranking_score: float
    reason: str
    availability_status: Literal['not_checked'] = 'not_checked'


class VenueRanking(Record):
    snapshot_id: Id
    slot_id: Id
    recommendations: tuple[Recommendation, ...] = Field(max_length=3)
    source: Literal['python_ranking'] = 'python_ranking'
    simulated: Literal[False] = False
