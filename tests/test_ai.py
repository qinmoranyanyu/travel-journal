import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ai import OpenAIService
from app.media import MediaPhoto
from app.models import ImageAnalysis, NearbyLandmark, PhotoLocation


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
    service.settings = SimpleNamespace(openai_image_model="gpt-image-1.5")
    service.client = SimpleNamespace(images=images)
    service.photo_revival_rules = "Preserve the source image."
    return service, images


def build_photo(source_path: Path) -> MediaPhoto:
    return MediaPhoto(
        id="photo-00001",
        original_name=source_path.name,
        source_path=source_path,
        upload_order=0,
        generation_path=source_path,
    )


def test_generate_revival_consumes_stream_completed_event(tmp_path):
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

    result = service.generate_revival(build_photo(source_path), "caption", output_path)

    assert result == output_path
    assert output_path.read_bytes() == expected
    assert images.edit_kwargs["stream"] is True
    assert images.edit_kwargs["size"] == "1024x1536"
    assert stream.closed is True


def test_generate_revival_rejects_stream_without_completed_event(tmp_path):
    source_path = tmp_path / "source.jpg"
    source_path.write_bytes(b"source-image")
    stream = FakeImageStream(
        [SimpleNamespace(type="image_edit.partial_image", partial_image_index=0)]
    )
    service, _ = build_service(stream)
    output_path = tmp_path / "generated" / "photo.png"

    with pytest.raises(RuntimeError, match="image_edit.completed"):
        service.generate_revival(build_photo(source_path), "caption", output_path)

    assert output_path.exists() is False
    assert stream.closed is True


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
    assert "连起来必须是一首完整" in requirements
    assert prompt["output"]["poem_lines"]
    assert plan.chapters[0].photo_ids == [photo.id for photo in photos]
    assert list(plan.poem_lines) == [photo.id for photo in photos]
    assert plan.poem_lines[photos[0].id] == "风从旅途的第一页起身，"
    assert "温柔的回声" in plan.poem_lines[photos[1].id]
    assert all(plan.poem_lines[photo.id] != plan.captions[photo.id] for photo in photos)
