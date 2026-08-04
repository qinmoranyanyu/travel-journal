from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from openai import BadRequestError, OpenAI

from .config import Settings
from .image_styles import (
    get_image_style_spec,
    load_image_style_rules,
    load_story_caption_rules,
    normalize_image_caption,
)
from .media import MediaPhoto
from .models import ImageAnalysis, ImageStyle, StoryChapter, StoryPlan


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
        self.image_style_rules = {
            style: load_image_style_rules(settings, style) for style in ImageStyle
        }
        self.story_caption_rules = {
            style: load_story_caption_rules(settings, style) for style in ImageStyle
        }

    def analyze_photos(self, photos: list[MediaPhoto]) -> list[ImageAnalysis]:
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "分析以下旅行照片。为每张照片返回客观、简洁的数据，不猜测人物姓名、对话、"
                    "具体事件或未提供的地点。capture_location 来自照片 EXIF GPS 的地址解析，"
                    "属于可信元数据，可用于描述拍摄地点。nearby_landmark 仅表示拍摄点约 3 公里"
                    "范围内的地标，可用“附近”“靠近”等措辞丰富语境，但严禁写成照片拍摄于、"
                    "到访了或能看见该地标；不要补充元数据中没有的地名。"
                    "category 使用人物、风景、建筑、食物、交通、细节、活动、其他之一。"
                    "story_value 和 technical_quality 是 0 到 1 的小数。输出 JSON："
                    '{"photos":[{"photo_id":"...","description":"...","category":"...",'
                    '"story_value":0.5,"technical_quality":0.5,"memorable_details":["..."],'
                    '"caption_seed":"4-30字克制中文旁白"}]}。'
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
                    exclude={
                        "provider",
                        "confidence",
                        "location_key",
                        "nearby_landmark",
                        "nearby_searched",
                    },
                )
                if photo.location.nearby_landmark:
                    metadata["nearby_landmark"] = photo.location.nearby_landmark.model_dump(
                        mode="json",
                        exclude={"provider"},
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
        image_style = ImageStyle(
            context.get("image_style", ImageStyle.photo_revival)
        )
        style_spec = get_image_style_spec(image_style)
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
                            exclude={
                                "provider",
                                "confidence",
                                "location_key",
                                "nearby_landmark",
                                "nearby_searched",
                            },
                        )
                        if photo.location
                        else None
                    ),
                    "nearby_landmark": (
                        photo.location.nearby_landmark.model_dump(
                            mode="json",
                            exclude={"provider"},
                        )
                        if photo.location and photo.location.nearby_landmark
                        else None
                    ),
                    "description": analysis.description if analysis else "",
                    "category": analysis.category if analysis else "其他",
                    "details": (analysis.memorable_details if analysis else [])[:3],
                    **(
                        {"caption_seed": analysis.caption_seed if analysis else ""}
                        if image_style == ImageStyle.photo_revival
                        else {}
                    ),
                }
            )

        upstream_caption_rules = self.story_caption_rules[image_style]
        prompt = {
            "role": "你是一位克制的中文旅行手记编辑。",
            "requirements": [
                "trip 中用户填写的旅行名称、地点、同行关系和回忆是可信事实，必须优先用于标题与文案，不得称其为线索缺失。",
                "按照片当前时间顺序分成自然章节，不遗漏或重复 photo_id。",
                "章节数量随内容决定，通常 1-6 个。",
                "章节引言 40-80 字，结尾 80-150 字。",
                "联系相邻照片形成叙事，但不编造姓名、对话、具体事件或无依据地点。",
                "capture_location 来自照片 GPS，可用于章节划分、标题和旁白；trip.location 是用户确认的旅行级事实，二者冲突时优先采用 trip.location，并避免断言矛盾地点。",
                "nearby_landmark 只是拍摄点约 3 公里内的地理参照。可写“附近”或“靠近”，但不得声称照片拍摄于、人物到访了或从照片中能看见该地标；没有叙事价值时可以不写。",
                "地点措辞应自然克制，优先使用城市、区域或景点名称，不在旁白中堆砌完整门牌地址。",
                "避免时光定格、岁月静好、奔赴山海等套话。",
                "时间可信度为 estimated 时，不写清晨、傍晚、第二天等具体推断。",
                "上述事实与叙事约束适用于 cover_subtitle、chapters、closing 和 poem_lines；captions 只遵循所选图片风格的文案规则。",
                style_spec.story_caption_requirement,
                "poem_lines 是 HTML 页面和分享长图中每张照片下方的诗行；必须覆盖每个 photo_id，且不得直接复制同一张的 captions。",
                "严格按照 photos 的当前顺序创作 poem_lines，所有诗行依次连起来必须是一首完整、连贯、符合整组照片意境的中文诗。现代诗、古体诗或其他诗体均可，但整首风格与语气必须统一。",
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
                "captions": {"photo_id": style_spec.story_caption_example},
                "poem_lines": {"photo_id": "按照片顺序组成完整诗的对应诗行"},
                "closing": "旅程回望",
            },
        }
        if upstream_caption_rules:
            prompt["upstream_image_text_rules"] = upstream_caption_rules
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
        return _repair_story(plan, photos, context, image_style)

    def generate_image(
        self,
        photo: MediaPhoto,
        caption: str,
        output_path: Path,
        image_style: ImageStyle = ImageStyle.photo_revival,
    ) -> Path:
        if photo.generation_path is None:
            raise RuntimeError(f"缺少图片生成副本: {photo.original_name}")
        image_style = ImageStyle(image_style)
        prompt = _build_image_prompt(
            photo,
            caption,
            image_style,
            self.image_style_rules[image_style],
        )
        logger.info(
            "image_generation_stream_start photo_id=%s style=%s model=%s",
            photo.id,
            image_style.value,
            self.settings.openai_image_model,
        )
        with photo.generation_path.open("rb") as image_file:
            stream = self.client.images.edit(
                model=self.settings.openai_image_model,
                image=image_file,
                prompt=prompt,
                size="1024x1536",
                stream=True,
                timeout=self.settings.image_generation_timeout_seconds,
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
            "image_generation_stream_completed photo_id=%s style=%s bytes=%d",
            photo.id,
            image_style.value,
            len(image_bytes),
        )
        return output_path

    def generate_revival(
        self,
        photo: MediaPhoto,
        caption: str,
        output_path: Path,
    ) -> Path:
        return self.generate_image(
            photo,
            caption,
            output_path,
            ImageStyle.photo_revival,
        )

    def _chat_json(self, content: list[dict[str, Any]]) -> dict[str, Any]:
        request_content = [
            {
                "type": "text",
                "text": "Return a JSON object. 请返回 JSON 对象。",
            },
            *content,
        ]
        kwargs = {
            "model": self.settings.openai_text_model,
            "messages": [{"role": "user", "content": request_content}],
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


def _build_image_prompt(
    photo: MediaPhoto,
    caption: str,
    image_style: ImageStyle,
    style_rules: str,
) -> str:
    analysis = photo.analysis
    description = analysis.description if analysis else photo.original_name
    details = "、".join((analysis.memorable_details if analysis else [])[:3])
    location_note = photo.location.display_name if photo.location else "not available"
    caption_block = f"<caption>\n{caption}\n</caption>"
    common = (
        "Photo-specific direction:\n"
        f"Source subject and spatial relationship: {description}.\n"
        f"Memorable source details: {details or 'the main subject and atmosphere'}.\n"
        f"Known capture location for semantic context: {location_note}. "
        "Do not independently add it as text unless it is already present in the supplied "
        "caption block.\n"
    )
    if image_style == ImageStyle.photo_revival:
        date_note = (
            photo.capture_time.strftime("%Y.%m.%d")
            if photo.capture_time
            else "FIELD NOTE"
        )
        direction = (
            f"{common}"
            f"Tiny handwritten Chinese caption, verbatim: {caption}\n"
            f"Tiny English field note/date: FIELD NOTE / {date_note}\n"
        )
    elif image_style == ImageStyle.scenes_gathered:
        direction = (
            "Create one vertical 3:5 paper poster. Keep the source scene recognizable and "
            "preserve a truthful photographic anchor, without promising pixel-identical "
            "reproduction. Build the larger illustration field, torn-paper handoff, and one "
            "source-derived high-chroma structure around that anchor. Keep all important "
            "content within the central 90% of the canvas for final 3:5 cropping.\n"
            f"{common}"
            "Use the supplied caption block as the complete readable text content. Render it "
            "verbatim, preserving every language, line break, punctuation mark, and separator; "
            "do not translate, rewrite, flatten, or append text. Apply the upstream micro-text "
            "hierarchy and material treatment.\n"
            f"{caption_block}\n"
        )
    else:
        variation = _minimal_zine_variation(photo.id)
        direction = (
            "Create one vertical 3:5 minimal zine poster. Recompose the source radically, but "
            "retain at least one clearly recognizable source-derived subject, silhouette, or "
            "spatial cue. Use one small visual cluster and 70%-90% aged-paper negative space. "
            "Keep all important content within the central 90% of the canvas for final 3:5 "
            "cropping.\n"
            f"{common}"
            f"Variation recipe for this page: {variation}.\n"
            "Use the supplied caption block as the complete readable text content. Render it "
            "verbatim, preserving every language, line break, punctuation mark, fragment, and "
            "metadata-like element; do not translate, rewrite, flatten, or append text. Apply "
            "the upstream typography mode freely to its hierarchy and placement.\n"
            f"{caption_block}\n"
        )
    return (
        f"Follow the {get_image_style_spec(image_style).skill_name} visual rules below. "
        "The rules were compiled from the pinned upstream skill to include only directions "
        "that become visible pixels; do not return explanations or prompt text.\n\n"
        f"{style_rules}\n\n{direction}"
    )


def _minimal_zine_variation(photo_id: str) -> str:
    layouts = (
        "lower-left floating cluster with open upper paper",
        "small upper-right block with loose text drift",
        "two adjacent fragments with a narrow gap",
        "single isolated central specimen",
        "irregular source-derived cutout off center",
    )
    anchors = (
        "torn-paper clipping",
        "flat source-derived silhouette",
        "old printed illustration",
        "small faded source fragment",
        "abstract texture window preserving one source cue",
    )
    textures = (
        "xerox softness",
        "risograph grain",
        "letterpress ink bleed",
        "halftone degradation",
        "scan noise and paper fibers",
    )
    colors = (
        "fully saturated cobalt-blue ink",
        "clean tomato-red printed shape",
        "vivid pear-green cut paper",
        "lemon-yellow dry-print field",
        "saturated magenta-pink silhouette",
    )
    digest = hashlib.sha256(photo_id.encode("utf-8")).digest()
    return ", ".join(
        (
            layouts[digest[0] % len(layouts)],
            anchors[digest[1] % len(anchors)],
            textures[digest[2] % len(textures)],
            colors[digest[3] % len(colors)],
        )
    )


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
    image_style = ImageStyle(
        context.get("image_style", ImageStyle.photo_revival)
    )
    ids = [photo.id for photo in photos]
    captions = {
        photo.id: _fallback_image_caption(photo, image_style)
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
        poem_lines=_fallback_poem_lines(photos),
        closing="旅程已经结束，照片仍保留着沿途的光线、距离和当时没有说完的话。",
    )


def _repair_story(
    plan: StoryPlan,
    photos: list[MediaPhoto],
    context: dict[str, Any],
    image_style: ImageStyle = ImageStyle.photo_revival,
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
    order = {photo_id: index for index, photo_id in enumerate(valid_ids)}
    for chapter in plan.chapters:
        chapter.photo_ids.sort(key=order.__getitem__)
    plan.chapters.sort(key=lambda chapter: min(order[photo_id] for photo_id in chapter.photo_ids))
    displayed_ids = [photo_id for chapter in plan.chapters for photo_id in chapter.photo_ids]
    if displayed_ids != valid_ids:
        first_chapter = plan.chapters[0]
        logger.warning(
            "story_photo_order_repaired selected_count=%d fallback=single_chapter",
            len(photos),
        )
        plan.chapters = [
            StoryChapter(
                id="chapter-1",
                title=first_chapter.title,
                intro=first_chapter.intro,
                photo_ids=valid_ids,
            )
        ]
    for index, chapter in enumerate(plan.chapters):
        chapter.id = f"chapter-{index + 1}"

    fallback_poem = _fallback_poem_lines(photos)
    for photo in photos:
        plan.captions.setdefault(
            photo.id,
            _fallback_image_caption(photo, image_style),
        )
        if image_style == ImageStyle.photo_revival:
            plan.captions[photo.id] = normalize_image_caption(
                image_style,
                plan.captions[photo.id],
            )
        poem_line = plan.poem_lines.get(photo.id, "").strip()
        if not poem_line or poem_line == plan.captions[photo.id].strip():
            plan.poem_lines[photo.id] = fallback_poem[photo.id]
    return plan


def _fallback_image_caption(photo: MediaPhoto, image_style: ImageStyle) -> str:
    seed = photo.analysis.caption_seed if photo.analysis else ""
    if image_style == ImageStyle.scenes_gathered:
        return "Gathered along the way"
    if image_style == ImageStyle.minimal_zine:
        return seed or "沿途微光"
    return normalize_image_caption(image_style, seed)


def _fallback_poem_lines(photos: list[MediaPhoto]) -> dict[str, str]:
    if not photos:
        return {}
    if len(photos) == 1:
        return {photos[0].id: "这一程的风景，在回望里落成一首安静的诗。"}

    lines: dict[str, str] = {}
    middle_lines = (
        "光沿着山水，把道路写向更远处，",
        "偶然相逢的颜色，被风轻轻收拢，",
        "脚步穿过人间，也穿过安静的云，",
        "未说完的话，留在一程又一程风景里，",
    )
    for index, photo in enumerate(photos):
        if index == 0:
            line = "风从旅途的第一页起身，"
        elif index == len(photos) - 1:
            line = "直到所有远方，都在这一页成为温柔的回声。"
        else:
            line = middle_lines[(index - 1) % len(middle_lines)]
        lines[photo.id] = line
    return lines
