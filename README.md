# <img height="24" src="https://github.com/Zaczero/osm-relatify/blob/main/static/img/favicon/256.webp?raw=true" alt="🗺️"> OSM Relatify

![Python version](https://shields.monicz.dev/badge/python-v3.13-blue)

OpenStreetMap public transport made easy.

<img width="60%" src="https://github.com/Zaczero/osm-relatify/blob/main/resources/application-preview.png?raw=true" alt="Application preview">

## About this fork

This is a fork of [Zaczero/osm-relatify](https://github.com/Zaczero/osm-relatify)
by Kamil Monicz, whose work the whole application is built on.

Upstream development has stalled — the last commit there is from January 2026 —
while a handful of things stayed on the roadmap that make everyday route editing
noticeably easier. Rather than let those sit, this fork carries them: tag editing,
custom changeset comments, U-turns at untagged turning points, and creating relations
from scratch. See [what this fork adds](#what-this-fork-adds) below.

The original remains the reference implementation, and the official instance at
[relatify.monicz.dev](https://relatify.monicz.dev) but has issues with Overpass Timeouts. If you find this tool useful, please
[support the original author](https://liberapay.com/Zaczero/).

### Is there a hosted instance?

Not yet. For now this fork is run locally — see [running it locally](#running-it-locally),
which takes a few minutes.

If enough people would rather click a link than run a server,
[open an issue](https://github.com/ryan-bush/osm-relatify/issues) and say so.
Hosting a public instance is straightforward and I am happy to do it once there is
demand for it.

## Running it locally

You need [uv](https://docs.astral.sh/uv/) and Python 3.13.

**1. Register an OAuth 2 application** at
[openstreetmap.org/oauth2/applications](https://www.openstreetmap.org/oauth2/applications):

- Redirect URI: `http://localhost:8000/callback`
- Permissions: **Read user preferences** (`read_prefs`) and
  **Modify the map** (`write_api`)

**2. Save the credentials.** Copy `.env.example` to `.env` and fill in the client id
and secret it gave you:

```sh
cp .env.example .env
```

**3. Start it.**

```sh
uv sync
.venv/bin/python -m uvicorn main:app --reload
```

Then open <http://localhost:8000> and sign in with your OSM account.

On startup you should *not* see a warning about `OSM_CLIENT` being unset. If you do,
`.env` is not being picked up — check it sits next to `main.py`.

<details>
<summary>Using nix instead</summary>

With [nix](https://nixos.org) available, `nix-shell` builds the virtualenv, loads
`.env` and puts the helper scripts on your path. `run` then starts the server under
gunicorn, and `cython-build` compiles the routing hot paths, which is a large speedup
over the pure-Python fallback.

</details>

### Optional configuration

Everything below has a working default and can be set in `.env`:

| Variable | Purpose |
| --- | --- |
| `OVERPASS_API_INTERPRETER` | Comma-separated Overpass endpoints, tried in order. |
| `OVERPASS_API_ATTEMPTS` | Attempts per endpoint before moving to the next one. |
| `WEBSITE` | The URL recorded in changesets and the user agent. |
| `SENTRY_DSN` | Enables error reporting, off unless set. |
| `OSM_URL` | The OSM instance to sign in to and upload to. Defaults to live OSM. |
| `OSM_API_URL` | Its API host, if different. Only live OSM needs this, and it is set for you. |
| `STOP_AREA_SEARCH_AREA` | How far apart the stops of one place can be, for stop areas. Metres; defaults to 150. |
| `NAPTAN_ENABLED` | Suggests and checks stops using NaPTAN. On by default; set to `0` to turn off. |
| `NAPTAN_DATA_DIR` | Where the NaPTAN download is kept. Defaults to `data`. |

### Running the tests

```sh
.venv/bin/python -m pytest
```

The browser-side geometry has its own tests, which need only node:

```sh
node --test "tests/js/*.mjs"
```

### Testing uploads against the OSM dev server

Set `OSM_URL=https://master.apis.dev.openstreetmap.org` and register a separate OAuth
application on that server — it has its own accounts, so sign up there first. A badge
next to your name shows which server you are uploading to.

Map data still comes from Overpass, which only indexes live OSM. Creating a new
relation works, but editing an existing relation, or anything that splits a way, fails:
the ids Overpass returns do not exist on the dev server.

## What this fork adds

There are several additions from the Planned section, plus others that help with QoL and removing the need for multiple editors.

- Editing relation tags
- Custom changeset comments
- U-turns without a turning circle
- Creating new bus relations
- Improved overpass resilience
- NaPTAN support in UK
- Stop Locations & Areas
- Route masters

For detailed guides on new features, see the wiki.

## User documentation

<https://wiki.openstreetmap.org/wiki/Relatify>

The wiki documents the original, and everything there still applies. The additions
this fork makes, listed above, are documented there too.

## Features

### Supported

- ✅ Bus routes
- ✅ Tram routes
- ✅ One-way roads
- ✅ Roundabouts
- ✅ Right-hand traffic
- ✅ `ref` & `local_ref`
- ✅ `roundtrip`
- ✅ `public_transport:version=2`
- ✅ `public_transport=platform`
- ✅ `public_transport=stop_position`
- ✅ `public_transport=stop_area`
- ✅ Left-hand traffic
- ✅ Tag editing
- ✅ Custom changeset comment
- ✅ Creating new relations
- ✅ U-turns without `highway=turning_circle`
- ✅ Loops driven the right way round for left- and right-hand traffic, with per-road direction overrides
- ✅ Creating new bus stops (platforms)
- ✅ Suggesting missing stops from NaPTAN (optional, Great Britain)
- ✅ Stop positions on the road, for new and existing stops
- ✅ Reviewing tags NaPTAN and OSM disagree on
- ✅ `stop_area` relations for grouped stops
- ✅ `route_master` relations: viewing, linking and editing
- ✅ Working through every variant of a line from its `route_master`


### Planned

- ⏳ Relation `type=restriction`
- ⏳ `direction=*`
- ⏳ `oneway=-1`
- ⏳ Trolleybuses, trains, etc.

### Unsupported

- ❌ Exceptionally poor tagging
- ❌ `public_transport:version=1`

## Notes

The application relies on OSM data being (more-or-less) accurately tagged. Incorrect
or poor tagging may need manual correction in an editor like iD or JOSM.

Only **bus** and **tram** relations are supported.
