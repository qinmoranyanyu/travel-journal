import json
import zipfile

from PIL import Image

from app.models import AlbumManifest, ImageStyle
from app.rendering import create_share_zip, render_album


def test_rendered_album_is_portable_and_zip_excludes_sources(tmp_path):
    output = tmp_path / "album"
    photos = output / "assets" / "photos"
    sources = output / "sources"
    photos.mkdir(parents=True)
    sources.mkdir()
    Image.new("RGB", (30, 40), "white").save(photos / "p1.jpg")
    Image.new("RGB", (30, 40), "red").save(sources / "p1.jpg")
    manifest = AlbumManifest(
        id="test",
        title="测试旅行",
        date_range="2025.06.01",
        cover_subtitle="山路与旧友",
        closing="旅程已经结束，沿途仍留在纸上。",
        route_locations=["杭州 · 孤山"],
        route_summary="杭州 · 孤山",
        image_style=ImageStyle.minimal_zine,
        image_width=900,
        image_height=1500,
        chapters=[{"id": "chapter-1", "title": "出发", "intro": "沿着清晰的时间顺序出发。", "photo_ids": ["p1"]}],
        photos=[
            {
                "id": "p1",
                "image": "assets/photos/p1.jpg",
                "source": "sources/p1.jpg",
                "description": "山路",
                "caption": "路向更远处展开",
                "display_date": "2025.06.01",
                "display_location": "杭州 · 孤山",
                "capture_location": "杭州 · 北山街道",
                "nearby_landmark": "孤山",
            }
        ],
    )

    render_album(manifest, output)
    archive = create_share_zip(output)

    assert "测试旅行" in (output / "index.html").read_text(encoding="utf-8")
    album_data = json.loads((output / "album.json").read_text(encoding="utf-8"))
    assert album_data["id"] == "test"
    assert album_data["photos"][0]["display_location"] == "杭州 · 孤山"
    assert album_data["photos"][0]["capture_location"] == "杭州 · 北山街道"
    assert album_data["photos"][0]["nearby_landmark"] == "孤山"
    assert album_data["image_style"] == "minimal-zine-v0-1"
    assert "--photo-aspect: 900 / 1500" in (output / "index.html").read_text(
        encoding="utf-8"
    )
    assert "latitude" not in json.dumps(album_data)
    assert "distance_meters" not in json.dumps(album_data)
    album_html = (output / "index.html").read_text(encoding="utf-8")
    assert ">AT<" in album_html
    assert ">NEAR<" in album_html
    with zipfile.ZipFile(archive) as zipped:
        assert "index.html" in zipped.namelist()
        assert "sources/p1.jpg" not in zipped.namelist()


def test_share_locations_deduplicate_pairs_and_support_legacy_manifests(tmp_path):
    output = tmp_path / "album"
    photos_dir = output / "assets" / "photos"
    photos_dir.mkdir(parents=True)
    for photo_id in ("p1", "p2"):
        Image.new("RGB", (30, 40), "white").save(photos_dir / f"{photo_id}.jpg")

    manifest = AlbumManifest(
        id="legacy",
        title="测试旅行",
        chapters=[
            {
                "id": "chapter-1",
                "title": "沿途",
                "intro": "沿途记录。",
                "photo_ids": ["p1", "p2"],
            }
        ],
        photos=[
            {
                "id": "p1",
                "image": "assets/photos/p1.jpg",
                "description": "山路",
                "caption": "向前",
                "display_date": "2025.06.01",
                "display_location": "杭州 · 北山街道",
            },
            {
                "id": "p2",
                "image": "assets/photos/p2.jpg",
                "description": "山路",
                "caption": "再向前",
                "display_date": "2025.06.01",
                "display_location": "杭州 · 北山街道",
            },
        ],
    )

    render_album(manifest, output)

    share_html = (output / "share.html").read_text(encoding="utf-8")
    assert share_html.count("share-location-label--at") == 1
    assert "杭州 · 北山街道" in share_html
