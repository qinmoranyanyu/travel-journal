import asyncio
import json
from datetime import datetime, timezone

import httpx

from app import main
from app.config import Settings
from app.jobs import JobManager


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
