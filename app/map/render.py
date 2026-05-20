"""Folium map rendering.

This module ONLY reads `GameState`. It never mutates anything.
All state changes happen via Flask POST handlers — Folium markers do not
trigger Python callbacks in the browser.

Coordinate conventions:
- Folium Markers and PolyLines take `[lat, lon]`.
- `RouteRecord.coordinates_lonlat` stores GeoJSON `[lon, lat]` — must be flipped here.
"""
from __future__ import annotations

from html import escape

import folium
from branca.element import MacroElement
from jinja2 import Template

from app.config import Config
from app.models import GameState, Poi, RouteRecord
from app.services.geometry import (
    build_meter_transformers,
    buffer_route_meters,
    route_to_meter_linestring,
)


# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------

_NEUTRAL_COLOR = "#888888"
_PLAYER_COLORS: dict[int, str] = {
    1: "#1f77ff",  # blue
    2: "#ff3344",  # red
}
_PLAYER_FOLIUM_ICON_COLORS: dict[int, str] = {
    1: "blue",
    2: "red",
}


def _owner_color(owner: int | None) -> str:
    if owner is None:
        return _NEUTRAL_COLOR
    return _PLAYER_COLORS.get(owner, _NEUTRAL_COLOR)


def _active_routes(state: GameState) -> list[RouteRecord]:
    """Routes still worth showing on the board.

    A route (and its buffer) is only rendered while both endpoints still
    belong to the player who drew it. Once either endpoint goes neutral or
    is flipped by the opponent, the route is stale and must disappear —
    line AND buffer together. `state.routes` is read-only here; the history
    is kept for scoring/debug.
    """
    active: list[RouteRecord] = []
    for route in state.routes:
        from_poi = state.get_poi(route.from_poi_id)
        to_poi = state.get_poi(route.to_poi_id)
        if from_poi is None or to_poi is None:
            continue
        if from_poi.owner != route.player_id or to_poi.owner != route.player_id:
            continue
        active.append(route)
    return active


# ---------------------------------------------------------------------------
# Popup builder — HTML escape every dynamic field to prevent XSS
# ---------------------------------------------------------------------------

def _build_popup_html(
    poi: Poi,
    state: GameState,
    *,
    current_pid: int,
    trump_eligible: bool,
    is_finished: bool,
) -> str:
    """Render a popup body for one POI.

    Neutral POIs (owner=None) include an inline POST /move form with
    target="_top" — the map runs inside an iframe and the form must
    navigate the top frame so the player sees the redirected game page.

    Owned POIs (flipped or anchor) never get an insert button.

    The trump checkbox is rendered only when `trump_eligible` is True —
    i.e. when the current player has at least one anchor AND their trump
    is still available. The /move handler ignores the field if the player
    can't actually use it, but hiding it here matches the S5 spec.
    """
    owner_label = "中立" if poi.owner is None else f"Player {poi.owner}"
    is_anchor = False
    if poi.owner is not None:
        is_anchor = any(p.id == poi.id for p in state.anchor_pois(poi.owner))
    anchor_label = "（anchor flag）" if is_anchor else ""

    parts = [
        f"<strong>{escape(poi.name)}</strong>",
        f"<br>{escape(poi.category)} / {escape(poi.poi_type)}",
        f"<br>分數：{escape(str(poi.score))}",
        f"<br>擁有者：{escape(owner_label)}{escape(anchor_label)}",
    ]

    if poi.owner is None and not is_finished:
        trump_box = ""
        if trump_eligible:
            trump_box = (
                '<label style="display:block;margin-top:4px;font-size:12px;">'
                '<input type="checkbox" name="use_trump" value="on"> 使用王牌'
                '</label>'
            )
        parts.append(
            '<form method="post" action="/move" target="_top" '
            'style="margin-top:8px;">'
            f'<input type="hidden" name="poi_id" value="{escape(poi.id)}">'
            f'{trump_box}'
            f'<button type="submit">插旗為 Player {escape(str(current_pid))}</button>'
            '</form>'
        )

    return "".join(parts)


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def render_map_html(state: GameState, config: Config) -> str:
    """Render the current game state as a full standalone Folium HTML page."""
    center_lat, center_lon = _compute_center(state, config)

    fmap = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=config.DEFAULT_ZOOM,
        tiles="CartoDB voyager",
        control_scale=True,
    )

    placed_id_sets: dict[int, set[str]] = {
        1: set(state.placed_flag_poi_ids(1)),
        2: set(state.placed_flag_poi_ids(2)),
    }

    # ---- popup form context ----
    is_finished = state.is_finished()
    current_pid = state.current_player_id()
    trump_eligible = (
        not is_finished
        and state.has_anchor_flag(current_pid)
        and state.players[current_pid].trump_available
    )

    # ---- only routes whose endpoints both still belong to the drawer ----
    active_routes = _active_routes(state)

    # ---- buffers (z-bottom: drawn first so markers + routes stay clickable) ----
    _render_route_buffers(fmap, active_routes)

    # ---- routes ----
    for route in active_routes:
        # OSRM/GeoJSON gives [lon, lat] — Folium wants [lat, lon]. Flip here.
        latlon_path = [[pt[1], pt[0]] for pt in route.coordinates_lonlat]
        if len(latlon_path) < 2:
            continue

        is_trump = route.buffer_m >= 150
        color = _PLAYER_COLORS.get(route.player_id, _NEUTRAL_COLOR)

        folium.PolyLine(
            locations=latlon_path,
            color=color,
            weight=5 if is_trump else 3,
            opacity=0.85,
            dash_array="10,6" if is_trump else None,
        ).add_to(fmap)

    # ---- markers (z-top: drawn last so they sit above buffers + routes) ----
    for poi in state.pois:
        popup_html = _build_popup_html(
            poi,
            state,
            current_pid=current_pid,
            trump_eligible=trump_eligible,
            is_finished=is_finished,
        )
        popup = folium.Popup(popup_html, max_width=320)
        owner = poi.owner
        has_flag = owner is not None and poi.id in placed_id_sets.get(owner, set())

        if has_flag:
            folium.Marker(
                location=[poi.lat, poi.lon],
                popup=popup,
                tooltip=escape(poi.name),
                icon=folium.Icon(
                    color=_PLAYER_FOLIUM_ICON_COLORS.get(owner, "gray"),
                    icon="flag",
                    prefix="fa",
                ),
            ).add_to(fmap)
        else:
            folium.CircleMarker(
                location=[poi.lat, poi.lon],
                radius=7,
                color=_owner_color(owner),
                weight=2,
                fill=True,
                fill_color=_owner_color(owner),
                fill_opacity=0.85,
                popup=popup,
                tooltip=escape(poi.name),
            ).add_to(fmap)

    # ---- fit bounds if we have POIs ----
    if state.pois:
        lats = [p.lat for p in state.pois]
        lons = [p.lon for p in state.pois]
        sw = [min(lats), min(lons)]
        ne = [max(lats), max(lons)]
        if sw != ne:
            fmap.fit_bounds([sw, ne], padding=(30, 30))

    # ---- finished-state banner ----
    if state.status == "finished":
        winner = state.winner()
        if winner is None:
            text = "平手"
        else:
            text = f"Player {winner} 勝利"
        banner_html = (
            f'<div style="position:fixed;top:10px;left:50%;'
            f'transform:translateX(-50%);background:#fff;padding:8px 16px;'
            f'border:2px solid #333;border-radius:6px;font-weight:bold;'
            f'z-index:9999;">{escape(text)}</div>'
        )
        fmap.get_root().html.add_child(folium.Element(banner_html))

    # ---- persist/restore pan+zoom across iframe reloads (BUG-B) ----
    # Added last so its script runs AFTER fit_bounds and can override it.
    # game_id scopes the key so a new board always starts at fit_bounds.
    _inject_view_persistence(fmap, state.game_id)

    return fmap.get_root().render()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _render_route_buffers(fmap: folium.Map, routes: list[RouteRecord]) -> None:
    """Draw a semi-transparent polygon for each active route buffer.

    `routes` is the already-filtered list of currently-valid routes — a
    stale route's buffer must vanish along with its line, so callers pass
    the same filtered list used for the polylines.

    Buffers are drawn before routes and markers so the flag pins and
    polylines stay clickable on top. Trump buffers (≥150m) use a dashed
    outline; normal buffers (≤50m) use a solid outline.
    """
    if not routes:
        return

    ref_pair: list[float] | None = None
    for route in routes:
        if route.coordinates_lonlat:
            ref_pair = route.coordinates_lonlat[0]
            break
    if ref_pair is None:
        return

    ref_lon, ref_lat = float(ref_pair[0]), float(ref_pair[1])
    to_meters, to_wgs84 = build_meter_transformers(ref_lon, ref_lat)

    for route in routes:
        if len(route.coordinates_lonlat) < 2 or route.buffer_m <= 0:
            continue
        try:
            line_m = route_to_meter_linestring(route.coordinates_lonlat, to_meters)
            buf = buffer_route_meters(line_m, route.buffer_m)
        except ValueError:
            continue

        if buf.is_empty:
            continue

        if buf.geom_type == "Polygon":
            polys = [buf]
        elif buf.geom_type == "MultiPolygon":
            polys = list(buf.geoms)
        else:
            continue

        is_trump = route.buffer_m >= 150
        color = _PLAYER_COLORS.get(route.player_id, _NEUTRAL_COLOR)

        for poly in polys:
            latlon: list[list[float]] = []
            for x, y in poly.exterior.coords:
                lon, lat = to_wgs84.transform(x, y)
                latlon.append([lat, lon])
            if len(latlon) < 3:
                continue
            folium.Polygon(
                locations=latlon,
                color=color,
                weight=2,
                opacity=0.6,
                fill=True,
                fill_color=color,
                fill_opacity=0.15,
                dash_array="6,4" if is_trump else None,
            ).add_to(fmap)


# Restores a previously saved pan/zoom (if any) and keeps sessionStorage in
# sync on every moveend/zoomend. With no saved view, the render's fit_bounds
# stands — so a fresh board still shows every POI. __MAP_NAME__ is the Folium
# map's JS global. No Jinja tags here (single braces only).
_VIEW_PERSIST_JS = """
var _gfMap = __MAP_NAME__;
var _GF_KEY = "geoflip_view___GAME_ID__";
function _gfSaveView() {
    try {
        var c = _gfMap.getCenter();
        sessionStorage.setItem(_GF_KEY, JSON.stringify(
            {lat: c.lat, lon: c.lng, zoom: _gfMap.getZoom()}));
    } catch (e) {}
}
try {
    var _gfRaw = sessionStorage.getItem(_GF_KEY);
    if (_gfRaw) {
        var _gfView = JSON.parse(_gfRaw);
        if (_gfView && isFinite(_gfView.lat) && isFinite(_gfView.lon)
                && isFinite(_gfView.zoom)) {
            _gfMap.setView([_gfView.lat, _gfView.lon], _gfView.zoom,
                {animate: false});
        }
    }
} catch (e) {}
_gfMap.on("moveend", _gfSaveView);
_gfMap.on("zoomend", _gfSaveView);
"""


def _inject_view_persistence(fmap: folium.Map, game_id: str) -> None:
    """Inject JS that saves/restores the Leaflet view via sessionStorage.

    The key is scoped to game_id so a new board always starts at fit_bounds
    rather than inheriting the previous game's saved view.
    Added as the map's last child so its script runs AFTER fit_bounds.
    """
    js = (
        _VIEW_PERSIST_JS
        .replace("__MAP_NAME__", fmap.get_name())
        .replace("__GAME_ID__", game_id)
    )
    macro = MacroElement()
    macro._template = Template(
        "{% macro script(this, kwargs) %}" + js + "{% endmacro %}"
    )
    fmap.add_child(macro)


def _compute_center(state: GameState, config: Config) -> tuple[float, float]:
    if state.pois:
        avg_lat = sum(p.lat for p in state.pois) / len(state.pois)
        avg_lon = sum(p.lon for p in state.pois) / len(state.pois)
        return avg_lat, avg_lon
    return config.DEFAULT_CENTER_LAT, config.DEFAULT_CENTER_LON
