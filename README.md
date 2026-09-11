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
[relatify.monicz.dev](https://relatify.monicz.dev) but has issued with Overpass. If you find this tool useful, please
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

### Testing uploads against the OSM dev server

Set `OSM_URL=https://master.apis.dev.openstreetmap.org` and register a separate OAuth
application on that server — it has its own accounts, so sign up there first. A badge
next to your name shows which server you are uploading to.

Map data still comes from Overpass, which only indexes live OSM. Creating a new
relation works, but editing an existing relation, or anything that splits a way, fails:
the ids Overpass returns do not exist on the dev server.

## What this fork adds

### Editing relation tags

The edit view shows the relation's tags in a table you can change in place, add to,
and delete from. Previously the only way to fix a `name` or add an `operator` was to
leave for another editor.

Edits are merged rather than overwritten: on upload the relation is fetched fresh and
only the keys you actually touched are applied, so a tag someone else changed while
you were working is left alone. The structural tags that decide whether the app can
load the relation at all — `type`, `route`, `public_transport:version` and the
`disused:`/`was:` variants — are shown but locked.

Changing `roundtrip` re-runs the route calculation, since it changes which stop roles
are valid.

### Custom changeset comments

The submit view has a comment field. Leave it blank and you get the generated comment
as before, shown as the placeholder so you can see what will be used.

### U-turns without a turning circle

The router will only turn a bus around where OSM has `highway=turning_circle`. Plenty
of real routes turn where there is no such tag — a road end, or a corner the bus swings
round — and the way then has nowhere to continue to and drops out as unused.

Right-click a member way near the end where the bus turns and choose **Allow U-turn**.
A purple ring marks the node, and the route can now run in and back out over that way,
which is listed twice in the relation as PTv2 requires. The setting lives in your
browser only: nothing is uploaded, and it survives the incremental downloads that
happen as you pan.

The only end that cannot be turned at is either end of a `oneway`, since coming back
would mean driving it the wrong way.

### Creating new relations

Instead of entering an existing relation id, pan to where the route starts, pick
**Bus** or **Tram**, and click **Create**. The visible area is downloaded and you get
an empty relation to build up.

From there it works like editing an existing one: click the ways the route follows,
right-click to set **START** and **END**, and fill in `name`, `ref`, `from` and `to`
in the tag table. The three structural PTv2 tags are set for you. Panning downloads
more of the map as you go, so you only need to start somewhere sensible rather than
fit the whole route on screen.

Nothing is written to OSM until you upload. The relation is created by that upload,
and its new id is shown as a link when it succeeds.

> **New in this fork and lightly tested.** Try it against the
> [OSM dev server](#testing-uploads-against-the-osm-dev-server) first. On live OSM,
> start with a short, simple route and check the changeset afterwards; the success
> message links a revert tool if you need it.

### Adding bus stops

A stop the route serves that is missing from OSM can be added without leaving the
editor. On a bus or trolleybus route, right-click where the stop is and fill in its
`name`, and optionally `local_ref`, `shelter` and `bench`. Put it beside the road on
the side the bus stops on, as that is how the route calculation decides which
direction serves it.

New stops have a yellow glow. Drag one to move it, or click it to edit or delete it.
If another stop is within 50 m, the form says so, since the two would most likely be
the same stop.

The upload creates each one as a node tagged `highway=bus_stop`,
`public_transport=platform` and `bus=yes` (or `trolleybus=yes`), in the same changeset
as the route, and adds it to the relation.

Only platforms are created, not stop positions on the road, and there is no
`stop_area` relation. A stop must have a name: the map data leaves out unnamed
platforms, so it would disappear the next time the route loads. Overpass also lags
OSM by a few minutes, so a new stop may not show when reloading straight after upload.

### Overpass resilience

`OVERPASS_API_INTERPRETER` accepts several comma-separated endpoints. Each is retried,
then the next is tried, so a single overloaded instance no longer fails the download.
Only worldwide instances work — a regional extract silently returns nothing outside
its own area.

## User documentation

<https://wiki.openstreetmap.org/wiki/Relatify>

The wiki documents the original. Everything there still applies; the additions above
are not covered by it.

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
- ✅ Creating new bus stops (platforms)


### Planned

- ⏳ Stop positions and `stop_area` for new stops
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
