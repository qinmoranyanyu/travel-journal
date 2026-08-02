from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .models import (
    AlbumInput,
    AlbumSummary,
    JobSnapshot,
    JobStatus,
    TERMINAL_STATUSES,
    utc_now,
)


@dataclass
class Job:
    snapshot: JobSnapshot
    album_input: AlbumInput
    workspace: Path
    uploads: list[dict[str, Any]] = field(default_factory=list)
    task: asyncio.Task[None] | None = None


class JobManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.jobs: dict[str, Job] = {}
        self.current_job_id: str | None = None
        self._lock = asyncio.Lock()
        self._restore_interrupted()

    async def create(self, job_id: str, album_input: AlbumInput) -> Job:
        async with self._lock:
            active = self.current()
            if active and active.snapshot.status not in TERMINAL_STATUSES:
                raise RuntimeError("当前已有相册正在生成")
            workspace = self.settings.job_dir / job_id
            (workspace / "uploads").mkdir(parents=True, exist_ok=False)
            (workspace / "analysis").mkdir()
            (workspace / "generation").mkdir()
            job = Job(
                snapshot=JobSnapshot(id=job_id),
                album_input=album_input,
                workspace=workspace,
            )
            self.jobs[job_id] = job
            self.current_job_id = job_id
            self._persist(job)
            return job

    def current(self) -> Job | None:
        if not self.current_job_id:
            return None
        return self.jobs.get(self.current_job_id)

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def update(self, job: Job, **changes: Any) -> None:
        snapshot = job.snapshot
        for key, value in changes.items():
            if hasattr(snapshot, key):
                setattr(snapshot, key, value)
        snapshot.updated_at = utc_now()
        self._persist(job)

    def persist(self, job: Job) -> None:
        self._persist(job)

    def history(self) -> list[AlbumSummary]:
        albums: list[AlbumSummary] = []
        for manifest_path in self.settings.output_dir.glob("*/album.json"):
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                folder = manifest_path.parent.name
                exports = [f"/albums/{folder}/{item}" for item in data.get("exports", [])]
                photos = data.get("photos", [])
                cover = None
                if photos:
                    cover = f"/albums/{folder}/{photos[0]['image']}"
                zip_path = manifest_path.parent / f"{folder}.zip"
                albums.append(
                    AlbumSummary(
                        id=data.get("id", folder),
                        title=data.get("title", folder),
                        location=data.get("location", ""),
                        photo_count=len(photos),
                        created_at=datetime.fromisoformat(data["created_at"]),
                        cover_url=cover,
                        output_url=f"/albums/{folder}/index.html",
                        zip_url=f"/albums/{folder}/{folder}.zip" if zip_path.exists() else None,
                        export_urls=exports,
                    )
                )
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return sorted(albums, key=lambda album: album.created_at, reverse=True)

    def _persist(self, job: Job) -> None:
        if not job.workspace.exists():
            return
        data = {
            "snapshot": job.snapshot.model_dump(mode="json"),
            "album_input": job.album_input.model_dump(mode="json"),
            "uploads": job.uploads,
        }
        (job.workspace / "job.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _restore_interrupted(self) -> None:
        candidates: list[Job] = []
        for state_path in self.settings.job_dir.glob("*/job.json"):
            try:
                data = json.loads(state_path.read_text(encoding="utf-8"))
                snapshot = JobSnapshot.model_validate(data["snapshot"])
                if snapshot.status not in TERMINAL_STATUSES:
                    snapshot.status = JobStatus.interrupted
                    snapshot.stage = "interrupted"
                    snapshot.message = "本地服务曾在任务完成前停止"
                    snapshot.updated_at = utc_now()
                job = Job(
                    snapshot=snapshot,
                    album_input=AlbumInput.model_validate(data["album_input"]),
                    workspace=state_path.parent,
                    uploads=data.get("uploads", []),
                )
                self.jobs[snapshot.id] = job
                candidates.append(job)
                self._persist(job)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        if candidates:
            latest = max(candidates, key=lambda item: item.snapshot.updated_at)
            self.current_job_id = latest.snapshot.id
