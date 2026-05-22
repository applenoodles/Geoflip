"""Core game-rules engine.

apply_move() is transaction-like:
  - All validation runs on the unchanged input state.
  - State mutation happens AFTER all checks pass, on a deepcopy.
  - Invalid moves never change turn_index, owner, trump, routes, or moves.

Only the SHORTEST successful OSRM route (by duration_s) drives the buffer.
Route endpoints come exclusively from anchor_pois() — never owned_pois().
"""
from __future__ import annotations

import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from app.models import (
    GameState,
    MoveRecord,
    MoveResult,
    Poi,
    RouteRecord,
    RouteResult,
)
from app.services.geometry import (
    build_meter_transformers,
    buffer_route_meters,
    point_in_buffer,
    route_to_meter_linestring,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_WALK_DURATION_S: float = 600.0
BUFFER_NORMAL_M: float = 50.0
BUFFER_TRUMP_M: float = 150.0


# ---------------------------------------------------------------------------
# Protocol — anything with a .route() that returns a RouteResult fits
# ---------------------------------------------------------------------------

class RoutingService(Protocol):
    def route(
        self,
        from_lat: float,
        from_lon: float,
        to_lat: float,
        to_lon: float,
    ) -> RouteResult: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _invalid(state: GameState, message: str) -> MoveResult:
    """Build a MoveResult for an invalid move — returns the ORIGINAL state untouched."""
    return MoveResult(
        ok=False,
        message=message,
        state=state,
        placed_poi_id=None,
        flipped_poi_ids=[],
        route_ids=[],
    )


@dataclass
class _CandidateRoute:
    anchor_poi_id: str
    route: RouteResult


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class RulesEngine:
    """Pure game-rules engine — no I/O, no Flask, no Folium."""

    def __init__(
        self,
        max_walk_duration_s: float = MAX_WALK_DURATION_S,
        buffer_normal_m: float = BUFFER_NORMAL_M,
        buffer_trump_m: float = BUFFER_TRUMP_M,
    ) -> None:
        self._max_walk_duration_s = max_walk_duration_s
        self._buffer_normal_m = buffer_normal_m
        self._buffer_trump_m = buffer_trump_m

    def apply_move(
        self,
        state: GameState,
        poi_id: str,
        use_trump: bool,
        routing_service: RoutingService,
    ) -> MoveResult:
        # ---- Validation (no mutation) ----

        if state.status == "finished" or state.turn_index >= state.max_turns:
            return _invalid(state, "遊戲已結束")

        current_player_id = state.current_player_id()
        player = state.players[current_player_id]

        candidate: Poi | None = state.get_poi(poi_id)
        if candidate is None:
            return _invalid(state, "POI 不存在")
        if candidate.owner is not None:
            return _invalid(state, "該地點已被擁有")

        if use_trump and not player.trump_available:
            return _invalid(state, "王牌已使用")

        # SNAPSHOT taken BEFORE any mutation. Excludes flipped POIs and
        # excludes own placed POIs that have since been flipped away by opponent.
        old_anchor_pois: list[Poi] = state.anchor_pois(current_player_id)

        chosen_route: RouteResult | None = None
        chosen_anchor_poi_id: str | None = None

        if not old_anchor_pois:
            # Branch A: no anchor → free place allowed, but trump forbidden.
            if use_trump:
                return _invalid(state, "沒有可放大的路線，無法使用王牌")
            # chosen_route stays None
        else:
            # Branch B: have anchors → must reach at least one within 600 s.
            successful: list[_CandidateRoute] = []
            for anchor in old_anchor_pois:
                try:
                    route_res = routing_service.route(
                        candidate.lat,
                        candidate.lon,
                        anchor.lat,
                        anchor.lon,
                    )
                except Exception:
                    # Per spec: route failures are skipped, not propagated as invalid.
                    continue
                successful.append(
                    _CandidateRoute(anchor_poi_id=anchor.id, route=route_res)
                )

            if not successful:
                return _invalid(state, "找不到步行路線")

            # Take the shortest-duration successful route.
            best = min(successful, key=lambda c: c.route.duration_s)
            if best.route.duration_s > self._max_walk_duration_s:
                return _invalid(
                    state,
                    f"新旗必須在己方旗子步行 {int(self._max_walk_duration_s)} 秒內",
                )

            chosen_route = best.route
            chosen_anchor_poi_id = best.anchor_poi_id

        # ---- Commit (mutate a deepcopy) ----

        new_state = deepcopy(state)
        new_candidate = new_state.get_poi(poi_id)
        assert new_candidate is not None  # validated above
        new_player = new_state.players[current_player_id]

        new_candidate.owner = current_player_id
        new_candidate.placed_turn = state.turn_index

        route_ids: list[str] = []
        flipped_poi_ids: list[str] = []

        if chosen_route is not None:
            assert chosen_anchor_poi_id is not None
            buffer_m = self._buffer_trump_m if use_trump else self._buffer_normal_m

            # Project to meters using the new candidate as the reference point.
            to_m, _ = build_meter_transformers(
                new_candidate.lon, new_candidate.lat
            )
            line_m = route_to_meter_linestring(
                chosen_route.coordinates_lonlat, to_m
            )
            buffer_poly = buffer_route_meters(line_m, buffer_m)

            # Flip opponent POIs that lie inside the buffer.
            opponent_id = new_state.opponent_id(current_player_id)
            for poi in new_state.pois:
                if poi.owner == opponent_id and point_in_buffer(
                    poi.lat, poi.lon, buffer_poly, to_m
                ):
                    poi.owner = current_player_id
                    flipped_poi_ids.append(poi.id)

            route_record = RouteRecord(
                id="route_" + uuid.uuid4().hex,
                turn_index=state.turn_index,
                player_id=current_player_id,
                from_poi_id=new_candidate.id,
                to_poi_id=chosen_anchor_poi_id,
                coordinates_lonlat=list(chosen_route.coordinates_lonlat),
                distance_m=float(chosen_route.distance_m),
                duration_s=float(chosen_route.duration_s),
                buffer_m=float(buffer_m),
            )
            new_state.routes.append(route_record)
            route_ids.append(route_record.id)

        if use_trump:
            new_player.trump_available = False

        move_record = MoveRecord(
            turn_index=state.turn_index,
            player_id=current_player_id,
            placed_poi_id=new_candidate.id,
            used_trump=use_trump,
            route_ids=list(route_ids),
            flipped_poi_ids=list(flipped_poi_ids),
        )
        new_state.moves.append(move_record)

        new_state.turn_index += 1
        new_state.updated_at = _now_iso()
        if new_state.turn_index >= new_state.max_turns:
            new_state.status = "finished"

        return MoveResult(
            ok=True,
            message="OK",
            state=new_state,
            placed_poi_id=new_candidate.id,
            flipped_poi_ids=flipped_poi_ids,
            route_ids=route_ids,
        )
