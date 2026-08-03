from __future__ import annotations

import base64
import binascii
import json
import logging
import re
from pathlib import Path
from typing import Any

from openai import BadRequestError, OpenAI

from .config import Settings
from .media import MediaPhoto
from .models import ImageAnalysis, StoryChapter, StoryPlan


logger = logging.getLogger(__name__)


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
                    "具体事件或未提供的地点。capture_location 来自照片 EXIF GPS 的地址解析，"
                    "属于可信元数据，可用于描述地点氛围，但不要补充元数据中没有的地名。"
                    "category 使用人物、风景、建筑、食物、交通、细节、活动、其他之一。"
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
            metadata: dict[str, Any] = {"photo_id": photo.id}
            if photo.location:
                metadata["capture_location"] = photo.location.model_dump(
                    mode="json",
                    exclude={"provider", "confidence", "location_key"},
                )
            content.append(
                {"type": "text", "text": json.dumps(metadata, ensure_ascii=False)}
            )
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
                logger.warning(
                    "image_analysis_item_invalid photo_id=%s",
                    item.get("photo_id") if isinstance(item, dict) else "unknown",
                    exc_info=True,
                )
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
                    "capture_location": (
                        photo.location.model_dump(
                            mode="json",
                            exclude={"provider", "confidence", "location_key"},
                        )
                        if photo.location
                        else None
                    ),
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
                "capture_location 来自照片 GPS，可用于章节划分、标题和旁白；trip.location 是用户确认的旅行级事实，二者冲突时优先采用 trip.location，并避免断言矛盾地点。",
                "地点措辞应自然克制，优先使用城市、区域或景点名称，不在旁白中堆砌完整门牌地址。",
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
            logger.warning(
                "story_plan_invalid selected_count=%d fallback=local",
                len(photos),
                exc_info=True,
            )
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
        location_note = photo.location.display_name if photo.location else ""
        prompt = (
            "Follow the Photo Revival skill below exactly.\n\n"
            f"{self.photo_revival_rules}\n\n"
            "Photo-specific direction:\n"
            f"Preserve the recognizable subject and spatial relationship: "
            f"{analysis.description if analysis else photo.original_name}.\n"
            f"Memorable details to preserve: {details or 'the main subject and atmosphere'}.\n"
            f"Known capture location for visual context: {location_note or 'not available'}.\n"
            f"Tiny handwritten Chinese caption: {caption}\n"
            f"Tiny English field note/date: FIELD NOTE / {date_note}\n"
        )
        logger.info(
            "image_generation_stream_start photo_id=%s model=%s",
            photo.id,
            self.settings.openai_image_model,
        )
        with photo.generation_path.open("rb") as image_file:
            stream = self.client.images.edit(
                model=self.settings.openai_image_model,
                image=image_file,
                prompt=prompt,
                size="1024x1536",
                stream=True,
            )

            image_bytes: bytes | None = None
            with stream as events:
                for event in events:
                    event_type = getattr(event, "type", "")
                    if event_type == "image_edit.partial_image":
                        logger.debug(
                            "image_generation_stream_partial photo_id=%s index=%s",
                            photo.id,
                            getattr(event, "partial_image_index", "unknown"),
                        )
                        continue
                    if event_type != "image_edit.completed":
                        continue

                    encoded = getattr(event, "b64_json", "")
                    if not encoded:
                        raise RuntimeError("图片编辑完成事件未包含 b64_json")
                    try:
                        image_bytes = base64.b64decode(encoded, validate=True)
                    except (binascii.Error, ValueError) as exc:
                        raise RuntimeError("图片编辑完成事件包含无效的 base64 数据") from exc

        if image_bytes is None:
            logger.error(
                "image_generation_stream_incomplete photo_id=%s model=%s",
                photo.id,
                self.settings.openai_image_model,
            )
            raise RuntimeError("图片编辑流已结束，但未收到 image_edit.completed 事件")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(image_bytes)
        logger.info(
            "image_generation_stream_completed photo_id=%s bytes=%d",
            photo.id,
            len(image_bytes),
        )
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
            logger.warning(
                "structured_output_unsupported model=%s retry=plain_json",
                self.settings.openai_text_model,
                exc_info=True,
            )
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
