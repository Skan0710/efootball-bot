"""Pillow-based image rendering utilities for FC 27 player cards and analytics charts."""

from __future__ import annotations

import io
import os
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Loads a TrueType font cleanly across Windows and Linux environments."""
    candidates = (
        [
            r"C:\Windows\Fonts\arialbd.ttf",
            r"C:\Windows\Fonts\calibrib.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
        if bold
        else [
            r"C:\Windows\Fonts\arial.ttf",
            r"C:\Windows\Fonts\calibri.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
    )
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _card_theme(rating: int) -> Dict[str, Tuple[int, int, int]]:
    """Selects EA FC 27 tier color palette based on OVR rating."""
    if rating >= 88:
        # Icon / TOTY Royal Gold
        return {
            "bg_top": (16, 26, 62),
            "bg_bot": (8, 13, 36),
            "accent": (236, 193, 92),
            "accent_soft": (62, 86, 158),
            "text": (250, 232, 178),
            "subtext": (210, 198, 162),
            "divider": (212, 168, 72),
            "tier_label": "TOTY ICON",
        }
    if rating >= 75:
        # Gold Rare
        return {
            "bg_top": (228, 189, 96),
            "bg_bot": (152, 114, 38),
            "accent": (255, 240, 178),
            "accent_soft": (196, 154, 62),
            "text": (38, 28, 12),
            "subtext": (64, 48, 20),
            "divider": (112, 82, 26),
            "tier_label": "GOLD RARE",
        }
    if rating >= 65:
        # Silver Rare
        return {
            "bg_top": (205, 214, 224),
            "bg_bot": (124, 135, 148),
            "accent": (244, 248, 252),
            "accent_soft": (164, 176, 189),
            "text": (26, 32, 42),
            "subtext": (52, 62, 74),
            "divider": (96, 108, 122),
            "tier_label": "SILVER RARE",
        }
    # Bronze Rare
    return {
        "bg_top": (196, 138, 94),
        "bg_bot": (124, 76, 46),
        "accent": (244, 202, 168),
        "accent_soft": (160, 106, 68),
        "text": (36, 20, 12),
        "subtext": (66, 40, 26),
        "divider": (102, 62, 36),
        "tier_label": "BRONZE RARE",
    }


def _lerp_color(
    c1: Tuple[int, int, int],
    c2: Tuple[int, int, int],
    t: float,
) -> Tuple[int, int, int]:
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def render_fc27_card(card: dict) -> io.BytesIO:
    """Renders an FC 27 Ultimate Team-style PNG card into an in-memory PNG buffer."""
    width, height = 460, 660
    theme = _card_theme(int(card["rating"]))

    # Outer shield silhouette points (classic UT / FC 27 card shape)
    shield_outer = [
        (48, 36),
        (115, 20),
        (width // 2, 14),
        (width - 115, 20),
        (width - 48, 36),
        (width - 28, 72),
        (width - 28, 515),
        (width - 60, 575),
        (width // 2, 642),
        (60, 575),
        (28, 515),
        (28, 72),
    ]
    shield_inner = [
        (56, 46),
        (118, 31),
        (width // 2, 25),
        (width - 118, 31),
        (width - 56, 46),
        (width - 39, 78),
        (width - 39, 510),
        (width - 68, 566),
        (width // 2, 628),
        (68, 566),
        (39, 510),
        (39, 78),
    ]

    # Paint gradient layer clipped to shield mask
    mask = Image.new("L", (width, height), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.polygon(shield_outer, fill=255)

    gradient = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    grad_draw = ImageDraw.Draw(gradient)
    for y in range(height):
        t = y / max(height - 1, 1)
        col = _lerp_color(theme["bg_top"], theme["bg_bot"], t)
        grad_draw.line([(0, y), (width, y)], fill=(*col, 255))

    # Subtle diagonal sheen rays inside card
    sheen = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    sheen_draw = ImageDraw.Draw(sheen)
    sheen_col = (*theme["accent_soft"], 55)
    sheen_draw.polygon([(20, 140), (260, 18), (340, 18), (60, 220)], fill=sheen_col)
    sheen_draw.polygon([(140, 390), (440, 110), (440, 195), (210, 420)], fill=sheen_col)
    sheen_draw.polygon([(30, 460), (430, 460), (390, 580), (width // 2, 635), (70, 580)], fill=(0, 0, 0, 38))
    gradient = Image.alpha_composite(gradient, sheen)

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas.paste(gradient, (0, 0), mask)
    draw = ImageDraw.Draw(canvas)

    # Outer & inner metallic card borders
    draw.polygon(shield_outer, outline=(*theme["accent"], 255), width=6)
    draw.polygon(shield_inner, outline=(*theme["divider"], 200), width=2)

    # Fonts
    font_ovr = _load_font(68, bold=True)
    font_pos = _load_font(26, bold=True)
    font_tier = _load_font(14, bold=True)
    font_name = _load_font(36, bold=True)
    font_stat_val = _load_font(30, bold=True)
    font_stat_lbl = _load_font(24, bold=False)
    font_footer = _load_font(16, bold=True)
    font_crest = _load_font(56, bold=True)

    # Top-left Rating & Position
    ovr_text = str(card["rating"])
    draw.text((88, 66), ovr_text, font=font_ovr, fill=theme["text"], anchor="mm")
    draw.line([(56, 108), (120, 108)], fill=theme["divider"], width=2)
    draw.text((88, 128), str(card["position"]), font=font_pos, fill=theme["text"], anchor="mm")

    # Small club pill under position
    draw.text((88, 162), "CO-OP FC", font=font_tier, fill=theme["subtext"], anchor="mm")

    # Stylized Avatar / Crest Emblem on center-right
    cx, cy = 266, 200
    draw.ellipse([(cx - 88, cy - 88), (cx + 88, cy + 88)], fill=(*theme["accent_soft"], 90), outline=(*theme["accent"], 220), width=4)
    draw.ellipse([(cx - 72, cy - 72), (cx + 72, cy + 72)], fill=(*theme["bg_bot"], 180), outline=(*theme["divider"], 180), width=2)
    # Stylized footballer shoulders & head silhouette
    draw.ellipse([(cx - 24, cy - 48), (cx + 24, cy + 0)], fill=theme["accent"])
    draw.chord([(cx - 50, cy + 2), (cx + 50, cy + 78)], start=180, end=360, fill=theme["accent"])
    initials = "".join(ch for ch in str(card["player"])[:2]).upper() or "FC"
    draw.text((cx, cy + 32), initials, font=_load_font(22, bold=True), fill=theme["bg_bot"], anchor="mm")

    # Tier badge at top-right inside shield
    draw.text((width - 102, 68), theme["tier_label"], font=font_tier, fill=theme["subtext"], anchor="mm")

    # Name Plate
    name_y = 338
    draw.line([(68, name_y - 26), (width - 68, name_y - 26)], fill=theme["divider"], width=2)
    player_label = str(card["player"]).upper()
    if len(player_label) > 12:
        font_name = _load_font(30, bold=True)
    draw.text((width // 2, name_y), player_label, font=font_name, fill=theme["text"], anchor="mm")
    draw.line([(68, name_y + 26), (width - 68, name_y + 26)], fill=theme["divider"], width=3)

    # 6 Core FC 27 Stats in 2 balanced columns
    left_stats = [
        (card["pace"], "PAC"),
        (card["shooting"], "SHO"),
        (card["passing"], "PAS"),
    ]
    right_stats = [
        (card["dribbling"], "DRI"),
        (card["defending"], "DEF"),
        (card["physical"], "PHY"),
    ]

    start_y = 402
    row_gap = 50
    for idx, (val, lbl) in enumerate(left_stats):
        y = start_y + idx * row_gap
        draw.text((134, y), f"{val:02d}", font=font_stat_val, fill=theme["text"], anchor="rm")
        draw.text((146, y + 1), lbl, font=font_stat_lbl, fill=theme["subtext"], anchor="lm")

    # Vertical center divider between stat columns
    draw.line([(width // 2, start_y - 20), (width // 2, start_y + 2 * row_gap + 18)], fill=theme["divider"], width=2)

    for idx, (val, lbl) in enumerate(right_stats):
        y = start_y + idx * row_gap
        draw.text((294, y), f"{val:02d}", font=font_stat_val, fill=theme["text"], anchor="rm")
        draw.text((306, y + 1), lbl, font=font_stat_lbl, fill=theme["subtext"], anchor="lm")

    # Bottom Career Summary Strip
    draw.line([(106, 544), (width - 106, 544)], fill=theme["divider"], width=2)
    summary_text = f"{card['matches_played']} MP  •  {card['goals']}G  •  {card['assists']}A  •  {card['win_rate']:.0f}% WR"
    draw.text((width // 2, 572), summary_text, font=font_footer, fill=theme["text"], anchor="mm")
    draw.text((width // 2, 602), f"{card['clean_sheets']} CS  •  {card['goals_conceded']} GA CONCEDED", font=_load_font(12, bold=True), fill=theme["subtext"], anchor="mm")

    out = io.BytesIO()
    canvas.save(out, format="PNG")
    out.seek(0)
    return out
