from __future__ import annotations

from html import unescape
import re
from typing import Any

import httpx

from app.models import Poi, score_poi
from app.services.tls import build_ssl_context, is_certificate_verify_error


class OverpassError(Exception):
    """Raised when an Overpass API call fails for any reason."""


# ---------------------------------------------------------------------------
# Tag profile
# ---------------------------------------------------------------------------
#
# Ordered list of (category, allowed_types).
# * allowed_types is a frozenset → only those values count as supported.
# * allowed_types is None → any value is acceptable (used for `historic`);
#   for that case we additionally require a name tag, mirroring the QL.

_TAG_PROFILE: tuple[tuple[str, frozenset[str] | None], ...] = (
    (
        "amenity",
        frozenset(
            {
                "cafe", "restaurant", "fast_food", "bar", "library",
                "school", "college", "university", "hospital",
                "theatre", "arts_centre", "place_of_worship", "marketplace",
            }
        ),
    ),
    (
        "tourism",
        frozenset(
            {
                "museum", "attraction", "gallery", "hotel", "hostel",
                "viewpoint", "zoo", "theme_park",
            }
        ),
    ),
    (
        "leisure",
        frozenset({"park", "garden", "playground", "sports_centre", "stadium"}),
    ),
    (
        "shop",
        frozenset(
            {"convenience", "supermarket", "books", "mall", "department_store", "bakery"}
        ),
    ),
    ("railway", frozenset({"station", "halt"})),
    ("public_transport", frozenset({"station", "stop_position", "platform"})),
    ("historic", None),
)


_NAME_KEYS_PRIORITY: tuple[str, ...] = ("name:zh-TW", "name:zh", "name", "name:en")

_RAW_TAG_WHITELIST = frozenset(
    {
        "name", "name:zh", "name:zh-TW", "name:en",
        "website", "wikidata", "wikipedia", "opening_hours", "phone",
    }
)


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _pick_category(tags: dict) -> tuple[str, str] | None:
    """Return (category, poi_type) for the first matching profile tag, else None."""
    for category, allowed in _TAG_PROFILE:
        value = tags.get(category)
        if not value or not isinstance(value, str):
            continue
        if allowed is None:
            if not any(tags.get(k) for k in _NAME_KEYS_PRIORITY):
                return None
            return category, value
        if value in allowed:
            return category, value
    return None


def _pick_name(tags: dict, category: str, poi_type: str) -> tuple[str, bool]:
    """Return (name, has_real_name)."""
    for key in _NAME_KEYS_PRIORITY:
        v = tags.get(key)
        if v:
            return v, True
    return f"{category}:{poi_type}", False


def _build_raw(tags: dict, category: str) -> dict:
    raw: dict = {}
    for k in _RAW_TAG_WHITELIST:
        if k in tags:
            raw[k] = tags[k]
    if category in tags:
        raw[category] = tags[category]
    return raw


def _element_coords(element: dict) -> tuple[float, float] | None:
    if element.get("type") == "node":
        lat = element.get("lat")
        lon = element.get("lon")
    else:
        center = element.get("center") or {}
        lat = center.get("lat")
        lon = center.get("lon")
    if lat is None or lon is None:
        return None
    try:
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None


def _normalize(element: dict) -> Poi | None:
    """
    Convert one Overpass element to a Poi.
    Returns None for unsupported / malformed elements.
    """
    elem_type = element.get("type")
    if elem_type not in {"node", "way", "relation"}:
        return None

    tags = element.get("tags")
    if not isinstance(tags, dict) or not tags:
        return None

    picked = _pick_category(tags)
    if picked is None:
        return None
    category, poi_type = picked

    coords = _element_coords(element)
    if coords is None:
        return None
    lat, lon = coords

    osm_id_raw = element.get("id")
    if osm_id_raw is None:
        return None
    try:
        osm_id = int(osm_id_raw)
    except (TypeError, ValueError):
        return None

    name, _has_real_name = _pick_name(tags, category, poi_type)

    return Poi(
        id=f"{elem_type}:{osm_id}",
        name=name,
        lat=lat,
        lon=lon,
        osm_type=elem_type,
        osm_id=osm_id,
        category=category,
        poi_type=poi_type,
        score=score_poi(category, poi_type),
        owner=None,
        discovered_turn=None,
        placed_turn=None,
        raw=_build_raw(tags, category),
    )


# ---------------------------------------------------------------------------
# Query builder
# ---------------------------------------------------------------------------

def _build_query(
    center_lat: float,
    center_lon: float,
    radius_m: float,
    limit: int,
) -> str:
    rad = int(round(radius_m))
    parts: list[str] = []
    for category, allowed in _TAG_PROFILE:
        if allowed is None:
            parts.append(
                f'  nwr["{category}"]["name"](around:{rad},{center_lat},{center_lon});'
            )
        else:
            alt = "|".join(sorted(allowed))
            parts.append(
                f'  nwr["{category}"~"^({alt})$"](around:{rad},{center_lat},{center_lon});'
            )
    body = "\n".join(parts)
    return (
        "[out:json][timeout:25];\n"
        "(\n"
        f"{body}\n"
        ");\n"
        f"out center {int(limit)};\n"
    )


def _response_error_summary(response: httpx.Response) -> str:
    """Return a compact plain-text Overpass error body, if one exists."""
    text = response.text
    if not isinstance(text, str):
        return ""
    text = text.strip()
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = " ".join(text.split())
    return text[:300]


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class OverpassClient:
    """
    Thin wrapper around the Overpass API.

    Fetches a bounded set of POI candidates around a center coordinate,
    using a curated tag profile that maps to existing game scoring.
    """

    def __init__(self, base_url: str, timeout_seconds: float = 25.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._cache: dict[tuple[float, float, float, int], list[Poi]] = {}
        self._ssl_context = build_ssl_context()

    def _cache_key(
        self, center_lat: float, center_lon: float, radius_m: float, limit: int
    ) -> tuple[float, float, float, int]:
        # Round center to ~10m grid so nearby setups share a board fetch.
        return (
            round(center_lat, 4),
            round(center_lon, 4),
            float(radius_m),
            int(limit),
        )

    def fetch_board_pois(
        self,
        center_lat: float,
        center_lon: float,
        radius_m: float,
        limit: int = 60,
    ) -> list[Poi]:
        """
        Fetch POI candidates around (center_lat, center_lon) within radius_m.

        Returns a de-duplicated list of Poi objects (owner=None).
        Raises OverpassError on any network / parse failure.
        """
        key = self._cache_key(center_lat, center_lon, radius_m, limit)
        if key in self._cache:
            return self._cache[key]

        query = _build_query(center_lat, center_lon, radius_m, limit)
        url = f"{self._base_url}/api/interpreter"

        try:
            with httpx.Client(
                headers={"User-Agent": "geoflip-coursework/0.1"},
                timeout=self._timeout,
                verify=self._ssl_context,
            ) as client:
                resp = client.post(url, data={"data": query})
                resp.raise_for_status()
                payload: Any = resp.json()
        except httpx.TimeoutException as exc:
            raise OverpassError("Overpass 服務暫時無法使用，請稍後再試") from exc
        except httpx.HTTPStatusError as exc:
            detail = _response_error_summary(exc.response)
            message = f"Overpass HTTP error {exc.response.status_code}"
            if detail:
                message = f"{message}: {detail}"
            raise OverpassError(message) from exc
        except httpx.RequestError as exc:
            if is_certificate_verify_error(exc):
                raise OverpassError(
                    "SSL 憑證驗證失敗，請更新 certifi/truststore 或確認系統根憑證"
                ) from exc
            raise OverpassError(f"Overpass request error: {exc}") from exc
        except (ValueError, KeyError) as exc:
            raise OverpassError(f"Overpass response parse error: {exc}") from exc

        if not isinstance(payload, dict):
            raise OverpassError("Overpass response is not a JSON object")

        elements = payload.get("elements")
        if not isinstance(elements, list):
            raise OverpassError("Overpass response missing 'elements' list")

        pois: list[Poi] = []
        seen_ids: set[str] = set()
        for element in elements:
            if not isinstance(element, dict):
                continue
            poi = _normalize(element)
            if poi is None:
                continue
            if poi.id in seen_ids:
                continue
            seen_ids.add(poi.id)
            pois.append(poi)
            if len(pois) >= limit:
                break

        self._cache[key] = pois
        return pois
