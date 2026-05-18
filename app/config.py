from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Config:
    DEFAULT_CENTER_LAT: float = float(os.getenv("DEFAULT_CENTER_LAT", "25.0330"))
    DEFAULT_CENTER_LON: float = float(os.getenv("DEFAULT_CENTER_LON", "121.5654"))
    DEFAULT_ZOOM: int = int(os.getenv("DEFAULT_ZOOM", "15"))

    NOMINATIM_USER_AGENT: str = os.getenv(
        "NOMINATIM_USER_AGENT", "geoflip-coursework/0.1 (geoflip@example.com)"
    )
    NOMINATIM_EMAIL: str = os.getenv("NOMINATIM_EMAIL", "geoflip@example.com")
    NOMINATIM_BASE_URL: str = os.getenv(
        "NOMINATIM_BASE_URL", "https://nominatim.openstreetmap.org"
    )
    NOMINATIM_MIN_INTERVAL_SECONDS: float = float(
        os.getenv("NOMINATIM_MIN_INTERVAL_SECONDS", "1.0")
    )

    OSRM_BASE_URL: str = os.getenv(
        "OSRM_BASE_URL", "https://router.project-osrm.org"
    )
    OSRM_PROFILE: str = os.getenv("OSRM_PROFILE", "foot")

    STATE_FILE: str = os.getenv("STATE_FILE", "data/state.json")
    REQUEST_TIMEOUT_SECONDS: float = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "10"))
