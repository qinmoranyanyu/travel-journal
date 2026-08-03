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
    PhotoLocation,
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
        if self.generation_calls <= 3:
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


class AlwaysFailImageService(FlakyImageService):
    generation_calls = 0

    def generate_revival(self, photo, caption, output_path: Path):
        type(self).generation_calls += 1
        raise APIConnectionError(request=httpx.Request("POST", "https://example.test"))


class StopWhenRetryStartsManager(RecordingManager):
    def update(self, job: Job, **changes) -> None:
        super().update(job, **changes)
        if changes.get("stage") == "generation_retry":
            job.snapshot.retry_stop_requested = True
            job.stop_retry_event.set()


class NoCallService:
    def __init__(self, settings: Settings) -> None:
        pass

    def analyze_photos(self, photos):
        raise AssertionError("analysis checkpoint was not restored")

    def create_story(self, photos, context):
        raise AssertionError("story checkpoint was not restored")

    def generate_revival(self, photo, caption, output_path: Path):
        raise AssertionError("completed image was generated again")


class RecordingGeocoder:
    calls: list[tuple[float, float]] = []

    def __init__(self, api_key: str) -> None:
        assert api_key == "amap-test"

    def reverse(self, latitude: float, longitude: float) -> PhotoLocation:
        type(self).calls.append((latitude, longitude))
        return PhotoLocation(
            province="浙江省",
            city="杭州市",
            district="西湖区",
            poi_name="孤山",
            formatted_address="浙江省杭州市西湖区孤山路",
            display_name="杭州 · 孤山",
            location_key="浙江省|杭州市|西湖区|孤山",
            confidence="poi",
        )


def test_location_resolution_clusters_requests_and_checkpoints(monkeypatch, tmp_path):
    settings = Settings(amap_api_key="amap-test", location_cluster_radius_meters=200)
    job = Job(
        snapshot=JobSnapshot(id="location-job", gps_photo_count=2),
        album_input=AlbumInput(title="location album", target_count=1),
        workspace=tmp_path,
    )
    photos = [
        pipeline.MediaPhoto("one", "one.jpg", tmp_path / "one.jpg", 0),
        pipeline.MediaPhoto("two", "two.jpg", tmp_path / "two.jpg", 1),
    ]
    photos[0].latitude, photos[0].longitude = 30.2731, 120.1645
    photos[1].latitude, photos[1].longitude = 30.2735, 120.1648
    for photo in photos:
        photo.gps_inspected = True

    RecordingGeocoder.calls = []
    monkeypatch.setattr(pipeline, "AmapReverseGeocoder", RecordingGeocoder)

    asyncio.run(
        pipeline._resolve_photo_locations(
            job,
            RecordingManager(),
            settings,
            photos,
            photos,
        )
    )

    assert RecordingGeocoder.calls == [(30.2731, 120.1645)]
    assert {photo.location.display_name for photo in photos} == {"杭州 · 孤山"}
    assert job.snapshot.resolved_location_count == 2
    assert job.pipeline_state["photos"][0]["latitude"] == 30.2731


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

    assert FlakyImageService.generation_calls == 4
    assert job.snapshot.status == JobStatus.completed
    assert not job.snapshot.error
    generated = list((tmp_path / "outputs").glob("*/assets/photos/photo-00001.jpg"))
    assert len(generated) == 1

    output_folder = job.pipeline_state["output_folder"]
    monkeypatch.setattr(pipeline, "OpenAIService", NoCallService)
    asyncio.run(pipeline.run_pipeline(job, RecordingManager(), settings))

    assert job.snapshot.status == JobStatus.completed
    assert job.pipeline_state["output_folder"] == output_folder
    assert len(list((tmp_path / "outputs").glob("*/assets/photos/photo-00001.jpg"))) == 1


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


def test_pipeline_stops_retry_loop_and_uses_original_fallback(monkeypatch, tmp_path):
    workspace = tmp_path / ".jobs" / "job-stop"
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
        snapshot=JobSnapshot(id="job-stop"),
        album_input=AlbumInput(title="fallback album", target_count=1),
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
    AlwaysFailImageService.generation_calls = 0
    monkeypatch.setattr(pipeline, "OpenAIService", AlwaysFailImageService)

    async def skip_share_export(output_dir):
        return []

    monkeypatch.setattr(pipeline, "export_share_images", skip_share_export)

    asyncio.run(pipeline.run_pipeline(job, StopWhenRetryStartsManager(), settings))

    assert AlwaysFailImageService.generation_calls == 1
    assert job.snapshot.status == JobStatus.partial
    assert job.snapshot.failed_items == 1
    assert "使用原图" in (job.snapshot.error or "")
    generated = list((tmp_path / "outputs").glob("*/assets/photos/photo-00001.jpg"))
    assert len(generated) == 1


def test_pipeline_honors_pause_request_before_next_checkpoint(tmp_path):
    workspace = tmp_path / ".jobs" / "job-pause"
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
        snapshot=JobSnapshot(id="job-pause", status=JobStatus.queued),
        album_input=AlbumInput(title="pause album", target_count=1),
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
    job.pause_event.set()

    asyncio.run(pipeline.run_pipeline(job, RecordingManager(), settings))

    assert job.snapshot.status == JobStatus.paused
    assert job.snapshot.stage == "metadata"
    assert job.snapshot.pause_requested is False
    assert "任务已暂停" in job.snapshot.message
