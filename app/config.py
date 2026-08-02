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
    frontend_dist: Path = ROOT_DIR / "frontend" / "dist"
    photo_revival_skill: Path = ROOT_DIR / "third_party" / "photo-revival" / "SKILL.md"

    openai_base_url: str = "https://www.hellotranfer.top/"
    openai_api_key: str = ""
    openai_text_model: str = ""
    openai_image_model: str = "gpt-image-1"
    image_generation_concurrency: int = 2
    vision_batch_size: int = 4

    @property
    def api_configured(self) -> bool:
        return bool(self.openai_api_key and self.openai_text_model and self.openai_image_model)

    @property
    def openai_sdk_base_url(self) -> str:
        base = self.openai_base_url.rstrip("/")
        if base.lower().endswith("/v1"):
            return f"{base}/"
        return f"{base}/v1/"

    def ensure_directories(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.job_dir.mkdir(parents=True, exist_ok=True)


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
        image_generation_concurrency=max(
            1, int(os.getenv("IMAGE_GENERATION_CONCURRENCY", "2"))
        ),
        vision_batch_size=max(1, int(os.getenv("VISION_BATCH_SIZE", "4"))),
    )
    settings.ensure_directories()
    return settings
