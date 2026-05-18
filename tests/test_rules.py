"""Tests for app/game/rules.py — RulesEngine.apply_move()."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import pytest

from app.game.rules import (
    BUFFER_NORMAL_M,
    BUFFER_TRUMP_M,
    MAX_WALK_DURATION_S,
    RulesEngine,
)
from app.models import Poi, RouteResult
from app.services.geometry import build_meter_transformers
from app.state import new_game


# ---------------------------------------------------------------------------
# Fixture helpers — generate WGS84 coordinates from METER offsets via pyproj
# ---------------------------------------------------------------------------

BASE_LAT = 25.0330
BASE_LON = 121.5654
_TO_M, _TO_WGS = build_meter_transformers(BASE_LON, BASE_LAT)
_BASE_X, _BASE_Y = _TO_M.transform(BASE_LON, BASE_LAT)


def lonlat_at_offset(east_m: float, north_m: float) -> tuple[float, float]:
    """Return (lon, lat) for a point offset (east_m east, north_m north) from BASE."""
    x = _BASE_X + east_m
    y = _BASE_Y + north_m
    lon, lat = _TO_WGS.transform(x, y)
    return lon, lat


def make_poi(
    poi_id: str,
    east_m: float = 0.0,
    north_m: float = 0.0,
    *,
    category: str = "amenity",
    poi_type: str = "cafe",
    owner=None,
    score: int = 2,
    name: str = "Test POI",
) -> Poi:
    lon, lat = lonlat_at_offset(east_m, north_m)
    return Poi(
        id=poi_id,
        name=name,
        lat=lat,
        lon=lon,
        osm_type="node",
        osm_id=hash(poi_id) & 0xFFFFFFFF,
        category=category,
        poi_type=poi_type,
        score=score,
        owner=owner,
        discovered_turn=0,
        placed_turn=None,
        raw={},
    )


def route_lonlat_between(poi_a: Poi, poi_b: Poi, n_points: int = 5) -> list[list[float]]:
    """Interpolate a straight line between two POIs in METERS, return GeoJSON [lon, lat]."""
    ax, ay = _TO_M.transform(poi_a.lon, poi_a.lat)
    bx, by = _TO_M.transform(poi_b.lon, poi_b.lat)
    coords: list[list[float]] = []
    for i in range(n_points):
        t = i / (n_points - 1)
        x = ax + (bx - ax) * t
        y = ay + (by - ay) * t
        lon, lat = _TO_WGS.transform(x, y)
        coords.append([lon, lat])
    return coords


# ---------------------------------------------------------------------------
# Fake routing service
# ---------------------------------------------------------------------------

@dataclass
class FakeRoutingService:
    """Routes are looked up by ((from_lat, from_lon), (to_lat, to_lon)) tuple keys.

    `responses` maps that key → RouteResult OR an exception-raising callable.
    Anything missing raises RuntimeError (so tests catch unintended calls).
    """
    responses: dict = field(default_factory=dict)
    call_log: list[tuple] = field(default_factory=list)

    def _key(self, fl, fn, tl, tn):
        return (round(fl, 7), round(fn, 7), round(tl, 7), round(tn, 7))

    def add(
        self,
        from_poi: Poi,
        to_poi: Poi,
        duration_s: float,
        distance_m: float | None = None,
        n_points: int = 5,
    ) -> RouteResult:
        coords = route_lonlat_between(from_poi, to_poi, n_points=n_points)
        if distance_m is None:
            distance_m = duration_s * 1.4  # rough walking speed
        rr = RouteResult(
            coordinates_lonlat=coords,
            distance_m=distance_m,
            duration_s=duration_s,
        )
        self.responses[self._key(from_poi.lat, from_poi.lon, to_poi.lat, to_poi.lon)] = rr
        return rr

    def add_failure(self, from_poi: Poi, to_poi: Poi, exc: Exception) -> None:
        self.responses[self._key(from_poi.lat, from_poi.lon, to_poi.lat, to_poi.lon)] = exc

    def route(self, from_lat, from_lon, to_lat, to_lon) -> RouteResult:
        self.call_log.append((from_lat, from_lon, to_lat, to_lon))
        key = self._key(from_lat, from_lon, to_lat, to_lon)
        if key not in self.responses:
            raise RuntimeError(
                f"Unexpected route call {key} (configured: {list(self.responses)})"
            )
        val = self.responses[key]
        if isinstance(val, Exception):
            raise val
        return val


# ---------------------------------------------------------------------------
# Common state factory
# ---------------------------------------------------------------------------

def _state_with_pois(*pois: Poi):
    state = new_game()
    state.pois = list(pois)
    return state


# ---------------------------------------------------------------------------
# Branch A — no anchor (first flag / lost all anchors)
# ---------------------------------------------------------------------------

def test_first_flag_can_be_any_neutral_poi():
    p1_first = make_poi("a", east_m=0)
    p2_first = make_poi("b", east_m=2000)  # far away, but free
    state = _state_with_pois(p1_first, p2_first)
    engine = RulesEngine()

    res = engine.apply_move(state, "a", use_trump=False, routing_service=FakeRoutingService())
    assert res.ok
    assert res.state.get_poi("a").owner == 1
    assert res.state.turn_index == 1
    assert res.route_ids == []  # no route on first flag
    assert res.flipped_poi_ids == []


def test_no_anchor_cannot_use_trump():
    """No anchor flag → trump must be rejected even on first flag."""
    p1 = make_poi("a")
    state = _state_with_pois(p1)
    res = RulesEngine().apply_move(state, "a", use_trump=True, routing_service=FakeRoutingService())
    assert res.ok is False
    assert "王牌" in res.message
    # No mutation
    assert state.players[1].trump_available is True
    assert state.turn_index == 0
    assert state.get_poi("a").owner is None


def test_flipped_only_player_cannot_use_trump():
    """Player owns only flipped POIs (no anchor) → free place ok, trump invalid."""
    # P1 owns a flipped POI but has no anchor of their own
    flipped = make_poi("f", east_m=0, owner=1)  # owned but never placed by p1
    candidate = make_poi("c", east_m=100)
    state = _state_with_pois(flipped, candidate)
    # P1's turn (turn 0)
    engine = RulesEngine()

    # Trump → invalid
    res_trump = engine.apply_move(state, "c", use_trump=True, routing_service=FakeRoutingService())
    assert res_trump.ok is False
    # Free place → ok
    res_free = engine.apply_move(state, "c", use_trump=False, routing_service=FakeRoutingService())
    assert res_free.ok is True


# ---------------------------------------------------------------------------
# Branch B — second flag onward must reach an anchor in <= 600 s
# ---------------------------------------------------------------------------

def test_second_flag_within_600s_valid():
    anchor = make_poi("a1", east_m=0)
    candidate = make_poi("c1", east_m=300)
    state = _state_with_pois(anchor, candidate)
    engine = RulesEngine()

    # Manually mark anchor as placed by P1
    res1 = engine.apply_move(state, "a1", use_trump=False, routing_service=FakeRoutingService())
    state = res1.state

    # P2's turn — give them a quick free flag far away so P1 can move next
    p2_flag = make_poi("p2", east_m=5000)
    state.pois.append(p2_flag)
    res2 = engine.apply_move(state, "p2", use_trump=False, routing_service=FakeRoutingService())
    state = res2.state

    # P1's second flag: distance 300m, fake 200s walk
    routing = FakeRoutingService()
    routing.add(state.get_poi("c1"), state.get_poi("a1"), duration_s=200.0)
    res3 = engine.apply_move(state, "c1", use_trump=False, routing_service=routing)
    assert res3.ok
    assert len(res3.route_ids) == 1
    assert res3.state.get_poi("c1").owner == 1


def test_second_flag_over_600s_invalid():
    anchor = make_poi("a1", east_m=0)
    candidate = make_poi("c1", east_m=300)
    state = _state_with_pois(anchor, candidate)
    engine = RulesEngine()

    res1 = engine.apply_move(state, "a1", use_trump=False, routing_service=FakeRoutingService())
    state = res1.state
    p2_flag = make_poi("p2", east_m=5000)
    state.pois.append(p2_flag)
    res2 = engine.apply_move(state, "p2", use_trump=False, routing_service=FakeRoutingService())
    state = res2.state

    routing = FakeRoutingService()
    routing.add(state.get_poi("c1"), state.get_poi("a1"), duration_s=601.0)
    pre_turn = state.turn_index
    pre_trump = state.players[1].trump_available
    res3 = engine.apply_move(state, "c1", use_trump=False, routing_service=routing)
    assert res3.ok is False
    assert "10 分鐘" in res3.message or "600" in res3.message
    # Original state untouched
    assert state.turn_index == pre_turn
    assert state.get_poi("c1").owner is None
    assert state.players[1].trump_available == pre_trump
    assert len(state.routes) == 0
    assert len(state.moves) == 2  # only the two first-flag placements


# ---------------------------------------------------------------------------
# Owner / candidate validation
# ---------------------------------------------------------------------------

def test_cannot_flag_owned_poi():
    a = make_poi("a", owner=2)
    state = _state_with_pois(a)
    res = RulesEngine().apply_move(state, "a", use_trump=False, routing_service=FakeRoutingService())
    assert res.ok is False
    assert "擁有" in res.message


def test_cannot_flag_unknown_poi():
    state = _state_with_pois(make_poi("x"))
    res = RulesEngine().apply_move(state, "ghost", use_trump=False, routing_service=FakeRoutingService())
    assert res.ok is False
    assert "POI" in res.message


# ---------------------------------------------------------------------------
# Invalid move = pure no-op
# ---------------------------------------------------------------------------

def test_invalid_move_does_not_mutate_state():
    anchor = make_poi("a1", east_m=0)
    candidate = make_poi("c1", east_m=300)
    state = _state_with_pois(anchor, candidate)
    engine = RulesEngine()
    state = engine.apply_move(state, "a1", False, FakeRoutingService()).state  # P1 first
    p2 = make_poi("p2", east_m=5000)
    state.pois.append(p2)
    state = engine.apply_move(state, "p2", False, FakeRoutingService()).state  # P2 first

    snapshot_turn = state.turn_index
    snapshot_trump1 = state.players[1].trump_available
    snapshot_owners = {p.id: p.owner for p in state.pois}
    snapshot_routes = len(state.routes)
    snapshot_moves = len(state.moves)

    routing = FakeRoutingService()
    routing.add(state.get_poi("c1"), state.get_poi("a1"), duration_s=999.0)
    res = engine.apply_move(state, "c1", use_trump=True, routing_service=routing)
    assert res.ok is False

    assert state.turn_index == snapshot_turn
    assert state.players[1].trump_available == snapshot_trump1
    assert {p.id: p.owner for p in state.pois} == snapshot_owners
    assert len(state.routes) == snapshot_routes
    assert len(state.moves) == snapshot_moves


# ---------------------------------------------------------------------------
# Buffer geometry — 50m flips nearby, not far ones; 150m trump flips farther
# ---------------------------------------------------------------------------

def _p1_second_flag_setup(extras: list[Poi]):
    """Set up state where P1 has 1 anchor at (0,0) and is about to place at (300,0)."""
    anchor = make_poi("a1", east_m=0)
    candidate = make_poi("c1", east_m=300)
    state = _state_with_pois(anchor, candidate, *extras)
    engine = RulesEngine()

    state = engine.apply_move(state, "a1", False, FakeRoutingService()).state
    p2 = make_poi("p2_first", east_m=5000)
    state.pois.append(p2)
    state = engine.apply_move(state, "p2_first", False, FakeRoutingService()).state
    return state, engine


def test_normal_buffer_50m_flips_near_not_far():
    # P2 owns 3 POIs along the route at 30m / 100m / 200m north of the line
    near = make_poi("near", east_m=150, north_m=30, owner=2)
    mid = make_poi("mid", east_m=150, north_m=100, owner=2)
    far = make_poi("far", east_m=150, north_m=200, owner=2)
    state, engine = _p1_second_flag_setup([near, mid, far])

    routing = FakeRoutingService()
    routing.add(state.get_poi("c1"), state.get_poi("a1"), duration_s=250.0)
    res = engine.apply_move(state, "c1", use_trump=False, routing_service=routing)
    assert res.ok

    flipped = set(res.flipped_poi_ids)
    assert "near" in flipped
    assert "mid" not in flipped
    assert "far" not in flipped
    assert res.state.get_poi("near").owner == 1
    assert res.state.get_poi("mid").owner == 2
    assert res.state.get_poi("far").owner == 2


def test_trump_buffer_150m_flips_100m_point():
    near = make_poi("near", east_m=150, north_m=30, owner=2)
    mid = make_poi("mid", east_m=150, north_m=100, owner=2)
    far = make_poi("far", east_m=150, north_m=200, owner=2)
    state, engine = _p1_second_flag_setup([near, mid, far])

    routing = FakeRoutingService()
    routing.add(state.get_poi("c1"), state.get_poi("a1"), duration_s=250.0)
    res = engine.apply_move(state, "c1", use_trump=True, routing_service=routing)
    assert res.ok

    flipped = set(res.flipped_poi_ids)
    assert "near" in flipped
    assert "mid" in flipped  # 100m within 150m trump buffer
    assert "far" not in flipped
    assert res.state.players[1].trump_available is False
    assert res.state.routes[-1].buffer_m == BUFFER_TRUMP_M


def test_normal_route_records_50m_buffer():
    state, engine = _p1_second_flag_setup([])
    routing = FakeRoutingService()
    routing.add(state.get_poi("c1"), state.get_poi("a1"), duration_s=250.0)
    res = engine.apply_move(state, "c1", use_trump=False, routing_service=routing)
    assert res.state.routes[-1].buffer_m == BUFFER_NORMAL_M


# ---------------------------------------------------------------------------
# Trump consumption
# ---------------------------------------------------------------------------

def test_trump_success_consumes_trump():
    state, engine = _p1_second_flag_setup([])
    routing = FakeRoutingService()
    routing.add(state.get_poi("c1"), state.get_poi("a1"), duration_s=250.0)
    res = engine.apply_move(state, "c1", use_trump=True, routing_service=routing)
    assert res.ok
    assert res.state.players[1].trump_available is False


def test_trump_invalid_does_not_consume():
    state, engine = _p1_second_flag_setup([])
    # route too long → invalid
    routing = FakeRoutingService()
    routing.add(state.get_poi("c1"), state.get_poi("a1"), duration_s=999.0)
    res = engine.apply_move(state, "c1", use_trump=True, routing_service=routing)
    assert res.ok is False
    assert state.players[1].trump_available is True


def test_trump_already_used_invalid():
    state, engine = _p1_second_flag_setup([])
    state.players[1].trump_available = False
    routing = FakeRoutingService()
    routing.add(state.get_poi("c1"), state.get_poi("a1"), duration_s=200.0)
    res = engine.apply_move(state, "c1", use_trump=True, routing_service=routing)
    assert res.ok is False
    assert "王牌" in res.message


# ---------------------------------------------------------------------------
# Anchor vs owned — route source correctness
# ---------------------------------------------------------------------------

def test_route_only_to_anchor_not_to_flipped_poi():
    """A POI flipped to P1 must NOT become an anchor for P1's next route."""
    anchor = make_poi("a1", east_m=0)
    flip_target = make_poi("ft", east_m=10, owner=2)  # will be flipped to P1
    candidate1 = make_poi("c1", east_m=300)
    far_candidate = make_poi("c2", east_m=2000)  # far from anchor; near flipped target
    state = _state_with_pois(anchor, flip_target, candidate1, far_candidate)
    engine = RulesEngine()

    # P1 first flag = anchor
    state = engine.apply_move(state, "a1", False, FakeRoutingService()).state
    # P2 first flag (free, anywhere)
    p2 = make_poi("p2_first", east_m=5000)
    state.pois.append(p2)
    state = engine.apply_move(state, "p2_first", False, FakeRoutingService()).state

    # P1 second flag: c1 → flips flip_target (right next to c1's line through anchor)
    routing = FakeRoutingService()
    routing.add(state.get_poi("c1"), state.get_poi("a1"), duration_s=250.0)
    state = engine.apply_move(state, "c1", False, routing).state
    assert state.get_poi("ft").owner == 1  # flipped to P1
    # ft is owned by P1, but P1 didn't PLACE it → not an anchor
    anchor_ids = {p.id for p in state.anchor_pois(1)}
    assert "ft" not in anchor_ids
    assert anchor_ids == {"a1", "c1"}

    # P2 free placement far away
    p2_second = make_poi("p2_second", east_m=6000)
    state.pois.append(p2_second)
    state = engine.apply_move(state, "p2_second", False, FakeRoutingService()).state

    # P1's next flag at far_candidate (2000m east). Only routes via REAL anchors
    # (a1@0 and c1@300) should be queried — not via flipped ft.
    routing2 = FakeRoutingService()
    routing2.add(state.get_poi("c2"), state.get_poi("a1"), duration_s=2000.0)
    routing2.add(state.get_poi("c2"), state.get_poi("c1"), duration_s=2000.0)
    res = engine.apply_move(state, "c2", False, routing2)

    # All routes too long → invalid. But more importantly: no call to "ft" was made.
    assert res.ok is False
    called_targets = {(round(t[2], 6), round(t[3], 6)) for t in routing2.call_log}
    ft = state.get_poi("ft")
    assert (round(ft.lat, 6), round(ft.lon, 6)) not in called_targets


def test_lost_anchor_does_not_appear_in_routes():
    """If P1's placed POI is flipped away by P2, it's no longer an anchor."""
    a1 = make_poi("a1", east_m=0)
    a2 = make_poi("a2", east_m=200)  # P1 will place here too
    candidate = make_poi("c1", east_m=400)
    state = _state_with_pois(a1, a2, candidate)
    engine = RulesEngine()

    state = engine.apply_move(state, "a1", False, FakeRoutingService()).state
    p2_first = make_poi("p2_first", east_m=5000)
    state.pois.append(p2_first)
    state = engine.apply_move(state, "p2_first", False, FakeRoutingService()).state

    # Now manually simulate: P1 placed a2, then P2 flipped it.
    state.get_poi("a2").owner = 2
    state.moves.append(
        type(state.moves[0])(
            turn_index=99,
            player_id=1,
            placed_poi_id="a2",
            used_trump=False,
            route_ids=[],
            flipped_poi_ids=[],
        )
    )

    # P1 places c1. Only a1 is a current anchor; a2 has been flipped away.
    assert {p.id for p in state.anchor_pois(1)} == {"a1"}
    routing = FakeRoutingService()
    routing.add(state.get_poi("c1"), state.get_poi("a1"), duration_s=300.0)
    res = engine.apply_move(state, "c1", False, routing)
    assert res.ok
    # Only a1 was queried — not a2 (since it's no longer an anchor)
    called = routing.call_log
    assert len(called) == 1


# ---------------------------------------------------------------------------
# Multi-anchor: partial route failure tolerated; pick shortest success
# ---------------------------------------------------------------------------

def _state_with_two_p1_anchors():
    """Build a state where P1 has anchors {a1, a2} and it is P1's turn to place c1.

    Layout (east m): a1=0, a2=200, c1=400, p2 flags at 5000/6000.
    Returns (state, engine).
    """
    a1 = make_poi("a1", east_m=0)
    a2 = make_poi("a2", east_m=200)
    candidate = make_poi("c1", east_m=400)
    p2f = make_poi("p2f", east_m=5000)
    p2s = make_poi("p2s", east_m=6000)
    state = _state_with_pois(a1, a2, candidate, p2f, p2s)
    engine = RulesEngine()

    # turn 0: P1 places a1 (free)
    state = engine.apply_move(state, "a1", False, FakeRoutingService()).state
    # turn 1: P2 places p2f (free)
    state = engine.apply_move(state, "p2f", False, FakeRoutingService()).state
    # turn 2: P1 places a2 — route from a2 to a1 (200s)
    r_a2 = FakeRoutingService()
    r_a2.add(state.get_poi("a2"), state.get_poi("a1"), duration_s=200.0)
    state = engine.apply_move(state, "a2", False, r_a2).state
    # turn 3: P2 places p2s — route from p2s to p2f (300s)
    r_p2s = FakeRoutingService()
    r_p2s.add(state.get_poi("p2s"), state.get_poi("p2f"), duration_s=300.0)
    state = engine.apply_move(state, "p2s", False, r_p2s).state
    # Now turn 4: P1's turn, anchors are {a1, a2}.
    assert state.turn_index == 4
    assert state.current_player_id() == 1
    assert {p.id for p in state.anchor_pois(1)} == {"a1", "a2"}
    return state, engine


def test_multi_anchor_partial_failure_uses_successful_route():
    state, engine = _state_with_two_p1_anchors()
    routing = FakeRoutingService()
    routing.add_failure(state.get_poi("c1"), state.get_poi("a1"), RuntimeError("OSRM down"))
    routing.add(state.get_poi("c1"), state.get_poi("a2"), duration_s=300.0)
    pre_routes = len(state.routes)
    res = engine.apply_move(state, "c1", False, routing)
    assert res.ok
    assert len(res.state.routes) == pre_routes + 1
    new_route = res.state.routes[-1]
    assert new_route.to_poi_id == "a2"
    assert new_route.duration_s == 300.0


def test_multi_anchor_all_routes_fail_is_invalid():
    state, engine = _state_with_two_p1_anchors()
    routing = FakeRoutingService()
    routing.add_failure(state.get_poi("c1"), state.get_poi("a1"), RuntimeError("x"))
    routing.add_failure(state.get_poi("c1"), state.get_poi("a2"), RuntimeError("y"))
    pre_turn = state.turn_index
    res = engine.apply_move(state, "c1", False, routing)
    assert res.ok is False
    assert "步行路線" in res.message
    assert state.turn_index == pre_turn


def test_multi_anchor_all_routes_over_600_is_invalid():
    state, engine = _state_with_two_p1_anchors()
    routing = FakeRoutingService()
    routing.add(state.get_poi("c1"), state.get_poi("a1"), duration_s=800.0)
    routing.add(state.get_poi("c1"), state.get_poi("a2"), duration_s=700.0)
    res = engine.apply_move(state, "c1", False, routing)
    assert res.ok is False


def test_multi_anchor_picks_shortest_duration():
    state, engine = _state_with_two_p1_anchors()
    routing = FakeRoutingService()
    routing.add(state.get_poi("c1"), state.get_poi("a1"), duration_s=500.0)
    routing.add(state.get_poi("c1"), state.get_poi("a2"), duration_s=120.0)  # winner
    res = engine.apply_move(state, "c1", False, routing)
    assert res.ok
    chosen = res.state.routes[-1]
    assert chosen.to_poi_id == "a2"
    assert chosen.duration_s == 120.0


# ---------------------------------------------------------------------------
# Game end conditions
# ---------------------------------------------------------------------------

def test_finished_after_max_turns():
    # Create a state with all 16 first-flag-style placements feasible (no anchors needed except first)
    # Easier: take 16 free placements far from each other (each player only acts on first flag rule)
    pois = [make_poi(f"p{i}", east_m=i * 5000) for i in range(16)]
    state = _state_with_pois(*pois)
    engine = RulesEngine()
    # The rule says "first flag free" — for subsequent flags we need anchors w/ route ≤ 600.
    # So routing is needed from move 2 onwards (each player's 2nd+ flags).
    # Approach: link each new flag to player's previous (single) anchor with duration 0.
    for i in range(16):
        target = pois[i]
        # current player anchors before this move:
        cur = state.current_player_id()
        anchors = state.anchor_pois(cur)
        routing = FakeRoutingService()
        for a in anchors:
            routing.add(target, a, duration_s=1.0)
        res = engine.apply_move(state, target.id, False, routing)
        assert res.ok, f"Move {i} failed: {res.message}"
        state = res.state

    assert state.status == "finished"
    assert state.turn_index == 16


def test_move_after_finished_is_invalid():
    p = make_poi("p", east_m=0)
    state = _state_with_pois(p)
    state.status = "finished"
    res = RulesEngine().apply_move(state, "p", False, FakeRoutingService())
    assert res.ok is False
    assert "結束" in res.message


def test_move_at_max_turns_is_invalid_even_if_active():
    p = make_poi("p", east_m=0)
    state = _state_with_pois(p)
    state.turn_index = state.max_turns
    res = RulesEngine().apply_move(state, "p", False, FakeRoutingService())
    assert res.ok is False


# ---------------------------------------------------------------------------
# RouteRecord invariants
# ---------------------------------------------------------------------------

def test_valid_move_produces_at_most_one_route_record():
    state, engine = _p1_second_flag_setup([])
    routing = FakeRoutingService()
    routing.add(state.get_poi("c1"), state.get_poi("a1"), duration_s=200.0)
    pre_routes = len(state.routes)
    res = engine.apply_move(state, "c1", False, routing)
    assert res.ok
    assert len(res.state.routes) - pre_routes == 1
    assert len(res.state.moves[-1].route_ids) == 1


def test_first_flag_produces_no_route_record():
    state, _ = (_state_with_pois(make_poi("a", east_m=0)), None)
    res = RulesEngine().apply_move(state, "a", False, FakeRoutingService())
    assert res.ok
    assert res.state.routes == []
    assert res.state.moves[-1].route_ids == []


def test_route_record_id_format():
    state, engine = _p1_second_flag_setup([])
    routing = FakeRoutingService()
    routing.add(state.get_poi("c1"), state.get_poi("a1"), duration_s=200.0)
    res = engine.apply_move(state, "c1", False, routing)
    assert res.ok
    assert res.state.routes[-1].id.startswith("route_")


def test_move_record_route_ids_length_le_1():
    """Per spec — MoveRecord.route_ids is at most 1 element."""
    state, engine = _p1_second_flag_setup([])
    routing = FakeRoutingService()
    routing.add(state.get_poi("c1"), state.get_poi("a1"), duration_s=200.0)
    res = engine.apply_move(state, "c1", False, routing)
    assert len(res.state.moves[-1].route_ids) <= 1
