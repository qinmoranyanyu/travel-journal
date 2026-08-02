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
    pipeline_state: dict[str, Any] = field(default_factory=dict)
    task: asyncio.Task[None] | None = None
    stop_retry_event: asyncio.Event = field(default_factory=asyncio.Event)
    pause_event: asyncio.Event = field(default_factory=asyncio.Event)


class JobManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.jobs: dict[str, Job] = {}
        self.current_job_id: str | None = None
        self._lock = asyncio.Lock()
        self._restore_interrupted()

    async def create(self, job_id: str, album_input: AlbumInput) -> Job:
        async with self._lock:
            workspace = self.settings.job_dir / job_id
            (workspace / "uploads").mkdir(parents=True, exist_ok=False)
            (workspace / "analysis").mkdir()
            (workspace / "generation").mkdir()
            job = Job(
                snapshot=JobSnapshot(
                    id=job_id,
                    status=JobStatus.paused,
                    stage="queued",
                    message="任务已创建，等待手动开始",
                ),
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

    def list_jobs(self) -> list[Job]:
        return sorted(
            self.jobs.values(),
            key=lambda job: job.snapshot.updated_at,
            reverse=True,
        )

    async def prepare_start(self, job_id: str) -> Job:
        async with self._lock:
            job = self.get(job_id)
            if not job:
                raise KeyError(job_id)
            if job.task and not job.task.done():
                raise RuntimeError("任务正在运行")
            active = next(
                (
                    other
                    for other in self.jobs.values()
                    if other.snapshot.id != job_id
                    and other.snapshot.status in {JobStatus.queued, JobStatus.running}
                ),
                None,
            )
            if active:
                raise RuntimeError(f"请先暂停正在运行的任务：{active.album_input.title}")
            if job.snapshot.status not in {
                JobStatus.paused,
                JobStatus.failed,
                JobStatus.interrupted,
                JobStatus.queued,
            }:
                raise RuntimeError("当前任务不能开始")
            if not job.snapshot.retry_stop_requested:
                job.stop_retry_event.clear()
            job.pause_event.clear()
            self.update(
                job,
                status=JobStatus.queued,
                message=f"准备从 {job.snapshot.stage} 阶段继续",
                error=None,
                can_stop_retries=False,
                pause_requested=False,
            )
            self.current_job_id = job.snapshot.id
            return job

    async def prepare_resume(self, job_id: str) -> Job:
        return await self.prepare_start(job_id)

    def request_pause(self, job: Job) -> None:
        if job.snapshot.status != JobStatus.running or not job.task or job.task.done():
            raise RuntimeError("当前任务没有在运行")
        if job.snapshot.pause_requested:
            raise RuntimeError("任务正在暂停")
        job.pause_event.set()
        self.update(
            job,
            pause_requested=True,
            can_stop_retries=False,
            message="已请求暂停，当前步骤结束后保存断点",
        )

    def request_stop_retries(self, job: Job) -> None:
        if not job.snapshot.can_stop_retries or job.snapshot.retry_stop_requested:
            raise RuntimeError("当前没有可终止的生图重试")
        job.stop_retry_event.set()
        self.update(
            job,
            retry_stop_requested=True,
            can_stop_retries=False,
            message="已请求终止重试，当前请求结束后将继续排版",
        )

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
                album_id = str(data.get("id", folder))
                exports = [f"/albums/{folder}/{item}" for item in data.get("exports", [])]
                photos = data.get("photos", [])
                cover = None
                if photos:
                    cover = f"/albums/{folder}/{photos[0]['image']}"
                albums.append(
                    AlbumSummary(
                        id=album_id,
                        title=data.get("title", folder),
                        location=data.get("location", ""),
                        photo_count=len(photos),
                        created_at=datetime.fromisoformat(data["created_at"]),
                        cover_url=cover,
                        output_url=f"/albums/{folder}/index.html",
                        share_url=f"/albums/{folder}/share.html",
                        zip_url=f"/api/albums/{album_id}/zip",
                        export_urls=exports,
                    )
                )
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return sorted(albums, key=lambda album: album.created_at, reverse=True)

    def album_directory(self, album_id: str) -> Path | None:
        for manifest_path in self.settings.output_dir.glob("*/album.json"):
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                if str(data.get("id", manifest_path.parent.name)) == album_id:
                    return manifest_path.parent
            except (OSError, ValueError, TypeError):
                continue
        return None

    def _persist(self, job: Job) -> None:
        if not job.workspace.exists():
            return
        data = {
            "snapshot": job.snapshot.model_dump(mode="json"),
            "album_input": job.album_input.model_dump(mode="json"),
            "uploads": job.uploads,
            "pipeline_state": job.pipeline_state,
        }
        state_path = job.workspace / "job.json"
        temporary_path = job.workspace / "job.json.tmp"
        temporary_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(state_path)

    def _restore_interrupted(self) -> None:
        candidates: list[Job] = []
        for state_path in self.settings.job_dir.glob("*/job.json"):
            try:
                data = json.loads(state_path.read_text(encoding="utf-8"))
                snapshot = JobSnapshot.model_validate(data["snapshot"])
                if snapshot.status not in TERMINAL_STATUSES:
                    snapshot.status = JobStatus.paused
                    snapshot.can_stop_retries = False
                    snapshot.pause_requested = False
                    snapshot.message = f"服务曾在运行中停止，可从 {snapshot.stage} 阶段继续"
                    snapshot.updated_at = utc_now()
                job = Job(
                    snapshot=snapshot,
                    album_input=AlbumInput.model_validate(data["album_input"]),
                    workspace=state_path.parent,
                    uploads=data.get("uploads", []),
                    pipeline_state=data.get("pipeline_state", {}),
                )
                if snapshot.retry_stop_requested:
                    job.stop_retry_event.set()
                self.jobs[snapshot.id] = job
                candidates.append(job)
                self._persist(job)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        if candidates:
            latest = max(candidates, key=lambda item: item.snapshot.updated_at)
            self.current_job_id = latest.snapshot.id
