import asyncio
from pathlib import Path

import httpx
from openai import APIConnectionError
from PIL import Image

from app.config import Settings
from app.jobs import Job
from app.models import (
    AlbumInput,
    ImageAnalysis,
    JobSnapshot,
    JobStatus,
    StoryChapter,
    StoryPlan,
)
from app import pipeline


class RecordingManager:
    def update(self, job: Job, **changes) -> None:
        for key, value in changes.items():
            setattr(job.snapshot, key, value)


class FlakyImageService:
    generation_calls = 0

    def __init__(self, settings: Settings) -> None:
        pass

    def analyze_photos(self, photos):
        return [
            ImageAnalysis(
                photo_id=photo.id,
                description="coast",
                category="landscape",
                story_value=0.8,
                technical_quality=0.8,
                caption_seed="at the coast",
            )
            for photo in photos
        ]

    def create_story(self, photos, context):
        return StoryPlan(
            cover_subtitle="coast",
            chapters=[
                StoryChapter(
                    id="chapter-1",
                    title="arrival",
                    intro="along the coast",
                    photo_ids=[photo.id for photo in photos],
                )
            ],
            captions={photo.id: "at the coast" for photo in photos},
            closing="end",
        )

    def generate_revival(self, photo, caption, output_path: Path):
        type(self).generation_calls += 1
        if self.generation_calls == 1:
            raise APIConnectionError(request=httpx.Request("POST", "https://example.test"))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (30, 40), "white").save(output_path)
        return output_path


class StableImageService(FlakyImageService):
    generated_photo_ids: list[str] = []

    def generate_revival(self, photo, caption, output_path: Path):
        type(self).generated_photo_ids.append(photo.id)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (30, 40), "white").save(output_path)
        return output_path


def test_pipeline_retries_transport_failure_serially(monkeypatch, tmp_path):
    workspace = tmp_path / ".jobs" / "job-one"
    uploads = workspace / "uploads"
    uploads.mkdir(parents=True)
    (workspace / "analysis").mkdir()
    (workspace / "generation").mkdir()
    source = uploads / "00000-photo.jpg"
    Image.new("RGB", (100, 80), "white").save(source)

    settings = Settings(
        output_dir=tmp_path / "outputs",
        job_dir=tmp_path / ".jobs",
        openai_api_key="test",
        openai_text_model="test",
        image_generation_interval_seconds=0,
    )
    settings.ensure_directories()
    job = Job(
        snapshot=JobSnapshot(id="job-one"),
        album_input=AlbumInput(title="test album", target_count=1),
        workspace=workspace,
        uploads=[
            {
                "id": "photo-00001",
                "original_name": "photo.jpg",
                "path": str(source),
                "order": 0,
                "modified_at": None,
            }
        ],
    )
    FlakyImageService.generation_calls = 0
    monkeypatch.setattr(pipeline, "OpenAIService", FlakyImageService)

    async def skip_share_export(output_dir):
        return []

    monkeypatch.setattr(pipeline, "export_share_images", skip_share_export)

    asyncio.run(pipeline.run_pipeline(job, RecordingManager(), settings))

    assert FlakyImageService.generation_calls == 2
    assert job.snapshot.status == JobStatus.completed
    assert not job.snapshot.error
    generated = list((tmp_path / "outputs").glob("*/assets/photos/photo-00001.jpg"))
    assert len(generated) == 1


def test_pipeline_generates_serially_with_configured_interval(monkeypatch, tmp_path):
    workspace = tmp_path / ".jobs" / "job-two"
    uploads = workspace / "uploads"
    uploads.mkdir(parents=True)
    (workspace / "analysis").mkdir()
    (workspace / "generation").mkdir()
    upload_rows = []
    for index, color in enumerate(("white", "black")):
        source = uploads / f"{index:05d}-photo-{index}.jpg"
        Image.new("RGB", (100, 80), color).save(source)
        upload_rows.append(
            {
                "id": f"photo-{index + 1:05d}",
                "original_name": source.name,
                "path": str(source),
                "order": index,
                "modified_at": None,
            }
        )

    settings = Settings(
        output_dir=tmp_path / "outputs",
        job_dir=tmp_path / ".jobs",
        openai_api_key="test",
        openai_text_model="test",
        image_generation_interval_seconds=2.5,
    )
    settings.ensure_directories()
    job = Job(
        snapshot=JobSnapshot(id="job-two"),
        album_input=AlbumInput(title="serial album", target_count=2),
        workspace=workspace,
        uploads=upload_rows,
    )
    StableImageService.generated_photo_ids = []
    monkeypatch.setattr(pipeline, "OpenAIService", StableImageService)
    monkeypatch.setattr(pipeline, "near_duplicate_representatives", lambda photos: photos)

    sleep_calls: list[float] = []

    async def record_sleep(seconds: float):
        sleep_calls.append(seconds)

    async def skip_share_export(output_dir):
        return []

    monkeypatch.setattr(pipeline.asyncio, "sleep", record_sleep)
    monkeypatch.setattr(pipeline, "export_share_images", skip_share_export)

    asyncio.run(pipeline.run_pipeline(job, RecordingManager(), settings))

    assert StableImageService.generated_photo_ids == ["photo-00001", "photo-00002"]
    assert sleep_calls == [2.5]
    assert job.snapshot.status == JobStatus.completed
