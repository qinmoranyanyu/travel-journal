from pathlib import Path

import pytest

from app.geocoding import (
    cluster_photos_by_location,
    parse_amap_location,
    parse_amap_nearby,
    wgs84_to_gcj02,
)
from app.media import MediaPhoto
from app.models import PhotoLocation


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


def test_nearby_landmark_prefers_category_then_rating_then_distance():
    landmark = parse_amap_nearby(
        {
            "status": "1",
            "pois": [
                {
                    "name": "北山公园",
                    "distance": "120",
                    "type": "风景名胜;公园广场;公园",
                    "typecode": "110101",
                    "biz_ext": {"rating": "4.9"},
                },
                {
                    "name": "西湖国家级风景名胜区",
                    "distance": "2200",
                    "type": "风景名胜;风景名胜;国家级景点",
                    "typecode": "110202",
                    "biz_ext": {"rating": "4.6"},
                },
            ],
        },
        PhotoLocation(poi_name="曲院风荷"),
    )

    assert landmark is not None
    assert landmark.name == "西湖国家级风景名胜区"
    assert landmark.category == "重要景区"
    assert landmark.distance_meters == 2200
    assert landmark.rating == 4.6


def test_nearby_landmark_omits_capture_poi_and_low_confidence_places():
    landmark = parse_amap_nearby(
        {
            "status": "1",
            "pois": [
                {
                    "name": "孤山",
                    "distance": "80",
                    "type": "风景名胜;风景名胜",
                    "typecode": "110200",
                },
                {
                    "name": "某便利店",
                    "distance": "40",
                    "type": "购物服务;便民商店",
                    "typecode": "060200",
                },
                {
                    "name": "某培训学校",
                    "distance": "300",
                    "type": "科教文化服务;培训机构",
                    "typecode": "141400",
                },
                {
                    "name": "某景区-日落时刻(打卡点)",
                    "distance": "180",
                    "type": "风景名胜;风景名胜;观景点",
                    "typecode": "110209",
                    "biz_ext": {"rating": "4.9"},
                },
                {
                    "name": "无名小景",
                    "distance": "100",
                    "type": "风景名胜;风景名胜相关;旅游景点",
                    "typecode": "110000",
                    "biz_ext": {"rating": "3.2"},
                },
            ],
        },
        PhotoLocation(poi_name="孤山"),
    )

    assert landmark is None


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
