from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Any

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
from .models import AlbumManifest, ImageAnalysis, JobStatus, StoryPlan
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
            message="正在读取照片时间与画面信息，已完成的步骤会自动跳过",
            error=None,
            can_stop_retries=False,
            total_items=len(job.uploads),
        )

        photos = _restore_media_photos(job) or _build_media_photos(job)
        for index, photo in enumerate(photos):
            if not _metadata_ready(photo):
                await asyncio.to_thread(inspect_photo, photo)
                await asyncio.to_thread(
                    create_variants,
                    photo,
                    job.workspace / "analysis",
                    job.workspace / "generation",
                )
                _checkpoint_photos(job, manager, photos)
            manager.update(
                job,
                completed_items=index + 1,
                progress=5 + 12 * (index + 1) / len(photos),
                message=f"正在整理第 {index + 1}/{len(photos)} 张照片",
            )

        manager.update(job, stage="deduplicate", progress=18, message="正在过滤近似照片")
        photo_by_id = {photo.id: photo for photo in photos}
        candidate_ids = job.pipeline_state.get("candidate_ids")
        candidates = [photo_by_id[photo_id] for photo_id in candidate_ids or [] if photo_id in photo_by_id]
        if not candidates:
            candidates = await asyncio.to_thread(near_duplicate_representatives, photos)
            _checkpoint(job, manager, candidate_ids=[photo.id for photo in candidates])

        service = OpenAIService(settings)
        analyzed_count = sum(photo.analysis is not None for photo in candidates)
        manager.update(
            job,
            stage="analysis",
            progress=22,
            completed_items=analyzed_count,
            total_items=len(candidates),
            message="正在理解照片中的人物、场景与记忆点",
        )
        pending_analysis = [photo for photo in candidates if photo.analysis is None]
        for start in range(0, len(pending_analysis), settings.vision_batch_size):
            batch = pending_analysis[start : start + settings.vision_batch_size]
            analyses = await asyncio.to_thread(service.analyze_photos, batch)
            for photo, analysis in zip(batch, analyses, strict=False):
                photo.analysis = analysis
            _checkpoint_photos(job, manager, photos)
            completed = sum(photo.analysis is not None for photo in candidates)
            manager.update(
                job,
                completed_items=completed,
                progress=22 + 18 * completed / len(candidates),
                message=f"已理解 {completed}/{len(candidates)} 张候选照片",
            )

        manager.update(job, stage="selection", progress=41, message="正在组合最有故事价值的照片")
        selected_ids = job.pipeline_state.get("selected_ids")
        selected = [photo_by_id[photo_id] for photo_id in selected_ids or [] if photo_id in photo_by_id]
        if not selected:
            selected = select_story_set(candidates, job.album_input.target_count)
            _checkpoint(job, manager, selected_ids=[photo.id for photo in selected])
        if not selected:
            raise RuntimeError("没有找到可用于生成相册的照片")

        manager.update(job, stage="story", progress=46, message="正在编排旅行章节与旁白")
        story_data = job.pipeline_state.get("story")
        if story_data:
            story = StoryPlan.model_validate(story_data)
        else:
            context = job.album_input.model_dump()
            story = await asyncio.to_thread(service.create_story, selected, context)
            _checkpoint(job, manager, story=story.model_dump(mode="json"))
        chapter_by_photo: dict[str, str] = {}
        for chapter in story.chapters:
            for photo_id in chapter.photo_ids:
                chapter_by_photo[photo_id] = chapter.id
        for photo in selected:
            photo.caption = story.captions.get(photo.id, photo.analysis.caption_seed)
            photo.chapter_id = chapter_by_photo.get(photo.id, story.chapters[-1].id)
        _checkpoint_photos(job, manager, photos)

        output_dir = _get_output_dir(settings.output_dir, job, manager)
        photos_dir = output_dir / "assets" / "photos"
        sources_dir = output_dir / "sources"
        photos_dir.mkdir(parents=True, exist_ok=True)
        sources_dir.mkdir(parents=True, exist_ok=True)
        source_paths = dict(job.pipeline_state.get("source_paths", {}))
        for photo in selected:
            relative = source_paths.get(photo.id)
            source_copy = output_dir / relative if relative else None
            if source_copy is None or not source_copy.exists():
                source_copy = copy_selected_source(photo, sources_dir)
                source_paths[photo.id] = source_copy.relative_to(output_dir).as_posix()
        _checkpoint(job, manager, source_paths=source_paths)

        for photo in selected:
            final_path = photos_dir / f"{photo.id}.jpg"
            if _usable_file(final_path):
                photo.generated_path = final_path

        manager.update(
            job,
            stage="generation",
            progress=50,
            completed_items=0,
            total_items=len(selected),
            message=f"正在生成第 1/{len(selected)} 张手绘照片",
        )
        generation_attempted = False
        generation_errors = dict(job.pipeline_state.get("generation_errors", {}))

        async def wait_for_generation_slot(waiting_message: str, allow_stop: bool) -> bool:
            nonlocal generation_attempted
            if generation_attempted and settings.image_generation_interval_seconds > 0:
                manager.update(job, message=waiting_message)
                if allow_stop:
                    try:
                        await asyncio.wait_for(
                            job.stop_retry_event.wait(),
                            timeout=settings.image_generation_interval_seconds,
                        )
                        return False
                    except TimeoutError:
                        pass
                else:
                    await asyncio.sleep(settings.image_generation_interval_seconds)
            return not (allow_stop and job.stop_retry_event.is_set())

        async def generate_photo(photo: MediaPhoto) -> None:
            nonlocal generation_attempted
            generation_attempted = True
            raw_path = job.workspace / "generated" / f"{photo.id}.png"
            final_path = photos_dir / f"{photo.id}.jpg"
            temporary_path = photos_dir / f"{photo.id}.tmp.jpg"
            await asyncio.to_thread(
                service.generate_revival,
                photo,
                photo.caption,
                raw_path,
            )
            await asyncio.to_thread(normalize_generated_page, raw_path, temporary_path)
            temporary_path.replace(final_path)
            photo.generated_path = final_path

        if not job.pipeline_state.get("generation_first_pass_complete"):
            initial_queue = [photo for photo in selected if photo.generated_path is None]
            for index, photo in enumerate(initial_queue):
                await wait_for_generation_slot(
                    (
                        f"等待 {settings.image_generation_interval_seconds:g} 秒后生成"
                        f"第 {index + 1}/{len(initial_queue)} 张照片"
                    ),
                    allow_stop=False,
                )
                try:
                    await generate_photo(photo)
                    generation_errors.pop(photo.id, None)
                except Exception as exc:
                    generation_errors[photo.id] = str(exc)
                _checkpoint_photos(job, manager, photos)
                _checkpoint(job, manager, generation_errors=generation_errors)
                completed = len(selected) - len(
                    [item for item in selected if item.generated_path is None]
                )
                manager.update(
                    job,
                    completed_items=index + 1,
                    progress=50 + 38 * (index + 1) / max(1, len(initial_queue)),
                    message=f"首轮已处理 {index + 1}/{len(initial_queue)} 张照片，成功 {completed} 张",
                )
            _checkpoint(job, manager, generation_first_pass_complete=True)

        pending = [photo for photo in selected if photo.generated_path is None]
        retry_round = int(job.pipeline_state.get("retry_round", 0))
        while pending and not job.stop_retry_event.is_set():
            retry_round += 1
            manager.update(
                job,
                stage="generation_retry",
                progress=89,
                completed_items=0,
                total_items=len(pending),
                failed_items=len(pending),
                retry_round=retry_round,
                can_stop_retries=True,
                message=f"第 {retry_round} 轮重试：还有 {len(pending)} 张照片未成功",
            )
            current_round = list(pending)
            for index, photo in enumerate(current_round):
                if job.stop_retry_event.is_set():
                    break
                should_continue = await wait_for_generation_slot(
                    (
                        f"等待 {settings.image_generation_interval_seconds:g} 秒后进行"
                        f"第 {retry_round} 轮的 {index + 1}/{len(current_round)} 项重试"
                    ),
                    allow_stop=True,
                )
                if not should_continue:
                    break
                try:
                    await generate_photo(photo)
                    generation_errors.pop(photo.id, None)
                except Exception as exc:
                    generation_errors[photo.id] = str(exc)
                _checkpoint_photos(job, manager, photos)
                _checkpoint(
                    job,
                    manager,
                    generation_errors=generation_errors,
                    retry_round=retry_round,
                )
                remaining = sum(photo.generated_path is None for photo in selected)
                manager.update(
                    job,
                    completed_items=index + 1,
                    failed_items=remaining,
                    progress=89,
                    can_stop_retries=True,
                    message=f"第 {retry_round} 轮已重试 {index + 1}/{len(current_round)} 张，还剩 {remaining} 张",
                )
            pending = [photo for photo in selected if photo.generated_path is None]

        skipped_ids = set(job.pipeline_state.get("skipped_generation_ids", []))
        if pending:
            manager.update(
                job,
                stage="generation_fallback",
                can_stop_retries=False,
                message=f"已终止重试，正在用处理后的原图补齐 {len(pending)} 张照片",
            )
            for photo in pending:
                final_path = photos_dir / f"{photo.id}.jpg"
                temporary_path = photos_dir / f"{photo.id}.tmp.jpg"
                fallback_source = photo.generation_path or photo.source_path
                await asyncio.to_thread(normalize_generated_page, fallback_source, temporary_path)
                temporary_path.replace(final_path)
                photo.generated_path = final_path
                skipped_ids.add(photo.id)
                previous_error = generation_errors.get(photo.id)
                generation_errors[photo.id] = (
                    f"{previous_error}；用户终止重试，已使用原图"
                    if previous_error
                    else "用户终止重试，已使用原图"
                )
                _checkpoint_photos(job, manager, photos)
                _checkpoint(
                    job,
                    manager,
                    skipped_generation_ids=sorted(skipped_ids),
                    generation_errors=generation_errors,
                )
            _checkpoint(
                job,
                manager,
                skipped_generation_ids=sorted(skipped_ids),
                generation_errors=generation_errors,
            )

        failures = [
            (photo, generation_errors.get(photo.id, "用户终止重试，已使用原图"))
            for photo in selected
            if photo.id in skipped_ids
        ]
        completed_photos = sorted(
            [photo for photo in selected if photo.generated_path is not None],
            key=photo_sort_key,
        )

        manager.update(
            job,
            stage="render",
            progress=90,
            can_stop_retries=False,
            failed_items=len(failures),
            message="正在排版离线旅行手记",
        )
        manifest = _build_manifest(
            job,
            completed_photos,
            source_paths,
            story,
        )
        await asyncio.to_thread(render_album, manifest, output_dir)
        _checkpoint(job, manager, render_complete=True)

        manager.update(job, stage="export", progress=94, message="正在导出朋友圈章节长图")
        export_error: str | None = None
        exports = list(job.pipeline_state.get("exports", []))
        if not exports or any(not (output_dir / item).exists() for item in exports):
            try:
                exports = await export_share_images(output_dir)
            except Exception as exc:
                exports = []
                export_error = str(exc)
            _checkpoint(job, manager, exports=exports)
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
                f"相册已完成，{len(failures)} 张照片已使用原图"
                if failures
                else "相册与分享文件已完成"
            ),
            error="\n".join(errors) or None,
            output_url=f"/albums/{folder}/index.html",
            share_url=f"/albums/{folder}/share.html",
            zip_url=f"/albums/{folder}/{folder}.zip",
            export_urls=[f"/albums/{folder}/{item}" for item in exports],
            can_stop_retries=False,
            failed_items=len(failures),
        )
    except Exception as exc:
        manager.update(
            job,
            status=JobStatus.failed,
            stage="failed",
            message="生成任务未完成",
            error=str(exc),
            can_stop_retries=False,
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


def _restore_media_photos(job: Job) -> list[MediaPhoto]:
    rows = job.pipeline_state.get("photos")
    if not isinstance(rows, list) or not rows:
        return []
    uploads = {upload["id"]: upload for upload in job.uploads}
    if any(row.get("id") not in uploads for row in rows):
        return []

    photos: list[MediaPhoto] = []
    for row in rows:
        upload = uploads[row["id"]]
        modified = upload.get("modified_at")
        analysis_data = row.get("analysis")
        generated_path = Path(row["generated_path"]) if row.get("generated_path") else None
        photos.append(
            MediaPhoto(
                id=upload["id"],
                original_name=upload["original_name"],
                source_path=Path(upload["path"]),
                upload_order=upload["order"],
                browser_modified_at=datetime.fromisoformat(modified) if modified else None,
                width=int(row.get("width", 0)),
                height=int(row.get("height", 0)),
                capture_time=(
                    datetime.fromisoformat(row["capture_time"])
                    if row.get("capture_time")
                    else None
                ),
                time_source=row.get("time_source", "upload_order"),
                time_confidence=row.get("time_confidence", "estimated"),
                analysis_path=Path(row["analysis_path"]) if row.get("analysis_path") else None,
                generation_path=(
                    Path(row["generation_path"]) if row.get("generation_path") else None
                ),
                phash=row.get("phash", ""),
                local_quality=float(row.get("local_quality", 0.5)),
                analysis=ImageAnalysis.model_validate(analysis_data) if analysis_data else None,
                generated_path=(
                    generated_path if generated_path and _usable_file(generated_path) else None
                ),
                caption=row.get("caption", ""),
                chapter_id=row.get("chapter_id", ""),
            )
        )
    return photos


def _photo_state(photo: MediaPhoto) -> dict[str, Any]:
    analysis = photo.analysis
    return {
        "id": photo.id,
        "width": photo.width,
        "height": photo.height,
        "capture_time": photo.capture_time.isoformat() if photo.capture_time else None,
        "time_source": photo.time_source,
        "time_confidence": photo.time_confidence,
        "analysis_path": str(photo.analysis_path) if photo.analysis_path else None,
        "generation_path": str(photo.generation_path) if photo.generation_path else None,
        "phash": photo.phash,
        "local_quality": photo.local_quality,
        "analysis": analysis.model_dump(mode="json") if isinstance(analysis, ImageAnalysis) else None,
        "generated_path": str(photo.generated_path) if photo.generated_path else None,
        "caption": photo.caption,
        "chapter_id": photo.chapter_id,
    }


def _checkpoint_photos(job: Job, manager: JobManager, photos: list[MediaPhoto]) -> None:
    _checkpoint(job, manager, photos=[_photo_state(photo) for photo in photos])


def _checkpoint(job: Job, manager: JobManager, **changes: Any) -> None:
    job.pipeline_state.update(changes)
    persist = getattr(manager, "persist", None)
    if persist:
        persist(job)


def _metadata_ready(photo: MediaPhoto) -> bool:
    return bool(
        photo.phash
        and photo.analysis_path
        and photo.analysis_path.exists()
        and photo.generation_path
        and photo.generation_path.exists()
    )


def _usable_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _get_output_dir(root: Path, job: Job, manager: JobManager) -> Path:
    folder = job.pipeline_state.get("output_folder")
    if not folder:
        safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", job.album_input.title).strip(" .-")
        folder = f"{safe_title or 'travel-journal'}-{datetime.now():%Y%m%d-%H%M}-{job.snapshot.id[:6]}"
        _checkpoint(job, manager, output_folder=folder)
    output = root / folder
    output.mkdir(parents=True, exist_ok=True)
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
