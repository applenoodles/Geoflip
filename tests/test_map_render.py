"""Tests for app/map/render.py — Folium HTML output (no real network)."""
from __future__ import annotations

import re

import pytest

from app.config import Config
from app.map.render import _build_popup_html, render_map_html
from app.models import MoveRecord, Poi, RouteRecord
from app.state import new_game


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _poi(
    poi_id: str,
    lat: float = 25.0330,
    lon: float = 121.5654,
    *,
    name: str = "Test POI",
    owner=None,
    category: str = "amenity",
    poi_type: str = "cafe",
    score: int = 2,
) -> Poi:
    return Poi(
        id=poi_id,
        name=name,
        lat=lat,
        lon=lon,
        osm_type="node",
        osm_id=1,
        category=category,
        poi_type=poi_type,
        score=score,
        owner=owner,
        discovered_turn=0,
        placed_turn=None,
        raw={},
    )


@pytest.fixture()
def cfg():
    return Config()


# ---------------------------------------------------------------------------
# Smoke: empty state
# ---------------------------------------------------------------------------

def test_render_empty_state(cfg):
    state = new_game()
    html = render_map_html(state, cfg)
    assert isinstance(html, str)
    assert "<html" in html.lower() or "<!doctype" in html.lower()
    # Folium / Leaflet artifacts
    assert "leaflet" in html.lower()


def test_render_uses_default_center_when_no_pois(cfg):
    state = new_game()
    html = render_map_html(state, cfg)
    # Default center should appear in the Folium init
    assert str(cfg.DEFAULT_CENTER_LAT)[:6] in html or f"{cfg.DEFAULT_CENTER_LAT:.4f}" in html


# ---------------------------------------------------------------------------
# POI rendering
# ---------------------------------------------------------------------------

def test_render_includes_poi_name(cfg):
    state = new_game()
    state.pois = [_poi("p1", name="Cool Cafe")]
    html = render_map_html(state, cfg)
    assert "Cool Cafe" in html


def test_render_includes_poi_metadata(cfg):
    state = new_game()
    state.pois = [_poi("p1", name="Cool Cafe", category="amenity", poi_type="cafe", score=2)]
    html = render_map_html(state, cfg)
    assert "amenity" in html
    assert "cafe" in html
    # Score shown somewhere
    assert "2" in html


def test_render_neutral_uses_circle_marker(cfg):
    state = new_game()
    state.pois = [_poi("p1")]  # owner=None
    html = render_map_html(state, cfg)
    assert "circleMarker" in html  # Folium's JS function call


def test_render_placed_flag_uses_marker_not_circle(cfg):
    """A POI that the player actively placed should use folium.Marker (pin icon)."""
    state = new_game()
    state.pois = [_poi("p1", owner=1)]
    state.moves.append(MoveRecord(
        turn_index=0, player_id=1, placed_poi_id="p1",
        used_trump=False, route_ids=[], flipped_poi_ids=[],
    ))
    html = render_map_html(state, cfg)
    # folium.Marker generates "L.marker(" in JS
    assert "L.marker(" in html


def test_render_flipped_poi_uses_circle_not_pin(cfg):
    """A flipped POI (owned but never placed) should be a CircleMarker in owner color."""
    state = new_game()
    state.pois = [_poi("flipped", owner=2)]  # no MoveRecord placing it
    html = render_map_html(state, cfg)
    assert "circleMarker" in html


def test_render_owner_colors_differ(cfg):
    state = new_game()
    state.pois = [
        _poi("a", lat=25.03, lon=121.56, owner=1),
        _poi("b", lat=25.04, lon=121.57, owner=2),
    ]
    html = render_map_html(state, cfg)
    # Player 1 = blue (#1f77ff), Player 2 = red (#ff3344)
    assert "#1f77ff" in html.lower() or "1f77ff" in html.lower()
    assert "#ff3344" in html.lower() or "ff3344" in html.lower()


# ---------------------------------------------------------------------------
# Coordinate flipping (critical)
# ---------------------------------------------------------------------------

def test_render_route_flips_lonlat_to_latlon(cfg):
    """Route stored as [lon, lat] must be emitted as [lat, lon] in Folium JS."""
    state = new_game()
    state.routes.append(RouteRecord(
        id="route_1",
        turn_index=0,
        player_id=1,
        from_poi_id="x",
        to_poi_id="y",
        coordinates_lonlat=[
            [121.5654, 25.0330],
            [121.5700, 25.0400],
        ],
        distance_m=100.0,
        duration_s=80.0,
        buffer_m=50,
    ))
    html = render_map_html(state, cfg)
    # Folium polyline JS: L.polyline([[lat, lon], ...]) — find the array near 25.033
    # The lat 25.x must appear FIRST in each pair, lon 121.x must appear SECOND.
    # Look for the rendered pair "[25.033, 121.5654]" tolerantly.
    matches = re.findall(r"\[\s*25\.0\d+\s*,\s*121\.\d+\s*\]", html)
    assert matches, "Expected [lat, lon] pair with lat=25.0xx and lon=121.xxx in Folium output"
    # And the WRONG order [121.x, 25.x] should NOT appear as the route emission
    wrong = re.findall(r"\[\s*121\.\d+\s*,\s*25\.0\d+\s*\]", html)
    # (some Folium internals might contain bounds; assert at most ≤ matches)
    assert len(wrong) < len(matches) or wrong == [], (
        f"Found [lon, lat] pairs in polyline output: {wrong[:3]}"
    )


def test_render_route_polyline_present(cfg):
    state = new_game()
    state.routes.append(RouteRecord(
        id="route_1", turn_index=0, player_id=1,
        from_poi_id="x", to_poi_id="y",
        coordinates_lonlat=[[121.5, 25.0], [121.51, 25.01]],
        distance_m=10, duration_s=10, buffer_m=50,
    ))
    html = render_map_html(state, cfg)
    assert "polyline" in html.lower()


def test_render_trump_route_styled_differently(cfg):
    state = new_game()
    state.routes.append(RouteRecord(
        id="r_normal", turn_index=0, player_id=1,
        from_poi_id="x", to_poi_id="y",
        coordinates_lonlat=[[121.5, 25.0], [121.51, 25.01]],
        distance_m=10, duration_s=10, buffer_m=50,
    ))
    state.routes.append(RouteRecord(
        id="r_trump", turn_index=1, player_id=2,
        from_poi_id="a", to_poi_id="b",
        coordinates_lonlat=[[121.5, 25.0], [121.52, 25.02]],
        distance_m=20, duration_s=20, buffer_m=150,
    ))
    html = render_map_html(state, cfg)
    # Trump uses dashArray — look for "dash" in the JS options
    assert "dash" in html.lower()


def test_render_skips_short_route_geometry(cfg):
    """A route with fewer than 2 coordinates should be skipped, not crash."""
    state = new_game()
    state.routes.append(RouteRecord(
        id="r_bad", turn_index=0, player_id=1,
        from_poi_id="x", to_poi_id="y",
        coordinates_lonlat=[[121.5, 25.0]],  # only 1 point
        distance_m=0, duration_s=0, buffer_m=50,
    ))
    # Should NOT raise
    html = render_map_html(state, cfg)
    assert "leaflet" in html.lower()


# ---------------------------------------------------------------------------
# Popup HTML escaping (XSS)
# ---------------------------------------------------------------------------

def test_popup_escapes_html(cfg):
    """Hostile POI name must not break out of the popup HTML."""
    state = new_game()
    state.pois = [_poi("p1", name='<script>alert("x")</script>')]
    html = render_map_html(state, cfg)
    # The raw script tag must not appear unescaped
    assert '<script>alert("x")</script>' not in html
    # Escaped form should appear
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# Finished state banner
# ---------------------------------------------------------------------------

def test_finished_state_shows_winner(cfg):
    state = new_game()
    state.status = "finished"
    state.turn_index = state.max_turns
    # P1 owns the only scoring POI
    state.pois = [_poi("p1", owner=1)]
    html = render_map_html(state, cfg)
    assert "Player 1" in html
    assert "勝利" in html


def test_finished_state_tie_shown(cfg):
    state = new_game()
    state.status = "finished"
    state.turn_index = state.max_turns
    # No POIs → tie 0-0
    html = render_map_html(state, cfg)
    assert "平手" in html


# ---------------------------------------------------------------------------
# Render does NOT mutate state
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Popup forms (S5: neutral popups have inline /move form; owned do not)
# ---------------------------------------------------------------------------

def _popup(poi, state, *, current_pid=1, trump_eligible=False, is_finished=False):
    return _build_popup_html(
        poi, state,
        current_pid=current_pid,
        trump_eligible=trump_eligible,
        is_finished=is_finished,
    )


def test_popup_neutral_has_move_form_targeting_top():
    state = new_game()
    poi = _poi("p1")
    html = _popup(poi, state)
    assert 'action="/move"' in html
    assert 'method="post"' in html
    assert 'target="_top"' in html  # map is in an iframe
    assert 'name="poi_id"' in html
    assert 'value="p1"' in html


def test_popup_neutral_does_not_show_trump_when_ineligible():
    state = new_game()
    poi = _poi("p1")
    html = _popup(poi, state, trump_eligible=False)
    assert 'name="use_trump"' not in html
    assert "使用王牌" not in html


def test_popup_neutral_shows_trump_when_eligible():
    state = new_game()
    poi = _poi("p1")
    html = _popup(poi, state, trump_eligible=True)
    assert 'name="use_trump"' in html
    assert "使用王牌" in html


def test_popup_owned_has_no_form():
    """Owned POIs (flipped or anchor) must never offer an insert button."""
    state = new_game()
    poi = _poi("owned", owner=1)
    state.pois = [poi]
    html = _popup(poi, state, current_pid=2, trump_eligible=True)
    assert "<form" not in html
    assert "/move" not in html
    assert "插旗" not in html


def test_popup_finished_state_has_no_form():
    """No insert button after the game ends, even on neutral POIs."""
    state = new_game()
    state.status = "finished"
    state.turn_index = state.max_turns
    poi = _poi("p1")
    html = _popup(poi, state, is_finished=True)
    assert "<form" not in html
    assert "/move" not in html


def test_popup_escapes_poi_id_in_form_value():
    """A hostile POI id must not break out of the hidden input."""
    state = new_game()
    poi = _poi('a"><script>alert(1)</script>')
    html = _popup(poi, state)
    assert '<script>alert(1)</script>' not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# Trump-checkbox gating wired through render_map_html (uses state to derive)
# ---------------------------------------------------------------------------

def test_render_no_trump_checkbox_when_player_has_no_anchor(cfg):
    """Trump available but no anchor → checkbox hidden in popups."""
    state = new_game()
    # P1's turn (turn_index=0), P1 has trump but no placed flag.
    state.pois = [_poi("p1")]
    html = render_map_html(state, cfg)
    assert "使用王牌" not in html


def test_render_no_trump_checkbox_when_trump_unavailable(cfg):
    """Player has anchor but already spent trump → checkbox hidden."""
    state = new_game()
    state.pois = [_poi("anchor", owner=1), _poi("target", lat=25.05, lon=121.57)]
    state.moves.append(MoveRecord(
        turn_index=0, player_id=1, placed_poi_id="anchor",
        used_trump=False, route_ids=[], flipped_poi_ids=[],
    ))
    state.players[1].trump_available = False
    state.turn_index = 2  # back to P1's turn
    html = render_map_html(state, cfg)
    assert "使用王牌" not in html


def test_render_trump_checkbox_when_anchor_and_trump_available(cfg):
    state = new_game()
    state.pois = [_poi("anchor", owner=1), _poi("target", lat=25.05, lon=121.57)]
    state.moves.append(MoveRecord(
        turn_index=0, player_id=1, placed_poi_id="anchor",
        used_trump=False, route_ids=[], flipped_poi_ids=[],
    ))
    state.turn_index = 2
    html = render_map_html(state, cfg)
    # JS-escaped form attribute survives inside Folium's popup string literal.
    assert "使用王牌" in html


# ---------------------------------------------------------------------------
# Route buffer polygons
# ---------------------------------------------------------------------------

def _route(rid="r1", *, buffer_m=50, player_id=1, coords=None):
    coords = coords or [[121.5654, 25.0330], [121.5700, 25.0400]]
    return RouteRecord(
        id=rid, turn_index=0, player_id=player_id,
        from_poi_id="a", to_poi_id="b",
        coordinates_lonlat=coords,
        distance_m=100.0, duration_s=80.0,
        buffer_m=buffer_m,
    )


def test_render_buffer_polygon_emitted_for_route(cfg):
    state = new_game()
    state.routes.append(_route(buffer_m=50))
    html = render_map_html(state, cfg)
    # Folium Polygon renders as L.polygon(...) in JS
    assert "L.polygon(" in html


def test_render_one_buffer_per_route(cfg):
    state = new_game()
    state.routes.append(_route(rid="r1", buffer_m=50))
    state.routes.append(_route(rid="r2", buffer_m=150, player_id=2,
                               coords=[[121.5654, 25.0330], [121.5800, 25.0500]]))
    html = render_map_html(state, cfg)
    assert html.count("L.polygon(") == 2


def test_render_no_buffer_when_no_routes(cfg):
    state = new_game()
    state.pois = [_poi("p1")]
    html = render_map_html(state, cfg)
    assert "L.polygon(" not in html


def test_render_skips_buffer_for_short_route(cfg):
    """A 1-point route can't form a buffer; renderer must not crash."""
    state = new_game()
    state.routes.append(RouteRecord(
        id="r_bad", turn_index=0, player_id=1,
        from_poi_id="x", to_poi_id="y",
        coordinates_lonlat=[[121.5, 25.0]],
        distance_m=0, duration_s=0, buffer_m=50,
    ))
    html = render_map_html(state, cfg)
    assert "L.polygon(" not in html


def test_render_trump_buffer_uses_dash_array(cfg):
    state = new_game()
    state.routes.append(_route(rid="r_trump", buffer_m=150))
    html = render_map_html(state, cfg)
    # The Polygon for a trump buffer must carry a dashArray option.
    # (folium emits it inside the polygon options object.)
    assert "dashArray" in html


def test_render_normal_buffer_solid(cfg):
    """A non-trump (50m) buffer should still draw, no dash mandate."""
    state = new_game()
    state.routes.append(_route(rid="r_norm", buffer_m=50))
    html = render_map_html(state, cfg)
    assert "L.polygon(" in html


def test_render_buffer_uses_player_color(cfg):
    state = new_game()
    state.routes.append(_route(rid="r1", buffer_m=50, player_id=2))
    html = render_map_html(state, cfg)
    # Player 2 buffer should color with red (#ff3344)
    assert "ff3344" in html.lower()


def test_render_draws_buffers_before_markers(cfg):
    """Buffers must be added before markers so markers stay clickable on top.

    Folium injects layer JS in add-order; assert the buffer L.polygon(
    call appears before the first marker (L.marker(/L.circleMarker(
    call in the rendered output.
    """
    state = new_game()
    state.pois = [_poi("anchor", owner=1), _poi("target", lat=25.05, lon=121.57)]
    state.moves.append(MoveRecord(
        turn_index=0, player_id=1, placed_poi_id="anchor",
        used_trump=False, route_ids=[], flipped_poi_ids=[],
    ))
    state.routes.append(_route(buffer_m=50))

    html = render_map_html(state, cfg)
    buffer_at = html.find("L.polygon(")
    marker_at = html.find("L.marker(")
    circle_at = html.find("L.circleMarker(")
    first_marker = min(x for x in (marker_at, circle_at) if x != -1)
    assert buffer_at != -1
    assert first_marker != -1
    assert buffer_at < first_marker


def test_render_does_not_mutate_state(cfg):
    state = new_game()
    state.pois = [_poi("p1", owner=1)]
    state.moves.append(MoveRecord(
        turn_index=0, player_id=1, placed_poi_id="p1",
        used_trump=False, route_ids=[], flipped_poi_ids=[],
    ))
    pre_turn = state.turn_index
    pre_owner = state.pois[0].owner
    pre_moves = len(state.moves)
    pre_pois = len(state.pois)

    render_map_html(state, cfg)

    assert state.turn_index == pre_turn
    assert state.pois[0].owner == pre_owner
    assert len(state.moves) == pre_moves
    assert len(state.pois) == pre_pois
