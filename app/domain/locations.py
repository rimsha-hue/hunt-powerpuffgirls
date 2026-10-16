"""Locations: resolve what people type ("isb", "Lahore, PK", "wfh", "USA") to canonical places.

Free text is never stored. A place is kept by a stable key ("city:1176615", "country:PK",
"remote") so job search, matching and display all agree on what the user meant, and nonsense
("bla bla") is rejected with suggestions instead of being searched for.

The index is pure: it is built from Place records. `load_geonames()` supplies the real data
(GeoNames: every country, and cities over 15,000 people, with alternate spellings).
"""

import bisect
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

PlaceKind = Literal["remote", "country", "city"]

REMOTE_ALIASES = (
    "remote",
    "anywhere",
    "worldwide",
    "global",
    "work from home",
    "wfh",
    "fully remote",
    "remote first",
    "remote only",
    "telecommute",
    "distributed",
)
# Common names that GeoNames doesn't list as country aliases.
COUNTRY_ALIASES = {
    "usa": "US",
    "us": "US",
    "united states of america": "US",
    "america": "US",
    "uk": "GB",
    "united kingdom": "GB",
    "great britain": "GB",
    "britain": "GB",
    "england": "GB",
    "uae": "AE",
    "emirates": "AE",
    "ksa": "SA",
    "saudi": "SA",
    "holland": "NL",
    "south korea": "KR",
    "korea": "KR",
    "russia": "RU",
    "turkiye": "TR",
}
MIN_ALIAS_LENGTH = 3
MAX_SUGGESTIONS = 10
_KIND_RANK = {"remote": 0, "country": 1, "city": 2}


def fold(text: str) -> str:
    """Accent-, case- and punctuation-insensitive form used for matching."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True)
class Place:
    key: str
    kind: PlaceKind
    name: str
    country_code: str | None = None
    country_name: str | None = None
    population: int = 0
    currency: str | None = None

    @property
    def label(self) -> str:
        if self.kind == "city" and self.country_name:
            return f"{self.name}, {self.country_name}"
        return self.name


REMOTE = Place(key="remote", kind="remote", name="Remote")


@dataclass
class LocationIndex:
    """Folded names and aliases to places.

    A place's own name always outranks an alternate spelling: GeoNames lists "Islamabad" as a
    historical name of Chattogram (a bigger city), but someone typing "Islamabad" means Islamabad.
    """

    places: dict[str, Place]
    _aliases: dict[str, list[Place]] = field(default_factory=dict)
    _primary: dict[str, list[Place]] = field(default_factory=dict)
    _sorted_aliases: list[str] = field(default_factory=list)

    @classmethod
    def build(cls, places: Iterable[tuple[Place, Iterable[str]]]) -> "LocationIndex":
        index = cls(places={REMOTE.key: REMOTE})
        index._add(REMOTE.name, REMOTE, primary=True)
        for alias in REMOTE_ALIASES:
            index._add(alias, REMOTE)
        for place, aliases in places:
            index.places[place.key] = place
            index._add(place.name, place, primary=True)
            for alias in aliases:
                index._add(alias, place)
        for alias, code in COUNTRY_ALIASES.items():
            country = index.places.get(f"country:{code}")
            if country:
                index._add(alias, country)
        for table in (index._aliases, index._primary):
            for candidates in table.values():
                candidates.sort(key=lambda p: (_KIND_RANK[p.kind], -p.population))
        index._sorted_aliases = sorted(index._aliases)
        return index

    def _add(self, alias: str, place: Place, primary: bool = False) -> None:
        key = fold(alias)
        if len(key) < MIN_ALIAS_LENGTH and place.kind != "country":
            return
        for table in (self._aliases, self._primary) if primary else (self._aliases,):
            candidates = table.setdefault(key, [])
            if place not in candidates:
                candidates.append(place)

    def _lookup(self, folded: str) -> list[Place]:
        return self._primary.get(folded) or self._aliases.get(folded) or []

    @property
    def currencies(self) -> frozenset[str]:
        return frozenset(p.currency for p in self.places.values() if p.currency)

    def get(self, key: str) -> Place | None:
        return self.places.get(key)

    def resolve(self, text: str) -> Place | None:
        """The place a person most likely meant, or None if the text names no known place."""
        if text in self.places:  # already a key
            return self.places[text]
        folded = fold(text)
        if not folded:
            return None
        if "," in text:
            head, _, qualifier = text.partition(",")
            country = self._country(qualifier)
            if country is not None:
                head_key = fold(head)
                for place in [*self._primary.get(head_key, []), *self._aliases.get(head_key, [])]:
                    if place.kind == "city" and place.country_code == country.country_code:
                        return place
                if not head_key:
                    return country
            found = self._lookup(fold(head))
            if found:
                return found[0]
        found = self._lookup(folded)
        return found[0] if found else None

    def _country(self, text: str) -> Place | None:
        for place in self._lookup(fold(text)):
            if place.kind == "country":
                return place
        return None

    def suggest(self, text: str, limit: int = MAX_SUGGESTIONS) -> list[Place]:
        """Places whose name or alias starts with the text, most useful first."""
        prefix = fold(text.split(",")[0])
        if len(prefix) < 2:
            return []
        start = bisect.bisect_left(self._sorted_aliases, prefix)
        found: dict[str, Place] = {}
        for alias in self._sorted_aliases[start : start + 400]:
            if not alias.startswith(prefix):
                break
            for place in self._aliases[alias]:
                found.setdefault(place.key, place)

        def rank(place: Place) -> tuple[int, int, int, int, str]:
            own = fold(place.name)
            return (
                own != prefix,  # exact own-name match first
                not own.startswith(
                    prefix
                ),  # then own-name prefix matches, before alternate spellings
                _KIND_RANK[place.kind],
                -place.population,
                place.name,
            )

        return sorted(found.values(), key=rank)[:limit]


def _ascii_aliases(names: Iterable[str]) -> list[str]:
    """GeoNames lists hundreds of spellings; keep the plain Latin ones people actually type.

    The cheap `isascii` test runs first so the costly Unicode folding only sees likely keepers.
    """
    return [n for n in names if n.isascii() and re.fullmatch(r"[a-z0-9 ]+", fold(n) or "-")]


@lru_cache(maxsize=1)
def load_geonames() -> LocationIndex:
    import geonamescache

    cache = geonamescache.GeonamesCache()
    countries = cache.get_countries()
    places: list[tuple[Place, list[str]]] = []
    for code, c in countries.items():
        place = Place(
            key=f"country:{code}",
            kind="country",
            name=c["name"],
            country_code=code,
            country_name=c["name"],
            population=int(c.get("population") or 0),
            currency=c.get("currencycode") or None,
        )
        places.append((place, [code, c.get("iso3", "")]))
    for city in cache.get_cities().values():
        code = city["countrycode"]
        country = countries.get(code)
        place = Place(
            key=f"city:{city['geonameid']}",
            kind="city",
            name=city["name"],
            country_code=code,
            country_name=country["name"] if country else None,
            population=int(city.get("population") or 0),
        )
        places.append((place, _ascii_aliases(city.get("alternatenames") or [])))
    return LocationIndex.build(places)
