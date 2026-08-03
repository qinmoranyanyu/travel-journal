from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel


ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseModel):
    root_dir: Path = ROOT_DIR
    output_dir: Path = ROOT_DIR / "outputs"
    job_dir: Path = ROOT_DIR / ".jobs"
    log_dir: Path = ROOT_DIR / "logs"
    frontend_dist: Path = ROOT_DIR / "frontend" / "dist"
    photo_revival_skill: Path = ROOT_DIR / "third_party" / "photo-revival" / "SKILL.md"

    openai_base_url: str = "https://www.hellotranfer.top/"
    openai_api_key: str = ""
    openai_text_model: str = ""
    openai_image_model: str = "gpt-image-1"
    amap_api_key: str = ""
    image_generation_interval_seconds: float = 10.0
    vision_batch_size: int = 4
    location_cluster_radius_meters: float = 200.0
    log_level: str = "INFO"

    @property
    def api_configured(self) -> bool:
        return bool(self.openai_api_key and self.openai_text_model and self.openai_image_model)

    @property
    def openai_sdk_base_url(self) -> str:
        base = self.openai_base_url.rstrip("/")
        if base.lower().endswith("/v1"):
            return f"{base}/"
        return f"{base}/v1/"

    @property
    def location_configured(self) -> bool:
        return bool(self.amap_api_key)

    def ensure_directories(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _load_dotenv(ROOT_DIR / ".env")
    settings = Settings(
        openai_base_url=os.getenv("OPENAI_BASE_URL", "https://www.hellotranfer.top/"),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        openai_text_model=os.getenv("OPENAI_TEXT_MODEL", ""),
        openai_image_model=os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1"),
        amap_api_key=os.getenv("AMAP_API_KEY", ""),
        image_generation_interval_seconds=max(
            0.0, float(os.getenv("IMAGE_GENERATION_INTERVAL_SECONDS", "10"))
        ),
        vision_batch_size=max(1, int(os.getenv("VISION_BATCH_SIZE", "4"))),
        location_cluster_radius_meters=max(
            10.0, float(os.getenv("LOCATION_CLUSTER_RADIUS_METERS", "200"))
        ),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
    settings.ensure_directories()
    return settings
