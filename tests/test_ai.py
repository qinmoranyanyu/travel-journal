import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ai import (
    OpenAIService,
    _fallback_image_caption,
    _fallback_poem_lines,
    _minimal_zine_variation,
    _zine_typography_variation,
)
from app.media import MediaPhoto
from app.models import ImageAnalysis, ImageStyle, NearbyLandmark, PhotoLocation


class FakeImageStream:
    def __init__(self, events):
        self.events = events
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.closed = True

    def __iter__(self):
        return iter(self.events)


class FakeImages:
    def __init__(self, stream):
        self.stream = stream
        self.edit_kwargs = None

    def edit(self, **kwargs):
        self.edit_kwargs = kwargs
        return self.stream


class FakeChatCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content='{"ok":true}')
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def build_service(stream: FakeImageStream) -> tuple[OpenAIService, FakeImages]:
    images = FakeImages(stream)
    service = object.__new__(OpenAIService)
    service.settings = SimpleNamespace(
        openai_image_model="gpt-image-1.5",
        image_generation_timeout_seconds=360,
    )
    service.client = SimpleNamespace(images=images)
    service.image_style_rules = {
        style: "Preserve the source image." for style in ImageStyle
    }
    return service, images


def build_photo(source_path: Path) -> MediaPhoto:
    return MediaPhoto(
        id="photo-00001",
        original_name=source_path.name,
        source_path=source_path,
        upload_order=0,
        generation_path=source_path,
    )


def test_generate_image_consumes_stream_completed_event(tmp_path):
    source_path = tmp_path / "source.jpg"
    source_path.write_bytes(b"source-image")
    expected = b"generated-image"
    stream = FakeImageStream(
        [
            SimpleNamespace(type="image_edit.partial_image", partial_image_index=0),
            SimpleNamespace(
                type="image_edit.completed",
                b64_json=base64.b64encode(expected).decode("ascii"),
            ),
        ]
    )
    service, images = build_service(stream)
    output_path = tmp_path / "generated" / "photo.png"

    result = service.generate_image(
        build_photo(source_path),
        "这一刻被轻轻留在旅途中",
        output_path,
        ImageStyle.photo_revival,
    )

    assert result == output_path
    assert output_path.read_bytes() == expected
    assert images.edit_kwargs["stream"] is True
    assert images.edit_kwargs["size"] == "1024x1536"
    assert images.edit_kwargs["timeout"] == 360
    assert stream.closed is True


def test_generate_image_rejects_stream_without_completed_event(tmp_path):
    source_path = tmp_path / "source.jpg"
    source_path.write_bytes(b"source-image")
    stream = FakeImageStream(
        [SimpleNamespace(type="image_edit.partial_image", partial_image_index=0)]
    )
    service, _ = build_service(stream)
    output_path = tmp_path / "generated" / "photo.png"

    with pytest.raises(RuntimeError, match="image_edit.completed"):
        service.generate_image(
            build_photo(source_path),
            "Gathered light",
            output_path,
            ImageStyle.scenes_gathered,
        )

    assert output_path.exists() is False
    assert stream.closed is True


def test_generate_image_preserves_multiline_mixed_caption_in_prompt(tmp_path):
    source_path = tmp_path / "source.jpg"
    source_path.write_bytes(b"source-image")
    stream = FakeImageStream(
        [
            SimpleNamespace(
                type="image_edit.completed",
                b64_json=base64.b64encode(b"generated-image").decode("ascii"),
            )
        ]
    )
    service, images = build_service(stream)
    caption = "Cloud / Ridge / Silence\n山在雾里\n1998.07"

    service.generate_image(
        build_photo(source_path),
        caption,
        tmp_path / "generated.jpg",
        ImageStyle.scenes_gathered,
    )

    assert f"<caption>\n{caption}\n</caption>" in images.edit_kwargs["prompt"]
    assert "Page-specific typography recipe:" in images.edit_kwargs["prompt"]
    assert "do not collapse them into one uniform paragraph" in images.edit_kwargs["prompt"]
    assert "exact English phrase" not in images.edit_kwargs["prompt"]
    assert "Do not add dates" not in images.edit_kwargs["prompt"]


def test_minimal_zine_prompt_assigns_distinct_typographic_roles(tmp_path):
    source_path = tmp_path / "source.jpg"
    source_path.write_bytes(b"source-image")
    stream = FakeImageStream(
        [
            SimpleNamespace(
                type="image_edit.completed",
                b64_json=base64.b64encode(b"generated-image").decode("ascii"),
            )
        ]
    )
    service, images = build_service(stream)
    caption = "风从窄巷经过\nA TURN IN THE ROAD\n2026.08.05"

    service.generate_image(
        build_photo(source_path),
        caption,
        tmp_path / "generated.jpg",
        ImageStyle.minimal_zine,
    )

    prompt = images.edit_kwargs["prompt"]
    assert f"<caption>\n{caption}\n</caption>" in prompt
    assert "Page-specific typography recipe:" in prompt
    assert "one memorable typographic event" in prompt
    assert "Treat this as one combined recipe" in prompt
    assert "The final image must not be monochrome or near-monochrome" in prompt
    assert "layout:" in prompt
    assert "image anchor:" in prompt
    assert "texture:" in prompt
    assert "mood:" in prompt
    assert "high-chroma color:" in prompt


def test_minimal_zine_variation_exposes_every_skill_axis_and_option():
    recipes = []
    typography_recipes = set()
    for index in range(2_048):
        photo_id = f"photo-{index:05d}"
        recipe = dict(
            field.split(": ", 1)
            for field in _minimal_zine_variation(photo_id).split("; ")
        )
        recipes.append(recipe)
        typography_recipes.add(
            _zine_typography_variation(photo_id, ImageStyle.minimal_zine)
        )

    assert all(
        set(recipe)
        == {"layout", "image anchor", "texture", "mood", "high-chroma color"}
        for recipe in recipes
    )
    assert len({recipe["layout"] for recipe in recipes}) == 8
    assert len({recipe["image anchor"] for recipe in recipes}) == 8
    assert len({recipe["texture"] for recipe in recipes}) == 8
    assert len({recipe["mood"] for recipe in recipes}) == 9
    assert len({recipe["high-chroma color"] for recipe in recipes}) == 9
    assert len(typography_recipes) == 8


def test_location_metadata_is_separated_and_nearby_wording_is_constrained(tmp_path):
    source_path = tmp_path / "source.jpg"
    source_path.write_bytes(b"source-image")
    photo = build_photo(source_path)
    photo.analysis_path = source_path
    photo.analysis = ImageAnalysis(photo_id=photo.id, description="湖边", caption_seed="湖边一刻")
    photo.location = PhotoLocation(
        city="杭州市",
        township="北山街道",
        display_name="杭州 · 北山街道",
        nearby_searched=True,
        nearby_landmark=NearbyLandmark(
            name="孤山",
            distance_meters=860,
            category="自然地标",
            typecode="110200",
            rating=4.8,
        ),
    )
    service = object.__new__(OpenAIService)
    captured: list[list[dict]] = []

    def capture(content):
        captured.append(content)
        return {"photos": []}

    service._chat_json = capture
    service.analyze_photos([photo])

    metadata = json.loads(captured[0][1]["text"])
    assert metadata["capture_location"]["display_name"] == "杭州 · 北山街道"
    assert "nearby_landmark" not in metadata["capture_location"]
    assert metadata["nearby_landmark"]["name"] == "孤山"
    assert metadata["nearby_landmark"]["distance_meters"] == 860
    assert "严禁写成照片拍摄于" in captured[0][0]["text"]


def test_chat_json_always_adds_explicit_json_instruction():
    completions = FakeChatCompletions()
    service = object.__new__(OpenAIService)
    service.settings = SimpleNamespace(openai_text_model="gpt-test")
    service.client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )
    original_content = [{"type": "text", "text": "编排旅行故事。"}]

    result = service._chat_json(original_content)

    assert result == {"ok": True}
    assert len(completions.calls) == 1
    request = completions.calls[0]
    assert request["response_format"] == {"type": "json_object"}
    request_content = request["messages"][0]["content"]
    assert request_content[0]["text"] == "Return a JSON object. 请返回 JSON 对象。"
    assert request_content[1:] == original_content


def test_story_uses_separate_image_captions_and_complete_page_poem(tmp_path):
    photos = []
    for index in range(2):
        photo = build_photo(tmp_path / f"source-{index}.jpg")
        photo.id = f"photo-{index + 1:05d}"
        photo.analysis = ImageAnalysis(
            photo_id=photo.id,
            description="湖边行走",
            caption_seed=f"图片短句 {index + 1}",
        )
        photos.append(photo)

    service = object.__new__(OpenAIService)
    service.story_caption_rules = {style: "" for style in ImageStyle}
    captured = []

    def capture(content):
        captured.append(content)
        return {
            "cover_subtitle": "沿湖",
            "chapters": [
                {
                    "id": "chapter-1",
                    "title": "水边",
                    "intro": "沿着湖岸向前。",
                    "photo_ids": [photo.id for photo in reversed(photos)],
                }
            ],
            "captions": {photo.id: f"图内-{photo.id}" for photo in photos},
            "poem_lines": {},
            "closing": "回望湖岸。",
        }

    service._chat_json = capture
    plan = service.create_story(photos, {"title": "湖边手记"})

    prompt = json.loads(captured[0][0]["text"])
    requirements = "\n".join(prompt["requirements"])
    assert "图片内部的独立短旁白" in requirements
    assert "4-30" in requirements
    assert "连起来必须是一首完整" in requirements
    assert "主韵部" in requirements
    assert "相邻两行或隔行" in requirements
    assert "语意和画面事实优先" in requirements
    assert prompt["output"]["poem_lines"]
    assert plan.chapters[0].photo_ids == [photo.id for photo in photos]
    assert list(plan.poem_lines) == [photo.id for photo in photos]
    assert plan.poem_lines[photos[0].id].endswith("天光，")
    assert plan.poem_lines[photos[1].id].endswith("相框。")
    assert all(plan.poem_lines[photo.id] != plan.captions[photo.id] for photo in photos)


def test_story_uses_upstream_rules_and_keeps_new_style_caption_verbatim(tmp_path):
    photo = build_photo(tmp_path / "source.jpg")
    photo.analysis = ImageAnalysis(
        photo_id=photo.id,
        description="云雾中的山脊",
        caption_seed="山脊被云雾轻轻遮住",
    )
    caption = "Cloud / Ridge / Silence\n山在雾里\n1998.07"
    service = object.__new__(OpenAIService)
    service.story_caption_rules = {
        ImageStyle.scenes_gathered: "## Micro-Text System\nCloud / Ridge / Silence"
    }
    captured = []

    def capture(content):
        captured.append(content)
        return {
            "cover_subtitle": "山间",
            "chapters": [
                {
                    "id": "chapter-1",
                    "title": "雾线",
                    "intro": "山脊在云雾之间显出轮廓。",
                    "photo_ids": [photo.id],
                }
            ],
            "captions": {photo.id: caption},
            "poem_lines": {photo.id: "雾沿山脊缓慢经过。"},
            "closing": "山仍留在云后。",
        }

    service._chat_json = capture
    plan = service.create_story(
        [photo],
        {"title": "山间", "image_style": ImageStyle.scenes_gathered.value},
    )

    prompt = json.loads(captured[0][0]["text"])
    assert prompt["upstream_image_text_rules"].startswith("## Micro-Text System")
    assert "2-4 个文字层级" in "\n".join(prompt["requirements"])
    assert "\n" in prompt["output"]["captions"]["photo_id"]
    assert "caption_seed" not in prompt["photos"][0]
    assert plan.captions[photo.id] == caption


def test_fallback_zine_captions_are_multilevel_and_fallback_poem_uses_one_rhyme(tmp_path):
    photos = []
    for index in range(4):
        photo = build_photo(tmp_path / f"source-{index}.jpg")
        photo.id = f"photo-{index + 1:05d}"
        photo.analysis = ImageAnalysis(
            photo_id=photo.id,
            description="湖面与远山",
            category="风景",
            caption_seed="雨后湖面泛起微光",
        )
        photos.append(photo)

    gathered = _fallback_image_caption(photos[0], ImageStyle.scenes_gathered)
    minimal = _fallback_image_caption(photos[1], ImageStyle.minimal_zine)
    poem = _fallback_poem_lines(photos)

    assert len(gathered.splitlines()) >= 2
    assert "LAND / LIGHT / DISTANCE" in gathered
    assert len(minimal.splitlines()) >= 2
    assert [line[-2] for line in poem.values()] == ["光", "长", "晃", "框"]
