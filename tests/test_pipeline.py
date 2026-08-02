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
