# GeoFlip Board Mode Plan

This document is a scoped implementation plan for changing GeoFlip from
search-driven play into board-driven play. It is intentionally split into small
sections so an agent can read only the relevant section and a few files.

## [S1] Product Decision

Recommended direction: use Nominatim for the player-chosen starting location,
then use Overpass API to build the POI board around that location.

This keeps Nominatim as the primary user-facing search tool: the player starts a
game by searching a recognizable place, station, school, park, district, or
landmark. Overpass is used only after that, as the board-data provider for
nearby POIs. During the actual game, players no longer search POIs; they click
visible POIs on the map.

Why this is better than five default Nominatim searches:

- It makes the board intentional: the player chooses "where this match happens".
- It avoids turning game skill into "who knows more POI names".
- It uses each API for its intended role: Nominatim for geocoding/search,
  Overpass for "give me objects of these types in this area".
- It avoids an empty first map while preserving a simple setup flow.

Non-goals for the first implementation:

- Do not implement live multiplayer.
- Do not implement account state.
- Do not preview future flips before move confirmation yet.
- Do not replace OSRM routing or Shapely flip logic.

## [S2] API Responsibility And Policy Constraints

Nominatim role:

- Search user-entered starting location text.
- Return a small list of location candidates.
- The selected candidate provides center latitude/longitude and display name.
- Keep caching, User-Agent, email, attribution, and low request rate.

Overpass role:

- Given a selected center and radius, fetch a bounded set of POI candidates.
- Query only a curated list of tags that make sense as game targets.
- Cache board fetches by rounded center, radius, and tag profile.
- Keep requests modest; do not stitch large areas or repeatedly scrape.

Recommended defaults:

- Radius: 900 meters.
- Minimum acceptable POIs: 18.
- Target maximum displayed POIs: 36.
- Hard query timeout: 25 seconds.
- Use a configurable `OVERPASS_BASE_URL`.

If Overpass returns too few POIs:

- Show a setup error and let the player choose a different starting point or
  larger radius.
- Do not silently start a bad board.

If Overpass returns too many POIs:

- Filter and rank deterministically to a target max.
- Prefer named POIs, category variety, higher game score, and spatial spread.

## [S3] Setup Flow

New user flow:

1. First visit shows a setup panel instead of an empty game.
2. Player searches a starting location using Nominatim.
3. UI shows 3 to 5 location candidates.
4. Player selects one candidate and starts the match.
5. Server creates a fresh game state.
6. Server fetches Overpass POIs around the selected center.
7. Server merges those POIs into state and saves.
8. Game screen appears with all neutral POIs visible.

Gameplay flow after board creation:

- No POI search box.
- Player clicks a neutral POI popup and submits "insert flag".
- Existing `/move` remains the only state mutation route for moves.
- `/new-game` resets to the setup screen, not directly to a random/default board.

Suggested routes:

- `GET /`:
  - If fresh game with no POIs and no setup candidates, render setup form.
  - If active board exists, render game.
- `POST /setup/search`:
  - Search Nominatim for starting locations.
  - Render setup form with candidate choices.
- `POST /setup/start`:
  - Accept selected lat/lon/display name/radius.
  - Reset state, fetch Overpass board POIs, save state.
- `POST /move`:
  - Existing move route, unchanged except redirect target.
- `POST /new-game`:
  - Reset and redirect to setup screen.

Implementation note: avoid trusting arbitrary POI lat/lon for gameplay. It is
fine for setup to submit selected location coordinates from Nominatim candidates,
but actual moves must still submit only server-known `poi_id`.

## [S4] Overpass Client Design

Add `app/services/overpass.py`.

Public API sketch:

```python
class OverpassError(Exception):
    pass

class OverpassClient:
    def __init__(self, base_url: str, timeout_seconds: float = 25.0) -> None:
        ...

    def fetch_board_pois(
        self,
        center_lat: float,
        center_lon: float,
        radius_m: float,
        limit: int = 60,
    ) -> list[Poi]:
        ...
```

Recommended tag profile:

- `amenity`: cafe, restaurant, fast_food, bar, library, school, college,
  university, hospital, theatre, arts_centre, place_of_worship, marketplace
- `tourism`: museum, attraction, gallery, hotel, hostel, viewpoint, zoo,
  theme_park
- `leisure`: park, garden, playground, sports_centre, stadium
- `shop`: convenience, supermarket, books, mall, department_store, bakery
- `railway`: station, halt
- `public_transport`: station, stop_position, platform
- `historic`: any named historic object

Overpass QL sketch:

```text
[out:json][timeout:25];
(
  nwr["amenity"~"^(cafe|restaurant|fast_food|bar|library|school|college|university|hospital|theatre|arts_centre|place_of_worship|marketplace)$"](around:RADIUS,LAT,LON);
  nwr["tourism"~"^(museum|attraction|gallery|hotel|hostel|viewpoint|zoo|theme_park)$"](around:RADIUS,LAT,LON);
  nwr["leisure"~"^(park|garden|playground|sports_centre|stadium)$"](around:RADIUS,LAT,LON);
  nwr["shop"~"^(convenience|supermarket|books|mall|department_store|bakery)$"](around:RADIUS,LAT,LON);
  nwr["railway"~"^(station|halt)$"](around:RADIUS,LAT,LON);
  nwr["public_transport"~"^(station|stop_position|platform)$"](around:RADIUS,LAT,LON);
  nwr["historic"]["name"](around:RADIUS,LAT,LON);
);
out center tags qt LIMIT;
```

Normalization rules:

- Element id: `node:<id>`, `way:<id>`, or `relation:<id>`.
- Coordinates:
  - For node: use `lat` / `lon`.
  - For way/relation: use `center.lat` / `center.lon`.
- Name priority:
  - `name:zh-TW`, `name:zh`, `name`, then a short fallback label.
- Category / type:
  - Pick the first matching tag from the supported profile.
  - Example: `amenity=cafe` becomes `category="amenity"`,
    `poi_type="cafe"`.
- Score:
  - Reuse `score_poi(category, poi_type)`.
- Raw:
  - Store only useful whitelisted tags, not the entire Overpass object.
- Deduplicate by element id.
- Prefer named POIs. If unnamed fallback POIs are included, cap them heavily.

Tests:

- Normalize node element.
- Normalize way element using `center`.
- Filter unsupported tags.
- Deduplicate ids.
- Raise `OverpassError` on timeout / HTTP / bad JSON.
- Verify query includes `around:radius,lat,lon`, `out center`, and supported tags.

## [S5] Map Interaction And Flip Transparency

Map changes:

- Neutral POI popup contains a small POST form to `/move`.
- Form uses `target="_top"` because the map is inside an iframe.
- Owned POIs do not show an insert button.
- Trump checkbox appears only when current player has an anchor and trump is
  available.

Route buffer transparency:

- Draw a semi-transparent polygon for every past route buffer.
- Draw buffers before markers and routes so POIs remain clickable.
- Normal buffer: 50m, low-opacity player color.
- Trump buffer: 150m, low-opacity player color with dashed outline.

Limit of this phase:

- These polygons explain past flips.
- They do not yet preview a future move. Future preview should be a later,
  separate task because it requires server-side route simulation.

## [S6] Turn Count And End Summary

Recommended first change:

- Change `max_turns` from 16 to 12.

Why 12 instead of 8:

- It reduces repetition but keeps enough time for both players to build multiple
  anchors.
- It is a conservative change for a project already implemented around 16 turns.
- After map-click play is tested, 8 or 10 can be evaluated from playtesting.

End summary v1:

- Winner or tie.
- Final scores.
- Owned POI count per player.
- Total flips.
- Total routes.
- Trump usage by player.

End summary v2, later:

- Per-turn move history.
- For each move: placed POI, connected anchor, route duration, buffer size,
  flipped POI names.

Tests to update:

- `tests/test_models.py`: max-turn expectations.
- `tests/test_rules.py`: finished-after-max-turn loop count.
- `tests/test_integration.py`: full game loop count.
- `tests/test_web.py`: result summary rendering.

## [S7] Suggested Implementation Order

1. Add Overpass service and tests without changing gameplay.
2. Add setup screen and board generation routes.
3. Change map popups to allow direct flag placement.
4. Remove gameplay POI search UI.
5. Add route buffer polygons.
6. Change max turns to 12 and update tests.
7. Add end summary v1.

Do not combine steps 1, 2, and 3 into one edit. Those touch different failure
modes: external data, web flow, and map HTML.

## [S8] Prompt Pack For Agents

Use one prompt at a time. Each prompt names the only plan section and files the
agent should read before editing.

### Prompt 1: Overpass Client Only

Read only `GeoFlip_Board_Mode_Plan.md` sections [S2] and [S4], then read
`app/models.py`, `app/config.py`, `app/services/nominatim.py`, and
`tests/test_nominatim.py`.

Implement `app/services/overpass.py` with an injectable `OverpassClient` and
offline tests in a new `tests/test_overpass.py`. Do not edit Flask routes,
templates, map rendering, game rules, or state. The client should fetch board
POIs around a center coordinate, normalize Overpass node/way/relation elements
into existing `Poi` objects, reuse `score_poi`, filter unsupported tags, and
raise `OverpassError` for network/parse failures. Keep tests fully offline with
mocked HTTP.

Run only:

```powershell
python -m pytest tests/test_overpass.py tests/test_nominatim.py
python -m compileall app
```

### Prompt 2: Setup Flow And Board Creation

Read only `GeoFlip_Board_Mode_Plan.md` sections [S1], [S2], and [S3], then read
`app/web.py`, `app/state.py`, `app/config.py`, `app/templates/index.html`,
`tests/test_web.py`, and `app/services/overpass.py`.

Add the start-location setup flow: `POST /setup/search` uses Nominatim to show
candidate starting locations, `POST /setup/start` resets state and uses
Overpass to populate the board, and `/new-game` returns to setup. Do not change
game rules, route flipping, map rendering internals, or turn count in this
task. If the existing Nominatim client cannot represent location candidates
cleanly, add a small location-candidate method or dataclass with focused tests.

Run only:

```powershell
python -m pytest tests/test_web.py tests/test_overpass.py tests/test_state.py
python -m compileall app
```

### Prompt 3: Map Click Placement And Buffer Polygons

Read only `GeoFlip_Board_Mode_Plan.md` section [S5], then read
`app/map/render.py`, `app/templates/index.html`, `app/static/app.css`,
`tests/test_map_render.py`, and the `/move` handler inside `app/web.py`.

Change the map so neutral POI popups include an inline `/move` form with
`target="_top"`. Owned POIs must not show an insert button. Show the trump
checkbox only when the current player has an anchor and trump is available.
Draw past route buffer polygons on the map. Do not implement setup search,
Overpass fetching, max-turn changes, or end summaries in this task.

Run only:

```powershell
python -m pytest tests/test_map_render.py tests/test_web.py
python -m compileall app
```

### Prompt 4: Remove Gameplay Search UI And Add Summary

Read only `GeoFlip_Board_Mode_Plan.md` sections [S3] and [S6], then read
`app/templates/index.html`, `app/static/app.css`, `app/web.py`,
`tests/test_web.py`, and `README.md`.

Remove the gameplay POI search form from the main game screen, keeping
start-location search only in setup. Add end-summary v1 to the sidebar: winner,
scores, owned POI counts, total flips, total routes, and trump usage. Do not
change Overpass client code, game rules, or map rendering.

Run only:

```powershell
python -m pytest tests/test_web.py
python -m compileall app
```

### Prompt 5: Turn Count To 12

Read only `GeoFlip_Board_Mode_Plan.md` section [S6], then read `app/state.py`,
`tests/test_models.py`, `tests/test_rules.py`, and `tests/test_integration.py`.

Change new games from 16 turns to 12 turns and update only the tests whose
expectations are directly tied to max turns. Do not edit web routes, templates,
map rendering, services, or scoring.

Run only:

```powershell
python -m pytest tests/test_models.py tests/test_rules.py tests/test_integration.py
python -m compileall app
```

### Prompt 6: Final Integration Pass

Read only `GeoFlip_Board_Mode_Plan.md` sections [S1], [S7], and any changelog
notes from previous agents. Then read only files changed by previous agents plus
the failing test output, if any.

Run the full suite, fix integration mismatches, update README minimally, and do
not introduce new product behavior. Preserve unrelated user changes.

Run:

```powershell
python -m pytest
python -m compileall app
```

