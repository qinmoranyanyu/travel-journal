from __future__ import annotations

from .media import MediaPhoto, photo_sort_key


def select_story_set(photos: list[MediaPhoto], target_count: int) -> list[MediaPhoto]:
    if not photos:
        return []
    if target_count >= len(photos):
        return sorted(photos, key=photo_sort_key)

    chronological = sorted(photos, key=photo_sort_key)
    positions = {
        photo.id: index / max(1, len(chronological) - 1)
        for index, photo in enumerate(chronological)
    }
    selected: list[MediaPhoto] = []

    while len(selected) < target_count:
        selected_categories = {
            item.analysis.category for item in selected if item.analysis is not None
        }
        selected_locations = {
            item.location.location_key
            for item in selected
            if item.location is not None and item.location.location_key
        }
        selected_positions = [positions[item.id] for item in selected]
        best: MediaPhoto | None = None
        best_score = -1.0

        for photo in photos:
            if photo in selected:
                continue
            analysis = photo.analysis
            story_value = analysis.story_value if analysis else 0.5
            ai_quality = analysis.technical_quality if analysis else photo.local_quality
            quality = (ai_quality + photo.local_quality) / 2
            if selected_positions:
                timeline_novelty = min(
                    1.0,
                    min(abs(positions[photo.id] - value) for value in selected_positions) * 4,
                )
            else:
                timeline_novelty = 1.0
            category_novelty = (
                1.0
                if analysis and analysis.category not in selected_categories
                else 0.25
            )
            if photo.location is None or not photo.location.location_key:
                location_novelty = 0.5
            elif photo.location.location_key not in selected_locations:
                location_novelty = 1.0
            else:
                location_novelty = 0.2
            score = (
                0.32 * story_value
                + 0.23 * quality
                + 0.18 * timeline_novelty
                + 0.15 * category_novelty
                + 0.12 * location_novelty
            )
            if score > best_score:
                best = photo
                best_score = score

        if best is None:
            break
        selected.append(best)

    return sorted(selected, key=photo_sort_key)
