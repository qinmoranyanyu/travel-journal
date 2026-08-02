import json
import zipfile

from PIL import Image

from app.models import AlbumManifest
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
        chapters=[{"id": "chapter-1", "title": "出发", "intro": "沿着清晰的时间顺序出发。", "photo_ids": ["p1"]}],
        photos=[
            {
                "id": "p1",
                "image": "assets/photos/p1.jpg",
                "source": "sources/p1.jpg",
                "description": "山路",
                "caption": "路向更远处展开",
                "display_date": "2025.06.01",
            }
        ],
    )

    render_album(manifest, output)
    archive = create_share_zip(output)

    assert "测试旅行" in (output / "index.html").read_text(encoding="utf-8")
    assert json.loads((output / "album.json").read_text(encoding="utf-8"))["id"] == "test"
    with zipfile.ZipFile(archive) as zipped:
        assert "index.html" in zipped.namelist()
        assert "sources/p1.jpg" not in zipped.namelist()
