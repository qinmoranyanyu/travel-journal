import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ai import OpenAIService
from app.media import MediaPhoto


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
