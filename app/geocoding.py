from __future__ import annotations

import json
import logging
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .media import MediaPhoto
from .models import PhotoLocation


logger = logging.getLogger(__name__)


AMAP_REVERSE_GEOCODE_URL = "https://restapi.amap.com/v3/geocode/regeo"
TRUSTED_POI_DISTANCE_METERS = 300.0


class GeocodingError(RuntimeError):
    pass


class AmapReverseGeocoder:
    def __init__(self, api_key: str, timeout_seconds: float = 8.0) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def reverse(self, latitude: float, longitude: float) -> PhotoLocation:
        gcj_latitude, gcj_longitude = wgs84_to_gcj02(latitude, longitude)
        query = urllib.parse.urlencode(
            {
                "key": self.api_key,
                "location": f"{gcj_longitude:.6f},{gcj_latitude:.6f}",
                "radius": "1000",
                "extensions": "all",
                "roadlevel": "0",
            }
        )
        request = urllib.request.Request(
            f"{AMAP_REVERSE_GEOCODE_URL}?{query}",
            headers={"User-Agent": "travel-journal/1.0"},
        )
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                return parse_amap_location(payload)
            except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
                last_error = exc
                if attempt == 0:
                    logger.warning(
                        "amap_reverse_geocode_retry error_type=%s",
                        type(exc).__name__,
                        exc_info=True,
                    )
                    time.sleep(0.25)
        raise GeocodingError("高德地址查询失败") from last_error


def parse_amap_location(payload: dict[str, Any]) -> PhotoLocation:
    if str(payload.get("status")) != "1":
        info = _clean_text(payload.get("info")) or "未知错误"
        raise GeocodingError(f"高德地址查询失败：{info}")
    regeocode = payload.get("regeocode")
    if not isinstance(regeocode, dict):
        raise GeocodingError("高德没有返回地址数据")
    component = regeocode.get("addressComponent")
    component = component if isinstance(component, dict) else {}

    province = _clean_text(component.get("province"))
    city = _clean_text(component.get("city")) or province
    district = _clean_text(component.get("district"))
    township = _clean_text(component.get("township"))
    formatted_address = _clean_text(regeocode.get("formatted_address"))

    poi_name = ""
    poi_distance: float | None = None
    pois = regeocode.get("pois")
    if isinstance(pois, list):
        nearby_pois: list[tuple[float, str]] = []
        for poi in pois:
            if not isinstance(poi, dict):
                continue
            name = _clean_text(poi.get("name"))
            try:
                distance = float(poi.get("distance"))
            except (TypeError, ValueError):
                continue
            if name and distance <= TRUSTED_POI_DISTANCE_METERS:
                nearby_pois.append((distance, name))
        if nearby_pois:
            poi_distance, poi_name = min(nearby_pois)

    city_label = _short_place(city)
    detail = poi_name or township or district
    detail_label = _short_place(detail)
    if detail_label and detail_label != city_label:
        display_name = f"{city_label} · {detail_label}" if city_label else detail_label
    else:
        display_name = city_label or _short_place(province) or detail_label
    location_key = "|".join(
        value for value in (province, city, district, poi_name or township) if value
    )

    return PhotoLocation(
        province=province,
        city=city,
        district=district,
        township=township,
        poi_name=poi_name,
        formatted_address=formatted_address,
        display_name=display_name,
        location_key=location_key or display_name,
        confidence="poi" if poi_name and poi_distance is not None else "address",
    )


def cluster_photos_by_location(
    photos: list[MediaPhoto], radius_meters: float = 200.0
) -> list[list[MediaPhoto]]:
    clusters: list[list[MediaPhoto]] = []
    for photo in photos:
        if photo.latitude is None or photo.longitude is None:
            continue
        matched = next(
            (
                cluster
                for cluster in clusters
                if haversine_meters(photo, cluster[0]) <= radius_meters
            ),
            None,
        )
        if matched is None:
            clusters.append([photo])
        else:
            matched.append(photo)
    return clusters


def haversine_meters(left: MediaPhoto, right: MediaPhoto) -> float:
    if (
        left.latitude is None
        or left.longitude is None
        or right.latitude is None
        or right.longitude is None
    ):
        return math.inf
    radius = 6_371_008.8
    lat1, lat2 = math.radians(left.latitude), math.radians(right.latitude)
    dlat = lat2 - lat1
    dlon = math.radians(right.longitude - left.longitude)
    value = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * radius * math.asin(min(1.0, math.sqrt(value)))


def wgs84_to_gcj02(latitude: float, longitude: float) -> tuple[float, float]:
    if not _inside_china(latitude, longitude):
        return latitude, longitude
    a = 6_378_245.0
    eccentricity = 0.006693421622965943
    delta_latitude = _transform_latitude(longitude - 105.0, latitude - 35.0)
    delta_longitude = _transform_longitude(longitude - 105.0, latitude - 35.0)
    radians = latitude / 180.0 * math.pi
    magic = math.sin(radians)
    magic = 1 - eccentricity * magic * magic
    sqrt_magic = math.sqrt(magic)
    latitude_scale = (a * (1 - eccentricity)) / (magic * sqrt_magic)
    delta_latitude = delta_latitude * 180.0 / (latitude_scale * math.pi)
    delta_longitude = delta_longitude * 180.0 / (a / sqrt_magic * math.cos(radians) * math.pi)
    return latitude + delta_latitude, longitude + delta_longitude


def _inside_china(latitude: float, longitude: float) -> bool:
    return 0.8293 <= latitude <= 55.8271 and 72.004 <= longitude <= 137.8347


def _transform_latitude(x: float, y: float) -> float:
    result = -100 + 2 * x + 3 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    result += (
        20 * math.sin(6 * x * math.pi) + 20 * math.sin(2 * x * math.pi)
    ) * 2 / 3
    result += (20 * math.sin(y * math.pi) + 40 * math.sin(y / 3 * math.pi)) * 2 / 3
    result += (
        160 * math.sin(y / 12 * math.pi) + 320 * math.sin(y * math.pi / 30)
    ) * 2 / 3
    return result


def _transform_longitude(x: float, y: float) -> float:
    result = 300 + x + 2 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    result += (
        20 * math.sin(6 * x * math.pi) + 20 * math.sin(2 * x * math.pi)
    ) * 2 / 3
    result += (20 * math.sin(x * math.pi) + 40 * math.sin(x / 3 * math.pi)) * 2 / 3
    result += (150 * math.sin(x / 12 * math.pi) + 300 * math.sin(x / 30 * math.pi)) * 2 / 3
    return result


def _clean_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _short_place(value: str) -> str:
    text = value.strip()
    suffixes = (
        "特别行政区",
        "壮族自治区",
        "回族自治区",
        "维吾尔自治区",
        "自治区",
        "自治州",
        "省",
        "市",
        "区",
        "县",
    )
    for suffix in suffixes:
        if text.endswith(suffix) and len(text) > len(suffix):
            return text[: -len(suffix)]
    return text
