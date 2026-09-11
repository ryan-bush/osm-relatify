import os
import secrets

import sentry_sdk
from dotenv import load_dotenv
from githead import githead

# the nix shell sources .env itself, but nothing else does; without this, running
# outside of it silently leaves OSM_CLIENT and friends unset. Real environment
# variables win, so the nix shell keeps behaving exactly as before.
load_dotenv()

try:
    VERSION = 'git#' + githead()[:7]
except OSError:
    # inside a git worktree .git is a file rather than a directory, which githead cannot read
    VERSION = 'git#unknown'
WEBSITE = os.getenv('WEBSITE', 'https://github.com/ryan-bush/osm-relatify')
CREATED_BY = f'osm-relatify {VERSION}'
USER_AGENT = f'osm-relatify/{VERSION} (+{WEBSITE})'

TEST_ENV = os.getenv('TEST_ENV', '0').strip().lower() in ('1', 'true', 'yes')
if TEST_ENV:
    print('[CONF] Running in test environment')

# Dedicated instance unavailable? Pick one from the public list:
# https://wiki.openstreetmap.org/wiki/Overpass_API#Public_Overpass_API_instances
# Multiple comma-separated endpoints may be given; they are tried in order whenever
# the preceding one is unreachable or overloaded. Only worldwide instances are
# suitable here - a regional extract (overpass.osm.ch, overpass.osm.jp, ...) silently
# answers with no data outside of its own area.
OVERPASS_API_INTERPRETER = os.getenv(
    'OVERPASS_API_INTERPRETER',
    'https://overpass-api.de/api/interpreter,'
    'https://overpass.kumi.systems/api/interpreter,'
    'https://overpass.private.coffee/api/interpreter',
)
OVERPASS_API_INTERPRETERS = tuple(u.strip() for u in OVERPASS_API_INTERPRETER.split(',') if u.strip())
assert OVERPASS_API_INTERPRETERS, 'OVERPASS_API_INTERPRETER must contain at least one URL'

# Number of attempts per endpoint before moving on to the next one
OVERPASS_API_ATTEMPTS = int(os.getenv('OVERPASS_API_ATTEMPTS', '2'))

TAG_MAX_LENGTH = 255

# Tags the application interprets when loading a relation; editing them would change
# whether the relation can be loaded at all. See get_route_type() in main.py.
PROTECTED_TAG_KEYS = frozenset(
    {
        'type',
        'route',
        'disused:route',
        'was:route',
        'public_transport:version',
    }
)

_LIVE_OSM_URL = 'https://www.openstreetmap.org'
_LIVE_OSM_API_URL = 'https://api.openstreetmap.org'


def resolve_osm_urls(osm_url: str | None, osm_api_url: str | None) -> tuple[str, str]:
    """
    Work out the website and API hosts of the OSM instance to sign in to and upload to.

    Live OSM serves its API from a separate host, but other instances (the dev server at
    master.apis.dev.openstreetmap.org) serve both from one, so setting only the website
    is enough there.
    """
    web = (osm_url or _LIVE_OSM_URL).rstrip('/')
    if osm_api_url:
        api = osm_api_url.rstrip('/')
    elif web == _LIVE_OSM_URL:
        api = _LIVE_OSM_API_URL
    else:
        api = web
    return web, api


# Map data always comes from Overpass, which only indexes live OSM. Pointing this at the
# dev server is for testing uploads: creating relations works, but editing an existing
# relation or way fails, as the ids Overpass returns do not exist there.
OSM_URL, OSM_API_URL = resolve_osm_urls(os.getenv('OSM_URL'), os.getenv('OSM_API_URL'))
OSM_IS_LIVE = OSM_URL == _LIVE_OSM_URL

if not OSM_IS_LIVE:
    print(f'[CONF] Signing in and uploading to {OSM_URL}, not live OpenStreetMap')

OSM_CLIENT = os.getenv('OSM_CLIENT', None)
OSM_SECRET = os.getenv('OSM_SECRET', None)
OSM_SCOPES = 'read_prefs write_api'

if not OSM_CLIENT or not OSM_SECRET:
    print(
        '🚧 Warning: '
        'Environment variables OSM_CLIENT and/or OSM_SECRET are not set. '
        'You will not be able to authenticate with OpenStreetMap.'
    )

CALC_ROUTE_MAX_REQUESTS = 3
CALC_ROUTE_N_PROCESSES = max(1, os.process_cpu_count() // 4)
CALC_ROUTE_MAX_PROCESSES = CALC_ROUTE_MAX_REQUESTS * CALC_ROUTE_N_PROCESSES

CHANGESET_ID_PLACEHOLDER = f'__CHANGESET_ID_PLACEHOLDER__{secrets.token_urlsafe(8)}__'

DOWNLOAD_RELATION_WAY_BB_EXPAND = 250  # meters
DOWNLOAD_RELATION_GRID_SIZE = 0.01  # degrees
DOWNLOAD_RELATION_GRID_CELL_EXPAND = 0.001  # degrees, only used for internal calculations, not sent to the user

print(f'[CONF] {DOWNLOAD_RELATION_GRID_SIZE * 111_111 = :.0f} meters')
print(f'[CONF] {DOWNLOAD_RELATION_GRID_CELL_EXPAND * 111_111 = :.0f} meters')

BUS_COLLECTION_SEARCH_AREA = 50  # meters

assert DOWNLOAD_RELATION_GRID_CELL_EXPAND * 111_111 > BUS_COLLECTION_SEARCH_AREA * 2

if SENTRY_DSN := os.getenv('SENTRY_DSN'):
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        release=VERSION,
        enable_tracing=True,
        traces_sample_rate=0.3,
        trace_propagation_targets=None,
        profiles_sample_rate=0.2,
    )
