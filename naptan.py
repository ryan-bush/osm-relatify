import asyncio
import csv
import fcntl
import os
import re
import sqlite3
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import chain
from math import radians
from pathlib import Path

import orjson
from rapidfuzz.fuzz import token_ratio
from sklearn.neighbors import BallTree

from config import NAPTAN_DATA_DIR, NAPTAN_MAX_AGE
from cython_lib.geoutils import haversine_distance, radians_tuple
from models.bounding_box import BoundingBox
from models.download_history import DownloadHistory
from models.fetch_relation import FetchRelationBusStopCollection
from models.naptan_stop import NaptanStop, NaptanTagSuggestion
from naptan_tags import missing_tags
from overpass import optimize_cells_and_get_bbs
from utils import HTTP, normalize_name

NAPTAN_URL = 'https://naptan.api.dft.gov.uk/v1/access-nodes?dataFormat=csv'

# on-street stops, and bays in a bus station
_BUS_STOP_TYPES = frozenset(('BCT', 'BCS', 'BCQ'))
# hail-and-ride, flexible and unmarked stops have no pole to put on the map
_POLELESS_BUS_STOP_TYPES = frozenset(('HAR', 'FLX', 'CUS'))

# "Stop A", "Stop P1", "Stance 1", "Bay 12"; other indicators ("opp", "o/s 103", "->N")
# describe where the stop is rather than naming it
_LOCAL_REF_RE = re.compile(r'^(?:stop|stand|stance|bay)\s+([a-z0-9]{1,4})$', re.IGNORECASE)

_TAG_COLUMNS = (
    ('naptan:AtcoCode', 'ATCOCode'),
    ('naptan:NaptanCode', 'NaptanCode'),
    ('naptan:CommonName', 'CommonName'),
    ('naptan:Indicator', 'Indicator'),
    ('naptan:Street', 'Street'),
    ('naptan:Bearing', 'Bearing'),
)

# bumped whenever what the database stores changes, so an older one is rebuilt on start
# rather than serving stale tags until the next daily refresh
DATA_VERSION = 3

# NaPTAN positions are often tens of metres out
MATCH_DISTANCE = 80  # meters
# the cutoff bus_collection_builder.py groups similar stop names with
MATCH_NAME_SCORE = 89


def parse_row(row: dict[str, str]) -> NaptanStop | None:
    if (
        row['StopType'] not in _BUS_STOP_TYPES
        or row['Status'] != 'active'
        or row['BusStopType'] in _POLELESS_BUS_STOP_TYPES
    ):
        return None

    name = row['CommonName'].strip()
    if not name or not row['Latitude'] or not row['Longitude']:
        return None

    indicator = row['Indicator'].strip()
    tags = {'name': name}

    if match := _LOCAL_REF_RE.match(indicator):
        tags['local_ref'] = match.group(1).upper()

    for key, column in _TAG_COLUMNS:
        if value := row[column].strip():
            tags[key] = value

    # https://wiki.openstreetmap.org/wiki/NaPTAN/Tag_mappings
    if naptan_code := row['NaptanCode'].strip():
        tags['ref'] = naptan_code

    # taken from NaPTAN rather than surveyed; a mapper removes it after checking the stop
    tags['naptan:verified'] = 'no'

    return NaptanStop(
        atcoCode=row['ATCOCode'].strip(),
        name=name,
        indicator=indicator,
        latLng=(float(row['Latitude']), float(row['Longitude'])),
        tags=tags,
    )


def build_database(csv_path: Path, db_path: Path) -> int:
    """Load the bus stops from a NaPTAN CSV export, replacing the database only on success."""
    tmp_path = db_path.with_name(f'{db_path.name}.{os.getpid()}.tmp')
    tmp_path.unlink(missing_ok=True)

    db = sqlite3.connect(tmp_path)
    try:
        db.execute(
            'CREATE TABLE stops (atco TEXT PRIMARY KEY, lat REAL NOT NULL, lon REAL NOT NULL, tags TEXT NOT NULL)'
        )
        # bus stops NaPTAN has retired, to spot routes still calling at them
        db.execute('CREATE TABLE inactive (atco TEXT PRIMARY KEY)')

        with csv_path.open(newline='', encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                if stop := parse_row(row):
                    db.execute(
                        'INSERT OR REPLACE INTO stops VALUES (?, ?, ?, ?)',
                        (stop.atcoCode, stop.latLng[0], stop.latLng[1], orjson.dumps(stop.tags)),
                    )
                elif row['StopType'] in _BUS_STOP_TYPES and row['Status'] == 'inactive':
                    db.execute('INSERT OR IGNORE INTO inactive VALUES (?)', (row['ATCOCode'].strip(),))

        db.execute('CREATE INDEX stops_lat_lon ON stops (lat, lon)')
        db.execute(f'PRAGMA user_version = {DATA_VERSION}')
        db.commit()
        (count,) = db.execute('SELECT COUNT(*) FROM stops').fetchone()
    finally:
        db.close()

    # a truncated or changed-format download must not replace data that works
    if not count:
        tmp_path.unlink()
        raise ValueError('The NaPTAN download contained no bus stops')

    tmp_path.replace(db_path)
    return count


@dataclass(frozen=True, slots=True)
class StopMatches:
    # NaPTAN stops that no OSM stop represents
    unmapped: list[NaptanStop]
    # OSM stops matched to a NaPTAN stop but missing some of its tags
    tag_suggestions: list[NaptanTagSuggestion]


def _suggest_tags(collection: FetchRelationBusStopCollection, naptan_stop: NaptanStop) -> NaptanTagSuggestion | None:
    platform = collection.platform

    # NaPTAN's tags belong on the platform; a lone stop position is left alone
    if platform is None:
        return None

    # a stop carrying some other code is not NaPTAN's to fill in from this match
    if collection.atco_codes - {naptan_stop.atcoCode}:
        return None

    tags = missing_tags(platform.tags, naptan_stop.tags)
    if not tags:
        return None

    return NaptanTagSuggestion(type=platform.type, id=platform.id, atcoCode=naptan_stop.atcoCode, tags=tags)


def find_unmapped_stops(
    naptan_stops: Sequence[NaptanStop],
    bus_stop_collections: Sequence[FetchRelationBusStopCollection],
) -> list[NaptanStop]:
    """The NaPTAN stops that no OSM stop already represents."""
    return match_stops(naptan_stops, bus_stop_collections).unmapped


def match_stops(
    naptan_stops: Sequence[NaptanStop],
    bus_stop_collections: Sequence[FetchRelationBusStopCollection],
) -> StopMatches:
    naptan_by_code = {stop.atcoCode: stop for stop in naptan_stops}
    mapped_codes: set[str] = set()
    # whether each OSM stop already stands for a NaPTAN stop through its code
    coded: list[bool] = []
    tag_suggestions: list[NaptanTagSuggestion] = []

    for collection in bus_stop_collections:
        # a code NaPTAN no longer lists says nothing about which stop this is, so the
        # stop is matched as though it had no code
        live_codes = collection.atco_codes & naptan_by_code.keys()
        mapped_codes |= live_codes
        coded.append(bool(live_codes))

        if len(live_codes) == 1 and (suggestion := _suggest_tags(collection, naptan_by_code[next(iter(live_codes))])):
            tag_suggestions.append(suggestion)

    candidates = [stop for stop in naptan_stops if stop.atcoCode not in mapped_codes]
    if not candidates or not bus_stop_collections:
        return StopMatches(candidates, tag_suggestions)

    tree = BallTree([radians_tuple(c.best.latLng) for c in bus_stop_collections], metric='haversine')
    nearby = tree.query_radius(
        [radians_tuple(stop.latLng) for stop in candidates],
        r=radians(MATCH_DISTANCE / 111_111),
    )

    def comparable(name: str) -> str:
        return normalize_name(name, lower=True, special=True, whitespace=True)

    pairs: list[tuple[float, int, int]] = []

    for i, (stop, indices) in enumerate(zip(candidates, nearby, strict=True)):
        stop_name = comparable(stop.name)
        stop_ref = stop.tags.get('local_ref', '').upper()

        for j in indices:
            collection = bus_stop_collections[j]
            osm_ref = collection.best.tags.get('local_ref', '').strip().upper()

            # a group of stops sharing a name is told apart by their letters
            if stop_ref and osm_ref and stop_ref != osm_ref:
                continue

            # A stop matched by code only takes on a second record for the same letter,
            # which NaPTAN sometimes has; otherwise it would hide a missing neighbour.
            if coded[j] and not (stop_ref and stop_ref == osm_ref):
                continue

            osm_name = comparable(collection.best.tags.get('name', ''))

            if token_ratio(stop_name, osm_name) < MATCH_NAME_SCORE:
                continue

            pairs.append((haversine_distance(stop.latLng, collection.best.latLng), i, j))

    # Stops come in same-named pairs either side of the road. Each OSM stop stands for
    # one of them, closest first, so a missing stop is not hidden by its mapped twin.
    matched_candidates: set[int] = set()
    matched_collections: set[int] = set()
    pairs_per_candidate = Counter(i for _, i, _ in pairs)
    pairs_per_collection = Counter(j for _, _, j in pairs)

    for _, i, j in sorted(pairs):
        if i in matched_candidates or j in matched_collections:
            continue
        matched_candidates.add(i)
        matched_collections.add(j)

        if coded[j]:
            continue

        stop = candidates[i]
        collection = bus_stop_collections[j]
        stop_ref = stop.tags.get('local_ref', '').upper()
        osm_ref = collection.best.tags.get('local_ref', '').strip().upper()

        # NaPTAN positions are too rough to tell same-named twins apart by distance, so
        # codes are only copied from a match the stop letters confirm, or that no other
        # pairing competes with
        certain = (stop_ref and stop_ref == osm_ref) or (pairs_per_candidate[i] == 1 and pairs_per_collection[j] == 1)

        if certain and (suggestion := _suggest_tags(collection, stop)):
            tag_suggestions.append(suggestion)

    return StopMatches([stop for i, stop in enumerate(candidates) if i not in matched_candidates], tag_suggestions)


class NaptanStore:
    def __init__(self, data_dir: Path):
        self._data_dir = data_dir
        self._db_path = data_dir / 'naptan.sqlite'

    def is_stale(self) -> bool:
        try:
            if time.time() - self._db_path.stat().st_mtime > NAPTAN_MAX_AGE:
                return True
        except FileNotFoundError:
            return True

        db = sqlite3.connect(f'file:{self._db_path}?mode=ro', uri=True)
        try:
            (version,) = db.execute('PRAGMA user_version').fetchone()
        finally:
            db.close()

        return version < DATA_VERSION

    async def keep_fresh(self) -> None:
        while True:
            if self.is_stale():
                try:
                    await self.refresh()
                except Exception as e:
                    # the previous download, if any, keeps being served
                    print(f'[NAPTAN] ⚠️ Refresh failed: {e!r}')

            await asyncio.sleep(3600)

    async def refresh(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)

        # each gunicorn worker runs this loop; only one of them needs to download
        with (self._data_dir / 'naptan.lock').open('w') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return

            if not self.is_stale():
                return

            csv_path = self._data_dir / f'naptan.{os.getpid()}.csv'
            print('[NAPTAN] Downloading NaPTAN')

            try:
                async with HTTP.stream('GET', NAPTAN_URL, timeout=300) as r:
                    r.raise_for_status()
                    with csv_path.open('wb') as f:
                        async for chunk in r.aiter_bytes():
                            f.write(chunk)

                count = await asyncio.to_thread(build_database, csv_path, self._db_path)
            finally:
                csv_path.unlink(missing_ok=True)

            print(f'[NAPTAN] Loaded {count} bus stops')

    def stops_within(self, bbs: Iterable[BoundingBox]) -> list[NaptanStop]:
        if not self._db_path.exists():
            return []

        result: dict[str, NaptanStop] = {}
        db = sqlite3.connect(f'file:{self._db_path}?mode=ro', uri=True)

        try:
            for bb in bbs:
                for atco, lat, lon, tags_json in db.execute(
                    'SELECT atco, lat, lon, tags FROM stops WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?',
                    (bb.minlat, bb.maxlat, bb.minlon, bb.maxlon),
                ):
                    tags = orjson.loads(tags_json)
                    result[atco] = NaptanStop(
                        atcoCode=atco,
                        name=tags['name'],
                        indicator=tags.get('naptan:Indicator', ''),
                        latLng=(lat, lon),
                        tags=tags,
                    )
        finally:
            db.close()

        return list(result.values())

    async def match(
        self,
        download_hist: DownloadHistory,
        bus_stop_collections: Sequence[FetchRelationBusStopCollection],
    ) -> StopMatches:
        cells = tuple(set(chain.from_iterable(download_hist.history)))
        if not cells:
            return StopMatches([], [])

        bbs, _ = optimize_cells_and_get_bbs(cells, start_horizontal=True)
        naptan_stops = await asyncio.to_thread(self.stops_within, bbs)
        return match_stops(naptan_stops, bus_stop_collections)

    def inactive_codes(self, codes: Iterable[str]) -> frozenset[str]:
        codes = tuple(set(codes))
        if not codes or not self._db_path.exists():
            return frozenset()

        db = sqlite3.connect(f'file:{self._db_path}?mode=ro', uri=True)
        try:
            placeholders = ','.join('?' * len(codes))
            rows = db.execute(f'SELECT atco FROM inactive WHERE atco IN ({placeholders})', codes)  # noqa: S608
            return frozenset(atco for (atco,) in rows)
        except sqlite3.OperationalError:
            # built before inactive stops were recorded; replaced by the next refresh
            return frozenset()
        finally:
            db.close()

    async def find_inactive(self, bus_stop_collections: Sequence[FetchRelationBusStopCollection]) -> frozenset[str]:
        codes = list(chain.from_iterable(collection.atco_codes for collection in bus_stop_collections))
        return await asyncio.to_thread(self.inactive_codes, codes)


NAPTAN = NaptanStore(NAPTAN_DATA_DIR)
