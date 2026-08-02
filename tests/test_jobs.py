import asyncio
import json
from datetime import datetime, timezone

import httpx

from app import main
from app.config import Settings
from app.jobs import JobManager
from app.models import AlbumInput, JobStatus


def test_history_exposes_share_page_and_on_demand_zip(tmp_path):
    output_dir = tmp_path / "outputs"
    album_dir = output_dir / "trip-folder"
    album_dir.mkdir(parents=True)
    (album_dir / "album.json").write_text(
        json.dumps(
            {
                "id": "album-one",
                "title": "Trip",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "photos": [],
                "exports": [],
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(output_dir=output_dir, job_dir=tmp_path / ".jobs")
    settings.ensure_directories()

    manager = JobManager(settings)
    album = manager.history()[0]

    assert album.share_url == "/albums/trip-folder/share.html"
    assert album.output_url == "/albums/trip-folder/index.html"
    assert album.zip_url == "/api/albums/album-one/zip"
    assert manager.album_directory("album-one") == album_dir


def test_zip_endpoint_rebuilds_missing_archive(monkeypatch, tmp_path):
    output_dir = tmp_path / "outputs"
    album_dir = output_dir / "trip-folder"
    album_dir.mkdir(parents=True)
    (album_dir / "album.json").write_text(
        json.dumps(
            {
                "id": "album-one",
                "title": "Trip",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "photos": [],
                "exports": [],
            }
        ),
        encoding="utf-8",
    )
    (album_dir / "index.html").write_text("<h1>Trip</h1>", encoding="utf-8")
    settings = Settings(output_dir=output_dir, job_dir=tmp_path / ".jobs")
    settings.ensure_directories()
    monkeypatch.setattr(main, "manager", JobManager(settings))

    async def request_zip():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/albums/album-one/zip")

    response = asyncio.run(request_zip())

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert (album_dir / "trip-folder.zip").exists()


def test_manager_restores_running_job_as_queued_with_checkpoint(tmp_path):
    settings = Settings(output_dir=tmp_path / "outputs", job_dir=tmp_path / ".jobs")
    settings.ensure_directories()
    manager = JobManager(settings)
    job = asyncio.run(manager.create("recover-me", AlbumInput(title="Trip", target_count=3)))
    job.pipeline_state = {"candidate_ids": ["photo-00001"], "retry_round": 2}
    manager.update(
        job,
        status=JobStatus.running,
        stage="generation_retry",
        retry_round=2,
        can_stop_retries=True,
    )

    restored_manager = JobManager(settings)
    restored = restored_manager.current()

    assert restored is not None
    assert restored.snapshot.status == JobStatus.queued
    assert restored.snapshot.stage == "generation_retry"
    assert not restored.snapshot.can_stop_retries
    assert restored.pipeline_state["candidate_ids"] == ["photo-00001"]
    assert restored.pipeline_state["retry_round"] == 2


def test_current_detail_and_photo_endpoint_restore_form_data(monkeypatch, tmp_path):
    settings = Settings(output_dir=tmp_path / "outputs", job_dir=tmp_path / ".jobs")
    settings.ensure_directories()
    manager = JobManager(settings)
    job = asyncio.run(
        manager.create(
            "form-job",
            AlbumInput(
                title="Autumn Trip",
                location="Hangzhou",
                companions="Family",
                memory="A quiet afternoon",
                target_count=1,
            ),
        )
    )
    source = job.workspace / "uploads" / "00000-photo.jpg"
    source.write_bytes(b"local-photo")
    job.uploads.append(
        {
            "id": "photo-00001",
            "original_name": "photo.jpg",
            "path": str(source),
            "order": 0,
            "modified_at": None,
        }
    )
    manager.persist(job)
    manager.update(
        job,
        status=JobStatus.running,
        stage="generation_retry",
        can_stop_retries=True,
        failed_items=1,
    )
    monkeypatch.setattr(main, "manager", manager)

    async def request_current_job():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            detail = await client.get("/api/jobs/current/detail")
            photo = await client.get("/api/jobs/form-job/photos/photo-00001")
            stopped = await client.post("/api/jobs/form-job/stop-retries")
            return detail, photo, stopped

    detail, photo, stopped = asyncio.run(request_current_job())

    assert detail.status_code == 200
    assert detail.json()["album_input"]["title"] == "Autumn Trip"
    assert detail.json()["album_input"]["target_count"] == 1
    assert detail.json()["uploads"][0]["original_name"] == "photo.jpg"
    assert photo.status_code == 200
    assert photo.content == b"local-photo"
    assert stopped.status_code == 200
    assert stopped.json()["retry_stop_requested"] is True
    assert stopped.json()["can_stop_retries"] is False
    assert job.stop_retry_event.is_set()
