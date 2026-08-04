from app.config import Settings
from app.image_styles import (
    get_image_style_spec,
    load_image_style_rules,
    load_story_caption_rules,
    normalize_image_caption,
)
from app.models import AlbumInput, ImageStyle


def test_legacy_album_input_defaults_to_photo_revival():
    album_input = AlbumInput.model_validate({"title": "Legacy", "target_count": 1})

    assert album_input.image_style == ImageStyle.photo_revival


def test_style_specs_keep_the_confirmed_native_aspect_ratios():
    revival = get_image_style_spec(ImageStyle.photo_revival)
    gathered = get_image_style_spec(ImageStyle.scenes_gathered)
    minimal = get_image_style_spec(ImageStyle.minimal_zine)

    assert revival.aspect_css == "3 / 4"
    assert gathered.aspect_css == "3 / 5"
    assert minimal.aspect_css == "3 / 5"
    assert (gathered.output_width, gathered.output_height) == (900, 1500)


def test_new_style_captions_are_not_rewritten_locally():
    gathered = "Cloud / Ridge / Silence\n山在雾里"
    minimal = "after rain\n08.17 / Kyoto\n未完"

    assert normalize_image_caption(ImageStyle.scenes_gathered, gathered) == gathered
    assert normalize_image_caption(ImageStyle.minimal_zine, minimal) == minimal


def test_photo_revival_caption_uses_four_to_thirty_total_characters():
    assert normalize_image_caption(ImageStyle.photo_revival, "风，来。") == "风，来。"
    assert normalize_image_caption(ImageStyle.photo_revival, "2026，出发。") == "2026，出发。"
    assert len(normalize_image_caption(ImageStyle.photo_revival, "短")) >= 4
    assert normalize_image_caption(ImageStyle.photo_revival, "山" * 31) == "山" * 30


def test_upstream_skills_are_compiled_to_pixel_level_prompt_rules():
    settings = Settings()

    gathered = load_image_style_rules(settings, ImageStyle.scenes_gathered)
    minimal = load_image_style_rules(settings, ImageStyle.minimal_zine)

    assert "## Runtime visual compiler" in gathered
    assert "hand-torn contour" in gathered
    assert "page-specific typography recipe" in gathered
    assert "at most two compatible lettering materials" in gathered
    assert "supplied English micro-text" not in gathered
    assert "## Generation Workflow" not in gathered
    assert "## Output Format" not in gathered
    assert len(gathered) < 6_000
    assert "## Runtime visual compiler" in minimal
    assert "70%-90% plain paper" in minimal
    assert "one deliberate typography event" in minimal
    assert "vertical text rail" in minimal
    assert "supplied Chinese phrase" not in minimal
    assert "## Output Format" not in minimal
    assert len(minimal) < 5_000


def test_story_caption_rules_are_read_from_pinned_upstream_skills():
    settings = Settings()

    gathered = load_story_caption_rules(settings, ImageStyle.scenes_gathered)
    minimal = load_story_caption_rules(settings, ImageStyle.minimal_zine)

    assert "## Micro-Text System" in gathered
    assert "Cloud / Ridge / Silence" in gathered
    assert "Chinese–English pairing" in gathered
    assert "keyword sequence" in gathered
    assert "optional tiny date/location/weather and signature" in minimal
    assert "### Typography Mode" in minimal
    assert "invent one short poetic English or Chinese phrase" in minimal
    assert load_story_caption_rules(settings, ImageStyle.photo_revival) == ""
