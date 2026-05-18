from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

PlayerId = Literal[1, 2]
GameStatus = Literal["active", "finished"]

_VALID_STATUSES = {"active", "finished"}

# ---------------------------------------------------------------------------
# Score mapping
# ---------------------------------------------------------------------------

_SCORE_3_TOURISM = {"museum", "attraction", "gallery", "zoo", "theme_park"}
_SCORE_3_AMENITY = {"university", "hospital", "theatre", "arts_centre"}
_SCORE_3_LEISURE = {"park", "stadium"}

_SCORE_2_AMENITY = {
    "restaurant", "cafe", "bar", "fast_food", "library",
    "school", "college", "place_of_worship", "marketplace",
}
_SCORE_2_RAILWAY = {"station", "halt"}
_SCORE_2_TOURISM = {"hotel", "hostel", "viewpoint"}
_SCORE_2_LEISURE = {"garden", "playground", "sports_centre"}


def score_poi(category: str, poi_type: str) -> int:
    if category == "historic":
        return 3
    if category == "tourism" and poi_type in _SCORE_3_TOURISM:
        return 3
    if category == "amenity" and poi_type in _SCORE_3_AMENITY:
        return 3
    if category == "leisure" and poi_type in _SCORE_3_LEISURE:
        return 3

    if category == "amenity" and poi_type in _SCORE_2_AMENITY:
        return 2
    if category == "shop":
        return 2
    if category == "railway" and poi_type in _SCORE_2_RAILWAY:
        return 2
    if category == "public_transport":
        return 2
    if category == "tourism" and poi_type in _SCORE_2_TOURISM:
        return 2
    if category == "leisure" and poi_type in _SCORE_2_LEISURE:
        return 2

    return 1


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Poi:
    id: str
    name: str
    lat: float
    lon: float
    osm_type: str | None
    osm_id: int | None
    category: str
    poi_type: str
    score: int
    owner: PlayerId | None
    discovered_turn: int | None
    placed_turn: int | None
    raw: dict

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "lat": self.lat,
            "lon": self.lon,
            "osm_type": self.osm_type,
            "osm_id": self.osm_id,
            "category": self.category,
            "poi_type": self.poi_type,
            "score": self.score,
            "owner": self.owner,
            "discovered_turn": self.discovered_turn,
            "placed_turn": self.placed_turn,
            "raw": self.raw,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Poi:
        return cls(
            id=d["id"],
            name=d["name"],
            lat=float(d["lat"]),
            lon=float(d["lon"]),
            osm_type=d.get("osm_type"),
            osm_id=d.get("osm_id"),
            category=d["category"],
            poi_type=d["poi_type"],
            score=int(d["score"]),
            owner=d.get("owner"),
            discovered_turn=d.get("discovered_turn"),
            placed_turn=d.get("placed_turn"),
            raw=d.get("raw", {}),
        )


@dataclass
class PlayerState:
    id: PlayerId
    name: str
    trump_available: bool

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "trump_available": self.trump_available,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PlayerState:
        return cls(
            id=d["id"],
            name=d["name"],
            trump_available=bool(d["trump_available"]),
        )


@dataclass
class RouteRecord:
    id: str
    turn_index: int
    player_id: PlayerId
    from_poi_id: str
    to_poi_id: str
    coordinates_lonlat: list[list[float]]
    distance_m: float
    duration_s: float
    buffer_m: float

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "turn_index": self.turn_index,
            "player_id": self.player_id,
            "from_poi_id": self.from_poi_id,
            "to_poi_id": self.to_poi_id,
            "coordinates_lonlat": self.coordinates_lonlat,
            "distance_m": self.distance_m,
            "duration_s": self.duration_s,
            "buffer_m": self.buffer_m,
        }

    @classmethod
    def from_dict(cls, d: dict) -> RouteRecord:
        return cls(
            id=d["id"],
            turn_index=int(d["turn_index"]),
            player_id=d["player_id"],
            from_poi_id=d["from_poi_id"],
            to_poi_id=d["to_poi_id"],
            coordinates_lonlat=d["coordinates_lonlat"],
            distance_m=float(d["distance_m"]),
            duration_s=float(d["duration_s"]),
            buffer_m=float(d["buffer_m"]),
        )


@dataclass
class MoveRecord:
    turn_index: int
    player_id: PlayerId
    placed_poi_id: str
    used_trump: bool
    route_ids: list[str]
    flipped_poi_ids: list[str]

    def to_dict(self) -> dict:
        return {
            "turn_index": self.turn_index,
            "player_id": self.player_id,
            "placed_poi_id": self.placed_poi_id,
            "used_trump": self.used_trump,
            "route_ids": list(self.route_ids),
            "flipped_poi_ids": list(self.flipped_poi_ids),
        }

    @classmethod
    def from_dict(cls, d: dict) -> MoveRecord:
        return cls(
            turn_index=int(d["turn_index"]),
            player_id=d["player_id"],
            placed_poi_id=d["placed_poi_id"],
            used_trump=bool(d["used_trump"]),
            route_ids=list(d["route_ids"]),
            flipped_poi_ids=list(d["flipped_poi_ids"]),
        )


@dataclass
class RouteResult:
    coordinates_lonlat: list[list[float]]
    distance_m: float
    duration_s: float


@dataclass
class MoveResult:
    ok: bool
    message: str
    state: GameState
    placed_poi_id: str | None
    flipped_poi_ids: list[str]
    route_ids: list[str]


# ---------------------------------------------------------------------------
# GameState
# ---------------------------------------------------------------------------

@dataclass
class GameState:
    game_id: str
    turn_index: int
    max_turns: int
    players: dict[PlayerId, PlayerState]
    pois: list[Poi]
    routes: list[RouteRecord]
    moves: list[MoveRecord]
    created_at: str
    updated_at: str
    status: GameStatus

    # --- turn / finish ---

    def current_player_id(self) -> PlayerId:
        return 1 if self.turn_index % 2 == 0 else 2

    def is_finished(self) -> bool:
        return self.status == "finished" or self.turn_index >= self.max_turns

    # --- player helpers ---

    def opponent_id(self, player_id: PlayerId) -> PlayerId:
        return 2 if player_id == 1 else 1

    # --- POI queries ---

    def owned_pois(self, player_id: PlayerId) -> list[Poi]:
        """All POIs owned by player (score use only — NOT anchor source)."""
        return [p for p in self.pois if p.owner == player_id]

    def neutral_pois(self) -> list[Poi]:
        return [p for p in self.pois if p.owner is None]

    def get_poi(self, poi_id: str) -> Poi | None:
        for p in self.pois:
            if p.id == poi_id:
                return p
        return None

    # --- scoring ---

    def scores(self) -> dict[PlayerId, int]:
        """Real-time score; never cached."""
        result: dict[PlayerId, int] = {1: 0, 2: 0}
        for poi in self.pois:
            if poi.owner in result:
                result[poi.owner] += poi.score
        return result

    def winner(self) -> PlayerId | None:
        """Meaningful only after is_finished(); None on tie."""
        if not self.is_finished():
            return None
        sc = self.scores()
        if sc[1] > sc[2]:
            return 1
        if sc[2] > sc[1]:
            return 2
        return None

    # --- flag / anchor ---

    def placed_flag_poi_ids(self, player_id: PlayerId) -> list[str]:
        """POI IDs the player actively placed (from MoveRecord history)."""
        return [m.placed_poi_id for m in self.moves if m.player_id == player_id]

    def placed_flag_pois(self, player_id: PlayerId) -> list[Poi]:
        ids = set(self.placed_flag_poi_ids(player_id))
        return [p for p in self.pois if p.id in ids]

    def anchor_pois(self, player_id: PlayerId) -> list[Poi]:
        """POIs placed by player AND still owned by player — only valid route endpoints."""
        placed_ids = set(self.placed_flag_poi_ids(player_id))
        return [p for p in self.pois if p.id in placed_ids and p.owner == player_id]

    def has_anchor_flag(self, player_id: PlayerId) -> bool:
        return len(self.anchor_pois(player_id)) > 0

    # --- merge ---

    def merge_discovered_pois(self, pois: list[Poi]) -> None:
        existing_ids = {p.id for p in self.pois}
        for new_poi in pois:
            if new_poi.id not in existing_ids:
                new_poi.discovered_turn = self.turn_index
                new_poi.owner = None
                new_poi.placed_turn = None
                self.pois.append(new_poi)
                existing_ids.add(new_poi.id)
            # existing POIs: owner / placed_turn / discovered_turn are never overwritten

    # --- serialization ---

    def to_dict(self) -> dict:
        return {
            "game_id": self.game_id,
            "turn_index": self.turn_index,
            "max_turns": self.max_turns,
            "players": {str(k): v.to_dict() for k, v in self.players.items()},
            "pois": [p.to_dict() for p in self.pois],
            "routes": [r.to_dict() for r in self.routes],
            "moves": [m.to_dict() for m in self.moves],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> GameState:
        status = d.get("status", "")
        if status not in _VALID_STATUSES:
            raise ValueError(f"Invalid GameState status: {status!r}")

        players: dict[PlayerId, PlayerState] = {}
        for k, v in d["players"].items():
            pid: PlayerId = int(k)  # type: ignore[assignment]
            players[pid] = PlayerState.from_dict(v)

        return cls(
            game_id=d["game_id"],
            turn_index=int(d["turn_index"]),
            max_turns=int(d["max_turns"]),
            players=players,
            pois=[Poi.from_dict(p) for p in d.get("pois", [])],
            routes=[RouteRecord.from_dict(r) for r in d.get("routes", [])],
            moves=[MoveRecord.from_dict(m) for m in d.get("moves", [])],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            status=status,  # type: ignore[arg-type]
        )
