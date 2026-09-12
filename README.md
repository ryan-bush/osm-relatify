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

## What this fork adds

There are several additions from the Planned section, plus others that help with QoL and removing the need for multiple editors.

- Editing relation tags
- Custom changeset comments
- U-turns without a turning circle
- Creating new bus relations
- Improved overpass resilience
- NaPTAN support in UK
- Stop Locations & Areas

For detailed guides on new features, see the wiki.

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
- ✅ Creating new bus stops
- ✅ NaPTAN data

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
