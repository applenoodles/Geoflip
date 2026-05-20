"""Tests for app/web.py — Flask routes wired with fake services."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.config import Config
from app.models import Poi, RouteResult
from app.services.nominatim import LocationCandidate, NominatimError
from app.services.overpass import OverpassError
from app.state import StateStore, new_game
from app.web import create_app


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

@dataclass
class FakeNominatim:
    """Returns canned POIs and records calls. Raises NominatimError if `error` set."""
    results: list[Poi] = field(default_factory=list)
    error: Exception | None = None
    calls: list[tuple] = field(default_factory=list)
    locations: list[LocationCandidate] = field(default_factory=list)
    location_error: Exception | None = None
    location_calls: list[tuple] = field(default_factory=list)

    def search(
        self,
        query: str,
        center_lat: float | None = None,
        center_lon: float | None = None,
        search_km: float | None = None,
        limit: int = 10,
    ) -> list[Poi]:
        self.calls.append((query, center_lat, center_lon, search_km, limit))
        if self.error is not None:
            raise self.error
        return list(self.results)

    def search_locations(
        self,
        query: str,
        limit: int = 5,
    ) -> list[LocationCandidate]:
        self.location_calls.append((query, limit))
        if self.location_error is not None:
            raise self.location_error
        return list(self.locations)


@dataclass
class FakeOverpass:
    """Returns canned board POIs. Raises OverpassError if `error` set."""
    pois: list[Poi] = field(default_factory=list)
    error: Exception | None = None
    calls: list[tuple] = field(default_factory=list)

    def fetch_board_pois(
        self,
        center_lat: float,
        center_lon: float,
        radius_m: float,
        limit: int = 60,
    ) -> list[Poi]:
        self.calls.append((center_lat, center_lon, radius_m, limit))
        if self.error is not None:
            raise self.error
        return list(self.pois)


@dataclass
class FakeOsrm:
    """Returns canned RouteResults keyed by rounded (from_lat, from_lon, to_lat, to_lon)."""
    routes: dict = field(default_factory=dict)
    calls: list[tuple] = field(default_factory=list)

    def route(self, from_lat, from_lon, to_lat, to_lon) -> RouteResult:
        self.calls.append((from_lat, from_lon, to_lat, to_lon))
        key = (round(from_lat, 6), round(from_lon, 6), round(to_lat, 6), round(to_lon, 6))
        if key in self.routes:
            return self.routes[key]
        raise RuntimeError(f"unexpected route call {key}")


def _make_poi(poi_id: str, lat: float = 25.0330, lon: float = 121.5654, **kw) -> Poi:
    defaults = dict(
        name=f"POI {poi_id}",
        osm_type="node",
        osm_id=abs(hash(poi_id)) & 0xFFFFFFFF,
        category="amenity",
        poi_type="cafe",
        score=2,
        owner=None,
        discovered_turn=None,
        placed_turn=None,
        raw={},
    )
    defaults.update(kw)
    return Poi(id=poi_id, lat=lat, lon=lon, **defaults)


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def deps(tmp_path):
    state_store = StateStore(tmp_path / "state.json")
    nominatim = FakeNominatim()
    osrm = FakeOsrm()
    overpass = FakeOverpass()
    config = Config()
    # Setup tests tune these thresholds without juggling env vars.
    config.OVERPASS_MIN_POIS = 2
    config.OVERPASS_MAX_POIS = 36
    return {
        "state_store": state_store,
        "nominatim": nominatim,
        "osrm": osrm,
        "overpass": overpass,
        "config": config,
    }


@pytest.fixture()
def app(deps):
    app = create_app(
        config=deps["config"],
        state_store=deps["state_store"],
        nominatim_client=deps["nominatim"],
        osrm_client=deps["osrm"],
        overpass_client=deps["overpass"],
    )
    app.config["TESTING"] = True
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

def test_index_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"GeoFlip" in resp.data


def test_index_does_not_call_nominatim(client, deps):
    """The main game screen no longer has a POI search box."""
    state = new_game()
    state.pois = [_make_poi("p1")]
    deps["state_store"].save(state)

    client.get("/")
    assert deps["nominatim"].calls == []
    assert deps["nominatim"].location_calls == []


def test_index_has_no_gameplay_search_form(client, deps):
    """No GET-form to /, no in-game search box, no 搜尋 POI label."""
    state = new_game()
    state.pois = [_make_poi("p1")]
    deps["state_store"].save(state)

    resp = client.get("/")
    assert resp.status_code == 200
    # The old gameplay POI search field used name="q" inside a GET form
    # pointing back at /. After this change it must be gone.
    assert b'name="q"' not in resp.data
    assert "搜尋 POI".encode("utf-8") not in resp.data
    # The setup-search form must NOT leak onto the game screen either.
    assert b'action="/setup/search"' not in resp.data


# ---------------------------------------------------------------------------
# Flash auto-dismiss (BUG-A: success banner never disappears)
# ---------------------------------------------------------------------------

def test_index_has_flash_auto_dismiss_script(client, deps):
    """The auto-dismiss script must be present on the game page."""
    state = new_game()
    state.pois = [_make_poi("p1")]
    deps["state_store"].save(state)

    resp = client.get("/")
    assert resp.status_code == 200
    assert b"data-auto-dismiss" in resp.data
    assert b"flash--dismissing" in resp.data


def test_success_flash_is_marked_for_auto_dismiss(client, deps):
    """A successful flag placement flashes success WITH the auto-dismiss marker."""
    state = new_game()
    state.pois = [_make_poi("p1")]
    deps["state_store"].save(state)

    resp = client.post("/move", data={"poi_id": "p1"}, follow_redirects=True)
    assert resp.status_code == 200
    # Success flash carries the auto-dismiss attribute.
    assert b'class="flash success" data-auto-dismiss' in resp.data


def test_error_flash_is_not_auto_dismissed(client, deps):
    """An error flash must NOT carry the auto-dismiss marker so it persists."""
    state = new_game()
    state.pois = [_make_poi("p1")]
    deps["state_store"].save(state)

    # Missing poi_id → error flash, but board stays so the game page renders it.
    resp = client.post("/move", data={}, follow_redirects=True)
    assert resp.status_code == 200
    assert b'class="flash error"' in resp.data
    assert b'class="flash error" data-auto-dismiss' not in resp.data


# ---------------------------------------------------------------------------
# POST /move — success
# ---------------------------------------------------------------------------

def test_move_first_flag_success_saves_state(client, deps):
    # Pre-seed the state with one POI directly
    state = new_game()
    state.pois = [_make_poi("p1")]
    deps["state_store"].save(state)

    resp = client.post("/move", data={"poi_id": "p1"}, follow_redirects=False)
    assert resp.status_code == 302  # redirect to /

    saved = deps["state_store"].load()
    assert saved.turn_index == 1
    assert saved.get_poi("p1").owner == 1
    assert len(saved.moves) == 1


def test_move_redirects_to_index(client, deps):
    state = new_game()
    state.pois = [_make_poi("p1")]
    deps["state_store"].save(state)

    resp = client.post("/move", data={"poi_id": "p1"})
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")


# ---------------------------------------------------------------------------
# POST /move — invalid (no save)
# ---------------------------------------------------------------------------

def test_move_invalid_does_not_save_mutated_state(client, deps):
    state = new_game()
    state.pois = [_make_poi("p1", owner=2)]  # already owned → invalid to flag
    deps["state_store"].save(state)
    pre = deps["state_store"].load()

    resp = client.post("/move", data={"poi_id": "p1"}, follow_redirects=False)
    assert resp.status_code == 302

    post = deps["state_store"].load()
    assert post.turn_index == pre.turn_index
    assert post.get_poi("p1").owner == 2
    assert len(post.moves) == 0


def test_move_invalid_poi_id_does_not_advance(client, deps):
    state = new_game()
    state.pois = [_make_poi("p1")]
    deps["state_store"].save(state)
    client.post("/move", data={"poi_id": "ghost"})
    after = deps["state_store"].load()
    assert after.turn_index == 0


def test_move_missing_poi_id_redirects_with_flash(client, deps):
    resp = client.post("/move", data={})
    assert resp.status_code == 302


def test_move_does_not_accept_latlon_from_form(client, deps):
    """Server must only honour poi_id — any lat/lon in form is ignored."""
    state = new_game()
    state.pois = [_make_poi("p1", lat=25.0330, lon=121.5654)]
    deps["state_store"].save(state)

    # Submit a different lat/lon that should be totally ignored
    client.post("/move", data={
        "poi_id": "p1",
        "lat": "0.0",
        "lon": "0.0",
    })

    after = deps["state_store"].load()
    # The actual POI's lat/lon must be unchanged
    p = after.get_poi("p1")
    assert p.lat == 25.0330
    assert p.lon == 121.5654


# ---------------------------------------------------------------------------
# POST /new-game
# ---------------------------------------------------------------------------

def test_new_game_resets_state(client, deps):
    state = new_game()
    state.pois = [_make_poi("p1", owner=1)]
    state.turn_index = 5
    deps["state_store"].save(state)

    resp = client.post("/new-game")
    assert resp.status_code == 302

    fresh = deps["state_store"].load()
    assert fresh.turn_index == 0
    assert fresh.pois == []


# ---------------------------------------------------------------------------
# GET /map (stub for now)
# ---------------------------------------------------------------------------

def test_map_returns_html(client):
    resp = client.get("/map")
    assert resp.status_code == 200
    assert b"<html" in resp.data.lower() or b"<!doctype" in resp.data.lower()


def test_map_view_includes_view_persistence_js(client, deps):
    """The served /map page must carry the sessionStorage view-restore JS."""
    state = new_game()
    state.pois = [_make_poi("p1")]
    deps["state_store"].save(state)

    resp = client.get("/map")
    assert resp.status_code == 200
    assert b"sessionStorage" in resp.data
    assert b"setView" in resp.data


# ---------------------------------------------------------------------------
# GET /api/state
# ---------------------------------------------------------------------------

def test_api_state_returns_json(client, deps):
    state = new_game()
    state.pois = [_make_poi("p1")]
    deps["state_store"].save(state)

    resp = client.get("/api/state")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "active"
    assert data["turn_index"] == 0
    assert any(p["id"] == "p1" for p in data["pois"])


def test_api_state_fresh_when_no_file(client, deps):
    resp = client.get("/api/state")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["turn_index"] == 0
    assert data["status"] == "active"


# ---------------------------------------------------------------------------
# Trump UI gating — flipped-only player must not get trump option
# ---------------------------------------------------------------------------

def test_sidebar_has_no_flag_form_or_trump_checkbox(client, deps):
    """Trump and flag UI live in map popups now, never in the sidebar HTML."""
    state = new_game()
    state.pois = [_make_poi("anchor", owner=1), _make_poi("other", lat=25.05, lon=121.57)]
    from app.models import MoveRecord
    state.moves.append(MoveRecord(
        turn_index=0, player_id=1, placed_poi_id="anchor",
        used_trump=False, route_ids=[], flipped_poi_ids=[],
    ))
    state.turn_index = 2
    deps["state_store"].save(state)

    resp = client.get("/")
    assert resp.status_code == 200
    # No sidebar flag-placement form, no trump checkbox in the sidebar HTML.
    assert b'name="poi_id"' not in resp.data
    assert "使用王牌".encode("utf-8") not in resp.data


# ---------------------------------------------------------------------------
# DI defaults — create_app() with no args still works
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Setup flow — GET /
# ---------------------------------------------------------------------------

def test_index_empty_state_renders_setup_form(client, deps):
    """Fresh game with no POIs and no query → setup screen, not game UI."""
    resp = client.get("/")
    assert resp.status_code == 200
    # Setup form posts to /setup/search
    assert b'action="/setup/search"' in resp.data
    # And it is NOT the game iframe map
    assert b'<iframe' not in resp.data


def test_index_with_board_renders_game(client, deps):
    """State with POIs → game UI (iframe map), not setup."""
    state = new_game()
    state.pois = [_make_poi("p1")]
    deps["state_store"].save(state)

    resp = client.get("/")
    assert resp.status_code == 200
    assert b'<iframe' in resp.data
    assert b'action="/setup/search"' not in resp.data


def test_index_setup_mode_does_not_call_nominatim(client, deps):
    client.get("/")
    assert deps["nominatim"].calls == []
    assert deps["nominatim"].location_calls == []


# ---------------------------------------------------------------------------
# POST /setup/search
# ---------------------------------------------------------------------------

def test_setup_search_calls_nominatim_search_locations(client, deps):
    deps["nominatim"].locations = [
        LocationCandidate(
            display_name="新竹車站, 新竹市",
            lat=24.8019,
            lon=120.9716,
            osm_type="node",
            osm_id=42,
            category="railway",
            poi_type="station",
        ),
    ]
    resp = client.post("/setup/search", data={"q": "新竹車站"})
    assert resp.status_code == 200
    assert deps["nominatim"].location_calls == [("新竹車站", 5)]
    # Should NOT touch the in-game POI search.
    assert deps["nominatim"].calls == []
    # Candidate must be rendered with a "start with this location" form.
    assert b'action="/setup/start"' in resp.data
    assert "新竹車站".encode("utf-8") in resp.data


def test_setup_search_empty_query_flashes_and_returns_setup(client, deps):
    resp = client.post("/setup/search", data={"q": "   "})
    # No redirect — re-render setup directly with the flash visible.
    assert resp.status_code == 200
    assert deps["nominatim"].location_calls == []
    assert "請輸入起始地點關鍵字".encode("utf-8") in resp.data
    assert b'action="/setup/search"' in resp.data


def test_setup_search_no_results_renders_info_message(client, deps):
    deps["nominatim"].locations = []
    resp = client.post("/setup/search", data={"q": "asdfqwerty"})
    assert resp.status_code == 200
    assert "找不到符合的地點".encode("utf-8") in resp.data
    # No candidate "start" form should appear.
    assert b'action="/setup/start"' not in resp.data


def test_setup_search_nominatim_error_flashes(client, deps):
    deps["nominatim"].location_error = NominatimError("nominatim 503")
    resp = client.post("/setup/search", data={"q": "anywhere"})
    assert resp.status_code == 200
    assert "搜尋失敗".encode("utf-8") in resp.data
    # Form must still be re-rendered so the player can retry.
    assert b'action="/setup/search"' in resp.data


# ---------------------------------------------------------------------------
# POST /setup/start
# ---------------------------------------------------------------------------

def _board_pois(n: int) -> list[Poi]:
    return [
        _make_poi(f"b{i}", lat=25.0 + i * 0.0001, lon=121.5 + i * 0.0001)
        for i in range(n)
    ]


def test_setup_start_fetches_overpass_and_populates_state(client, deps):
    deps["overpass"].pois = _board_pois(5)

    resp = client.post(
        "/setup/start",
        data={
            "lat": "24.8019",
            "lon": "120.9716",
            "display_name": "新竹車站",
            "radius_m": "900",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    assert len(deps["overpass"].calls) == 1
    call = deps["overpass"].calls[0]
    assert call[0] == 24.8019
    assert call[1] == 120.9716
    assert call[2] == 900.0
    assert call[3] == deps["config"].OVERPASS_MAX_POIS

    state = deps["state_store"].load()
    assert len(state.pois) == 5
    assert state.turn_index == 0
    assert state.status == "active"


def test_setup_start_default_radius_when_missing(client, deps):
    deps["overpass"].pois = _board_pois(3)
    client.post(
        "/setup/start",
        data={"lat": "24.8", "lon": "120.97", "display_name": "X"},
    )
    assert deps["overpass"].calls[0][2] == deps["config"].OVERPASS_RADIUS_M


def test_setup_start_invalid_coords_flashes_and_does_not_call_overpass(client, deps):
    resp = client.post(
        "/setup/start",
        data={"lat": "not-a-number", "lon": "abc"},
    )
    assert resp.status_code == 302
    assert deps["overpass"].calls == []
    state = deps["state_store"].load()
    assert state.pois == []


def test_setup_start_too_few_pois_keeps_state_empty(client, deps):
    deps["config"].OVERPASS_MIN_POIS = 5
    deps["overpass"].pois = _board_pois(2)  # < 5

    resp = client.post(
        "/setup/start",
        data={"lat": "24.8", "lon": "120.97", "display_name": "X"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    state = deps["state_store"].load()
    assert state.pois == []  # nothing saved


def test_setup_start_overpass_error_flashes_and_keeps_state_empty(client, deps):
    deps["overpass"].error = OverpassError("overpass down")
    resp = client.post(
        "/setup/start",
        data={"lat": "24.8", "lon": "120.97", "display_name": "X"},
    )
    assert resp.status_code == 302
    state = deps["state_store"].load()
    assert state.pois == []


def test_setup_start_resets_previous_state(client, deps):
    """A successful setup must wipe a prior game before building the new board."""
    prev = new_game()
    prev.pois = [_make_poi("old1", owner=2)]
    prev.turn_index = 7
    deps["state_store"].save(prev)

    deps["overpass"].pois = _board_pois(4)
    client.post(
        "/setup/start",
        data={"lat": "24.8", "lon": "120.97", "display_name": "X"},
    )

    state = deps["state_store"].load()
    # Old POI gone, fresh board in place, turn_index reset.
    assert state.get_poi("old1") is None
    assert len(state.pois) == 4
    assert state.turn_index == 0
    assert state.game_id != prev.game_id


def test_setup_start_skipped_does_not_advance_anything(client, deps):
    """Invalid setup_start must never mutate state."""
    deps["overpass"].error = OverpassError("nope")
    pre = deps["state_store"].load()
    client.post(
        "/setup/start",
        data={"lat": "1.0", "lon": "2.0", "display_name": "x"},
    )
    post = deps["state_store"].load()
    # Both fresh new_games — pois empty, turn 0.
    assert pre.pois == []
    assert post.pois == []
    assert post.turn_index == 0


# ---------------------------------------------------------------------------
# /new-game returns to setup
# ---------------------------------------------------------------------------

def test_new_game_redirects_and_landing_page_is_setup(client, deps):
    """After /new-game, the next GET / must render the setup screen."""
    state = new_game()
    state.pois = [_make_poi("p1", owner=1)]
    deps["state_store"].save(state)

    resp = client.post("/new-game", follow_redirects=True)
    assert resp.status_code == 200
    assert b'action="/setup/search"' in resp.data
    assert b'<iframe' not in resp.data


# ---------------------------------------------------------------------------
# End-of-game summary v1
# ---------------------------------------------------------------------------

def _finished_state_with_history():
    """Build a finished GameState with a known mix of moves/routes/flips.

    P1 placed two flags (one with trump), flipping 3 of P2's POIs.
    P2 placed one flag, no flips. Two RouteRecords on the board.
    """
    from app.models import MoveRecord, RouteRecord

    state = new_game()
    # 1 P1-placed + 1 P1-flipped (still P1 since end-of-game)
    # 1 P2-placed (their only POI)
    # Plus 2 neutral leftovers to round out totals.
    state.pois = [
        _make_poi("p1_anchor", owner=1, lat=25.01, lon=121.51),
        _make_poi("p1_grabbed", owner=1, lat=25.02, lon=121.52),
        _make_poi("p2_anchor", owner=2, lat=25.03, lon=121.53),
        _make_poi("neutral_a", owner=None, lat=25.04, lon=121.54),
        _make_poi("neutral_b", owner=None, lat=25.05, lon=121.55),
    ]
    state.moves = [
        MoveRecord(
            turn_index=0, player_id=1, placed_poi_id="p1_anchor",
            used_trump=False, route_ids=[], flipped_poi_ids=[],
        ),
        MoveRecord(
            turn_index=1, player_id=2, placed_poi_id="p2_anchor",
            used_trump=False, route_ids=["r1"], flipped_poi_ids=[],
        ),
        MoveRecord(
            turn_index=2, player_id=1, placed_poi_id="p1_grabbed",
            used_trump=True, route_ids=["r2"],
            flipped_poi_ids=["f1", "f2", "f3"],
        ),
    ]
    state.routes = [
        RouteRecord(
            id="r1", turn_index=1, player_id=2,
            from_poi_id="p2_anchor", to_poi_id="p1_anchor",
            coordinates_lonlat=[[121.51, 25.01], [121.53, 25.03]],
            distance_m=100.0, duration_s=120.0, buffer_m=50.0,
        ),
        RouteRecord(
            id="r2", turn_index=2, player_id=1,
            from_poi_id="p1_anchor", to_poi_id="p1_grabbed",
            coordinates_lonlat=[[121.51, 25.01], [121.52, 25.02]],
            distance_m=80.0, duration_s=90.0, buffer_m=150.0,
        ),
    ]
    state.players[1].trump_available = False  # P1 spent trump
    state.status = "finished"
    return state


def test_summary_absent_when_game_active(client, deps):
    state = new_game()
    state.pois = [_make_poi("p1")]
    deps["state_store"].save(state)

    resp = client.get("/")
    assert resp.status_code == 200
    assert "對局總結".encode("utf-8") not in resp.data


def test_summary_shows_winner_and_scores_when_finished(client, deps):
    state = _finished_state_with_history()
    deps["state_store"].save(state)

    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.data
    # Header
    assert "對局總結".encode("utf-8") in body
    # Winner = P1 (owns p1_anchor + p1_grabbed; P2 owns just p2_anchor)
    assert "Player 1 勝利".encode("utf-8") in body
    # Final scores line
    assert "最終分數".encode("utf-8") in body


def test_summary_shows_owned_counts_flips_routes_and_trump(client, deps):
    state = _finished_state_with_history()
    deps["state_store"].save(state)

    resp = client.get("/")
    body = resp.data
    # Owned counts: P1=2, P2=1
    assert "P1 擁有 POI：2 個".encode("utf-8") in body
    assert "P2 擁有 POI：1 個".encode("utf-8") in body
    # Total flips = 0 + 0 + 3 = 3
    assert "總翻面次數：3".encode("utf-8") in body
    # Total routes = 2
    assert "總路線數：2".encode("utf-8") in body
    # Trump usage — P1 used, P2 did not
    assert "P1 王牌：已使用".encode("utf-8") in body
    assert "P2 王牌：未使用".encode("utf-8") in body


def test_summary_shows_tie_when_scores_equal(client, deps):
    from app.models import MoveRecord

    state = new_game()
    state.pois = [
        _make_poi("a", owner=1, lat=25.01, lon=121.51),
        _make_poi("b", owner=2, lat=25.02, lon=121.52),
    ]
    state.moves = [
        MoveRecord(turn_index=0, player_id=1, placed_poi_id="a",
                   used_trump=False, route_ids=[], flipped_poi_ids=[]),
        MoveRecord(turn_index=1, player_id=2, placed_poi_id="b",
                   used_trump=False, route_ids=[], flipped_poi_ids=[]),
    ]
    state.status = "finished"
    deps["state_store"].save(state)

    resp = client.get("/")
    body = resp.data
    assert "對局總結".encode("utf-8") in body
    assert "平手".encode("utf-8") in body
    assert "Player 1 勝利".encode("utf-8") not in body
    assert "Player 2 勝利".encode("utf-8") not in body


def test_create_app_no_args_works(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
    # Patch Config to pick up the env var
    from importlib import reload
    import app.config as cfg_mod
    reload(cfg_mod)
    from app.web import create_app as ca
    app = ca()
    assert app is not None
    with app.test_client() as c:
        assert c.get("/health").status_code == 200
