from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import imagehash
import numpy as np
from PIL import Image, ImageOps

from .models import PhotoLocation

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
EXIF_DATETIME_ORIGINAL = 36867
EXIF_DATETIME_DIGITIZED = 36868
EXIF_DATETIME = 306
EXIF_GPS_INFO = 34853
GPS_LATITUDE_REF = 1
GPS_LATITUDE = 2
GPS_LONGITUDE_REF = 3
GPS_LONGITUDE = 4


@dataclass
class MediaPhoto:
    id: str
    original_name: str
    source_path: Path
    upload_order: int
    browser_modified_at: datetime | None = None
    width: int = 0
    height: int = 0
    capture_time: datetime | None = None
    time_source: str = "upload_order"
    time_confidence: str = "estimated"
    latitude: float | None = None
    longitude: float | None = None
    gps_source: str = ""
    gps_inspected: bool = False
    location: PhotoLocation | None = None
    analysis_path: Path | None = None
    generation_path: Path | None = None
    phash: str = ""
    local_quality: float = 0.5
    analysis: Any = None
    generated_path: Path | None = None
    caption: str = ""
    chapter_id: str = ""


def is_supported(filename: str) -> bool:
    return Path(filename).suffix.lower() in SUPPORTED_EXTENSIONS


def safe_filename(filename: str) -> str:
    stem = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "-", Path(filename).stem).strip(".-")
    suffix = Path(filename).suffix.lower()
    return f"{stem or 'photo'}{suffix}"


def _parse_exif_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = value.decode(errors="ignore") if isinstance(value, bytes) else str(value)
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
    return None


def _parse_filename_time(filename: str) -> datetime | None:
    stem = Path(filename).stem
    patterns = (
        r"(?P<y>20\d{2})[-_]?((?P<m>0[1-9]|1[0-2]))[-_]?(?P<d>[0-3]\d)[-_ T]?(?P<h>[0-2]\d)?[-_:]?(?P<mi>[0-5]\d)?[-_:]?(?P<s>[0-5]\d)?",
        r"(?P<y>19\d{2})[-_]?((?P<m>0[1-9]|1[0-2]))[-_]?(?P<d>[0-3]\d)",
    )
    for pattern in patterns:
        match = re.search(pattern, stem)
        if not match:
            continue
        parts = match.groupdict(default="0")
        try:
            return datetime(
                int(parts["y"]),
                int(parts["m"]),
                int(parts["d"]),
                int(parts.get("h") or 0),
                int(parts.get("mi") or 0),
                int(parts.get("s") or 0),
            )
        except ValueError:
            continue
    return None


def _text_value(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="ignore")
    return str(value or "")


def _degrees_to_decimal(value: Any, reference: Any) -> float | None:
    try:
        degrees, minutes, seconds = value
        decimal = float(degrees) + float(minutes) / 60 + float(seconds) / 3600
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if _text_value(reference).strip().upper() in {"S", "W"}:
        decimal = -decimal
    return decimal


def _parse_exif_gps(exif: Any) -> tuple[float, float] | None:
    try:
        gps = exif.get_ifd(EXIF_GPS_INFO)
    except (AttributeError, KeyError, TypeError, ValueError):
        gps = exif.get(EXIF_GPS_INFO, {}) if exif else {}
    if not isinstance(gps, dict):
        return None
    latitude = _degrees_to_decimal(gps.get(GPS_LATITUDE), gps.get(GPS_LATITUDE_REF))
    longitude = _degrees_to_decimal(gps.get(GPS_LONGITUDE), gps.get(GPS_LONGITUDE_REF))
    if latitude is None or longitude is None:
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return round(latitude, 7), round(longitude, 7)


def inspect_photo(photo: MediaPhoto) -> MediaPhoto:
    with Image.open(photo.source_path) as raw:
        exif = raw.getexif()
        image = ImageOps.exif_transpose(raw)
        photo.width, photo.height = image.size

        original = _parse_exif_time(exif.get(EXIF_DATETIME_ORIGINAL))
        digitized = _parse_exif_time(exif.get(EXIF_DATETIME_DIGITIZED))
        generic = _parse_exif_time(exif.get(EXIF_DATETIME))
        filename_time = _parse_filename_time(photo.original_name)
        gps = _parse_exif_gps(exif)

        if original:
            photo.capture_time, photo.time_source, photo.time_confidence = (
                original,
                "exif_original",
                "trusted",
            )
        elif digitized or generic:
            photo.capture_time, photo.time_source, photo.time_confidence = (
                digitized or generic,
                "exif_digitized",
                "trusted",
            )
        elif filename_time:
            photo.capture_time, photo.time_source = filename_time, "filename"
        elif photo.browser_modified_at:
            photo.capture_time, photo.time_source = photo.browser_modified_at, "file_modified"

        if gps:
            photo.latitude, photo.longitude = gps
            photo.gps_source = "exif_gps"
        else:
            photo.latitude = None
            photo.longitude = None
            photo.gps_source = ""
        photo.gps_inspected = True

        normalized = image.convert("RGB")
        thumb = normalized.copy()
        thumb.thumbnail((256, 256), Image.Resampling.LANCZOS)
        photo.phash = str(imagehash.phash(thumb))
        photo.local_quality = _quality_score(thumb)
    return photo


def create_variants(photo: MediaPhoto, analysis_dir: Path, generation_dir: Path) -> None:
    with Image.open(photo.source_path) as raw:
        image = ImageOps.exif_transpose(raw).convert("RGB")
        analysis = image.copy()
        analysis.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        generation = image.copy()
        generation.thumbnail((2048, 2048), Image.Resampling.LANCZOS)

        analysis_path = analysis_dir / f"{photo.id}.jpg"
        generation_path = generation_dir / f"{photo.id}.jpg"
        analysis.save(analysis_path, "JPEG", quality=80, optimize=True)
        generation.save(generation_path, "JPEG", quality=90, optimize=True)
        photo.analysis_path = analysis_path
        photo.generation_path = generation_path


def _quality_score(image: Image.Image) -> float:
    gray = np.asarray(image.convert("L"), dtype=np.float32)
    if min(gray.shape) < 2:
        return 0.2
    gradients = np.concatenate((np.diff(gray, axis=0).ravel(), np.diff(gray, axis=1).ravel()))
    sharpness = min(1.0, float(np.std(gradients)) / 42.0)
    exposure = max(0.0, 1.0 - abs(float(gray.mean()) - 127.5) / 127.5)
    contrast = min(1.0, float(gray.std()) / 64.0)
    return round(0.5 * sharpness + 0.3 * exposure + 0.2 * contrast, 4)


def near_duplicate_representatives(photos: list[MediaPhoto], threshold: int = 7) -> list[MediaPhoto]:
    ordered = sorted(photos, key=photo_sort_key)
    groups: list[list[MediaPhoto]] = []
    for photo in ordered:
        photo_hash = imagehash.hex_to_hash(photo.phash)
        matched: list[MediaPhoto] | None = None
        for group in groups[-20:]:
            representative = group[0]
            distance = photo_hash - imagehash.hex_to_hash(representative.phash)
            if distance <= threshold:
                matched = group
                break
        if matched is None:
            groups.append([photo])
        else:
            matched.append(photo)
            matched.sort(key=lambda item: item.local_quality, reverse=True)
    return [group[0] for group in groups]


def photo_sort_key(photo: MediaPhoto) -> tuple[datetime, int]:
    return (photo.capture_time or datetime.max, photo.upload_order)


def copy_selected_source(photo: MediaPhoto, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{photo.id}-{safe_filename(photo.original_name)}"
    shutil.copy2(photo.source_path, target)
    return target


def normalize_generated_page(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as raw:
        image = raw.convert("RGB")
        canvas = Image.new("RGB", (1024, 1365), "white")
        image.thumbnail(canvas.size, Image.Resampling.LANCZOS)
        x = (canvas.width - image.width) // 2
        y = (canvas.height - image.height) // 2
        canvas.paste(image, (x, y))
        canvas.save(target, "JPEG", quality=94, optimize=True)
