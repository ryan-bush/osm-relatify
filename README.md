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
| `NAPTAN_ENABLED` | [Suggests and checks stops using NaPTAN](#stops-from-naptan). On by default; set to `0` to turn off. |
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

### Viewing a stop's tags

Right-click a bus stop and choose **Tags** to see every tag it carries, rather than
only the handful the editor shows. Where the stop has both a platform and a stop
position, each is listed separately under its own element id. The list is read-only;
tags are changed in OSM itself, or through **Add NaPTAN tags** below.

The new stop form has the same list under **All tags**, showing what the upload will
actually write — including `highway=bus_stop`, `public_transport=platform` and
`bus=yes`, which are added on upload rather than typed in — and the stop position's
tags when one is being added. It updates as you type, so you can check a stop before
creating it.

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

A stop must have a name: the map data leaves out unnamed platforms, so it would
disappear the next time the route loads. Overpass also lags OSM by a few minutes, so a
new stop may not show when reloading straight after upload. There is still no
`stop_area` relation.

### Stop positions on the road

PTv2 puts the platform beside the road and a `public_transport=stop_position` node on
the road itself, where the bus actually halts. This fork can add one for a stop you are
creating and for a stop that has been in OSM all along.

For a new stop, tick **Also mark where the bus halts, on the road** in the form. For a
stop already in OSM that has no stop position, right-click it and choose **Add stop
position**; you get the exact tags to confirm before anything is added. Either way the
stop is projected onto the nearest member way and the node goes into that way between
the two nodes it falls between, shown as a ringed dot joined to the platform by a
dashed line, and joins the relation with role `stop`.

The node is created and the road way modified by the same changeset as everything else.
On upload the way is fetched again and the node is inserted between the two neighbours
the editor saw; if they are no longer next to each other, someone has edited the road
since, and the upload stops as a conflict rather than putting the node in the wrong
place. Dragging a new stop moves its stop position with it, and the same button or
checkbox takes it back out.

A stop position is not offered when no member way is within 30 m, or before the route
has any ways, since there is then nothing to put the node on. It is only offered for a
stop the route actually calls at, as the node joins the relation along with its stop;
taking a stop out of the route takes its stop position with it. New nodes are kept at
least half a metre from the way's existing nodes, so a stop level with one never lands
on top of it.

Ways come from the download cut up at every intersection, and the upload joins them
back together. Where the route uses only part of a road, that road really is split on
upload and its nodes rebuilt, so a stop position cannot go on it — such a road is
skipped when choosing where to put the node.

### Stops from NaPTAN

In Great Britain, stops listed in [NaPTAN](https://www.data.gov.uk/dataset/ff93ffc1-6656-47d8-9155-85ea0b8f2251/naptan)
but missing from OSM can be shown near the route as grey markers with a dashed ring.
Click one to add it: the form is filled in with the name, `local_ref` when the NaPTAN
indicator is a stop letter or stand number, `ref` from the NaPTAN code as
[the tag mappings](https://wiki.openstreetmap.org/wiki/NaPTAN/Tag_mappings) describe, the
`naptan:AtcoCode`, `naptan:NaptanCode`, `naptan:CommonName`, `naptan:Indicator`,
`naptan:Street` and `naptan:Bearing` tags, and `naptan:verified=no` to mark it as taken
from NaPTAN rather than surveyed. From there it is a new stop like any other, and the changeset
gets `source=NaPTAN`.

NaPTAN positions are often tens of metres out, so drag each stop to where the pole
actually is. Stops are added one at a time, on purpose: adding them in bulk would be an
import, which needs agreeing with the OSM community first.

A NaPTAN stop counts as already mapped when an OSM stop has its `naptan:AtcoCode`, or
has a similar name within 80 m. Each OSM stop accounts for one NaPTAN stop, so a
missing stop is still shown when its namesake across the road is mapped. Where both
have a `local_ref` it must agree, so a missing Stop M5 is not hidden by a mapped M3.
An OSM code NaPTAN no longer lists is ignored, and an OSM stop matched by its code also
covers a second NaPTAN record with the same letter, as NaPTAN occasionally has.
Hail-and-ride, flexible and unmarked stops are left out, as they have no pole.

This is on by default: the server downloads the national dataset, about 100 MB, in the
background on startup and again once a day, into `data/`. Suggestions appear once the
first download has finished. NaPTAN only covers Great Britain, so elsewhere set
`NAPTAN_ENABLED=0` to skip the download.

A stop already in OSM that NaPTAN has more tags for says so in its tooltip. Right-click
it and choose **Add NaPTAN tags** to see them and add them; the stop glows blue until
upload, and the same button takes them back out. Only `ref`, `local_ref` and the
`naptan:*` tags above are offered, never `name` or `naptan:verified`, and only ones the
stop lacks. On upload each stop is fetched again, tags it has gained since are skipped,
and a tag that has been given a different value stops the upload as a conflict. Tags
come only from a match that is certain: the stop's own `naptan:AtcoCode`, or a similar
name with nothing else competing for the pairing or with the same `local_ref`. Adding
tags counts as a change, so it can be uploaded even when the route itself is unchanged.

Two route warnings use NaPTAN as well. Both are low severity, so neither blocks
uploading.

- **Some stops are inactive in NaPTAN** lists stops on the route whose `naptan:AtcoCode`
  NaPTAN has marked inactive, which usually means the stop was taken out of use. It
  is skipped when `NAPTAN_ENABLED=0`.
- **Some stops serve the other direction** compares a stop's `naptan:Bearing` with the
  way the route passes it, which catches the stop across the road being picked. It only
  reads the tag, so it works without the download.

### Stop areas

A [`public_transport=stop_area`](https://wiki.openstreetmap.org/wiki/Tag:public_transport%3Dstop_area)
relation groups the stops of one place: for an ordinary bus stop, the platform either
side of the road and the stop position that serves each. The editor already works out
which stops belong together — same name, same place — so it can offer the relation.

Right-click a stop and choose **Stop area**. You get the stops it would group, each with
the role it takes (`platform` or `stop`), and a button to queue it. It is created by the
same changeset as the route, tagged `type=public_transport`, `public_transport=stop_area`
and `name`, taken from the stops' own name without the stop letter.

Stops the user has just added are grouped too, so a stop added from NaPTAN goes into the
area along with the one across the road that was already mapped.

Where you have accepted a NaPTAN rename for a stop in the same session, the stop area and
any stop position take the name the stop is about to have, not the one being replaced, so
everything in one changeset agrees.

Where the stops are already in a stop area, the editor says so and offers to add the ones
it is missing — a newly created stop position, say — instead of making a second relation.
The relation keeps the name it has. If the stops of one group sit in *different* stop
areas, nothing is offered: choosing between them is not the editor's call. On upload the
relation is fetched again, so a member added in the meantime is not added twice, and one
that has stopped being a stop area stops the upload as a conflict.

Existing stop areas are looked up through Overpass when the route is downloaded. If that
lookup fails the download still works; the editor simply does not know about them, so
check before creating one.

A stop area queued against a group is dropped if a later download changes which stops are
in that group, rather than being uploaded against a set you never saw.

This is derived from the stops themselves, not from NaPTAN. NaPTAN does define StopAreas,
but the feed this fork downloads does not carry them, and only about 28% of GB bus stops
are in one — so `naptan:StopAreaCode` is not set.

### When NaPTAN and OSM disagree

Filling in tags only ever adds what a stop lacks. Where the stop and NaPTAN hold
*different* values for the same tag — a stop NaPTAN has renamed, a changed indicator or
bearing — that is a judgement call, so the editor puts it in front of you instead.

Such a stop glows red and its right-click menu has **NaPTAN differs**, listing each tag
with both values side by side. Pick the one that is right: taking NaPTAN's value writes
it on upload, replacing what is there; keeping the stop's value changes nothing in OSM
and simply records that you looked. The list stays open as you work through it, counting
down what is left, so a stop with several disagreements is settled in one go; close it
when you are done, and you can change an answer at any time before uploading.

**Until every disagreement on a stop the route calls at has been decided, the route
cannot be uploaded** — the submit button stays hidden behind a warning, the same as any
other high-severity problem. Stops the route does not call at are left out of this, as
you are not editing them.

`name` is included here even though it is never filled in automatically, since a
renamed stop is the disagreement most worth seeing. On upload each replaced tag is
checked against the value you were shown; if someone has changed it since, the upload
stops as a conflict rather than overwriting their work. A decision is remembered against
the value NaPTAN gave at the time, so if the NaPTAN record itself changes you are asked
again.

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
- ✅ Suggesting missing stops from NaPTAN (optional, Great Britain)
- ✅ Stop positions on the road, for new and existing stops
- ✅ Reviewing tags NaPTAN and OSM disagree on
- ✅ `stop_area` relations for grouped stops


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
