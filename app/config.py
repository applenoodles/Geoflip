from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_str(name: str, default: str) -> str:
    return os.getenv(name, default)


def _env_float(name: str, default: str) -> float:
    return float(os.getenv(name, default))


def _env_int(name: str, default: str) -> int:
    return int(os.getenv(name, default))


@dataclass
class Config:
    DEFAULT_CENTER_LAT: float = field(
        default_factory=lambda: _env_float("DEFAULT_CENTER_LAT", "25.0330")
    )
    DEFAULT_CENTER_LON: float = field(
        default_factory=lambda: _env_float("DEFAULT_CENTER_LON", "121.5654")
    )
    DEFAULT_ZOOM: int = field(
        default_factory=lambda: _env_int("DEFAULT_ZOOM", "15")
    )

    NOMINATIM_USER_AGENT: str = field(
        default_factory=lambda: _env_str(
            "NOMINATIM_USER_AGENT",
            "geoflip-coursework/0.1 (geoflip@example.com)",
        )
    )
    NOMINATIM_EMAIL: str = field(
        default_factory=lambda: _env_str("NOMINATIM_EMAIL", "geoflip@example.com")
    )
    NOMINATIM_BASE_URL: str = field(
        default_factory=lambda: _env_str(
            "NOMINATIM_BASE_URL", "https://nominatim.openstreetmap.org"
        )
    )
    NOMINATIM_MIN_INTERVAL_SECONDS: float = field(
        default_factory=lambda: _env_float("NOMINATIM_MIN_INTERVAL_SECONDS", "1.0")
    )

    OSRM_BASE_URL: str = field(
        default_factory=lambda: _env_str(
            "OSRM_BASE_URL", "https://router.project-osrm.org"
        )
    )
    OSRM_PROFILE: str = field(
        default_factory=lambda: _env_str("OSRM_PROFILE", "foot")
    )

    OVERPASS_BASE_URL: str = field(
        default_factory=lambda: _env_str(
            "OVERPASS_BASE_URL", "https://overpass-api.de"
        )
    )
    OVERPASS_RADIUS_M: float = field(
        default_factory=lambda: _env_float("OVERPASS_RADIUS_M", "900")
    )
    OVERPASS_MIN_POIS: int = field(
        default_factory=lambda: _env_int("OVERPASS_MIN_POIS", "18")
    )
    OVERPASS_MAX_POIS: int = field(
        default_factory=lambda: _env_int("OVERPASS_MAX_POIS", "36")
    )
    OVERPASS_TIMEOUT_SECONDS: float = field(
        default_factory=lambda: _env_float("OVERPASS_TIMEOUT_SECONDS", "25")
    )

    STATE_FILE: str = field(
        default_factory=lambda: _env_str("STATE_FILE", "data/state.json")
    )
    REQUEST_TIMEOUT_SECONDS: float = field(
        default_factory=lambda: _env_float("REQUEST_TIMEOUT_SECONDS", "10")
    )
