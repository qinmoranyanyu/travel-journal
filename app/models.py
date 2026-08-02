from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    partial = "partial"
    failed = "failed"
    interrupted = "interrupted"


TERMINAL_STATUSES = {
    JobStatus.completed,
    JobStatus.partial,
    JobStatus.failed,
    JobStatus.interrupted,
}


class AlbumInput(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    location: str = Field(default="", max_length=120)
    companions: str = Field(default="", max_length=120)
    memory: str = Field(default="", max_length=500)
    target_count: int = Field(ge=1)


class JobSnapshot(BaseModel):
    id: str
    status: JobStatus = JobStatus.queued
    stage: str = "queued"
    progress: float = 0
    message: str = "等待开始"
    completed_items: int = 0
    total_items: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    error: str | None = None
    output_url: str | None = None
    share_url: str | None = None
    zip_url: str | None = None
    export_urls: list[str] = Field(default_factory=list)


class AlbumSummary(BaseModel):
    id: str
    title: str
    location: str = ""
    photo_count: int
    created_at: datetime
    cover_url: str | None = None
    output_url: str
    share_url: str
    zip_url: str | None = None
    export_urls: list[str] = Field(default_factory=list)


class ImageAnalysis(BaseModel):
    photo_id: str
    description: str
    category: str = "其他"
    story_value: float = Field(default=0.5, ge=0, le=1)
    technical_quality: float = Field(default=0.5, ge=0, le=1)
    memorable_details: list[str] = Field(default_factory=list)
    caption_seed: str = ""


class StoryChapter(BaseModel):
    id: str
    title: str
    intro: str
    photo_ids: list[str]


class StoryPlan(BaseModel):
    cover_subtitle: str = ""
    chapters: list[StoryChapter]
    captions: dict[str, str] = Field(default_factory=dict)
    closing: str = ""


class AlbumManifest(BaseModel):
    schema_version: int = 1
    id: str
    title: str
    location: str = ""
    companions: str = ""
    memory: str = ""
    date_range: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    cover_subtitle: str = ""
    closing: str = ""
    chapters: list[dict[str, Any]]
    photos: list[dict[str, Any]]
    exports: list[str] = Field(default_factory=list)
