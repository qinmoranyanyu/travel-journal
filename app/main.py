from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .jobs import JobManager
from .logging_config import configure_logging
from .media import is_supported, safe_filename
from .models import (
    AlbumInput,
    ImageStyle,
    JobDetail,
    JobListItem,
    JobSnapshot,
    JobUpload,
    TERMINAL_STATUSES,
)
from .pipeline import run_pipeline
from .rendering import create_share_zip


settings = get_settings()
log_file = configure_logging(
    settings.log_dir,
    settings.log_level,
    secrets=(settings.openai_api_key, settings.amap_api_key),
)
logger = logging.getLogger(__name__)
logger.info("application_logging_configured log_file=%s level=%s", log_file, settings.log_level)
manager = JobManager(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("application_started")
    try:
        yield
    finally:
        running = [job.task for job in manager.jobs.values() if job.task and not job.task.done()]
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)
        logger.info("application_stopped cancelled_tasks=%d", len(running))


app = FastAPI(title="Travel Journal", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_unhandled_request_errors(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception:
        logger.exception(
            "request_failed method=%s path=%s",
            request.method,
            request.url.path,
        )
        raise


@app.get("/api/health")
async def health() -> dict:
    return {
        "ok": True,
        "api_configured": settings.api_configured,
        "text_model": settings.openai_text_model or None,
        "image_model": settings.openai_image_model,
        "location_configured": settings.location_configured,
    }


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=204)


@app.get("/api/jobs/current", response_model=JobSnapshot | None)
async def current_job() -> JobSnapshot | None:
    job = manager.current()
    return job.snapshot if job else None


@app.get("/api/jobs/current/detail", response_model=JobDetail | None)
async def current_job_detail() -> JobDetail | None:
    job = manager.current()
    return _job_detail(job) if job else None


@app.get("/api/jobs", response_model=list[JobListItem])
async def list_jobs() -> list[JobListItem]:
    return [_job_list_item(job) for job in manager.list_jobs()]


@app.get("/api/jobs/{job_id}/detail", response_model=JobDetail)
async def job_detail(job_id: str) -> JobDetail:
    job = manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _job_detail(job)


@app.get("/api/jobs/{job_id}", response_model=JobSnapshot)
async def get_job(job_id: str) -> JobSnapshot:
    job = manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job.snapshot


@app.get("/api/jobs/{job_id}/photos/{photo_id}")
async def job_photo(job_id: str, photo_id: str) -> FileResponse:
    job = manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    upload = next((item for item in job.uploads if item.get("id") == photo_id), None)
    if not upload:
        raise HTTPException(status_code=404, detail="照片不存在")
    analysis_path = job.workspace / "analysis" / f"{photo_id}.jpg"
    source_path = Path(upload["path"])
    photo_path = analysis_path if analysis_path.exists() else source_path
    if not photo_path.exists():
        raise HTTPException(status_code=404, detail="照片文件不存在")
    return FileResponse(photo_path, filename=upload["original_name"])


@app.post("/api/jobs/{job_id}/start", response_model=JobSnapshot, status_code=202)
@app.post("/api/jobs/{job_id}/resume", response_model=JobSnapshot, status_code=202)
async def start_job(job_id: str) -> JobSnapshot:
    try:
        job = await manager.prepare_start(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    job.task = asyncio.create_task(run_pipeline(job, manager, settings))
    job.task.add_done_callback(lambda task: _log_job_task_result(job_id, task))
    logger.info("job_started job_id=%s", job_id)
    return job.snapshot


@app.post("/api/jobs/{job_id}/pause", response_model=JobSnapshot)
async def pause_job(job_id: str) -> JobSnapshot:
    job = manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    try:
        manager.request_pause(job)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    logger.info("job_pause_requested job_id=%s stage=%s", job_id, job.snapshot.stage)
    return job.snapshot


@app.post("/api/jobs/{job_id}/stop-retries", response_model=JobSnapshot)
async def stop_generation_retries(job_id: str) -> JobSnapshot:
    job = manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    try:
        manager.request_stop_retries(job)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    logger.info("job_generation_retries_stopped job_id=%s", job_id)
    return job.snapshot


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    if not manager.get(job_id):
        raise HTTPException(status_code=404, detail="任务不存在")

    async def event_stream():
        previous = ""
        while True:
            job = manager.get(job_id)
            if not job:
                break
            payload = job.snapshot.model_dump_json()
            if payload != previous:
                yield f"data: {payload}\n\n"
                previous = payload
            if job.snapshot.status in TERMINAL_STATUSES:
                break
            await asyncio.sleep(0.75)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/albums")
async def albums():
    return manager.history()


@app.get("/api/albums/{album_id}/zip")
async def download_album_zip(album_id: str) -> FileResponse:
    output_dir = manager.album_directory(album_id)
    if output_dir is None:
        raise HTTPException(status_code=404, detail="相册不存在")
    zip_path = output_dir / f"{output_dir.name}.zip"
    if not zip_path.exists():
        await asyncio.to_thread(create_share_zip, output_dir)
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"{output_dir.name}.zip",
    )


@app.post("/api/jobs", response_model=JobSnapshot, status_code=202)
async def create_job(
    title: Annotated[str, Form()],
    target_count: Annotated[int, Form()],
    photos: Annotated[list[UploadFile], File()],
    location: Annotated[str, Form()] = "",
    companions: Annotated[str, Form()] = "",
    memory: Annotated[str, Form()] = "",
    image_style: Annotated[ImageStyle, Form()] = ImageStyle.photo_revival,
    file_metadata: Annotated[str, Form()] = "[]",
) -> JobSnapshot:
    supported = [photo for photo in photos if photo.filename and is_supported(photo.filename)]
    if not supported:
        raise HTTPException(status_code=400, detail="没有找到支持的图片文件")
    if target_count < 1:
        raise HTTPException(status_code=422, detail="成片数量至少为 1")
    try:
        metadata_items = json.loads(file_metadata)
        metadata_by_name = {
            (item.get("name"), item.get("order")): item
            for item in metadata_items
            if isinstance(item, dict)
        }
    except json.JSONDecodeError:
        logger.warning("upload_metadata_invalid_json photo_count=%d", len(supported))
        metadata_by_name = {}

    album_input = AlbumInput(
        title=title.strip(),
        location=location.strip(),
        companions=companions.strip(),
        memory=memory.strip(),
        target_count=target_count,
        image_style=image_style,
    )
    job_id = uuid.uuid4().hex
    job = await manager.create(job_id, album_input)

    try:
        for order, upload in enumerate(supported):
            original_name = upload.filename or f"photo-{order + 1}.jpg"
            destination = job.workspace / "uploads" / f"{order:05d}-{safe_filename(original_name)}"
            with destination.open("wb") as target:
                while chunk := await upload.read(1024 * 1024):
                    target.write(chunk)
            meta = metadata_by_name.get((original_name, order), {})
            modified_at = _browser_timestamp(meta.get("lastModified"))
            job.uploads.append(
                {
                    "id": f"photo-{order + 1:05d}",
                    "original_name": original_name,
                    "path": str(destination),
                    "order": order,
                    "modified_at": modified_at.isoformat() if modified_at else None,
                }
            )
        manager.persist(job)
        logger.info(
            "job_created job_id=%s photo_count=%d target_count=%d image_style=%s",
            job_id,
            len(job.uploads),
            target_count,
            image_style.value,
        )
    except Exception as exc:
        logger.exception("job_upload_failed job_id=%s photo_count=%d", job_id, len(supported))
        manager.update(job, status="failed", stage="upload", message="照片保存失败", error=str(exc))
        raise HTTPException(status_code=500, detail="照片保存失败") from exc
    finally:
        for upload in photos:
            await upload.close()

    return job.snapshot


def _browser_timestamp(value) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(value) / 1000).replace(microsecond=0)
    except (TypeError, ValueError, OSError):
        return None


def _log_job_task_result(job_id: str, task: asyncio.Task[None]) -> None:
    if task.cancelled():
        logger.info("job_task_cancelled job_id=%s", job_id)
        return
    error = task.exception()
    if error is not None:
        logger.error(
            "job_task_unhandled_error job_id=%s",
            job_id,
            exc_info=(type(error), error, error.__traceback__),
        )


def _job_detail(job) -> JobDetail:
    return JobDetail(
        snapshot=job.snapshot,
        album_input=job.album_input,
        uploads=[
            JobUpload(
                id=upload["id"],
                original_name=upload["original_name"],
                order=upload["order"],
                modified_at=upload.get("modified_at"),
                preview_url=f"/api/jobs/{job.snapshot.id}/photos/{upload['id']}",
            )
            for upload in sorted(job.uploads, key=lambda item: item["order"])
        ],
    )


def _job_list_item(job) -> JobListItem:
    first_upload = min(job.uploads, key=lambda upload: upload["order"], default=None)
    return JobListItem(
        snapshot=job.snapshot,
        album_input=job.album_input,
        upload_count=len(job.uploads),
        preview_url=(
            f"/api/jobs/{job.snapshot.id}/photos/{first_upload['id']}"
            if first_upload
            else None
        ),
    )


app.mount("/albums", StaticFiles(directory=settings.output_dir, html=True), name="albums")

if settings.frontend_dist.exists():
    app.mount("/", StaticFiles(directory=settings.frontend_dist, html=True), name="frontend")
else:
    @app.get("/")
    async def development_root():
        return JSONResponse(
            {
                "message": "前端尚未构建，请运行 frontend 开发服务器或对应系统的安装脚本",
                "api": "/docs",
            }
        )
