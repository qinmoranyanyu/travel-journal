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
    paused = "paused"
    completed = "completed"
    partial = "partial"
    failed = "failed"
    interrupted = "interrupted"


class ImageStyle(str, Enum):
    photo_revival = "photo-revival-v1"
    scenes_gathered = "scenes-gathered-v1-3"
    minimal_zine = "minimal-zine-v0-1"


TERMINAL_STATUSES = {
    JobStatus.paused,
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
    image_style: ImageStyle = ImageStyle.photo_revival


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
    can_stop_retries: bool = False
    retry_stop_requested: bool = False
    retry_round: int = 0
    failed_items: int = 0
    pause_requested: bool = False
    gps_photo_count: int = 0
    resolved_location_count: int = 0
    missing_gps_count: int = 0


class JobUpload(BaseModel):
    id: str
    original_name: str
    order: int
    modified_at: datetime | None = None
    preview_url: str


class JobDetail(BaseModel):
    snapshot: JobSnapshot
    album_input: AlbumInput
    uploads: list[JobUpload] = Field(default_factory=list)


class JobListItem(BaseModel):
    snapshot: JobSnapshot
    album_input: AlbumInput
    upload_count: int
    preview_url: str | None = None


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
    image_style: ImageStyle = ImageStyle.photo_revival


class ImageAnalysis(BaseModel):
    photo_id: str
    description: str
    category: str = "其他"
    story_value: float = Field(default=0.5, ge=0, le=1)
    technical_quality: float = Field(default=0.5, ge=0, le=1)
    memorable_details: list[str] = Field(default_factory=list)
    caption_seed: str = ""


class NearbyLandmark(BaseModel):
    name: str
    distance_meters: float = Field(ge=0)
    category: str
    typecode: str = ""
    rating: float | None = Field(default=None, ge=0)
    provider: str = "amap"


class PhotoLocation(BaseModel):
    province: str = ""
    city: str = ""
    district: str = ""
    township: str = ""
    poi_name: str = ""
    formatted_address: str = ""
    display_name: str = ""
    location_key: str = ""
    provider: str = "amap"
    confidence: str = "address"
    nearby_landmark: NearbyLandmark | None = None
    nearby_searched: bool = False


class StoryChapter(BaseModel):
    id: str
    title: str
    intro: str
    photo_ids: list[str]


class StoryPlan(BaseModel):
    cover_subtitle: str = ""
    chapters: list[StoryChapter]
    captions: dict[str, str] = Field(default_factory=dict)
    poem_lines: dict[str, str] = Field(default_factory=dict)
    closing: str = ""


class AlbumManifest(BaseModel):
    schema_version: int = 4
    id: str
    title: str
    location: str = ""
    companions: str = ""
    memory: str = ""
    date_range: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    cover_subtitle: str = ""
    closing: str = ""
    route_locations: list[str] = Field(default_factory=list)
    route_summary: str = ""
    image_style: ImageStyle = ImageStyle.photo_revival
    image_width: int = 1024
    image_height: int = 1365
    chapters: list[dict[str, Any]]
    photos: list[dict[str, Any]]
    exports: list[str] = Field(default_factory=list)
