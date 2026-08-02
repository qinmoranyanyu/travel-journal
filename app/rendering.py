from __future__ import annotations

import json
import os
import shutil
import zipfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import ROOT_DIR
from .models import AlbumManifest


TEMPLATE_DIR = ROOT_DIR / "app" / "templates"
STATIC_DIR = ROOT_DIR / "app" / "static"


def render_album(manifest: AlbumManifest, output_dir: Path) -> None:
    environment = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "xml"]),
    )
    styles_dir = output_dir / "assets" / "styles"
    styles_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(STATIC_DIR / "album.css", styles_dir / "album.css")
    shutil.copy2(STATIC_DIR / "share.css", styles_dir / "share.css")

    data = manifest.model_dump(mode="json")
    chapter_photos = _chapter_photos(data)
    index_html = environment.get_template("album.html").render(
        album=data,
        chapters=chapter_photos,
    )
    share_html = environment.get_template("share.html").render(
        album=data,
        chapters=chapter_photos,
    )
    (output_dir / "index.html").write_text(index_html, encoding="utf-8")
    (output_dir / "share.html").write_text(share_html, encoding="utf-8")
    (output_dir / "album.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def export_share_images(output_dir: Path) -> list[str]:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("缺少 Playwright，无法导出朋友圈长图") from exc

    exports_dir = output_dir / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    exported: list[str] = []
    async with async_playwright() as playwright:
        browser_path = _browser_executable()
        browser = await playwright.chromium.launch(
            executable_path=str(browser_path) if browser_path else None
        )
        page = await browser.new_page(
            viewport={"width": 1120, "height": 900},
            device_scale_factor=1,
        )
        await page.goto((output_dir / "share.html").as_uri(), wait_until="networkidle")
        sheets = page.locator(".share-sheet")
        count = await sheets.count()
        for index in range(min(count, 9)):
            filename = f"{index + 1:02d}-{'cover' if index == 0 else 'chapter'}.jpg"
            await sheets.nth(index).screenshot(
                path=str(exports_dir / filename),
                type="jpeg",
                quality=92,
            )
            exported.append(f"exports/{filename}")
        await browser.close()
    return exported


def _browser_executable() -> Path | None:
    configured = os.getenv("PLAYWRIGHT_BROWSER_PATH")
    candidates = [
        Path(configured) if configured else None,
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    ]
    return next((path for path in candidates if path and path.exists()), None)


def create_share_zip(output_dir: Path) -> Path:
    zip_path = output_dir / f"{output_dir.name}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in output_dir.rglob("*"):
            if not path.is_file() or path == zip_path:
                continue
            relative = path.relative_to(output_dir)
            if relative.parts and relative.parts[0] == "sources":
                continue
            if relative.name == "share.html":
                continue
            archive.write(path, relative.as_posix())
    return zip_path


def update_manifest_exports(output_dir: Path, exports: list[str]) -> None:
    manifest_path = output_dir / "album.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["exports"] = exports
    manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _chapter_photos(data: dict) -> list[dict]:
    photos = {photo["id"]: photo for photo in data["photos"]}
    chapters = []
    for chapter in data["chapters"]:
        chapters.append(
            {
                **chapter,
                "photos": [photos[photo_id] for photo_id in chapter["photo_ids"] if photo_id in photos],
            }
        )
    return chapters
