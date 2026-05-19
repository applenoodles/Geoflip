"""Tests for app/models.py — domain model, score_poi, GameState helpers."""
import pytest
from app.models import (
    GameState,
    MoveRecord,
    Poi,
    PlayerState,
    RouteRecord,
    score_poi,
)
from app.state import new_game


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_poi(
    poi_id: str = "p1",
    name: str = "Test POI",
    lat: float = 25.033,
    lon: float = 121.565,
    category: str = "amenity",
    poi_type: str = "cafe",
    score: int = 2,
    owner=None,
    discovered_turn: int | None = 0,
    placed_turn: int | None = None,
) -> Poi:
    return Poi(
        id=poi_id,
        name=name,
        lat=lat,
        lon=lon,
        osm_type="node",
        osm_id=123,
        category=category,
        poi_type=poi_type,
        score=score,
        owner=owner,
        discovered_turn=discovered_turn,
        placed_turn=placed_turn,
        raw={"display_name": name},
    )


def _make_move(player_id, placed_poi_id, turn_index=0) -> MoveRecord:
    return MoveRecord(
        turn_index=turn_index,
        player_id=player_id,
        placed_poi_id=placed_poi_id,
        used_trump=False,
        route_ids=[],
        flipped_poi_ids=[],
    )


# ---------------------------------------------------------------------------
# new_game basics
# ---------------------------------------------------------------------------

def test_new_game_current_player_is_1():
    state = new_game()
    assert state.current_player_id() == 1


def test_new_game_game_id_prefix():
    state = new_game()
    assert state.game_id.startswith("game_")


def test_new_game_status_active():
    state = new_game()
    assert state.status == "active"
    assert not state.is_finished()


def test_new_game_trump_available():
    state = new_game()
    assert state.players[1].trump_available is True
    assert state.players[2].trump_available is True


def test_new_game_12_max_turns():
    state = new_game()
    assert state.max_turns == 12


# ---------------------------------------------------------------------------
# is_finished / current_player_id
# ---------------------------------------------------------------------------

def test_is_finished_after_12_turns():
    state = new_game()
    state.turn_index = 12
    assert state.is_finished()


def test_is_finished_false_at_11():
    state = new_game()
    state.turn_index = 11
    assert not state.is_finished()


def test_current_player_odd_turn_is_2():
    state = new_game()
    state.turn_index = 1
    assert state.current_player_id() == 2


def test_current_player_even_turn_is_1():
    state = new_game()
    state.turn_index = 4
    assert state.current_player_id() == 1


# ---------------------------------------------------------------------------
# score_poi
# ---------------------------------------------------------------------------

def test_score_poi_historic_is_3():
    assert score_poi("historic", "castle") == 3
    assert score_poi("historic", "ruins") == 3


def test_score_poi_tourism_3_types():
    for t in ("museum", "attraction", "gallery", "zoo", "theme_park"):
        assert score_poi("tourism", t) == 3


def test_score_poi_amenity_3_types():
    for t in ("university", "hospital", "theatre", "arts_centre"):
        assert score_poi("amenity", t) == 3


def test_score_poi_leisure_3_types():
    assert score_poi("leisure", "park") == 3
    assert score_poi("leisure", "stadium") == 3


def test_score_poi_amenity_2_types():
    for t in ("restaurant", "cafe", "bar", "fast_food", "library",
              "school", "college", "place_of_worship", "marketplace"):
        assert score_poi("amenity", t) == 2


def test_score_poi_shop_is_2():
    assert score_poi("shop", "bakery") == 2
    assert score_poi("shop", "supermarket") == 2


def test_score_poi_railway_station_is_2():
    assert score_poi("railway", "station") == 2
    assert score_poi("railway", "halt") == 2


def test_score_poi_public_transport_is_2():
    assert score_poi("public_transport", "stop_position") == 2


def test_score_poi_tourism_2_types():
    for t in ("hotel", "hostel", "viewpoint"):
        assert score_poi("tourism", t) == 2


def test_score_poi_leisure_2_types():
    for t in ("garden", "playground", "sports_centre"):
        assert score_poi("leisure", t) == 2


def test_score_poi_other_is_1():
    assert score_poi("amenity", "parking") == 1
    assert score_poi("unknown", "anything") == 1
    assert score_poi("tourism", "information") == 1


# ---------------------------------------------------------------------------
# scores() — real-time by owner
# ---------------------------------------------------------------------------

def test_scores_empty_state():
    state = new_game()
    assert state.scores() == {1: 0, 2: 0}


def test_scores_from_owner():
    state = new_game()
    state.pois = [
        _make_poi("a", score=3, owner=1),
        _make_poi("b", score=2, owner=2),
        _make_poi("c", score=1, owner=None),
        _make_poi("d", score=3, owner=1),
    ]
    sc = state.scores()
    assert sc[1] == 6
    assert sc[2] == 2


def test_scores_update_after_flip():
    state = new_game()
    poi = _make_poi("a", score=3, owner=1)
    state.pois = [poi]
    assert state.scores()[1] == 3
    # simulate flip
    poi.owner = 2
    assert state.scores()[1] == 0
    assert state.scores()[2] == 3


# ---------------------------------------------------------------------------
# winner
# ---------------------------------------------------------------------------

def test_winner_none_when_active():
    state = new_game()
    assert state.winner() is None


def test_winner_player1_wins():
    state = new_game()
    state.turn_index = 12
    state.status = "finished"
    state.pois = [_make_poi("a", score=3, owner=1), _make_poi("b", score=1, owner=2)]
    assert state.winner() == 1


def test_winner_tie_returns_none():
    state = new_game()
    state.turn_index = 12
    state.status = "finished"
    state.pois = [_make_poi("a", score=2, owner=1), _make_poi("b", score=2, owner=2)]
    assert state.winner() is None


# ---------------------------------------------------------------------------
# opponent_id / neutral_pois / get_poi
# ---------------------------------------------------------------------------

def test_opponent_id():
    state = new_game()
    assert state.opponent_id(1) == 2
    assert state.opponent_id(2) == 1


def test_neutral_pois():
    state = new_game()
    state.pois = [
        _make_poi("a", owner=None),
        _make_poi("b", owner=1),
        _make_poi("c", owner=None),
    ]
    neutrals = state.neutral_pois()
    assert len(neutrals) == 2
    assert all(p.owner is None for p in neutrals)


def test_get_poi_found():
    state = new_game()
    poi = _make_poi("x")
    state.pois = [poi]
    assert state.get_poi("x") is poi


def test_get_poi_not_found():
    state = new_game()
    assert state.get_poi("missing") is None


# ---------------------------------------------------------------------------
# anchor_pois — the critical helper
# ---------------------------------------------------------------------------

def test_anchor_pois_empty_when_no_moves():
    state = new_game()
    state.pois = [_make_poi("a", owner=1)]
    assert state.anchor_pois(1) == []


def test_anchor_pois_includes_placed_and_still_owned():
    state = new_game()
    poi = _make_poi("a", owner=1)
    state.pois = [poi]
    state.moves = [_make_move(player_id=1, placed_poi_id="a")]
    assert poi in state.anchor_pois(1)


def test_anchor_pois_excludes_flipped_poi():
    """POI flipped from opponent should NOT appear in opponent's anchors."""
    state = new_game()
    # P1 placed "a", P2 flipped it → a.owner = 2
    poi_a = _make_poi("a", owner=2)
    state.pois = [poi_a]
    state.moves = [_make_move(player_id=1, placed_poi_id="a")]
    # P2 owns it but never placed it → not P2's anchor
    assert poi_a not in state.anchor_pois(2)
    # P1 placed it but no longer owns it → not P1's anchor
    assert poi_a not in state.anchor_pois(1)


def test_anchor_pois_excludes_own_poi_flipped_away():
    """P1 placed "a" but P2 flipped it away → not in P1 anchors."""
    state = new_game()
    poi_a = _make_poi("a", owner=2)  # flipped to P2
    state.pois = [poi_a]
    state.moves = [_make_move(player_id=1, placed_poi_id="a")]
    assert state.anchor_pois(1) == []


def test_anchor_pois_owned_pois_not_anchor_when_not_placed():
    """owned_pois and anchor_pois differ: flipped POI appears in owned but not anchor."""
    state = new_game()
    # P2 placed "b"; P1 flipped it → b.owner = 1 but P1 never placed it
    poi_b = _make_poi("b", owner=1)
    state.pois = [poi_b]
    state.moves = [_make_move(player_id=2, placed_poi_id="b")]
    assert poi_b in state.owned_pois(1)
    assert poi_b not in state.anchor_pois(1)


def test_has_anchor_flag_false_when_empty():
    state = new_game()
    assert not state.has_anchor_flag(1)


def test_has_anchor_flag_true_when_placed():
    state = new_game()
    poi = _make_poi("a", owner=1)
    state.pois = [poi]
    state.moves = [_make_move(player_id=1, placed_poi_id="a")]
    assert state.has_anchor_flag(1)


def test_has_anchor_flag_false_when_all_flipped():
    state = new_game()
    poi = _make_poi("a", owner=2)  # flipped away from P1
    state.pois = [poi]
    state.moves = [_make_move(player_id=1, placed_poi_id="a")]
    assert not state.has_anchor_flag(1)


# ---------------------------------------------------------------------------
# merge_discovered_pois
# ---------------------------------------------------------------------------

def test_merge_new_poi_sets_discovered_turn():
    state = new_game()
    state.turn_index = 3
    new_poi = _make_poi("new", discovered_turn=None, owner=1)
    state.merge_discovered_pois([new_poi])
    assert state.get_poi("new").discovered_turn == 3


def test_merge_new_poi_owner_forced_none():
    state = new_game()
    new_poi = _make_poi("new", owner=1)
    state.merge_discovered_pois([new_poi])
    assert state.get_poi("new").owner is None


def test_merge_new_poi_placed_turn_forced_none():
    state = new_game()
    new_poi = _make_poi("new", placed_turn=5)
    state.merge_discovered_pois([new_poi])
    assert state.get_poi("new").placed_turn is None


def test_merge_existing_poi_no_overwrite_owner():
    state = new_game()
    existing = _make_poi("p", owner=1, discovered_turn=0)
    state.pois = [existing]
    incoming = _make_poi("p", owner=None, discovered_turn=99)
    state.merge_discovered_pois([incoming])
    assert state.get_poi("p").owner == 1
    assert state.get_poi("p").discovered_turn == 0


def test_merge_existing_poi_no_overwrite_placed_turn():
    state = new_game()
    existing = _make_poi("p", placed_turn=2)
    state.pois = [existing]
    incoming = _make_poi("p", placed_turn=None)
    state.merge_discovered_pois([incoming])
    assert state.get_poi("p").placed_turn == 2


def test_merge_does_not_duplicate():
    state = new_game()
    poi = _make_poi("p")
    state.pois = [poi]
    state.merge_discovered_pois([_make_poi("p")])
    assert len(state.pois) == 1


def test_merge_adds_new_and_skips_existing():
    state = new_game()
    state.pois = [_make_poi("old")]
    state.merge_discovered_pois([_make_poi("old"), _make_poi("new")])
    assert len(state.pois) == 2


# ---------------------------------------------------------------------------
# Serialization round-trips
# ---------------------------------------------------------------------------

def test_poi_serialization_roundtrip():
    poi = _make_poi("p1", owner=2, discovered_turn=3, placed_turn=4)
    restored = Poi.from_dict(poi.to_dict())
    assert restored.id == poi.id
    assert restored.lat == poi.lat
    assert restored.lon == poi.lon
    assert restored.owner == poi.owner
    assert restored.discovered_turn == poi.discovered_turn
    assert restored.placed_turn == poi.placed_turn
    assert restored.raw == poi.raw


def test_poi_from_dict_converts_strings_to_float():
    d = _make_poi("p").to_dict()
    d["lat"] = "25.033"
    d["lon"] = "121.565"
    p = Poi.from_dict(d)
    assert isinstance(p.lat, float)
    assert isinstance(p.lon, float)


def test_gamestate_serialization_roundtrip():
    state = new_game()
    state.pois = [_make_poi("a", owner=1), _make_poi("b", owner=None)]
    state.moves = [_make_move(1, "a", 0)]
    d = state.to_dict()
    restored = GameState.from_dict(d)
    assert restored.game_id == state.game_id
    assert restored.turn_index == state.turn_index
    assert restored.status == state.status
    assert len(restored.pois) == 2
    assert restored.pois[0].owner == 1
    assert len(restored.moves) == 1
    assert restored.players[1].trump_available is True


def test_from_dict_bad_status_raises():
    state = new_game()
    d = state.to_dict()
    d["status"] = "unknown"
    with pytest.raises(ValueError):
        GameState.from_dict(d)


def test_from_dict_empty_status_raises():
    state = new_game()
    d = state.to_dict()
    d["status"] = ""
    with pytest.raises(ValueError):
        GameState.from_dict(d)


def test_route_record_serialization_roundtrip():
    rr = RouteRecord(
        id="route_abc",
        turn_index=2,
        player_id=1,
        from_poi_id="p1",
        to_poi_id="p2",
        coordinates_lonlat=[[121.5, 25.0], [121.6, 25.1]],
        distance_m=500.0,
        duration_s=300.0,
        buffer_m=50.0,
    )
    restored = RouteRecord.from_dict(rr.to_dict())
    assert restored.id == rr.id
    assert restored.duration_s == 300.0
    assert restored.coordinates_lonlat == [[121.5, 25.0], [121.6, 25.1]]
