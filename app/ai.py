from __future__ import annotations

import base64
import json
import re
import urllib.request
from pathlib import Path
from typing import Any

from openai import BadRequestError, OpenAI

from .config import Settings
from .media import MediaPhoto
from .models import ImageAnalysis, StoryChapter, StoryPlan


class OpenAIService:
    def __init__(self, settings: Settings) -> None:
        if not settings.api_configured:
            raise RuntimeError("OpenAI 配置不完整，请检查 .env")
        self.settings = settings
        self.client = OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_sdk_base_url,
            max_retries=2,
            timeout=180,
        )
        self.photo_revival_rules = settings.photo_revival_skill.read_text(encoding="utf-8")

    def analyze_photos(self, photos: list[MediaPhoto]) -> list[ImageAnalysis]:
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "分析以下旅行照片。为每张照片返回客观、简洁的数据，不猜测人物姓名、对话、"
                    "具体事件或不可见地点。category 使用人物、风景、建筑、食物、交通、细节、活动、其他之一。"
                    "story_value 和 technical_quality 是 0 到 1 的小数。输出 JSON："
                    '{"photos":[{"photo_id":"...","description":"...","category":"...",'
                    '"story_value":0.5,"technical_quality":0.5,"memorable_details":["..."],'
                    '"caption_seed":"10-30字克制中文旁白"}]}。'
                ),
            }
        ]
        for photo in photos:
            if photo.analysis_path is None:
                continue
            content.append({"type": "text", "text": f"photo_id: {photo.id}"})
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _data_url(photo.analysis_path), "detail": "low"},
                }
            )

        payload = self._chat_json(content)
        raw_items = payload.get("photos", []) if isinstance(payload, dict) else []
        by_id: dict[str, ImageAnalysis] = {}
        for item in raw_items:
            try:
                parsed = ImageAnalysis.model_validate(item)
                by_id[parsed.photo_id] = parsed
            except (ValueError, TypeError):
                continue

        results: list[ImageAnalysis] = []
        for photo in photos:
            results.append(
                by_id.get(
                    photo.id,
                    ImageAnalysis(
                        photo_id=photo.id,
                        description="旅行中的一帧记录",
                        technical_quality=photo.local_quality,
                        story_value=0.5,
                        caption_seed="这一刻被轻轻留下",
                    ),
                )
            )
        return results

    def create_story(self, photos: list[MediaPhoto], context: dict[str, Any]) -> StoryPlan:
        photo_rows = []
        for photo in photos:
            analysis = photo.analysis
            photo_rows.append(
                {
                    "photo_id": photo.id,
                    "time": photo.capture_time.isoformat() if photo.capture_time else None,
                    "time_confidence": photo.time_confidence,
                    "description": analysis.description if analysis else "",
                    "category": analysis.category if analysis else "其他",
                    "details": (analysis.memorable_details if analysis else [])[:3],
                    "caption_seed": analysis.caption_seed if analysis else "",
                }
            )

        prompt = {
            "role": "你是一位克制的中文旅行手记编辑。",
            "requirements": [
                "trip 中用户填写的旅行名称、地点、同行关系和回忆是可信事实，必须优先用于标题与文案，不得称其为线索缺失。",
                "按照片当前时间顺序分成自然章节，不遗漏或重复 photo_id。",
                "章节数量随内容决定，通常 1-6 个。",
                "章节引言 40-80 字，每张旁白 10-30 字，结尾 80-150 字。",
                "联系相邻照片形成叙事，但不编造姓名、对话、具体事件或无依据地点。",
                "避免时光定格、岁月静好、奔赴山海等套话。",
                "时间可信度为 estimated 时，不写清晨、傍晚、第二天等具体推断。",
            ],
            "trip": context,
            "photos": photo_rows,
            "output": {
                "cover_subtitle": "一句简短副标题",
                "chapters": [
                    {
                        "id": "chapter-1",
                        "title": "短标题",
                        "intro": "章节引言",
                        "photo_ids": ["photo_id"],
                    }
                ],
                "captions": {"photo_id": "旁白"},
                "closing": "旅程回望",
            },
        }
        payload = self._chat_json([{"type": "text", "text": json.dumps(prompt, ensure_ascii=False)}])
        try:
            plan = StoryPlan.model_validate(payload)
        except ValueError:
            plan = _fallback_story(photos, context)
        return _repair_story(plan, photos, context)

    def generate_revival(
        self,
        photo: MediaPhoto,
        caption: str,
        output_path: Path,
    ) -> Path:
        if photo.generation_path is None:
            raise RuntimeError(f"缺少图片生成副本: {photo.original_name}")
        analysis = photo.analysis
        details = "、".join((analysis.memorable_details if analysis else [])[:3])
        date_note = photo.capture_time.strftime("%Y.%m.%d") if photo.capture_time else "FIELD NOTE"
        prompt = (
            "Follow the Photo Revival skill below exactly.\n\n"
            f"{self.photo_revival_rules}\n\n"
            "Photo-specific direction:\n"
            f"Preserve the recognizable subject and spatial relationship: "
            f"{analysis.description if analysis else photo.original_name}.\n"
            f"Memorable details to preserve: {details or 'the main subject and atmosphere'}.\n"
            f"Tiny handwritten Chinese caption: {caption}\n"
            f"Tiny English field note/date: FIELD NOTE / {date_note}\n"
        )
        with photo.generation_path.open("rb") as image_file:
            result = self.client.images.edit(
                model=self.settings.openai_image_model,
                image=image_file,
                prompt=prompt,
                size="1024x1536",
            )
        if not result.data:
            raise RuntimeError("图片接口没有返回数据")
        item = result.data[0]
        if getattr(item, "b64_json", None):
            image_bytes = base64.b64decode(item.b64_json)
        elif getattr(item, "url", None):
            with urllib.request.urlopen(item.url, timeout=180) as response:
                image_bytes = response.read()
        else:
            raise RuntimeError("图片接口未返回 b64_json 或 url")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(image_bytes)
        return output_path

    def _chat_json(self, content: list[dict[str, Any]]) -> dict[str, Any]:
        kwargs = {
            "model": self.settings.openai_text_model,
            "messages": [{"role": "user", "content": content}],
        }
        try:
            response = self.client.chat.completions.create(
                **kwargs,
                response_format={"type": "json_object"},
            )
        except BadRequestError:
            response = self.client.chat.completions.create(**kwargs)
        text = response.choices[0].message.content or "{}"
        return _parse_json_object(text)


def _data_url(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError("模型没有返回有效 JSON")
        value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise RuntimeError("模型 JSON 顶层必须是对象")
    return value


def _fallback_story(photos: list[MediaPhoto], context: dict[str, Any]) -> StoryPlan:
    ids = [photo.id for photo in photos]
    captions = {
        photo.id: (photo.analysis.caption_seed if photo.analysis else "这一刻被轻轻留下")
        for photo in photos
    }
    return StoryPlan(
        cover_subtitle=context.get("memory") or context.get("location") or "一册旅行手记",
        chapters=[
            StoryChapter(
                id="chapter-1",
                title="沿途所见",
                intro="照片依照它们被拍下的次序展开，风景、人物与细小片段共同构成这次旅行。",
                photo_ids=ids,
            )
        ],
        captions=captions,
        closing="旅程已经结束，照片仍保留着沿途的光线、距离和当时没有说完的话。",
    )


def _repair_story(
    plan: StoryPlan,
    photos: list[MediaPhoto],
    context: dict[str, Any],
) -> StoryPlan:
    valid_ids = [photo.id for photo in photos]
    remaining = set(valid_ids)
    repaired: list[StoryChapter] = []
    for index, chapter in enumerate(plan.chapters):
        ids = [photo_id for photo_id in chapter.photo_ids if photo_id in remaining]
        for photo_id in ids:
            remaining.remove(photo_id)
        if ids:
            repaired.append(
                StoryChapter(
                    id=f"chapter-{index + 1}",
                    title=chapter.title,
                    intro=chapter.intro,
                    photo_ids=ids,
                )
            )
    if remaining:
        missing = [photo_id for photo_id in valid_ids if photo_id in remaining]
        if repaired:
            repaired[-1].photo_ids.extend(missing)
        else:
            return _fallback_story(photos, context)
    plan.chapters = repaired
    for photo in photos:
        plan.captions.setdefault(
            photo.id,
            photo.analysis.caption_seed if photo.analysis else "这一刻被轻轻留下",
        )
    return plan
