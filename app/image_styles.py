from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .models import ImageStyle


@dataclass(frozen=True)
class ImageStyleSpec:
    style: ImageStyle
    label: str
    description: str
    skill_setting: str
    skill_name: str
    output_width: int
    output_height: int
    aspect_width: int
    aspect_height: int
    generated_fit: str
    canvas_color: str
    generation_noun: str
    story_caption_requirement: str
    story_caption_example: str
    story_caption_excerpts: tuple[tuple[str, str], ...] = ()
    runtime_rules: str = ""

    @property
    def aspect_css(self) -> str:
        return f"{self.aspect_width} / {self.aspect_height}"

    def skill_path(self, settings: Settings) -> Path:
        return getattr(settings, self.skill_setting)


IMAGE_STYLE_SPECS: dict[ImageStyle, ImageStyleSpec] = {
    ImageStyle.photo_revival: ImageStyleSpec(
        style=ImageStyle.photo_revival,
        label="旅行手绘",
        description="白纸、水彩与手写笔记",
        skill_setting="photo_revival_skill",
        skill_name="Photo Revival",
        output_width=1024,
        output_height=1365,
        aspect_width=3,
        aspect_height=4,
        generated_fit="contain",
        canvas_color="#ffffff",
        generation_noun="手绘照片",
        story_caption_requirement=(
            "captions 是写进生成图片内部的独立短旁白，每张使用 4-30 个中文字符，"
            "继续根据单张画面提炼，不要求彼此连成诗。"
        ),
        story_caption_example="4-30 字图片内中文短旁白",
    ),
    ImageStyle.scenes_gathered: ImageStyleSpec(
        style=ImageStyle.scenes_gathered,
        label="拾景纸刊",
        description="真实照片、撕纸与抽象插画",
        skill_setting="scenes_gathered_skill",
        skill_name="Gathered Scenes Zine v1.3",
        output_width=900,
        output_height=1500,
        aspect_width=3,
        aspect_height=5,
        generated_fit="cover",
        canvas_color="#f3eee3",
        generation_noun="拾景纸刊页",
        story_caption_requirement=(
            "captions 是写进生成图片内部的文字内容。逐张依据 upstream_image_text_rules "
            "创作，保留上游支持的语言模式、标点、分隔符和多行层级，不要附加应用级"
            "语言或格式限制；各张不要求使用同一种语言或结构。"
        ),
        story_caption_example="遵循上游 Micro-Text System 的单行或多行文字",
        story_caption_excerpts=(("## Micro-Text System", "## Prompt Shape"),),
        runtime_rules="""
Canvas and attention geometry:
- One vertical 3:5 paper poster, flat orthographic scan, no mockup or border.
- Keep a recognizable truthful photographic anchor across roughly 25%-50% of the poster.
- Let a larger source-derived illustration field influence roughly 45%-70% while most of that field remains quiet paper.
- Preserve the core subject, one key spatial relationship, and the dominant horizon, path, gaze, silhouette, or gesture.

Illustration and abstraction:
- Retain only one or two defining source forms. Merge repeated foliage, branches, windows, waves, crowds, and texture into a few large quiet masses.
- Remove 60%-80% of descriptive detail, and 85%-95% of foliage micro-detail when relevant.
- Choose one primary grammar: silhouette, broken contour, sparse field, restrained rhythm, or one/two cut-paper forms. Use at most one supporting grammar.
- Make blank paper active inside and around the illustration. Never trace or redraw the whole photo.

Photo-to-paper handoff and color:
- The main photographic anchor must meet the paper through a clearly visible irregular hand-torn contour with a narrow fibrous fringe and exposed warm paper tone. It should feel tactile but flat, with no cast shadow or curled edge.
- Add exactly one high-chroma print hue. Derive its contour, position, or rhythm from a real source shape; make it touch, cross, replace, pass behind, or continue through the photo/illustration boundary.
- The hue must change balance, eye path, figure-ground, or meaning. Use risograph ink, opaque cut paper, flat silhouette, dry print, or translucent halftone. Do not add a detached decorative dot or rectangle.

Typography and reproduction:
- Treat the supplied caption block as the exact readable text content. Preserve its languages, punctuation, separators, and line breaks without translating, rewriting, or flattening its hierarchy. Apply the upstream language mode and keep the complete text system quiet, legible, subordinate, and integrated as handwriting, typewriter, letterpress, faint pencil, worn stamp, or dry ink in a quiet paper area.
- Warm cream aged paper, matte fibers, restrained grain, xerox/risograph/letterpress wear, diffuse light, natural photographic color, no artificial depth.
- Avoid clean digital clipping, full-scene tracing, dense scrapbooking, generic decoration, multiple bright hues, logos, CTA, glossy mockups, neon, 3D, cinematic lighting, cute cartoon/anime, large display type, invented text beyond the supplied caption block, and watermarks.
""".strip(),
    ),
    ImageStyle.minimal_zine: ImageStyleSpec(
        style=ImageStyle.minimal_zine,
        label="极简纸刊",
        description="旧纸留白、微型主体与实验排版",
        skill_setting="minimal_zine_skill",
        skill_name="Minimal Zine Poster v0.1",
        output_width=900,
        output_height=1500,
        aspect_width=3,
        aspect_height=5,
        generated_fit="cover",
        canvas_color="#f1eadb",
        generation_noun="极简纸刊页",
        story_caption_requirement=(
            "captions 是写进生成图片内部的文字内容。逐张依据 upstream_image_text_rules "
            "创作，保留上游支持的语言、日期或其他文字元素、标点和多行层级，不要附加"
            "应用级语言或格式限制；各张不要求使用同一种语言或结构。"
        ),
        story_caption_example="遵循上游 Typography System 的单行或多行文字",
        story_caption_excerpts=(
            ("5. **Typography System:**", "6. **Color Logic:**"),
            ("### Typography Mode", "### Texture Mode"),
            ("2. Parse the user's content.", "3. Select a variation recipe."),
            ("4. Write the final image prompt.", "5. Generate the image."),
        ),
        runtime_rules="""
Canvas and attention geometry:
- One vertical 3:5 phone-poster, full-frame aged matte paper, flat orthographic scan, no border or mockup.
- Keep 70%-90% plain paper and one source-derived visual cluster occupying about 8%-25%, away from the edges.
- Recompose freely but preserve at least one recognizable source subject, silhouette, fragment, or spatial cue.

Image anchor and typography:
- Reduce the source to one object, small photo fragment, specimen, cutout, silhouette, old printed illustration, texture window, or compact conceptual relation.
- Treat it with photocopy softness, torn paper, halftone, scanline, risograph grain, xerox wear, ink bleed, or slight misregistration.
- Treat the supplied caption block as the exact readable text content. Preserve its languages, punctuation, metadata-like elements, fragments, and line breaks without translating, rewriting, or flattening its hierarchy. Choose an upstream typography mode such as small serif, typewriter, monospaced, letterpress, fragmented letters, archive microtext, or lightly imperfect handwriting, and keep the treatment integrated with the visual cluster.

Color and reproduction:
- Use paper tones plus gray/black and exactly one unmistakable high-chroma anchor visible at thumbnail size. The saturated hue should occupy about 0.8%-2.5% of the poster or 15%-35% of the visual cluster.
- The color may be the source-derived subject, flat silhouette, irregular cutout, printed block, partial-color photo region, or fragmented type. Keep it saturated through grain and ink defects.
- Diffuse light, low-to-medium contrast on paper and grayscale elements, matte absorbent fibers, quiet poetic Japanese/Korean indie-zine or minimal editorial mood.
- Avoid full-bleed scenes, commercial headlines, product-ad layouts, logos, CTA, clean UI white, glossy paper shadows, cinematic lighting, depth of field, 3D, neon, cute cartoon/anime, fashion drama, dense scrapbooks, many colors, long text, and watermarks.
""".strip(),
    ),
}


def get_image_style_spec(style: ImageStyle | str) -> ImageStyleSpec:
    return IMAGE_STYLE_SPECS[ImageStyle(style)]


def load_image_style_rules(settings: Settings, style: ImageStyle | str) -> str:
    spec = get_image_style_spec(style)
    upstream_rules = spec.skill_path(settings).read_text(encoding="utf-8")
    if not spec.runtime_rules:
        return upstream_rules
    identity = upstream_rules.split("\n## ", 1)[0].strip()
    return f"{identity}\n\n## Runtime visual compiler\n\n{spec.runtime_rules}"


def load_story_caption_rules(settings: Settings, style: ImageStyle | str) -> str:
    spec = get_image_style_spec(style)
    if not spec.story_caption_excerpts:
        return ""
    upstream_rules = spec.skill_path(settings).read_text(encoding="utf-8")
    return "\n\n".join(
        _extract_upstream_excerpt(upstream_rules, start, end)
        for start, end in spec.story_caption_excerpts
    )


def _extract_upstream_excerpt(markdown: str, start: str, end: str) -> str:
    start_index = markdown.find(start)
    if start_index < 0:
        raise ValueError(f"上游 Skill 缺少文案规则起点: {start}")
    end_index = markdown.find(end, start_index + len(start))
    if end_index < 0:
        raise ValueError(f"上游 Skill 缺少文案规则终点: {end}")
    return markdown[start_index:end_index].strip()


def normalize_image_caption(style: ImageStyle | str, caption: str) -> str:
    image_style = ImageStyle(style)
    if image_style != ImageStyle.photo_revival:
        return caption
    compact = " ".join(caption.strip().split())
    if 4 <= len(compact) <= 30:
        return compact
    if len(compact) > 30:
        return compact[:30]
    return "这一刻被轻轻留在旅途中"
