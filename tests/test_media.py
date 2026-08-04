from datetime import datetime

from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from app.media import (
    MediaPhoto,
    inspect_photo,
    near_duplicate_representatives,
    normalize_generated_page,
)


def test_extracts_original_exif_time(tmp_path):
    path = tmp_path / "photo.jpg"
    image = Image.new("RGB", (100, 80), "#8aaea0")
    exif = Image.Exif()
    exif[36867] = "2025:06:12 08:30:45"
    image.save(path, exif=exif)

    photo = inspect_photo(MediaPhoto("one", path.name, path, 0))

    assert photo.capture_time == datetime(2025, 6, 12, 8, 30, 45)
    assert photo.time_source == "exif_original"
    assert photo.time_confidence == "trusted"


def test_falls_back_to_filename_time(tmp_path):
    path = tmp_path / "IMG_20250718_142233.jpg"
    Image.new("RGB", (80, 80), "white").save(path)

    photo = inspect_photo(MediaPhoto("one", path.name, path, 0))

    assert photo.capture_time == datetime(2025, 7, 18, 14, 22, 33)
    assert photo.time_source == "filename"
    assert photo.time_confidence == "estimated"


def test_extracts_exif_gps(tmp_path):
    path = tmp_path / "gps-photo.jpg"
    image = Image.new("RGB", (100, 80), "white")
    exif = Image.Exif()
    exif[34853] = {
        1: "N",
        2: (IFDRational(30), IFDRational(16), IFDRational(234, 10)),
        3: "E",
        4: (IFDRational(120), IFDRational(9), IFDRational(522, 10)),
    }
    image.save(path, exif=exif)

    photo = inspect_photo(MediaPhoto("gps", path.name, path, 0))

    assert photo.latitude == 30.2731667
    assert photo.longitude == 120.1645
    assert photo.gps_source == "exif_gps"
    assert photo.gps_inspected is True


def test_near_duplicates_keep_higher_quality(tmp_path):
    dark_path = tmp_path / "dark.jpg"
    bright_path = tmp_path / "bright.jpg"
    Image.new("RGB", (100, 100), "#111111").save(dark_path)
    Image.new("RGB", (100, 100), "#888888").save(bright_path)
    dark = inspect_photo(MediaPhoto("dark", dark_path.name, dark_path, 0))
    bright = inspect_photo(MediaPhoto("bright", bright_path.name, bright_path, 1))

    representatives = near_duplicate_representatives([dark, bright], threshold=64)

    assert representatives == [bright]


def test_normalize_generated_page_supports_style_specific_cover(tmp_path):
    source = tmp_path / "source.jpg"
    target = tmp_path / "target.jpg"
    Image.new("RGB", (120, 180), "#c6533e").save(source)

    normalize_generated_page(source, target, (90, 150), "cover", "#f1eadb")

    with Image.open(target) as result:
        assert result.size == (90, 150)
