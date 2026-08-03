from pathlib import Path

import pytest

from app.geocoding import (
    cluster_photos_by_location,
    parse_amap_location,
    wgs84_to_gcj02,
)
from app.media import MediaPhoto


def make_photo(photo_id: str, latitude: float, longitude: float) -> MediaPhoto:
    photo = MediaPhoto(photo_id, f"{photo_id}.jpg", Path(f"{photo_id}.jpg"), 0)
    photo.latitude = latitude
    photo.longitude = longitude
    photo.gps_inspected = True
    return photo


def test_wgs84_is_converted_to_gcj02_in_china():
    latitude, longitude = wgs84_to_gcj02(39.908823, 116.39747)

    assert latitude == pytest.approx(39.910226, abs=0.0001)
    assert longitude == pytest.approx(116.403714, abs=0.0001)


def test_amap_response_uses_nearby_poi_for_display_name():
    location = parse_amap_location(
        {
            "status": "1",
            "regeocode": {
                "formatted_address": "浙江省杭州市西湖区北山街道孤山路",
                "addressComponent": {
                    "province": "浙江省",
                    "city": "杭州市",
                    "district": "西湖区",
                    "township": "北山街道",
                },
                "pois": [{"name": "孤山", "distance": "83"}],
            },
        }
    )

    assert location.display_name == "杭州 · 孤山"
    assert location.poi_name == "孤山"
    assert location.confidence == "poi"


def test_amap_response_ignores_distant_poi():
    location = parse_amap_location(
        {
            "status": "1",
            "regeocode": {
                "formatted_address": "浙江省杭州市西湖区北山街道",
                "addressComponent": {
                    "province": "浙江省",
                    "city": "杭州市",
                    "district": "西湖区",
                    "township": "北山街道",
                },
                "pois": [{"name": "某商店", "distance": "640"}],
            },
        }
    )

    assert location.display_name == "杭州 · 北山街道"
    assert location.poi_name == ""
    assert location.confidence == "address"


def test_nearby_photos_share_a_location_cluster():
    photos = [
        make_photo("one", 30.2731, 120.1645),
        make_photo("two", 30.2735, 120.1648),
        make_photo("three", 30.2900, 120.1800),
    ]

    clusters = cluster_photos_by_location(photos, radius_meters=200)

    assert [[photo.id for photo in cluster] for cluster in clusters] == [
        ["one", "two"],
        ["three"],
    ]
