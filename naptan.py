import asyncio
import csv
import fcntl
import os
import re
import sqlite3
import time
from collections.abc import Iterable, Sequence
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
from models.naptan_stop import NaptanStop
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

        with csv_path.open(newline='', encoding='utf-8-sig') as f:
            stops = filter(None, map(parse_row, csv.DictReader(f)))
            db.executemany(
                'INSERT OR REPLACE INTO stops VALUES (?, ?, ?, ?)',
                ((s.atcoCode, s.latLng[0], s.latLng[1], orjson.dumps(s.tags)) for s in stops),
            )

        db.execute('CREATE INDEX stops_lat_lon ON stops (lat, lon)')
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


def find_unmapped_stops(
    naptan_stops: Sequence[NaptanStop],
    bus_stop_collections: Sequence[FetchRelationBusStopCollection],
) -> list[NaptanStop]:
    """The NaPTAN stops that no OSM stop already represents."""
    active_codes = {stop.atcoCode for stop in naptan_stops}
    mapped_codes: set[str] = set()
    # whether each OSM stop already stands for a NaPTAN stop through its code
    coded: list[bool] = []

    for collection in bus_stop_collections:
        codes = {
            code.strip()
            for stop in (collection.platform, collection.stop)
            if stop is not None
            for code in stop.tags.get('naptan:AtcoCode', '').split(';')
            if code.strip()
        }

        # a code NaPTAN no longer lists says nothing about which stop this is, so the
        # stop is matched as though it had no code
        live_codes = codes & active_codes
        mapped_codes |= live_codes
        coded.append(bool(live_codes))

    candidates = [stop for stop in naptan_stops if stop.atcoCode not in mapped_codes]
    if not candidates or not bus_stop_collections:
        return candidates

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

    for _, i, j in sorted(pairs):
        if i in matched_candidates or j in matched_collections:
            continue
        matched_candidates.add(i)
        matched_collections.add(j)

    return [stop for i, stop in enumerate(candidates) if i not in matched_candidates]


class NaptanStore:
    def __init__(self, data_dir: Path):
        self._data_dir = data_dir
        self._db_path = data_dir / 'naptan.sqlite'

    def is_stale(self) -> bool:
        try:
            return time.time() - self._db_path.stat().st_mtime > NAPTAN_MAX_AGE
        except FileNotFoundError:
            return True

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

    async def find_unmapped(
        self,
        download_hist: DownloadHistory,
        bus_stop_collections: Sequence[FetchRelationBusStopCollection],
    ) -> list[NaptanStop]:
        cells = tuple(set(chain.from_iterable(download_hist.history)))
        if not cells:
            return []

        bbs, _ = optimize_cells_and_get_bbs(cells, start_horizontal=True)
        naptan_stops = await asyncio.to_thread(self.stops_within, bbs)
        return find_unmapped_stops(naptan_stops, bus_stop_collections)


NAPTAN = NaptanStore(NAPTAN_DATA_DIR)
