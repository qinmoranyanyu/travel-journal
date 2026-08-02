from pathlib import Path

from app.media import MediaPhoto
from app.models import ImageAnalysis
from app.selection import select_story_set


def make_photo(photo_id: str, category: str, story: float, quality: float) -> MediaPhoto:
    photo = MediaPhoto(photo_id, f"{photo_id}.jpg", Path(f"{photo_id}.jpg"), int(photo_id[-1]))
    photo.local_quality = quality
    photo.analysis = ImageAnalysis(
        photo_id=photo_id,
        description=photo_id,
        category=category,
        story_value=story,
        technical_quality=quality,
    )
    return photo


def test_selection_balances_story_and_category():
    photos = [
        make_photo("p0", "风景", 0.95, 0.9),
        make_photo("p1", "风景", 0.92, 0.9),
        make_photo("p2", "人物", 0.8, 0.75),
    ]

    selected = select_story_set(photos, 2)

    assert {photo.analysis.category for photo in selected} == {"风景", "人物"}
