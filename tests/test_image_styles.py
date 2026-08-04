from app.config import Settings
from app.image_styles import (
    get_image_style_spec,
    load_image_style_rules,
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


def test_style_caption_contracts_are_repaired_locally():
    assert normalize_image_caption(
        ImageStyle.scenes_gathered,
        "Light across the quiet mountain road forever",
    ) == "Light across the quiet mountain"
    assert normalize_image_caption(ImageStyle.scenes_gathered, "湖边微光") == (
        "Gathered along the way"
    )
    assert normalize_image_caption(ImageStyle.minimal_zine, "风从远山吹进湖面深处") == (
        "风从远山吹进湖面"
    )
    assert len(normalize_image_caption(ImageStyle.photo_revival, "太短")) >= 10


def test_upstream_skills_are_compiled_to_pixel_level_prompt_rules():
    settings = Settings()

    gathered = load_image_style_rules(settings, ImageStyle.scenes_gathered)
    minimal = load_image_style_rules(settings, ImageStyle.minimal_zine)

    assert "## Runtime visual compiler" in gathered
    assert "hand-torn contour" in gathered
    assert "## Generation Workflow" not in gathered
    assert "## Output Format" not in gathered
    assert len(gathered) < 6_000
    assert "## Runtime visual compiler" in minimal
    assert "70%-90% plain paper" in minimal
    assert "## Output Format" not in minimal
    assert len(minimal) < 5_000
