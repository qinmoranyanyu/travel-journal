import asyncio
import json
import logging
import threading
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
    NearbyLandmark,
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
            poem_lines={
                photo.id: f"poem line {index + 1}"
                for index, photo in enumerate(photos)
            },
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
    generated_captions: list[str] = []

    def generate_revival(self, photo, caption, output_path: Path):
        type(self).generated_photo_ids.append(photo.id)
        type(self).generated_captions.append(caption)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (30, 40), "white").save(output_path)
        return output_path


class ConcurrentImageService(StableImageService):
    active_calls = 0
    max_active_calls = 0
    lock = threading.Lock()
    first_batch_barrier: threading.Barrier | None = None

    def generate_revival(self, photo, caption, output_path: Path):
        with type(self).lock:
            type(self).active_calls += 1
            type(self).max_active_calls = max(
                type(self).max_active_calls,
                type(self).active_calls,
            )
        try:
            if type(self).first_batch_barrier and photo.id in {
                "photo-00001",
                "photo-00002",
            }:
                type(self).first_batch_barrier.wait(timeout=3)
            return super().generate_revival(photo, caption, output_path)
        finally:
            with type(self).lock:
                type(self).active_calls -= 1


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
    nearby_calls: list[tuple[float, float]] = []

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

    def nearby(
        self,
        latitude: float,
        longitude: float,
        capture_location: PhotoLocation,
    ) -> NearbyLandmark:
        assert capture_location.poi_name == "孤山"
        type(self).nearby_calls.append((latitude, longitude))
        return NearbyLandmark(
            name="西湖风景名胜区",
            distance_meters=860,
            category="重要景区",
            typecode="110202",
            rating=4.9,
        )


class FailingNearbyGeocoder(RecordingGeocoder):
    def nearby(
        self,
        latitude: float,
        longitude: float,
        capture_location: PhotoLocation,
    ) -> NearbyLandmark:
        raise RuntimeError("nearby service unavailable")


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
    RecordingGeocoder.nearby_calls = []
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
    assert RecordingGeocoder.nearby_calls == [(30.2731, 120.1645)]
    assert {photo.location.display_name for photo in photos} == {"杭州 · 孤山"}
    assert {
        photo.location.nearby_landmark.name for photo in photos
    } == {"西湖风景名胜区"}
    assert all(photo.location.nearby_searched for photo in photos)
    assert job.snapshot.resolved_location_count == 2
    assert job.pipeline_state["photos"][0]["latitude"] == 30.2731
    assert (
        job.pipeline_state["photos"][0]["location"]["nearby_landmark"]["distance_meters"]
        == 860
    )


def test_nearby_lookup_failure_is_logged_without_losing_capture_location(
    monkeypatch,
    tmp_path,
    caplog,
):
    settings = Settings(amap_api_key="amap-test")
    job = Job(
        snapshot=JobSnapshot(id="location-error-job", gps_photo_count=1),
        album_input=AlbumInput(title="location album", target_count=1),
        workspace=tmp_path,
    )
    photo = pipeline.MediaPhoto("one", "one.jpg", tmp_path / "one.jpg", 0)
    photo.latitude, photo.longitude = 30.2731, 120.1645
    photo.gps_inspected = True
    monkeypatch.setattr(pipeline, "AmapReverseGeocoder", FailingNearbyGeocoder)

    with caplog.at_level(logging.WARNING, logger="app.pipeline"):
        asyncio.run(
            pipeline._resolve_photo_locations(
                job,
                RecordingManager(),
                settings,
                [photo],
                [photo],
            )
        )

    assert photo.location is not None
    assert photo.location.display_name == "杭州 · 孤山"
    assert any(key.endswith(":nearby") for key in job.pipeline_state["location_errors"])
    assert "nearby_landmark_lookup_failed job_id=location-error-job cluster=1/1" in caplog.text
    assert "error_type=RuntimeError error=nearby service unavailable" in caplog.text


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


def test_pipeline_generates_concurrently_and_waits_between_batches(
    monkeypatch,
    tmp_path,
    caplog,
):
    workspace = tmp_path / ".jobs" / "job-two"
    uploads = workspace / "uploads"
    uploads.mkdir(parents=True)
    (workspace / "analysis").mkdir()
    (workspace / "generation").mkdir()
    upload_rows = []
    for index, color in enumerate(("white", "black", "gray")):
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
        image_generation_concurrency=2,
    )
    settings.ensure_directories()
    job = Job(
        snapshot=JobSnapshot(id="job-two"),
        album_input=AlbumInput(title="concurrent album", target_count=3),
        workspace=workspace,
        uploads=upload_rows,
    )
    ConcurrentImageService.generated_photo_ids = []
    ConcurrentImageService.generated_captions = []
    ConcurrentImageService.active_calls = 0
    ConcurrentImageService.max_active_calls = 0
    ConcurrentImageService.first_batch_barrier = threading.Barrier(2)
    monkeypatch.setattr(pipeline, "OpenAIService", ConcurrentImageService)
    monkeypatch.setattr(pipeline, "near_duplicate_representatives", lambda photos: photos)

    sleep_calls: list[float] = []

    async def record_sleep(seconds: float):
        sleep_calls.append(seconds)

    async def skip_share_export(output_dir):
        return []

    monkeypatch.setattr(pipeline.asyncio, "sleep", record_sleep)
    monkeypatch.setattr(pipeline, "export_share_images", skip_share_export)

    with caplog.at_level(logging.INFO, logger="app.pipeline"):
        asyncio.run(pipeline.run_pipeline(job, RecordingManager(), settings))

    assert set(ConcurrentImageService.generated_photo_ids) == {
        "photo-00001",
        "photo-00002",
        "photo-00003",
    }
    assert ConcurrentImageService.generated_captions == [
        "at the coast",
        "at the coast",
        "at the coast",
    ]
    assert ConcurrentImageService.max_active_calls == 2
    assert sleep_calls == [2.5]
    assert "image_generation_batch_started job_id=job-two pass=initial" in caplog.text
    assert "batch_size=2 concurrency=2" in caplog.text
    assert "image_generation_batch_completed job_id=job-two pass=initial" in caplog.text
    assert job.snapshot.status == JobStatus.completed
    manifest_path = next((tmp_path / "outputs").glob("*/album.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert [photo["caption"] for photo in manifest["photos"]] == [
        "poem line 1",
        "poem line 2",
        "poem line 3",
    ]
    album_html = (manifest_path.parent / "index.html").read_text(encoding="utf-8")
    share_html = (manifest_path.parent / "share.html").read_text(encoding="utf-8")
    assert "poem line 1" in album_html
    assert "poem line 3" in share_html
    assert "at the coast" not in album_html
    assert "at the coast" not in share_html
    assert job.pipeline_state["story_content_version"] == 2
    assert job.pipeline_state["photos"][0]["caption"] == "at the coast"
    assert job.pipeline_state["photos"][0]["poem_line"] == "poem line 1"


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
