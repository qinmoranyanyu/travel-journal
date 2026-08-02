from __future__ import annotations

import asyncio
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

from .ai import OpenAIService
from .config import Settings
from .jobs import Job, JobManager
from .media import (
    MediaPhoto,
    copy_selected_source,
    create_variants,
    inspect_photo,
    near_duplicate_representatives,
    normalize_generated_page,
    photo_sort_key,
)
from .models import AlbumManifest, JobStatus
from .rendering import (
    create_share_zip,
    export_share_images,
    render_album,
    update_manifest_exports,
)
from .selection import select_story_set


async def run_pipeline(job: Job, manager: JobManager, settings: Settings) -> None:
    output_dir: Path | None = None
    try:
        if not settings.api_configured:
            raise RuntimeError("OpenAI 配置不完整，请在 .env 中设置密钥和模型名称")
        manager.update(
            job,
            status=JobStatus.running,
            stage="metadata",
            progress=5,
            message="正在读取照片时间与画面信息",
            total_items=len(job.uploads),
        )

        photos = _build_media_photos(job)
        for index, photo in enumerate(photos):
            await asyncio.to_thread(inspect_photo, photo)
            await asyncio.to_thread(
                create_variants,
                photo,
                job.workspace / "analysis",
                job.workspace / "generation",
            )
            manager.update(
                job,
                completed_items=index + 1,
                progress=5 + 12 * (index + 1) / len(photos),
                message=f"正在整理第 {index + 1}/{len(photos)} 张照片",
            )

        manager.update(job, stage="deduplicate", progress=18, message="正在过滤近似照片")
        candidates = await asyncio.to_thread(near_duplicate_representatives, photos)

        service = OpenAIService(settings)
        manager.update(
            job,
            stage="analysis",
            progress=22,
            completed_items=0,
            total_items=len(candidates),
            message="正在理解照片中的人物、场景与记忆点",
        )
        for start in range(0, len(candidates), settings.vision_batch_size):
            batch = candidates[start : start + settings.vision_batch_size]
            analyses = await asyncio.to_thread(service.analyze_photos, batch)
            for photo, analysis in zip(batch, analyses, strict=False):
                photo.analysis = analysis
            completed = min(start + len(batch), len(candidates))
            manager.update(
                job,
                completed_items=completed,
                progress=22 + 18 * completed / len(candidates),
                message=f"已理解 {completed}/{len(candidates)} 张候选照片",
            )

        manager.update(job, stage="selection", progress=41, message="正在组合最有故事价值的照片")
        selected = select_story_set(candidates, job.album_input.target_count)
        if not selected:
            raise RuntimeError("没有找到可用于生成相册的照片")

        manager.update(job, stage="story", progress=46, message="正在编排旅行章节与旁白")
        context = job.album_input.model_dump()
        story = await asyncio.to_thread(service.create_story, selected, context)
        chapter_by_photo: dict[str, str] = {}
        for chapter in story.chapters:
            for photo_id in chapter.photo_ids:
                chapter_by_photo[photo_id] = chapter.id
        for photo in selected:
            photo.caption = story.captions.get(photo.id, photo.analysis.caption_seed)
            photo.chapter_id = chapter_by_photo.get(photo.id, story.chapters[-1].id)

        output_dir = _make_output_dir(settings.output_dir, job)
        photos_dir = output_dir / "assets" / "photos"
        sources_dir = output_dir / "sources"
        photos_dir.mkdir(parents=True)
        sources_dir.mkdir()
        source_paths = {
            photo.id: copy_selected_source(photo, sources_dir).relative_to(output_dir).as_posix()
            for photo in selected
        }

        manager.update(
            job,
            stage="generation",
            progress=50,
            completed_items=0,
            total_items=len(selected),
            message=f"正在生成第 1/{len(selected)} 张手绘照片",
        )
        semaphore = asyncio.Semaphore(settings.image_generation_concurrency)
        generated_count = 0
        attempt_errors: dict[str, Exception] = {}
        progress_lock = asyncio.Lock()

        async def generate_photo(photo: MediaPhoto) -> None:
            raw_path = job.workspace / "generated" / f"{photo.id}.png"
            final_path = photos_dir / f"{photo.id}.jpg"
            async with semaphore:
                await asyncio.to_thread(
                    service.generate_revival,
                    photo,
                    photo.caption,
                    raw_path,
                )
                await asyncio.to_thread(normalize_generated_page, raw_path, final_path)
                photo.generated_path = final_path

        async def generate_one(photo: MediaPhoto) -> None:
            nonlocal generated_count
            try:
                await generate_photo(photo)
            except Exception as exc:  # Keep completed images when one provider request fails.
                attempt_errors[photo.id] = exc
            finally:
                async with progress_lock:
                    generated_count += 1
                    manager.update(
                        job,
                        completed_items=generated_count,
                        progress=50 + 38 * generated_count / len(selected),
                        message=f"已生成 {generated_count}/{len(selected)} 张手绘照片",
                    )

        await asyncio.gather(*(generate_one(photo) for photo in selected))
        retry_photos = [
            photo
            for photo in selected
            if photo.generated_path is None
            and _is_retryable_generation_error(attempt_errors.get(photo.id))
        ]
        retry_errors: dict[str, Exception] = {}
        if retry_photos:
            manager.update(
                job,
                stage="generation",
                progress=88,
                completed_items=0,
                total_items=len(retry_photos),
                message=f"图片服务连接不稳定，正在串行重试 1/{len(retry_photos)} 张照片",
            )
            for index, photo in enumerate(retry_photos):
                try:
                    await generate_photo(photo)
                except Exception as exc:
                    retry_errors[photo.id] = exc
                manager.update(
                    job,
                    completed_items=index + 1,
                    progress=88 + 2 * (index + 1) / len(retry_photos),
                    message=f"已重试 {index + 1}/{len(retry_photos)} 张照片",
                )

        failures = [
            (
                photo,
                str(retry_errors.get(photo.id) or attempt_errors.get(photo.id) or "未知错误"),
            )
            for photo in selected
            if photo.generated_path is None
        ]
        completed_photos = sorted(
            [photo for photo in selected if photo.generated_path is not None],
            key=photo_sort_key,
        )
        if not completed_photos:
            details = failures[0][1] if failures else "未知错误"
            raise RuntimeError(f"所有图片均生成失败：{details}")

        manager.update(job, stage="render", progress=90, message="正在排版离线旅行手记")
        manifest = _build_manifest(
            job,
            completed_photos,
            source_paths,
            story,
        )
        await asyncio.to_thread(render_album, manifest, output_dir)

        manager.update(job, stage="export", progress=94, message="正在导出朋友圈章节长图")
        export_error: str | None = None
        try:
            exports = await export_share_images(output_dir)
        except Exception as exc:
            exports = []
            export_error = str(exc)
        await asyncio.to_thread(update_manifest_exports, output_dir, exports)
        await asyncio.to_thread(create_share_zip, output_dir)

        folder = output_dir.name
        status = JobStatus.partial if failures or export_error else JobStatus.completed
        errors = [f"{photo.original_name}: {error}" for photo, error in failures]
        if export_error:
            errors.append(f"长图导出: {export_error}")
        manager.update(
            job,
            status=status,
            stage="done",
            progress=100,
            message=(
                f"相册已完成，{len(failures)} 张照片生成失败"
                if failures
                else "相册与分享文件已完成"
            ),
            error="\n".join(errors) or None,
            output_url=f"/albums/{folder}/index.html",
            zip_url=f"/albums/{folder}/{folder}.zip",
            export_urls=[f"/albums/{folder}/{item}" for item in exports],
        )
        await asyncio.to_thread(_cleanup_workspace, job.workspace)
    except Exception as exc:
        manager.update(
            job,
            status=JobStatus.failed,
            stage="failed",
            message="生成任务未完成",
            error=str(exc),
        )


def _build_media_photos(job: Job) -> list[MediaPhoto]:
    photos = []
    for upload in job.uploads:
        modified = upload.get("modified_at")
        photos.append(
            MediaPhoto(
                id=upload["id"],
                original_name=upload["original_name"],
                source_path=Path(upload["path"]),
                upload_order=upload["order"],
                browser_modified_at=datetime.fromisoformat(modified) if modified else None,
            )
        )
    return photos


def _is_retryable_generation_error(error: Exception | None) -> bool:
    if isinstance(
        error,
        (APIConnectionError, APITimeoutError, RateLimitError, ConnectionError, TimeoutError),
    ):
        return True
    return isinstance(error, APIStatusError) and error.status_code >= 500


def _make_output_dir(root: Path, job: Job) -> Path:
    safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", job.album_input.title).strip(" .-")
    folder = f"{safe_title or 'travel-journal'}-{datetime.now():%Y%m%d-%H%M}-{job.snapshot.id[:6]}"
    output = root / folder
    output.mkdir(parents=True, exist_ok=False)
    return output


def _build_manifest(job: Job, photos: list[MediaPhoto], source_paths: dict[str, str], story) -> AlbumManifest:
    times = [photo.capture_time for photo in photos if photo.capture_time]
    if times:
        first, last = min(times), max(times)
        date_range = first.strftime("%Y.%m.%d")
        if first.date() != last.date():
            date_range += f" - {last:%Y.%m.%d}"
    else:
        date_range = "时间待考"

    photo_rows = []
    for photo in photos:
        analysis = photo.analysis
        photo_rows.append(
            {
                "id": photo.id,
                "original_name": photo.original_name,
                "capture_time": photo.capture_time.isoformat() if photo.capture_time else None,
                "time_source": photo.time_source,
                "time_confidence": photo.time_confidence,
                "display_date": photo.capture_time.strftime("%Y.%m.%d") if photo.capture_time else "顺序记录",
                "description": analysis.description if analysis else "旅行照片",
                "category": analysis.category if analysis else "其他",
                "caption": photo.caption,
                "chapter_id": photo.chapter_id,
                "image": f"assets/photos/{photo.id}.jpg",
                "source": source_paths[photo.id],
            }
        )

    valid_ids = {photo.id for photo in photos}
    chapters = []
    for chapter in story.chapters:
        ids = [photo_id for photo_id in chapter.photo_ids if photo_id in valid_ids]
        if ids:
            chapters.append(
                {
                    "id": chapter.id,
                    "title": chapter.title,
                    "intro": chapter.intro,
                    "photo_ids": ids,
                }
            )
    return AlbumManifest(
        id=job.snapshot.id,
        title=job.album_input.title,
        location=job.album_input.location,
        companions=job.album_input.companions,
        memory=job.album_input.memory,
        date_range=date_range,
        cover_subtitle=story.cover_subtitle,
        closing=story.closing,
        chapters=chapters,
        photos=photo_rows,
    )


def _cleanup_workspace(path: Path) -> None:
    if path.parent.name != ".jobs":
        raise RuntimeError("拒绝清理非任务目录")
    shutil.rmtree(path)
