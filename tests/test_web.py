"""Tests for app/web.py — Flask routes wired with fake services."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.config import Config
from app.models import Poi, RouteResult
from app.services.nominatim import NominatimError
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
    return {
        "state_store": state_store,
        "nominatim": nominatim,
        "osrm": osrm,
    }


@pytest.fixture()
def app(deps):
    config = Config()
    app = create_app(
        config=config,
        state_store=deps["state_store"],
        nominatim_client=deps["nominatim"],
        osrm_client=deps["osrm"],
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


def test_index_no_query_does_not_call_nominatim(client, deps):
    client.get("/")
    assert deps["nominatim"].calls == []


def test_index_with_query_calls_nominatim_and_merges(client, deps):
    poi = _make_poi("p1")
    deps["nominatim"].results = [poi]

    resp = client.get("/?q=cafe")
    assert resp.status_code == 200
    assert len(deps["nominatim"].calls) == 1
    assert deps["nominatim"].calls[0][0] == "cafe"

    # State should now contain that POI
    state = deps["state_store"].load()
    assert any(p.id == "p1" for p in state.pois)


def test_index_empty_results_shows_info_message(client, deps):
    deps["nominatim"].results = []
    resp = client.get("/?q=nothing")
    assert resp.status_code == 200
    assert "沒有找到可用 POI".encode("utf-8") in resp.data


def test_index_nominatim_error_flashes(client, deps):
    deps["nominatim"].error = NominatimError("backend down")
    resp = client.get("/?q=x", follow_redirects=False)
    assert resp.status_code == 200
    # Should NOT have persisted anything; state remains a fresh new game
    state = deps["state_store"].load()
    assert state.pois == []


def test_index_search_result_reflects_real_owner_not_nominatim_owner(client, deps):
    """Regression: a re-searched POI already owned by P2 must show as owned in the UI.

    merge_discovered_pois() correctly preserves the stored owner, but the previous
    template rendered the fresh Nominatim objects (owner=None) and showed a
    flag button. The view must rebuild search_results from state.
    """
    # Pre-seed: P2 already owns "p1"
    state = new_game()
    state.pois = [_make_poi("p1", owner=2)]
    deps["state_store"].save(state)

    # Nominatim returns a fresh copy of p1 with owner=None (typical)
    fresh_p1 = _make_poi("p1", owner=None)
    deps["nominatim"].results = [fresh_p1]

    resp = client.get("/?q=p1")
    assert resp.status_code == 200
    # Must surface the existing P2 ownership label
    assert "Player 2".encode("utf-8") in resp.data
    # Must NOT offer the "插旗" submit button for this owned POI
    # (The disabled "不可插旗" button uses different text)
    assert "不可插旗".encode("utf-8") in resp.data

    # State sanity: owner unchanged
    after = deps["state_store"].load()
    assert after.get_poi("p1").owner == 2


def test_index_query_does_not_advance_turn(client, deps):
    deps["nominatim"].results = [_make_poi("p1")]
    client.get("/?q=cafe")
    state = deps["state_store"].load()
    assert state.turn_index == 0


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

def test_can_use_trump_false_when_only_flipped_pois(client, deps):
    """Player owns POIs (flipped) but has placed no flags → can_use_trump=False."""
    state = new_game()
    # POI owned by P1 but no MoveRecord placing it → not an anchor
    state.pois = [_make_poi("flipped", owner=1)]
    deps["state_store"].save(state)

    resp = client.get("/")
    assert resp.status_code == 200
    # When can_use_trump is False there is no "使用王牌" checkbox label rendered.
    assert "使用王牌".encode("utf-8") not in resp.data


def test_can_use_trump_true_when_has_anchor(client, deps):
    """Player placed a flag → anchor exists → trump UI should be available."""
    state = new_game()
    # Build a POI placed by P1 and still owned by P1
    state.pois = [_make_poi("anchor", owner=1), _make_poi("other", lat=25.05, lon=121.57)]
    # Record the placement
    from app.models import MoveRecord
    state.moves.append(MoveRecord(
        turn_index=0, player_id=1, placed_poi_id="anchor",
        used_trump=False, route_ids=[], flipped_poi_ids=[],
    ))
    state.turn_index = 2  # back to P1's turn (even)
    deps["state_store"].save(state)

    # Search a query that returns the "other" neutral POI so a flag form renders
    deps["nominatim"].results = [state.get_poi("other")]
    resp = client.get("/?q=anything")
    assert resp.status_code == 200
    assert "使用王牌".encode("utf-8") in resp.data


# ---------------------------------------------------------------------------
# DI defaults — create_app() with no args still works
# ---------------------------------------------------------------------------

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
