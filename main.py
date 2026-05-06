import os
from fastapi import FastAPI, Query
from fastapi.responses import StreamingResponse
from PIL import Image, ImageDraw, ImageFilter, ImageFont
import httpx
import io
import math
import random
import colorsys
from typing import Optional

app = FastAPI(title="Music Card Generator")

W, H = 900, 380
CORNER = 28
THUMB = 200
THUMB_X, THUMB_Y = 40, 50

SOURCE_COLORS = {
    "youtube":   (0xCC, 0x00, 0x00),
    "spotify":   (0x1D, 0xB9, 0x54),
    "applemusic":(0xFC, 0x3C, 0x58),
    "soundcloud":(0xFF, 0x55, 0x00),
    "deezer":    (0xA2, 0x38, 0xFF),
    "default":   (0x88, 0x88, 0xFF),
}

def hex_to_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def dominant_color(img: Image.Image):
    small = img.resize((50, 50)).convert("RGB")
    pixels = list(small.getdata())
    r = sum(p[0] for p in pixels) // len(pixels)
    g = sum(p[1] for p in pixels) // len(pixels)
    b = sum(p[2] for p in pixels) // len(pixels)
    return r, g, b

def darken(color, factor=0.3):
    return tuple(max(10, int(c * factor)) for c in color)

def saturate(color, factor=1.4):
    h, s, v = colorsys.rgb_to_hsv(color[0]/255, color[1]/255, color[2]/255)
    s = min(1.0, s * factor)
    v = min(0.55, v * 0.8)
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))

def make_gradient_bg(w, h, col1, col2):
    base = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(base)
    for x in range(w):
        t = x / w
        r = int(col1[0] + (col2[0] - col1[0]) * t)
        g = int(col1[1] + (col2[1] - col1[1]) * t)
        b = int(col1[2] + (col2[2] - col1[2]) * t)
        draw.line([(x, 0), (x, h)], fill=(r, g, b))
    return base

def rounded_rect_mask(w, h, r):
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
    return mask

def paste_rounded(base, img, pos, radius):
    mask = rounded_rect_mask(img.width, img.height, radius)
    base.paste(img, pos, mask)

def draw_glow_border(draw, x, y, size, radius, color, thickness=3, glow_size=12):
    for i in range(glow_size, 0, -1):
        alpha = int(180 * (1 - i / glow_size))
        r = min(255, color[0])
        g = min(255, color[1])
        b = min(255, color[2])
        draw.rounded_rectangle(
            [x - i, y - i, x + size + i, y + size + i],
            radius=radius + i,
            outline=(r, g, b, alpha),
            width=1
        )
    draw.rounded_rectangle(
        [x, y, x + size, y + size],
        radius=radius,
        outline=(*color, 255),
        width=thickness
    )

def draw_progress_bar(draw, x, y, w, h, progress, col_start, col_end):
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=(255, 255, 255, 30))
    fill_w = int(w * progress)
    if fill_w > 0:
        bar_img = Image.new("RGBA", (fill_w, h))
        bar_draw = ImageDraw.Draw(bar_img)
        for px in range(fill_w):
            t = px / max(fill_w - 1, 1)
            r = int(col_start[0] + (col_end[0] - col_start[0]) * t)
            g = int(col_start[1] + (col_end[1] - col_start[1]) * t)
            b = int(col_start[2] + (col_end[2] - col_start[2]) * t)
            bar_draw.line([(px, 0), (px, h)], fill=(r, g, b))
        bar_mask = rounded_rect_mask(fill_w, h, h // 2)
        draw._image.paste(bar_img, (x, y), bar_mask)

        dot_x = x + fill_w
        dot_r = 8
        for i in range(6, 0, -1):
            alpha = int(120 * (1 - i / 6))
            draw.ellipse([dot_x - dot_r - i, y + h // 2 - dot_r - i,
                          dot_x - dot_r + dot_r * 2 + i, y + h // 2 + dot_r + i],
                         fill=(255, 255, 255, alpha))
        draw.ellipse([dot_x - dot_r, y + h // 2 - dot_r,
                      dot_x + dot_r, y + h // 2 + dot_r],
                     fill=(255, 255, 255, 255))

def draw_waveform(draw, x, y, w, h, seed=42, progress=0.35, col_active=(140, 80, 255), col_inactive=(255, 255, 255, 40)):
    rng = random.Random(seed)
    bars = 60
    bar_w = 4
    gap = (w - bars * bar_w) // (bars - 1)
    step = bar_w + gap
    fill_x = x + int(w * progress)
    for i in range(bars):
        bx = x + i * step
        height = int(rng.uniform(0.2, 1.0) * h)
        by = y + (h - height) // 2
        color = (*col_active, 200) if bx < fill_x else (255, 255, 255, 35)
        draw.rounded_rectangle([bx, by, bx + bar_w, by + height], radius=2, fill=color)

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")


def load_font(size, bold=False, medium=False):
    try:
        if bold:
            path = os.path.join(FONT_DIR, "Bold.ttf")
        elif medium:
            path = os.path.join(FONT_DIR, "Medium.ttf")
        else:
            path = os.path.join(FONT_DIR, "Regular.ttf")
        return ImageFont.truetype(path, size)
    except:
        try:
            fallback = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            return ImageFont.truetype(fallback, size)
        except:
            return ImageFont.load_default()

def truncate_text(draw, text, font, max_width):
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return text + "…"

def draw_source_badge(draw, x, y, source: str):
    color = SOURCE_COLORS.get(source.lower(), SOURCE_COLORS["default"])
    label = source.upper()
    font = load_font(13, bold=True)
    text_w = int(draw.textlength(label, font=font))
    pad_x, pad_y = 14, 6
    badge_w = text_w + pad_x * 2 + 20
    badge_h = 28
    draw.rounded_rectangle([x, y, x + badge_w, y + badge_h], radius=14,
                            fill=(*color, 30), outline=(*color, 120), width=1)
    dot_x = x + pad_x
    dot_y = y + badge_h // 2
    draw.ellipse([dot_x - 4, dot_y - 4, dot_x + 4, dot_y + 4], fill=(*color, 255))
    draw.text((dot_x + 10, y + pad_y - 1), label, font=font, fill=(*color, 220))
    return badge_w

def seconds_to_mmss(s: int) -> str:
    return f"{s // 60}:{s % 60:02d}"

async def fetch_thumbnail(url: str) -> Optional[Image.Image]:
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return Image.open(io.BytesIO(resp.content)).convert("RGB")
    except:
        return None

def make_placeholder_thumb(size):
    img = Image.new("RGB", (size, size))
    d = ImageDraw.Draw(img)

    # Sky gradient (top = deep space, bottom = twilight purple)
    for y in range(size):
        t = y / size
        r = int(5 + 40 * t)
        g = int(5 + 15 * t)
        b = int(30 + 60 * t)
        d.line([(0, y), (size, y)], fill=(r, g, b))

    # Stars
    rng = random.Random(7)
    for _ in range(60):
        sx = rng.randint(0, size)
        sy = rng.randint(0, int(size * 0.65))
        br = rng.randint(160, 255)
        sr = rng.randint(0, 1)
        d.ellipse([sx - sr, sy - sr, sx + sr + 1, sy + sr + 1], fill=(br, br, br))

    # Moon
    mx, my, mr = int(size * 0.72), int(size * 0.18), int(size * 0.09)
    d.ellipse([mx - mr, my - mr, mx + mr, my + mr], fill=(255, 245, 200))
    d.ellipse([mx + int(mr * 0.3), my - int(mr * 0.2),
               mx + int(mr * 1.1), my + int(mr * 0.8)],
              fill=(int(5 + 40 * 0.18), int(5 + 15 * 0.18), int(30 + 60 * 0.18)))

    # Distant mountains (light layer)
    mtn1 = [
        (0, size), (0, int(size * 0.62)),
        (int(size * 0.15), int(size * 0.38)),
        (int(size * 0.3), int(size * 0.55)),
        (int(size * 0.45), int(size * 0.32)),
        (int(size * 0.6), int(size * 0.5)),
        (int(size * 0.75), int(size * 0.28)),
        (int(size * 0.88), int(size * 0.48)),
        (size, int(size * 0.35)),
        (size, size),
    ]
    d.polygon(mtn1, fill=(25, 18, 55))

    # Closer mountains (dark layer)
    mtn2 = [
        (0, size), (0, int(size * 0.75)),
        (int(size * 0.1), int(size * 0.58)),
        (int(size * 0.25), int(size * 0.72)),
        (int(size * 0.38), int(size * 0.52)),
        (int(size * 0.52), int(size * 0.68)),
        (int(size * 0.65), int(size * 0.48)),
        (int(size * 0.8), int(size * 0.65)),
        (int(size * 0.9), int(size * 0.55)),
        (size, int(size * 0.7)),
        (size, size),
    ]
    d.polygon(mtn2, fill=(12, 8, 28))

    # Foreground ground with subtle purple tint
    ground = [
        (0, size), (0, int(size * 0.88)),
        (size, int(size * 0.88)), (size, size)
    ]
    d.polygon(ground, fill=(8, 5, 20))

    # Reflection shimmer on ground
    for i in range(3):
        rx = int(size * (0.3 + i * 0.15))
        ry = int(size * 0.91)
        d.ellipse([rx - 15, ry - 2, rx + 15, ry + 2], fill=(80, 60, 120))

    return img

@app.get("/card")
async def generate_card(
    title: str = Query(..., description="Track title"),
    artist: str = Query(..., description="Artist name(s)"),
    thumbnail: Optional[str] = Query(None, description="Thumbnail URL"),
    source: str = Query("youtube", description="Source: youtube/spotify/applemusic/soundcloud"),
    duration: int = Query(213, description="Total duration in seconds"),
    position: int = Query(72, description="Current position in seconds"),
    requester: Optional[str] = Query(None, description="Requester username"),
    seed: int = Query(42, description="Waveform seed"),
):
    thumb_img = await fetch_thumbnail(thumbnail) if thumbnail else None
    if thumb_img is None:
        thumb_img = make_placeholder_thumb(THUMB)

    dom_col = dominant_color(thumb_img)
    bg_col1 = saturate(dom_col, 1.5)
    bg_col2 = (
        max(5, int(bg_col1[0] * 0.4)),
        max(5, int(bg_col1[1] * 0.4)),
        min(60, int(bg_col1[2] * 0.6 + 15)),
    )

    card = make_gradient_bg(W, H, bg_col1, bg_col2).convert("RGBA")
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 60))
    card = Image.alpha_composite(card, overlay)
    draw = ImageDraw.Draw(card, "RGBA")

    thumb_resized = thumb_img.resize((THUMB, THUMB), Image.LANCZOS)
    glow_col = (
        min(255, dom_col[0] + 60),
        min(255, dom_col[1] + 40),
        min(255, dom_col[2] + 80),
    )
    draw_glow_border(draw, THUMB_X, THUMB_Y, THUMB, 18, glow_col, thickness=2, glow_size=14)
    paste_rounded(card, thumb_resized, (THUMB_X, THUMB_Y), 18)
    draw = ImageDraw.Draw(card, "RGBA")

    text_x = THUMB_X + THUMB + 36
    text_max_w = W - text_x - 36

    badge_y = THUMB_Y + 4
    draw_source_badge(draw, text_x, badge_y, source)

    title_font = load_font(36, bold=True)
    artist_font = load_font(20, medium=True)
    time_font = load_font(15)

    title_text = truncate_text(draw, title, title_font, text_max_w)
    draw.text((text_x, badge_y + 38), title_text, font=title_font, fill=(255, 255, 255, 255))

    artist_text = truncate_text(draw, artist, artist_font, text_max_w)
    draw.text((text_x, badge_y + 38 + 48), artist_text, font=artist_font, fill=(255, 255, 255, 140))

    progress = min(1.0, position / max(duration, 1))
    bar_x = 40
    bar_y = THUMB_Y + THUMB + 28
    bar_w = W - 80
    bar_h = 6

    draw_progress_bar(draw, bar_x, bar_y, bar_w, bar_h, progress,
                      col_start=(140, 80, 255), col_end=(60, 140, 255))

    cur_str = seconds_to_mmss(position)
    tot_str = seconds_to_mmss(duration)
    draw.text((bar_x, bar_y + 14), cur_str, font=time_font, fill=(255, 255, 255, 130))
    tot_w = draw.textlength(tot_str, font=time_font)
    draw.text((bar_x + bar_w - tot_w, bar_y + 14), tot_str, font=time_font, fill=(255, 255, 255, 130))

    wave_y = bar_y + 42
    wave_h = H - wave_y - 20
    if wave_h > 15:
        draw_waveform(draw, bar_x, wave_y, bar_w, wave_h, seed=seed,
                      progress=progress, col_active=glow_col)

    if requester:
        req_font = load_font(13)
        req_text = f"Requested by {requester}"
        rw = draw.textlength(req_text, font=req_font)
        draw.text((W - rw - 36, badge_y + 4), req_text, font=req_font, fill=(255, 255, 255, 70))

    card_mask = rounded_rect_mask(W, H, CORNER)
    final = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    final.paste(card, (0, 0), card_mask)

    buf = io.BytesIO()
    final.save(buf, format="PNG", optimize=True)
    buf.seek(0)

    return StreamingResponse(buf, media_type="image/png", headers={
        "Cache-Control": "no-cache",
        "Content-Disposition": 'inline; filename="card.png"'
    })

@app.get("/health")
def health():
    return {"status": "ok"}