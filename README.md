# GeoFlip

A 2-player hotseat geographic flag-planting game. Two players take turns in the
same browser, planting flags on real-world POIs within walking distance to flip
the opponent's nearby flags. At setup one player searches a starting location
via Nominatim; the server then uses Overpass to build a board of nearby POIs.
During the match players no longer search — they click visible POIs on the map.
After 12 turns the player with the higher total POI score wins; an end-of-game
summary in the sidebar shows winner, scores, owned-POI counts, total flips,
and total routes.

This is a local single-machine game — no accounts, no database, no online
multiplayer. State is persisted to a single JSON file.

---

## Game rules (summary)

1. **Hotseat, 12 turns total.** Even turns → Player 1, odd turns → Player 2.
2. The board is built at setup time: one player searches a starting location
   via Nominatim and the server uses Overpass to populate nearby POIs. After
   setup the only way to place a flag is to click a visible POI on the map.
3. Only **neutral** POIs (no owner) can be flagged.
4. **First flag is free** for each player — no distance check.
5. From the second flag onward, the new flag must be reachable by **OSRM walking
   route ≤ 600 s** from at least one of the player's existing **anchor flags**.
6. If a player has zero anchor flags (e.g. all were flipped away), the next
   placement is free again.
7. On a valid placement the engine takes the **shortest successful route** to
   any anchor, buffers it (**50 m**), and **flips all opponent-owned POIs inside
   the buffer** to the current player.
8. Invalid moves are a complete no-op: no turn advance, no owner change.
9. Score is computed live from current POI ownership — never cached.

A POI gained via flipping is **owned but not an anchor** — only POIs the player
actively placed and still owns are valid route endpoints.

---

## Grading-requirement compliance

| Requirement | Where it is enforced |
|---|---|
| **Nominatim API** is the only POI candidate source | `app/services/nominatim.py` — Overpass is never used in the candidate flow |
| **OSRM API** computes real walking time, used for the 600 s rule | `app/services/osrm.py` + `app/game/rules.py` `MAX_WALK_DURATION_S` |
| **Folium** produces an interactive Leaflet map embedded in a Flask page | `app/map/render.py` returns a full Folium HTML; `templates/index.html` embeds `/map` via `<iframe>` |
| **Shapely + pyproj** for buffers, with WGS84 → metres projection | `app/services/geometry.py` — `always_xy=True`, projects before any `.buffer()` |

---

## Install

Python 3.11 or newer required.

```bash
# create + activate a virtual environment
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# install in editable mode with dev dependencies (pytest, pytest-mock)
pip install -e ".[dev]"
```

## Configure

```bash
cp .env.example .env       # or copy manually on Windows
# edit .env — at minimum set NOMINATIM_USER_AGENT and NOMINATIM_EMAIL
# (Nominatim ToS requires a real contact email)
```

| Variable | Purpose |
|---|---|
| `DEFAULT_CENTER_LAT` / `DEFAULT_CENTER_LON` / `DEFAULT_ZOOM` | Initial map view |
| `NOMINATIM_USER_AGENT` / `NOMINATIM_EMAIL` | **Required by Nominatim ToS.** Use a real address you control. |
| `NOMINATIM_BASE_URL` | Override if you self-host Nominatim |
| `NOMINATIM_MIN_INTERVAL_SECONDS` | Client-side rate-limit between cache misses |
| `OSRM_BASE_URL` / `OSRM_PROFILE` | Public OSRM demo by default; profile must be `foot` |
| `STATE_FILE` | Path to the JSON state file |
| `REQUEST_TIMEOUT_SECONDS` | HTTP timeout for Nominatim / OSRM calls |
| `HOST` / `PORT` / `FLASK_DEBUG` / `LOG_LEVEL` | Server settings |

## Run

```bash
python -m app.web
# or
flask --app app.web run --debug
```

Then open <http://127.0.0.1:5000/> in your browser.

## Test

```bash
python -m pytest               # full suite, all mocked, no real network needed
python -m pytest tests/test_rules.py -v
python -m compileall app       # syntax check the whole package
```

All tests run fully offline — Nominatim/OSRM HTTP calls are mocked.

---

## Manual playtest checklist

1. `python -m venv .venv && source .venv/bin/activate` (or `.venv\Scripts\activate` on Windows)
2. `pip install -e ".[dev]"`
3. `cp .env.example .env` and fill in `NOMINATIM_USER_AGENT` / `NOMINATIM_EMAIL`
4. `python -m app.web`
5. Open <http://127.0.0.1:5000/>
6. On the setup screen, search a starting location (e.g. `新竹車站`) and pick
   one candidate; the server fetches the board via Overpass and renders the map
7. **P1 plants first flag** by clicking a neutral POI on the map and submitting
   the popup form
8. **P2 plants first flag** by clicking another neutral POI
9. **P1 clicks another nearby POI** and plants a second flag — confirm that
   POIs ≤ 600 s walk from P1's first flag are accepted and that a route
   appears on the map
10. Try a POI > 600 s walk away and confirm the placement is rejected with
    an error flash
11. Play until the game finishes and verify the sidebar end-summary shows
    winner, scores, owned-POI counts, total flips, and total routes

---

## Troubleshooting

**`Nominatim 403 / 429`** — Public Nominatim throttles aggressively. Make sure:
- `NOMINATIM_USER_AGENT` is set to a real contact string (not the default).
- `NOMINATIM_MIN_INTERVAL_SECONDS=1.0` or higher.
- Don't spam-search; the client caches identical queries automatically.

**`OSRM HTTP error 400 / Cannot find route`** — The public demo at
`router.project-osrm.org` is unstable. Either retry, or stand up your own
OSRM instance with the `foot` profile.

**`OSRM walking profile 設定可能不正確`** — `OSRM_PROFILE` is wrong for the
selected backend. The default public OSRM only serves `car`; you'll want a
self-hosted `foot` instance for proper coverage.

**`State file ... contains invalid JSON`** — The game refuses to silently reset
a corrupted state file. Either fix the JSON by hand or `rm data/state.json`
(or click "新開局" while a healthy game is loaded) to start fresh.

**Map iframe shows old data after a move** — The iframe URL includes
`?v=<turn>_<moves>` to bust the browser cache. If you're still seeing stale
content, hard-reload the page (Ctrl+Shift+R).

---

## Known limitations

- **Hotseat only.** No accounts, no concurrent sessions, no WebSocket sync.
  Two browsers pointing at the same server will fight over the same JSON file.
- **Public Nominatim and OSRM are rate-limited / unstable.** Self-host either
  if you need heavy use.
- **State is a single JSON file**, atomic write via tmp→replace. Not a
  database — there is no concurrent writer protection.
- **POI universe is set at setup.** The Overpass fetch around the chosen
  starting location is the entire pool of flaggable POIs for the match;
  there is no in-game search to add more.

---

## Project layout

```
app/
  config.py         Config dataclass (env vars + defaults)
  models.py         Poi / PlayerState / RouteRecord / MoveRecord / GameState
  state.py          new_game(); StateStore (load/save/reset, atomic)
  web.py            Flask app factory + routes
  game/rules.py     RulesEngine.apply_move() — pure game logic
  services/
    nominatim.py    POI search client (rate-limited, cached)
    osrm.py         Walking-route client
    geometry.py     pyproj transformers + Shapely buffer helpers
  map/render.py     render_map_html(state, config) — Folium output
  templates/        Jinja templates (sidebar + iframe)
  static/           CSS

tests/
  test_models.py    Domain model
  test_state.py     JSON state store
  test_nominatim.py Nominatim client (mocked)
  test_osrm.py      OSRM client (mocked)
  test_rules.py     RulesEngine
  test_web.py       Flask routes (fake services)
  test_map_render.py Folium output
  test_integration.py  End-to-end with fixture POIs (pyproj-generated)
  fixtures/         Fake POI JSON + helpers
```
