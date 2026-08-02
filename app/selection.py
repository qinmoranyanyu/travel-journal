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
            score = (
                0.35 * story_value
                + 0.25 * quality
                + 0.20 * timeline_novelty
                + 0.20 * category_novelty
            )
            if score > best_score:
                best = photo
                best_score = score

        if best is None:
            break
        selected.append(best)

    return sorted(selected, key=photo_sort_key)

