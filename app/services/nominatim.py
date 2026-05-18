from __future__ import annotations

import hashlib
import time
from typing import Any

import httpx

from app.models import Poi, score_poi


class NominatimError(Exception):
    """Raised when a Nominatim API call fails for any reason."""


# ---------------------------------------------------------------------------
# Raw-field whitelist (exactly as specified in 1.6)
# ---------------------------------------------------------------------------

_RAW_TOP = frozenset(
    {"display_name", "name", "class", "type", "osm_type", "osm_id", "importance"}
)
_RAW_ADDRESS = frozenset(
    {
        "country_code", "city", "town", "village", "suburb",
        "neighbourhood", "road", "house_number", "postcode",
    }
)
_RAW_EXTRATAGS = frozenset(
    {"website", "wikidata", "wikipedia", "opening_hours", "phone"}
)

# Boundary sub-types that are still interesting as game targets
_BOUNDARY_KEEP = frozenset({"national_park", "protected_area"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_raw(result: dict) -> dict:
    """Extract only whitelisted fields from a Nominatim result."""
    raw: dict = {}
    for key in _RAW_TOP:
        if key in result:
            raw[key] = result[key]

    address = result.get("address") or {}
    filtered_addr = {k: v for k, v in address.items() if k in _RAW_ADDRESS}
    if filtered_addr:
        raw["address"] = filtered_addr

    extratags = result.get("extratags") or {}
    filtered_extra = {k: v for k, v in extratags.items() if k in _RAW_EXTRATAGS}
    if filtered_extra:
        raw["extratags"] = filtered_extra

    return raw


def _fallback_id(lat: float, lon: float, name: str) -> str:
    """Stable, collision-resistant fallback ID for results without osm_type/osm_id."""
    key = f"{lat:.8f}:{lon:.8f}:{name}"
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    return f"hash:{digest}"


def _normalize(result: dict) -> Poi | None:
    """
    Convert one Nominatim result dict to a Poi.
    Returns None for results that should be filtered out.
    """
    lat_raw = result.get("lat")
    lon_raw = result.get("lon")
    if not lat_raw or not lon_raw:
        return None

    try:
        lat = float(lat_raw)
        lon = float(lon_raw)
    except (ValueError, TypeError):
        return None

    category: str = result.get("class", "")
    poi_type: str = result.get("type", "")

    # Filter administrative boundaries that aren't scenic POIs
    if category == "boundary" and poi_type not in _BOUNDARY_KEEP:
        return None

    osm_type: str | None = result.get("osm_type")
    osm_id_raw = result.get("osm_id")
    osm_id: int | None = int(osm_id_raw) if osm_id_raw is not None else None

    if osm_type and osm_id is not None:
        poi_id = f"{osm_type}:{osm_id}"
    else:
        name_for_hash = (
            result.get("name") or result.get("display_name") or ""
        )
        poi_id = _fallback_id(lat, lon, name_for_hash)

    # Name priority: namedetails["name"] > name > display_name
    namedetails: dict = result.get("namedetails") or {}
    name: str = (
        namedetails.get("name")
        or result.get("name")
        or result.get("display_name", "Unknown")
    )

    return Poi(
        id=poi_id,
        name=name,
        lat=lat,
        lon=lon,
        osm_type=osm_type,
        osm_id=osm_id,
        category=category,
        poi_type=poi_type,
        score=score_poi(category, poi_type),
        owner=None,
        discovered_turn=None,
        placed_turn=None,
        raw=_build_raw(result),
    )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class NominatimClient:
    """
    Thin wrapper around the Nominatim search API.

    Enforces:
    - User-Agent header (Nominatim ToS)
    - In-memory response cache
    - Minimum inter-request interval (rate limit) for cache misses
    """

    def __init__(
        self,
        base_url: str,
        user_agent: str,
        email: str,
        timeout_seconds: float = 10.0,
        min_interval_seconds: float = 1.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "User-Agent": user_agent,
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        }
        self._email = email
        self._timeout = timeout_seconds
        self._min_interval = min_interval_seconds

        self._cache: dict[str, list[Poi]] = {}
        self._last_request_time: float = 0.0

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _cache_key(
        self,
        query: str,
        center_lat: float | None,
        center_lon: float | None,
        search_km: float | None,
        limit: int,
    ) -> str:
        return f"{query}|{center_lat}|{center_lon}|{search_km}|{limit}"

    def _rate_limit(self) -> None:
        """Sleep if less than min_interval has elapsed since the last HTTP request."""
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

    def _record_request_time(self) -> None:
        self._last_request_time = time.monotonic()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        center_lat: float | None = None,
        center_lon: float | None = None,
        search_km: float | None = None,
        limit: int = 10,
    ) -> list[Poi]:
        """
        Search Nominatim for POI candidates matching *query*.

        Returns a de-duplicated list of Poi objects (owner=None).
        Raises NominatimError on any network / parse failure.
        """
        key = self._cache_key(query, center_lat, center_lon, search_km, limit)
        if key in self._cache:
            return self._cache[key]

        # --- rate limit before sending ---
        self._rate_limit()

        params: dict[str, Any] = {
            "format": "jsonv2",
            "q": query,
            "limit": limit,
            "addressdetails": 1,
            "extratags": 1,
            "dedupe": 1,
            "email": self._email,
        }

        if (
            center_lat is not None
            and center_lon is not None
            and search_km is not None
        ):
            delta = search_km / 111.0  # rough degrees/km
            params["viewbox"] = (
                f"{center_lon - delta},{center_lat + delta},"
                f"{center_lon + delta},{center_lat - delta}"
            )
            params["bounded"] = 1

        try:
            with httpx.Client(headers=self._headers, timeout=self._timeout) as client:
                resp = client.get(f"{self._base_url}/search", params=params)
                resp.raise_for_status()
                results: list[dict] = resp.json()
        except httpx.TimeoutException as exc:
            raise NominatimError("搜尋服務暫時無法使用，請稍後再試") from exc
        except httpx.HTTPStatusError as exc:
            raise NominatimError(
                f"Nominatim HTTP error {exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            raise NominatimError(f"Nominatim request error: {exc}") from exc
        except (ValueError, KeyError) as exc:
            raise NominatimError(f"Nominatim response parse error: {exc}") from exc

        self._record_request_time()

        pois: list[Poi] = []
        seen_ids: set[str] = set()
        for result in results:
            poi = _normalize(result)
            if poi is None:
                continue
            if poi.id in seen_ids:
                continue
            seen_ids.add(poi.id)
            pois.append(poi)

        self._cache[key] = pois
        return pois
