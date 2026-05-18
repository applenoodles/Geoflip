# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (editable + dev deps)
pip install -e ".[dev]"

# Run all tests
python -m pytest

# Run a single test file
python -m pytest tests/test_models.py -v

# Run a single test by name
python -m pytest tests/test_models.py::test_anchor_pois_excludes_flipped_poi -v

# Syntax-check the whole app package
python -m compileall app

# Start the dev server
python -m app.web          # or: flask --app app.web run --debug
```

## Architecture

GeoFlip is a 2-player hotseat game (same browser, take turns). The backend is a stateless Flask app that reads/writes a single JSON file (`data/state.json`) on every request. There is no database and no session state beyond the file.

### Request flow

```
Browser form POST /move
  → app/web.py  create_app()          # loads state from StateStore
  → app/game/rules.py  RulesEngine    # validates + mutates (deepcopy-then-commit)
  → app/services/osrm.py              # called by RulesEngine via RoutingService protocol
  → app/state.py  StateStore.save()   # atomic tmp→replace write
  → redirect GET /
  → app/map/render.py  render_map_html()  # Folium HTML returned at GET /map (iframe)
```

### Module responsibilities

| Module | Role |
|---|---|
| `app/models.py` | All dataclasses + `score_poi()`. No I/O, no external imports. |
| `app/state.py` | `new_game()` factory; `StateStore` (load/save/reset). Bad JSON raises `ValueError` — never silent-reset. |
| `app/config.py` | `Config` dataclass; all values from env vars with defaults. |
| `app/web.py` | `create_app(config, state_store, nominatim_client, osrm_client, rules_engine)` — all deps injectable for tests. |
| `app/game/rules.py` | `RulesEngine.apply_move()` — pure logic, calls OSRM via a `RoutingService` protocol. |
| `app/services/nominatim.py` | `NominatimClient` — search → `list[Poi]`. In-memory cache + rate limiter. |
| `app/services/osrm.py` | `OsrmClient` — route → `RouteResult`. |
| `app/services/geometry.py` | pyproj projections + Shapely buffer helpers. |
| `app/map/render.py` | `render_map_html(state, config) -> str` — Folium only. |

### Critical invariants (grading requirements — never break)

1. **POI source**: Nominatim API is the only candidate source. Overpass must never replace it.
2. **Route time**: OSRM API calculates real walking time; the 600-second rule uses this value.
3. **Map**: Folium produces the Leaflet map embedded via `<iframe src="/map">`.
4. **`anchor_pois()` is the only valid route endpoint source** — never `owned_pois()`. A flipped POI (gained via buffer) is owned but NOT an anchor.
5. **Invalid move is a no-op**: does not advance `turn_index`, does not consume trump, does not change `owner`, does not write a `RouteRecord`. `apply_move()` deepcopies state before any mutation.
6. **Only the shortest route is used**: among all anchor→candidate routes, take the one with minimum `duration_s`. Only that one gets a `RouteRecord` and drives the buffer.
7. **`scores()` is always live**: calculated from current `poi.owner`, never cached.

### Coordinate conventions

| Context | Order |
|---|---|
| `Poi.lat` / `Poi.lon` fields | stored separately |
| Folium `Marker` / `PolyLine` | `[lat, lon]` |
| OSRM request URL | `lon,lat` |
| OSRM GeoJSON response | `[lon, lat]` |
| `RouteRecord.coordinates_lonlat` | `[lon, lat]` |
| Shapely geometry | `(lon, lat)` — **project to metres before buffering** |
| pyproj `Transformer` | always `always_xy=True` |

### Testing rules

- Tests must **never** call real Nominatim or OSRM — mock all HTTP.
- Use `monkeypatch` to control time in rate-limiter tests (no `sleep`).
- Use pyproj to generate metre-level fixture offsets; never guess distances in degrees.
- `pytest testpaths` is set to `tests/`; no configuration needed beyond `pip install -e ".[dev]"`.

### ID conventions

- `GameState.game_id` = `"game_" + uuid.uuid4().hex`
- `RouteRecord.id` = `"route_" + uuid.uuid4().hex` (created inside `apply_move()` at commit time)

### `Poi.raw` whitelist

Only these fields may be stored in `Poi.raw` (no `geojson`, `boundingbox`, `icon`, full `addressdetails`):
```
display_name, name, class, type, osm_type, osm_id, importance
address.{country_code, city, town, village, suburb, neighbourhood, road, house_number, postcode}
extratags.{website, wikidata, wikipedia, opening_hours, phone}
```
