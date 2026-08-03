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
from .models import NearbyLandmark, PhotoLocation


logger = logging.getLogger(__name__)


AMAP_REVERSE_GEOCODE_URL = "https://restapi.amap.com/v3/geocode/regeo"
AMAP_NEARBY_SEARCH_URL = "https://restapi.amap.com/v3/place/around"
TRUSTED_POI_DISTANCE_METERS = 300.0
NEARBY_LANDMARK_RADIUS_METERS = 3000.0
NEARBY_LANDMARK_TYPES = "110000|140100|140200|140400|140600"

_EXCLUDED_LANDMARK_TERMS = (
    "停车场",
    "停车区",
    "住宅",
    "小区",
    "公寓",
    "便利店",
    "超市",
    "商场",
    "购物中心",
    "办公室",
    "写字楼",
    "公司",
    "售票处",
    "打卡点",
    "游客中心",
    "服务中心",
    "检票口",
    "入口",
    "出口",
    "卫生间",
    "厕所",
    "小广场",
)
_EXCLUDED_TYPE_TERMS = (
    "餐饮",
    "购物",
    "生活服务",
    "商务住宅",
    "停车场",
    "公司企业",
    "道路附属",
    "公共设施",
)


class GeocodingError(RuntimeError):
    pass


class AmapReverseGeocoder:
    def __init__(self, api_key: str, timeout_seconds: float = 8.0) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def reverse(self, latitude: float, longitude: float) -> PhotoLocation:
        gcj_latitude, gcj_longitude = wgs84_to_gcj02(latitude, longitude)
        payload = self._request_json(
            AMAP_REVERSE_GEOCODE_URL,
            {
                "key": self.api_key,
                "location": f"{gcj_longitude:.6f},{gcj_latitude:.6f}",
                "radius": "1000",
                "extensions": "all",
                "roadlevel": "0",
            },
            operation="reverse_geocode",
        )
        location = parse_amap_location(payload)
        logger.info(
            "amap_reverse_geocode_success confidence=%s has_poi=%s",
            location.confidence,
            bool(location.poi_name),
        )
        return location

    def nearby(
        self,
        latitude: float,
        longitude: float,
        capture_location: PhotoLocation,
    ) -> NearbyLandmark | None:
        gcj_latitude, gcj_longitude = wgs84_to_gcj02(latitude, longitude)
        payload = self._request_json(
            AMAP_NEARBY_SEARCH_URL,
            {
                "key": self.api_key,
                "location": f"{gcj_longitude:.6f},{gcj_latitude:.6f}",
                "radius": str(int(NEARBY_LANDMARK_RADIUS_METERS)),
                "types": NEARBY_LANDMARK_TYPES,
                "sortrule": "weight",
                "offset": "20",
                "page": "1",
                "extensions": "all",
            },
            operation="nearby_search",
        )
        landmark = parse_amap_nearby(payload, capture_location)
        if landmark:
            logger.info(
                "amap_nearby_search_success selected=%s category=%s distance_meters=%.0f",
                landmark.name,
                landmark.category,
                landmark.distance_meters,
            )
        else:
            logger.info("amap_nearby_search_empty meaningful_landmark=false")
        return landmark

    def _request_json(
        self,
        url: str,
        params: dict[str, str],
        *,
        operation: str,
    ) -> dict[str, Any]:
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f"{url}?{query}", headers={"User-Agent": "travel-journal/1.0"}
        )
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("高德返回了非对象 JSON")
                return payload
            except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
                last_error = exc
                if attempt == 0:
                    logger.warning(
                        "amap_request_retry operation=%s error_type=%s error=%s",
                        operation,
                        type(exc).__name__,
                        exc,
                        exc_info=True,
                    )
                    time.sleep(0.25)
        raise GeocodingError(f"高德{operation}请求失败") from last_error


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


def parse_amap_nearby(
    payload: dict[str, Any],
    capture_location: PhotoLocation,
) -> NearbyLandmark | None:
    if str(payload.get("status")) != "1":
        info = _clean_text(payload.get("info")) or "未知错误"
        raise GeocodingError(f"高德附近地标查询失败：{info}")

    pois = payload.get("pois")
    if not isinstance(pois, list):
        return None

    candidates: list[tuple[float, float, float, NearbyLandmark]] = []
    for poi in pois:
        if not isinstance(poi, dict):
            continue
        landmark = _parse_landmark_candidate(poi)
        if landmark is None or _same_place(landmark.name, capture_location.poi_name):
            continue
        priority = _landmark_priority(landmark.category)
        rating = landmark.rating if landmark.rating is not None else -1.0
        candidates.append((-priority, -rating, landmark.distance_meters, landmark))

    if not candidates:
        return None
    return min(candidates, key=lambda item: item[:3])[3]


def _parse_landmark_candidate(poi: dict[str, Any]) -> NearbyLandmark | None:
    name = _clean_text(poi.get("name"))
    type_name = _clean_text(poi.get("type"))
    typecode = _clean_text(poi.get("typecode"))
    if not name or any(term in name for term in _EXCLUDED_LANDMARK_TERMS):
        return None
    if any(term in type_name for term in _EXCLUDED_TYPE_TERMS):
        return None
    try:
        distance = float(poi.get("distance"))
    except (TypeError, ValueError):
        return None
    if not 0 <= distance <= NEARBY_LANDMARK_RADIUS_METERS:
        return None

    category = _landmark_category(name, type_name, typecode)
    if not category:
        return None
    biz_ext = poi.get("biz_ext")
    rating = _optional_float(biz_ext.get("rating")) if isinstance(biz_ext, dict) else None
    if category in {"景点", "文化场馆", "城市地标"} and (
        rating is None or rating < 4.0
    ):
        return None
    return NearbyLandmark(
        name=name,
        distance_meters=round(distance, 1),
        category=category,
        typecode=typecode,
        rating=rating,
    )


def _landmark_category(name: str, type_name: str, typecode: str) -> str:
    combined = f"{name}|{type_name.replace(';', '|')}"
    if any(term in combined for term in ("世界遗产", "国家级景点", "自然保护区", "地质公园")):
        return "重要景区"
    if any(
        term in combined
        for term in (
            "寺庙",
            "寺",
            "道观",
            "教堂",
            "清真寺",
            "古迹",
            "遗址",
            "纪念馆",
            "故居",
            "祠",
            "陵",
            "古城",
            "古镇",
        )
    ):
        return "历史文化"
    if any(term in combined for term in ("博物馆", "美术馆", "科技馆", "展览馆")):
        return "博物馆"
    if any(
        term in combined
        for term in ("自然保护区", "森林公园", "湿地", "山", "湖", "岛", "海滩", "瀑布", "自然景观")
    ):
        return "自然地标"
    if any(term in combined for term in ("公园", "植物园", "动物园", "广场")):
        return "公园"
    if typecode.startswith("110") or "风景名胜" in combined:
        if any(name.endswith(suffix) for suffix in ("塔", "楼", "桥")):
            return "城市地标"
        return "景点"
    if typecode.startswith(("1401", "1402", "1404", "1406")):
        return "文化场馆"
    return ""


def _landmark_priority(category: str) -> float:
    return {
        "重要景区": 6.0,
        "历史文化": 5.5,
        "自然地标": 5.0,
        "博物馆": 4.8,
        "城市地标": 4.6,
        "景点": 4.2,
        "公园": 4.0,
        "文化场馆": 3.5,
    }.get(category, 0.0)


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _same_place(left: str, right: str) -> bool:
    if not left or not right:
        return False
    def normalize(value: str) -> str:
        return "".join(character for character in value if character.isalnum())

    return normalize(left).casefold() == normalize(right).casefold()


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
