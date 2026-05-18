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

from app.config import Config
from app.models import GameState, Poi


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


# ---------------------------------------------------------------------------
# Popup builder — HTML escape every dynamic field to prevent XSS
# ---------------------------------------------------------------------------

def _build_popup_html(poi: Poi, state: GameState) -> str:
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

    # ---- markers ----
    for poi in state.pois:
        popup = folium.Popup(_build_popup_html(poi, state), max_width=320)
        owner = poi.owner
        has_flag = owner is not None and poi.id in placed_id_sets.get(owner, set())

        if has_flag:
            # Flag-bearing POI: use a Marker with pin icon in the player's color
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
            # Neutral or flipped: CircleMarker in owner color
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

    # ---- routes ----
    for route in state.routes:
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
            tooltip=(
                f"Player {route.player_id} · "
                f"{route.duration_s:.0f}s · "
                f"buffer {int(route.buffer_m)}m"
            ),
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

    return fmap.get_root().render()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_center(state: GameState, config: Config) -> tuple[float, float]:
    if state.pois:
        avg_lat = sum(p.lat for p in state.pois) / len(state.pois)
        avg_lon = sum(p.lon for p in state.pois) / len(state.pois)
        return avg_lat, avg_lon
    return config.DEFAULT_CENTER_LAT, config.DEFAULT_CENTER_LON
