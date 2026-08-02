"""Test image generation through an OpenAI-compatible proxy."""

import base64
import os
from pathlib import Path

# TLS key logging is not needed here and may point to a protected global path.
os.environ.pop("SSLKEYLOGFILE", None)

from openai import OpenAI


BASE_URL = os.getenv("OPENAI_BASE_URL", "https://www.hellotranfer.top/")
MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")
OUTPUT_FILE = Path(os.getenv("OPENAI_IMAGE_OUTPUT", "generated_image.png"))
PROMPT = os.getenv(
    "OPENAI_IMAGE_PROMPT",
    "A quiet mountain lake at sunrise, realistic photography, high detail",
)


def sdk_base_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    return f"{base}/" if base.lower().endswith("/v1") else f"{base}/v1/"


def main() -> None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit(
            "请先设置 OPENAI_API_KEY 环境变量，例如："
            ' $env:OPENAI_API_KEY="你的key"'
        )

    client = OpenAI(api_key=api_key, base_url=sdk_base_url(BASE_URL))
    print(f"正在请求图片生成: model={MODEL}, base_url={BASE_URL}")

    result = client.images.generate(
        model=MODEL,
        prompt=PROMPT,
        size="1024x1024",
    )

    if not result.data:
        raise RuntimeError("接口返回成功，但没有图片数据")

    image = result.data[0]
    if getattr(image, "b64_json", None):
        image_bytes = base64.b64decode(image.b64_json)
    elif getattr(image, "url", None):
        # Some OpenAI-compatible proxies return a temporary image URL.
        import urllib.request

        with urllib.request.urlopen(image.url) as response:
            image_bytes = response.read()
    else:
        raise RuntimeError(f"返回数据中没有 b64_json 或 url: {image!r}")

    OUTPUT_FILE.write_bytes(image_bytes)
    print(f"生成成功，图片已保存到: {OUTPUT_FILE.resolve()}")


if __name__ == "__main__":
    main()
