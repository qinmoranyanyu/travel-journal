from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .ai import OpenAIService
from .config import Settings
from .geocoding import AmapReverseGeocoder, cluster_photos_by_location, haversine_meters
from .image_styles import get_image_style_spec
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
from .models import AlbumManifest, ImageAnalysis, JobStatus, PhotoLocation, StoryPlan
from .rendering import (
    create_share_zip,
    export_share_images,
    render_album,
    update_manifest_exports,
)
from .selection import select_story_set


logger = logging.getLogger(__name__)


class PipelinePaused(Exception):
    pass


async def run_pipeline(job: Job, manager: JobManager, settings: Settings) -> None:
    output_dir: Path | None = None
    style_spec = get_image_style_spec(job.album_input.image_style)
    logger.info(
        "pipeline_started job_id=%s photo_count=%d target_count=%d image_style=%s",
        job.snapshot.id,
        len(job.uploads),
        job.album_input.target_count,
        job.album_input.image_style.value,
    )
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
            pause_requested=False,
            total_items=len(job.uploads),
        )

        photos = _restore_media_photos(job) or _build_media_photos(job)
        for index, photo in enumerate(photos):
            _check_pause(job)
            if not _metadata_ready(photo):
                await asyncio.to_thread(inspect_photo, photo)
                await asyncio.to_thread(
                    create_variants,
                    photo,
                    job.workspace / "analysis",
                    job.workspace / "generation",
                )
                _checkpoint_photos(job, manager, photos)
            _check_pause(job)
            manager.update(
                job,
                completed_items=index + 1,
                progress=5 + 12 * (index + 1) / len(photos),
                message=f"正在整理第 {index + 1}/{len(photos)} 张照片",
            )

        gps_photo_count = sum(_has_gps(photo) for photo in photos)
        manager.update(
            job,
            gps_photo_count=gps_photo_count,
            resolved_location_count=sum(photo.location is not None for photo in photos),
            missing_gps_count=len(photos) - gps_photo_count,
        )

        _check_pause(job)
        manager.update(job, stage="deduplicate", progress=18, message="正在过滤近似照片")
        photo_by_id = {photo.id: photo for photo in photos}
        candidate_ids = job.pipeline_state.get("candidate_ids")
        candidates = [photo_by_id[photo_id] for photo_id in candidate_ids or [] if photo_id in photo_by_id]
        if not candidates:
            candidates = await asyncio.to_thread(near_duplicate_representatives, photos)
            _checkpoint(job, manager, candidate_ids=[photo.id for photo in candidates])

        if int(job.pipeline_state.get("location_enrichment_version", 0)) < 2:
            for photo in candidates:
                photo.analysis = None
            job.pipeline_state.pop("selected_ids", None)
            job.pipeline_state.pop("story", None)
            _checkpoint_photos(job, manager, photos)

        await _resolve_photo_locations(job, manager, settings, photos, candidates)
        _checkpoint(job, manager, location_enrichment_version=2)

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
            _check_pause(job)
            batch = pending_analysis[start : start + settings.vision_batch_size]
            analyses = await asyncio.to_thread(service.analyze_photos, batch)
            for photo, analysis in zip(batch, analyses, strict=False):
                photo.analysis = analysis
            _checkpoint_photos(job, manager, photos)
            _check_pause(job)
            completed = sum(photo.analysis is not None for photo in candidates)
            manager.update(
                job,
                completed_items=completed,
                progress=22 + 18 * completed / len(candidates),
                message=f"已理解 {completed}/{len(candidates)} 张候选照片",
            )

        _check_pause(job)
        manager.update(job, stage="selection", progress=41, message="正在组合最有故事价值的照片")
        selected_ids = job.pipeline_state.get("selected_ids")
        selected = [photo_by_id[photo_id] for photo_id in selected_ids or [] if photo_id in photo_by_id]
        if not selected:
            selected = select_story_set(candidates, job.album_input.target_count)
            _checkpoint(job, manager, selected_ids=[photo.id for photo in selected])
        if not selected:
            raise RuntimeError("没有找到可用于生成相册的照片")

        _check_pause(job)
        manager.update(job, stage="story", progress=46, message="正在编排旅行章节与旁白")
        story_data = (
            job.pipeline_state.get("story")
            if int(job.pipeline_state.get("story_content_version", 0)) >= 2
            else None
        )
        if story_data:
            story = StoryPlan.model_validate(story_data)
        else:
            context = job.album_input.model_dump(mode="json")
            story = await asyncio.to_thread(service.create_story, selected, context)
            _checkpoint(
                job,
                manager,
                story=story.model_dump(mode="json"),
                story_content_version=2,
            )
        chapter_by_photo: dict[str, str] = {}
        for chapter in story.chapters:
            for photo_id in chapter.photo_ids:
                chapter_by_photo[photo_id] = chapter.id
        for photo in selected:
            photo.caption = story.captions.get(photo.id, photo.analysis.caption_seed)
            photo.poem_line = story.poem_lines.get(photo.id, "")
            photo.chapter_id = chapter_by_photo.get(photo.id, story.chapters[-1].id)
        _checkpoint_photos(job, manager, photos)

        _check_pause(job)
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
            message=(
                f"正在并发生成 {len(selected)} 张{style_spec.generation_noun}，"
                f"并发度 {settings.image_generation_concurrency}"
            ),
        )
        generation_batch_attempted = False
        generation_errors = dict(job.pipeline_state.get("generation_errors", {}))
        generation_concurrency = settings.image_generation_concurrency

        async def wait_for_generation_batch(
            waiting_message: str,
            allow_stop: bool,
        ) -> bool:
            nonlocal generation_batch_attempted
            if generation_batch_attempted and settings.image_generation_interval_seconds > 0:
                manager.update(job, message=waiting_message)
                if not await _wait_for_job_delay(
                    job,
                    settings.image_generation_interval_seconds,
                    allow_stop,
                ):
                    return False
            _check_pause(job)
            if allow_stop and job.stop_retry_event.is_set():
                return False
            generation_batch_attempted = True
            return True

        async def generate_photo(photo: MediaPhoto) -> None:
            raw_path = job.workspace / "generated" / f"{photo.id}.png"
            final_path = photos_dir / f"{photo.id}.jpg"
            temporary_path = photos_dir / f"{photo.id}.tmp.jpg"
            await asyncio.to_thread(
                service.generate_image,
                photo,
                photo.caption,
                raw_path,
                job.album_input.image_style,
            )
            await asyncio.to_thread(
                normalize_generated_page,
                raw_path,
                temporary_path,
                (style_spec.output_width, style_spec.output_height),
                style_spec.generated_fit,
                style_spec.canvas_color,
            )
            temporary_path.replace(final_path)
            photo.generated_path = final_path

        async def run_generation_batch(
            batch: list[MediaPhoto],
            pass_name: str,
            retry_round: int = 0,
        ) -> None:
            logger.info(
                "image_generation_batch_started job_id=%s pass=%s retry_round=%d "
                "batch_size=%d concurrency=%d",
                job.snapshot.id,
                pass_name,
                retry_round,
                len(batch),
                generation_concurrency,
            )

            async def run_one(photo: MediaPhoto) -> tuple[MediaPhoto, Exception | None]:
                try:
                    await generate_photo(photo)
                    return photo, None
                except Exception as exc:
                    logger.warning(
                        "image_generation_failed job_id=%s photo_id=%s pass=%s "
                        "retry_round=%d error_type=%s error=%s",
                        job.snapshot.id,
                        photo.id,
                        pass_name,
                        retry_round,
                        type(exc).__name__,
                        exc,
                        exc_info=True,
                    )
                    return photo, exc

            results = await asyncio.gather(*(run_one(photo) for photo in batch))
            success_count = 0
            for photo, error in results:
                if error is None:
                    generation_errors.pop(photo.id, None)
                    success_count += 1
                else:
                    generation_errors[photo.id] = str(error)
            logger.info(
                "image_generation_batch_completed job_id=%s pass=%s retry_round=%d "
                "batch_size=%d success_count=%d failure_count=%d",
                job.snapshot.id,
                pass_name,
                retry_round,
                len(batch),
                success_count,
                len(batch) - success_count,
            )

        if not job.pipeline_state.get("generation_first_pass_complete"):
            initial_queue = [photo for photo in selected if photo.generated_path is None]
            processed = 0
            for start in range(0, len(initial_queue), generation_concurrency):
                _check_pause(job)
                batch = initial_queue[start : start + generation_concurrency]
                batch_number = start // generation_concurrency + 1
                batch_count = max(
                    1,
                    (len(initial_queue) + generation_concurrency - 1)
                    // generation_concurrency,
                )
                await wait_for_generation_batch(
                    (
                        f"等待 {settings.image_generation_interval_seconds:g} 秒后启动"
                        f"首轮第 {batch_number}/{batch_count} 批"
                    ),
                    allow_stop=False,
                )
                await run_generation_batch(batch, "initial")
                processed += len(batch)
                _checkpoint_photos(job, manager, photos)
                _checkpoint(job, manager, generation_errors=generation_errors)
                completed = len(selected) - len(
                    [item for item in selected if item.generated_path is None]
                )
                manager.update(
                    job,
                    completed_items=processed,
                    progress=50 + 38 * processed / max(1, len(initial_queue)),
                    message=(
                        f"首轮已处理 {processed}/{len(initial_queue)} 张照片，"
                        f"成功 {completed} 张"
                    ),
                )
                _check_pause(job)
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
            processed = 0
            for start in range(0, len(current_round), generation_concurrency):
                _check_pause(job)
                if job.stop_retry_event.is_set():
                    break
                batch = current_round[start : start + generation_concurrency]
                batch_number = start // generation_concurrency + 1
                batch_count = max(
                    1,
                    (len(current_round) + generation_concurrency - 1)
                    // generation_concurrency,
                )
                should_continue = await wait_for_generation_batch(
                    (
                        f"等待 {settings.image_generation_interval_seconds:g} 秒后启动"
                        f"第 {retry_round} 轮重试的第 {batch_number}/{batch_count} 批"
                    ),
                    allow_stop=True,
                )
                if not should_continue:
                    break
                await run_generation_batch(batch, "retry", retry_round)
                processed += len(batch)
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
                    completed_items=processed,
                    failed_items=remaining,
                    progress=89,
                    can_stop_retries=True,
                    message=(
                        f"第 {retry_round} 轮已重试 {processed}/{len(current_round)} 张，"
                        f"还剩 {remaining} 张"
                    ),
                )
                _check_pause(job)
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
                _check_pause(job)
                final_path = photos_dir / f"{photo.id}.jpg"
                temporary_path = photos_dir / f"{photo.id}.tmp.jpg"
                fallback_source = photo.generation_path or photo.source_path
                await asyncio.to_thread(
                    normalize_generated_page,
                    fallback_source,
                    temporary_path,
                    (style_spec.output_width, style_spec.output_height),
                    "contain",
                    style_spec.canvas_color,
                )
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
                _check_pause(job)
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

        _check_pause(job)
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
        _check_pause(job)

        manager.update(job, stage="export", progress=94, message="正在导出朋友圈章节长图")
        export_error: str | None = None
        exports = list(job.pipeline_state.get("exports", []))
        if not exports or any(not (output_dir / item).exists() for item in exports):
            try:
                exports = await export_share_images(output_dir)
            except Exception as exc:
                logger.exception(
                    "share_image_export_failed job_id=%s output_dir=%s",
                    job.snapshot.id,
                    output_dir,
                )
                exports = []
                export_error = str(exc)
            _checkpoint(job, manager, exports=exports)
        _check_pause(job)
        await asyncio.to_thread(update_manifest_exports, output_dir, exports)
        await asyncio.to_thread(create_share_zip, output_dir)
        _check_pause(job)

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
        logger.info(
            "pipeline_finished job_id=%s status=%s selected_count=%d fallback_count=%d export_count=%d",
            job.snapshot.id,
            status,
            len(completed_photos),
            len(failures),
            len(exports),
        )
    except PipelinePaused:
        logger.info(
            "pipeline_paused job_id=%s stage=%s",
            job.snapshot.id,
            job.snapshot.stage,
        )
        manager.update(
            job,
            status=JobStatus.paused,
            message=f"任务已暂停，可从 {job.snapshot.stage} 阶段继续",
            pause_requested=False,
            can_stop_retries=False,
        )
    except Exception as exc:
        logger.exception(
            "pipeline_failed job_id=%s stage=%s",
            job.snapshot.id,
            job.snapshot.stage,
        )
        manager.update(
            job,
            status=JobStatus.failed,
            stage="failed",
            message="生成任务未完成",
            error=str(exc),
            can_stop_retries=False,
        )


def _check_pause(job: Job) -> None:
    if job.pause_event.is_set() or job.snapshot.pause_requested:
        raise PipelinePaused


async def _wait_for_job_delay(job: Job, seconds: float, allow_stop: bool) -> bool:
    delay = asyncio.create_task(asyncio.sleep(seconds))
    pause = asyncio.create_task(job.pause_event.wait())
    waiters = {delay, pause}
    stop = None
    if allow_stop:
        stop = asyncio.create_task(job.stop_retry_event.wait())
        waiters.add(stop)
    try:
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for waiter in waiters:
            if not waiter.done():
                waiter.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)
    _check_pause(job)
    return not (stop and stop.done() and job.stop_retry_event.is_set())


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
                latitude=(float(row["latitude"]) if row.get("latitude") is not None else None),
                longitude=(
                    float(row["longitude"]) if row.get("longitude") is not None else None
                ),
                gps_source=row.get("gps_source", ""),
                gps_inspected=bool(row.get("gps_inspected", False)),
                location=(
                    PhotoLocation.model_validate(row["location"])
                    if row.get("location")
                    else None
                ),
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
                poem_line=row.get("poem_line", ""),
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
        "latitude": photo.latitude,
        "longitude": photo.longitude,
        "gps_source": photo.gps_source,
        "gps_inspected": photo.gps_inspected,
        "location": photo.location.model_dump(mode="json") if photo.location else None,
        "analysis_path": str(photo.analysis_path) if photo.analysis_path else None,
        "generation_path": str(photo.generation_path) if photo.generation_path else None,
        "phash": photo.phash,
        "local_quality": photo.local_quality,
        "analysis": analysis.model_dump(mode="json") if isinstance(analysis, ImageAnalysis) else None,
        "generated_path": str(photo.generated_path) if photo.generated_path else None,
        "caption": photo.caption,
        "poem_line": photo.poem_line,
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
        photo.gps_inspected
        and photo.phash
        and photo.analysis_path
        and photo.analysis_path.exists()
        and photo.generation_path
        and photo.generation_path.exists()
    )


def _has_gps(photo: MediaPhoto) -> bool:
    return photo.latitude is not None and photo.longitude is not None


async def _resolve_photo_locations(
    job: Job,
    manager: JobManager,
    settings: Settings,
    photos: list[MediaPhoto],
    candidates: list[MediaPhoto],
) -> None:
    gps_candidates = [photo for photo in candidates if _has_gps(photo)]
    clusters = cluster_photos_by_location(
        gps_candidates,
        settings.location_cluster_radius_meters,
    )
    resolved_count = sum(photo.location is not None for photo in photos)
    manager.update(
        job,
        stage="location",
        progress=19,
        completed_items=sum(any(photo.location for photo in cluster) for cluster in clusters),
        total_items=len(clusters),
        resolved_location_count=resolved_count,
        message=(
            f"检测到 {job.snapshot.gps_photo_count} 张照片含 GPS，"
            f"正在解析 {len(clusters)} 个拍摄地点"
        ),
    )
    if not clusters:
        manager.update(job, progress=21, message="照片中未检测到可用 GPS，已继续整理画面")
        return

    if not settings.location_configured:
        manager.update(
            job,
            progress=21,
            message="检测到照片 GPS，但未配置高德 Web 服务 Key，已跳过地址解析",
        )
        return

    geocoder = AmapReverseGeocoder(settings.amap_api_key)
    errors = dict(job.pipeline_state.get("location_errors", {}))
    location_context_changed = False
    candidate_ids = {photo.id for photo in candidates}
    for index, cluster in enumerate(clusters):
        _check_pause(job)
        location = next((photo.location for photo in cluster if photo.location), None)
        representative = cluster[0]
        error_key = f"{representative.latitude:.5f},{representative.longitude:.5f}"
        reverse_error_key = f"{error_key}:reverse"
        nearby_error_key = f"{error_key}:nearby"
        cluster_context_changed = location is None
        if location is None:
            try:
                location = await asyncio.to_thread(
                    geocoder.reverse,
                    representative.latitude,
                    representative.longitude,
                )
                errors.pop(reverse_error_key, None)
                errors.pop(error_key, None)
            except Exception as exc:
                logger.warning(
                    "location_lookup_failed job_id=%s cluster=%d/%d "
                    "error_type=%s error=%s",
                    job.snapshot.id,
                    index + 1,
                    len(clusters),
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
                errors[reverse_error_key] = str(exc)

        if location is not None and not location.nearby_searched:
            try:
                landmark = await asyncio.to_thread(
                    geocoder.nearby,
                    representative.latitude,
                    representative.longitude,
                    location,
                )
                location.nearby_landmark = landmark
                location.nearby_searched = True
                cluster_context_changed = cluster_context_changed or landmark is not None
                errors.pop(nearby_error_key, None)
            except Exception as exc:
                logger.warning(
                    "nearby_landmark_lookup_failed job_id=%s cluster=%d/%d "
                    "error_type=%s error=%s",
                    job.snapshot.id,
                    index + 1,
                    len(clusters),
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
                errors[nearby_error_key] = str(exc)

        if location is not None:
            for photo in photos:
                if (
                    haversine_meters(photo, representative)
                    <= settings.location_cluster_radius_meters
                ):
                    if photo.location != location:
                        photo.location = location
                        location_context_changed = True
                    if cluster_context_changed and photo.id in candidate_ids:
                        photo.analysis = None
                        location_context_changed = True

        resolved_count = sum(photo.location is not None for photo in photos)
        _checkpoint_photos(job, manager, photos)
        _checkpoint(job, manager, location_errors=errors)
        _check_pause(job)
        manager.update(
            job,
            progress=19 + 2 * (index + 1) / len(clusters),
            completed_items=index + 1,
            resolved_location_count=resolved_count,
            message=(
                f"地点解析 {index + 1}/{len(clusters)}，"
                f"已为 {resolved_count} 张照片补充拍摄地与附近地标"
            ),
        )

    if location_context_changed:
        job.pipeline_state.pop("selected_ids", None)
        job.pipeline_state.pop("story", None)
        _checkpoint_photos(job, manager, photos)
    if errors:
        manager.update(
            job,
            message=f"已解析 {resolved_count} 张照片的位置，{len(errors)} 次地点查询暂时失败",
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

    route_locations: list[str] = []
    for photo in photos:
        display_name = photo.location.display_name if photo.location else ""
        if display_name and (not route_locations or route_locations[-1] != display_name):
            route_locations.append(display_name)
    visible_route = route_locations[:5]
    route_summary = " / ".join(visible_route)
    if len(route_locations) > len(visible_route):
        route_summary += f" / 等 {len(route_locations)} 处"

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
                "display_location": photo.location.display_name if photo.location else "",
                "capture_location": photo.location.display_name if photo.location else "",
                "nearby_landmark": (
                    photo.location.nearby_landmark.name
                    if photo.location and photo.location.nearby_landmark
                    else ""
                ),
                "caption": photo.poem_line or photo.caption,
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
        route_locations=route_locations,
        route_summary=route_summary,
        image_style=job.album_input.image_style,
        image_width=get_image_style_spec(job.album_input.image_style).output_width,
        image_height=get_image_style_spec(job.album_input.image_style).output_height,
        chapters=chapters,
        photos=photo_rows,
    )
