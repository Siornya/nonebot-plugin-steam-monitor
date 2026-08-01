from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .config import RESOURCE_DIR

WIDTH = 920
START_WIDTH = 624
START_HEIGHT = 205
END_WIDTH = 624
END_HEIGHT = 205
SWITCH_WIDTH = 624
SWITCH_HEIGHT = 205
START_TEMPLATE = RESOURCE_DIR / "steam_start_template.png"
END_TEMPLATE = RESOURCE_DIR / "steam_end_template.png"
SWITCH_TEMPLATE = RESOURCE_DIR / "steam_switch_template.png"
PADDING = 44
BG = (18, 21, 27)
PANEL = (28, 33, 42)
TEXT = (238, 242, 247)
MUTED = (156, 166, 180)
SUBTLE = (92, 104, 121)
GREEN = (55, 211, 153)
BLUE = (96, 165, 250)
AMBER = (251, 191, 36)
RED = (248, 113, 113)

_FONT_PATHS = [
    RESOURCE_DIR / "NotoSansSC.ttf",
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
]
_FONT_CACHE: dict[tuple[int, bool], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}

AVATAR_BOX = (22, 50, 126, 154)
TEXT_X = 142
TEXT_RIGHT = 444
LOGO_BOX = (492, 0, 624, 205)
DURATION_X = TEXT_X
DURATION_Y = 164
SWITCH_AVATAR_BOX = AVATAR_BOX
SWITCH_TEXT_X = TEXT_X
SWITCH_TEXT_RIGHT = TEXT_RIGHT
SWITCH_LOGO_BOX = LOGO_BOX
SWITCH_DURATION_Y = 164


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    for path in _FONT_PATHS:
        if path.exists():
            font = ImageFont.truetype(str(path), size)
            _FONT_CACHE[key] = font
            return font
    font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        trial = current + char
        if current and _text_width(draw, trial, font) > max_width:
            lines.append(current)
            current = char
        else:
            current = trial
    if current:
        lines.append(current)
    return lines or [""]


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    if _text_width(draw, text, font) <= max_width:
        return text
    suffix = "..."
    value = text
    while value and _text_width(draw, value + suffix, font) > max_width:
        value = value[:-1]
    return value + suffix if value else suffix


def _duration(minutes: float | None) -> str | None:
    if minutes is None:
        return None
    if minutes < 60:
        return f"{minutes:.1f} 分钟"
    return f"{minutes / 60:.1f} 小时"


def _duration_line(minutes: float | None) -> str | None:
    duration = _duration(minutes)
    return f"游玩时间：{duration}" if duration else None


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size[0], size[1]), radius=radius, fill=255)
    return mask


def _cover_image(data: bytes, box: tuple[int, int, int, int], radius: int) -> Image.Image | None:
    try:
        src = Image.open(BytesIO(data)).convert("RGB")
    except Exception:
        return None

    width = box[2] - box[0]
    height = box[3] - box[1]
    scale = max(width / src.width, height / src.height)
    resized = src.resize((max(1, int(src.width * scale)), max(1, int(src.height * scale))))
    left = max(0, (resized.width - width) // 2)
    top = max(0, (resized.height - height) // 2)
    cropped = resized.crop((left, top, left + width, top + height))
    cropped.putalpha(_rounded_mask((width, height), radius))
    return cropped


def _draw_avatar_placeholder(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int]) -> None:
    draw.rounded_rectangle(box, radius=18, fill=(38, 45, 57))
    x0, y0, x1, y1 = box
    cx = (x0 + x1) // 2
    draw.ellipse((cx - 18, y0 + 22, cx + 18, y0 + 58), fill=(71, 83, 101))
    draw.rounded_rectangle((cx - 26, y0 + 62, cx + 26, y0 + 84), radius=11, fill=(71, 83, 101))


def _draw_logo_placeholder(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int]) -> None:
    draw.rounded_rectangle(box, radius=14, fill=(35, 42, 53))
    x0, y0, x1, y1 = box
    draw.rounded_rectangle((x0 + 18, y0 + 18, x1 - 18, y1 - 18), radius=8, outline=(70, 82, 100), width=3)
    draw.line((x0 + 30, y0 + 42, x1 - 30, y0 + 42), fill=(70, 82, 100), width=3)
    draw.line((x0 + 30, y0 + 60, x1 - 30, y0 + 60), fill=(70, 82, 100), width=3)


def _base_template(width: int, height: int, accent: tuple[int, int, int]) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (width, height), PANEL)
    draw = ImageDraw.Draw(img)
    return img, draw


def create_start_template() -> Image.Image:
    img, draw = _base_template(START_WIDTH, START_HEIGHT, GREEN)
    _draw_avatar_placeholder(draw, AVATAR_BOX)
    _draw_logo_placeholder(draw, LOGO_BOX)
    draw.line((TEXT_X, 164, TEXT_RIGHT, 164), fill=(42, 50, 63), width=1)
    return img


def create_end_template() -> Image.Image:
    img, draw = _base_template(END_WIDTH, END_HEIGHT, AMBER)
    _draw_avatar_placeholder(draw, AVATAR_BOX)
    _draw_logo_placeholder(draw, LOGO_BOX)
    draw.line((TEXT_X, 164, TEXT_RIGHT, 164), fill=(42, 50, 63), width=1)
    return img


def create_switch_template() -> Image.Image:
    img, draw = _base_template(SWITCH_WIDTH, SWITCH_HEIGHT, BLUE)
    _draw_avatar_placeholder(draw, SWITCH_AVATAR_BOX)
    _draw_logo_placeholder(draw, SWITCH_LOGO_BOX)
    draw.line((SWITCH_TEXT_X, 164, SWITCH_TEXT_RIGHT, 164), fill=(42, 50, 63), width=1)
    return img


def save_start_template(path: Path = START_TEMPLATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    create_start_template().save(path, format="PNG")


def save_end_template(path: Path = END_TEMPLATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    create_end_template().save(path, format="PNG")


def save_switch_template(path: Path = SWITCH_TEMPLATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    create_switch_template().save(path, format="PNG")


def save_status_templates() -> None:
    save_start_template()
    save_end_template()
    save_switch_template()


def _load_template(path: Path, create_func) -> Image.Image:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        create_func().save(path, format="PNG")
    try:
        return Image.open(path).convert("RGB")
    except Exception:
        return create_func()


def _paste_optional(img: Image.Image, data: bytes | None, box: tuple[int, int, int, int], radius: int) -> None:
    if not data:
        return
    image = _cover_image(data, box, radius)
    if image:
        img.paste(image, box[:2], image)


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
) -> None:
    fitted = _fit_text(draw, text, font, box[2] - box[0] - 18)
    bbox = draw.textbbox((0, 0), fitted, font=font)
    x = box[0] + (box[2] - box[0] - (bbox[2] - bbox[0])) // 2
    y = box[1] + (box[3] - box[1] - (bbox[3] - bbox[1])) // 2 - 1
    draw.text((x, y), fitted, font=font, fill=fill)


def render_start_image(
    *,
    player_name: str,
    game_name: str,
    avatar_image: bytes | None = None,
    game_cover_image: bytes | None = None,
) -> bytes:
    img = _load_template(START_TEMPLATE, create_start_template)
    draw = ImageDraw.Draw(img)
    _paste_optional(img, avatar_image, AVATAR_BOX, 18)
    _paste_optional(img, game_cover_image, LOGO_BOX, 0)

    player_font = _font(30, True)
    action_font = _font(18)
    game_font = _font(27, True)
    text_width = TEXT_RIGHT - TEXT_X
    draw.text((TEXT_X, 50), _fit_text(draw, player_name, player_font, text_width), font=player_font, fill=TEXT)
    draw.text((TEXT_X, 96), "开始玩", font=action_font, fill=GREEN)
    draw.text((TEXT_X, 124), _fit_text(draw, game_name, game_font, text_width), font=game_font, fill=TEXT)

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_end_image(
    *,
    player_name: str,
    game_name: str,
    duration_min: float | None = None,
    avatar_image: bytes | None = None,
    game_cover_image: bytes | None = None,
) -> bytes:
    img = _load_template(END_TEMPLATE, create_end_template)
    draw = ImageDraw.Draw(img)
    _paste_optional(img, avatar_image, AVATAR_BOX, 18)
    _paste_optional(img, game_cover_image, LOGO_BOX, 0)

    player_font = _font(30, True)
    action_font = _font(18)
    game_font = _font(27, True)
    duration_font = _font(17)
    text_width = TEXT_RIGHT - TEXT_X
    draw.text((TEXT_X, 50), _fit_text(draw, player_name, player_font, text_width), font=player_font, fill=TEXT)
    draw.text((TEXT_X, 96), "结束了", font=action_font, fill=AMBER)
    draw.text((TEXT_X, 124), _fit_text(draw, game_name, game_font, text_width), font=game_font, fill=TEXT)
    duration = _duration_line(duration_min) or "游玩时间：已结束"
    draw.text((DURATION_X, DURATION_Y), _fit_text(draw, duration, duration_font, text_width), font=duration_font, fill=AMBER)

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_switch_image(
    *,
    player_name: str,
    old_game_name: str,
    new_game_name: str,
    duration_min: float | None = None,
    avatar_image: bytes | None = None,
    game_cover_image: bytes | None = None,
) -> bytes:
    img = _load_template(SWITCH_TEMPLATE, create_switch_template)
    draw = ImageDraw.Draw(img)
    _paste_optional(img, avatar_image, SWITCH_AVATAR_BOX, 18)
    _paste_optional(img, game_cover_image, SWITCH_LOGO_BOX, 0)

    player_font = _font(25, True)
    small_font = _font(16)
    old_font = _font(19)
    new_font = _font(23, True)
    duration_font = _font(17)
    text_width = SWITCH_TEXT_RIGHT - SWITCH_TEXT_X
    draw.text((SWITCH_TEXT_X, 44), _fit_text(draw, player_name, player_font, text_width), font=player_font, fill=TEXT)
    draw.text((SWITCH_TEXT_X, 82), "结束了", font=small_font, fill=AMBER)
    draw.text((SWITCH_TEXT_X + 62, 78), _fit_text(draw, old_game_name, old_font, text_width - 62), font=old_font, fill=MUTED)
    draw.text((SWITCH_TEXT_X, 118), "开始玩", font=small_font, fill=BLUE)
    draw.text((SWITCH_TEXT_X + 72, 113), _fit_text(draw, new_game_name, new_font, text_width - 72), font=new_font, fill=TEXT)
    duration = _duration_line(duration_min) or "游玩时间：已切换"
    draw.text((SWITCH_TEXT_X, SWITCH_DURATION_Y), _fit_text(draw, duration, duration_font, text_width), font=duration_font, fill=BLUE)

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_status_image(
    *,
    kind: str,
    player_name: str,
    game_name: str | None = None,
    new_game_name: str | None = None,
    ended_games: list[tuple[str, float]] | None = None,
    duration_min: float | None = None,
    avatar_image: bytes | None = None,
    game_cover_image: bytes | None = None,
) -> bytes:
    if kind == "start":
        return render_start_image(
            player_name=player_name,
            game_name=game_name or "未知游戏",
            avatar_image=avatar_image,
            game_cover_image=game_cover_image,
        )
    if kind == "end":
        return render_end_image(
            player_name=player_name,
            game_name=game_name or "未知游戏",
            duration_min=duration_min,
            avatar_image=avatar_image,
            game_cover_image=game_cover_image,
        )
    if kind == "switch":
        ended = ended_games or []
        old_game_name = "、".join(name for name, _ in ended) or (game_name or "未知游戏")
        duration = sum(minutes for _, minutes in ended) if ended else duration_min
        return render_switch_image(
            player_name=player_name,
            old_game_name=old_game_name,
            new_game_name=new_game_name or "未知游戏",
            duration_min=duration,
            avatar_image=avatar_image,
            game_cover_image=game_cover_image,
        )

    accent = RED
    title = "Steam 状态"
    headline = game_name or "未知游戏"
    detail_lines: list[str | None] = []
    detail_lines = [line for line in detail_lines if line]
    time_text = datetime.now().strftime("%Y-%m-%d %H:%M")

    tmp = Image.new("RGB", (WIDTH, 100), BG)
    draw = ImageDraw.Draw(tmp)
    title_font = _font(46, True)
    player_font = _font(28)
    meta_font = _font(22)
    detail_font = _font(24)

    headline_lines = _wrap(draw, headline, title_font, WIDTH - PADDING * 2)
    detail_wrapped: list[str] = []
    for line in detail_lines:
        detail_wrapped.extend(_wrap(draw, line, detail_font, WIDTH - PADDING * 2))

    height = 250 + len(headline_lines) * 56 + len(detail_wrapped) * 34
    img = Image.new("RGB", (WIDTH, height), BG)
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle((18, 18, WIDTH - 18, height - 18), radius=28, fill=PANEL)
    draw.rounded_rectangle((18, 18, 30, height - 18), radius=6, fill=accent)

    draw.text((PADDING, 42), "STEAM STATUS", font=meta_font, fill=accent)
    draw.text((WIDTH - PADDING - _text_width(draw, time_text, meta_font), 42), time_text, font=meta_font, fill=SUBTLE)

    y = 84
    draw.text((PADDING, y), player_name, font=player_font, fill=TEXT)
    y += 42
    draw.text((PADDING, y), title, font=meta_font, fill=MUTED)
    y += 40

    for line in headline_lines:
        draw.text((PADDING, y), line, font=title_font, fill=TEXT)
        y += 58

    if detail_wrapped:
        y += 12
        for line in detail_wrapped:
            draw.text((PADDING, y), line, font=detail_font, fill=MUTED)
            y += 36

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
