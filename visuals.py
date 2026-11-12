"""Pillow-based image rendering utilities for FC 27 player cards and analytics charts."""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageOps

from models import canonicalize_player_name


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


def _find_player_pfp(player_name: str, pfp_dir: Optional[Path] = None) -> Optional[Path]:
    """Locates an avatar image for the player in the pfp directory if present.

    Searches for exact name, lowercase name, canonical name, and alias variants.
    """
    directory = pfp_dir or (Path(__file__).resolve().parent / "pfp")
    if not directory.exists() or not directory.is_dir():
        return None

    raw = player_name.strip()
    canonical = canonicalize_player_name(raw)

    keys = {
        raw.lower(),
        canonical.lower(),
    }
    if canonical.lower() == "lilly":
        keys.add("maple")

    for file_path in directory.iterdir():
        if not file_path.is_file():
            continue
        stem = file_path.stem.strip().lower()
        if stem in keys:
            return file_path

    # Fallback to prefix matching (e.g. 'suribg' matching 'suri')
    for file_path in directory.iterdir():
        if not file_path.is_file():
            continue
        stem = file_path.stem.strip().lower()
        if any(stem.startswith(k) or k.startswith(stem) for k in keys if len(stem) >= 3):
            return file_path

    return None


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

    # Stylized Avatar / Crest Emblem or Player PFP on center-right
    cx, cy = 266, 200
    radius = 82
    draw.ellipse([(cx - radius - 6, cy - radius - 6), (cx + radius + 6, cy + radius + 6)], fill=(*theme["accent_soft"], 90), outline=(*theme["accent"], 220), width=4)

    pfp_path = _find_player_pfp(str(card["player"]))
    rendered_pfp = False

    if pfp_path:
        try:
            with Image.open(pfp_path) as pfp_img:
                # Convert to RGBA and center-crop / resize into a square
                pfp_rgba = pfp_img.convert("RGBA")
                size = radius * 2
                cropped = ImageOps.fit(pfp_rgba, (size, size), centering=(0.5, 0.5))

                # Circular mask
                circle_mask = Image.new("L", (size, size), 0)
                mask_draw = ImageDraw.Draw(circle_mask)
                mask_draw.ellipse([(0, 0), (size - 1, size - 1)], fill=255)

                canvas.paste(cropped, (cx - radius, cy - radius), circle_mask)
                draw.ellipse([(cx - radius, cy - radius), (cx + radius, cy + radius)], outline=(*theme["divider"], 220), width=3)
                rendered_pfp = True
        except Exception:
            rendered_pfp = False

    if not rendered_pfp:
        draw.ellipse([(cx - 72, cy - 72), (cx + 72, cy + 72)], fill=(*theme["bg_bot"], 180), outline=(*theme["divider"], 180), width=2)
        # Stylized footballer shoulders & head silhouette fallback
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


def render_form_chart(player_name: str, history: List[dict]) -> io.BytesIO:
    """Renders a dark-themed PNG bar+trend chart of a player's chronological recent matches."""
    width, height = 780, 400
    bg = (18, 22, 36)
    panel = (26, 32, 50)
    grid_col = (48, 58, 84)
    text_col = (240, 244, 252)
    muted_col = (160, 172, 198)
    goal_col = (56, 189, 248)    # cyan-blue
    assist_col = (250, 204, 21)  # gold
    ga_line_col = (167, 139, 250)  # violet

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

    font_title = _load_font(22, bold=True)
    font_sub = _load_font(14, bold=False)
    font_lbl = _load_font(13, bold=True)
    font_val = _load_font(12, bold=True)

    # Outer card panel
    draw.rounded_rectangle([(14, 14), (width - 14, height - 14)], radius=16, fill=panel, outline=grid_col, width=2)

    items = history[-10:]
    draw.text((34, 34), f"RECENT FORM CHART — {player_name.upper()}", font=font_title, fill=text_col, anchor="lm")
    draw.text((34, 60), f"Chronological last {len(items)} match(es) • Oldest → Latest", font=font_sub, fill=muted_col, anchor="lm")

    # Legend (top-right)
    legend = [("Goals", goal_col), ("Assists", assist_col), ("Total G+A", ga_line_col)]
    lx = width - 315
    for label, col in legend:
        draw.rectangle([(lx, 42), (lx + 12, 54)], fill=col)
        draw.text((lx + 18, 48), label, font=font_lbl, fill=text_col, anchor="lm")
        lx += 98

    # Plot box
    left, top, right, bottom = 68, 92, width - 36, height - 66
    max_y = max([max(a["goals"] + a["assists"], 3) for a in items], default=3)

    # Horizontal gridlines
    steps = min(max_y, 6)
    for i in range(steps + 1):
        val = round((max_y * i) / max(steps, 1))
        y = bottom - int(((bottom - top) * i) / max(steps, 1))
        draw.line([(left, y), (right, y)], fill=grid_col, width=1)
        draw.text((left - 10, y), str(val), font=font_val, fill=muted_col, anchor="rm")

    draw.line([(left, top), (left, bottom)], fill=muted_col, width=2)
    draw.line([(left, bottom), (right, bottom)], fill=muted_col, width=2)

    n = max(len(items), 1)
    slot_w = (right - left) / n
    bar_w = max(10, min(26, int(slot_w * 0.28)))

    outcome_colors = {
        "W": (34, 197, 94),
        "L": (239, 68, 68),
        "D": (148, 163, 184),
    }

    line_pts: List[Tuple[int, int]] = []
    for idx, app in enumerate(items):
        cx = int(left + slot_w * (idx + 0.5))
        g = int(app["goals"])
        a = int(app["assists"])
        ga = g + a

        g_h = int((g / max_y) * (bottom - top)) if max_y > 0 else 0
        a_h = int((a / max_y) * (bottom - top)) if max_y > 0 else 0
        ga_y = bottom - (int((ga / max_y) * (bottom - top)) if max_y > 0 else 0)
        line_pts.append((cx, ga_y))

        # Goals bar (left of center)
        if g_h > 0:
            draw.rounded_rectangle(
                [(cx - bar_w - 2, bottom - g_h), (cx - 2, bottom)],
                radius=4,
                fill=goal_col,
            )
            draw.text((cx - bar_w // 2 - 2, bottom - g_h - 10), str(g), font=font_val, fill=goal_col, anchor="mm")

        # Assists bar (right of center)
        if a_h > 0:
            draw.rounded_rectangle(
                [(cx + 2, bottom - a_h), (cx + bar_w + 2, bottom)],
                radius=4,
                fill=assist_col,
            )
            draw.text((cx + bar_w // 2 + 2, bottom - a_h - 10), str(a), font=font_val, fill=assist_col, anchor="mm")

        # Outcome pill badge below X-axis
        out_ch = str(app.get("outcome", "W"))
        pill_col = outcome_colors.get(out_ch, muted_col)
        draw.rounded_rectangle([(cx - 14, bottom + 10), (cx + 14, bottom + 32)], radius=6, fill=pill_col)
        draw.text((cx, bottom + 21), out_ch, font=font_lbl, fill=(15, 23, 42), anchor="mm")
        draw.text((cx, bottom + 43), f"M{idx + 1}", font=font_val, fill=muted_col, anchor="mm")

    if len(line_pts) >= 2:
        draw.line(line_pts, fill=ga_line_col, width=3)
    for pt in line_pts:
        draw.ellipse([(pt[0] - 5, pt[1] - 5), (pt[0] + 5, pt[1] + 5)], fill=ga_line_col, outline=bg, width=2)

    out = io.BytesIO()
    img.save(out, format="PNG")
    out.seek(0)
    return out


def render_compare_chart(
    r1: dict,
    rating1: float,
    r2: dict,
    rating2: float,
) -> io.BytesIO:
    """Renders a dark-themed head-to-head comparison bar chart for two players."""
    metrics = [
        ("Goals", float(r1["goals"]), float(r2["goals"]), False),
        ("Assists", float(r1["assists"]), float(r2["assists"]), False),
        ("G/A", float(r1["ga"]), float(r2["ga"]), False),
        ("Wins", float(r1["wins"]), float(r2["wins"]), False),
        ("Win Rate %", float(r1["win_rate"]), float(r2["win_rate"]), True),
        ("GPG", float(r1["gpg"]), float(r2["gpg"]), True),
        ("GAPG", float(r1["gapg"]), float(r2["gapg"]), True),
        ("Rating", float(rating1), float(rating2), True),
    ]

    width, height = 780, 470
    bg = (18, 22, 36)
    panel = (26, 32, 50)
    grid_col = (48, 58, 84)
    text_col = (240, 244, 252)
    muted_col = (160, 172, 198)
    p1_col = (59, 130, 246)   # vibrant blue
    p2_col = (245, 158, 11)   # vibrant amber

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

    font_title = _load_font(22, bold=True)
    font_hdr = _load_font(16, bold=True)
    font_lbl = _load_font(14, bold=True)
    font_val = _load_font(14, bold=True)

    draw.rounded_rectangle([(14, 14), (width - 14, height - 14)], radius=16, fill=panel, outline=grid_col, width=2)
    p1_name = str(r1["player"])
    p2_name = str(r2["player"])
    draw.text((width // 2, 40), f"{p1_name.upper()}  vs  {p2_name.upper()}", font=font_title, fill=text_col, anchor="mm")

    # Legend centered under title
    draw.rectangle([(width // 2 - 170, 62), (width // 2 - 154, 76)], fill=p1_col)
    draw.text((width // 2 - 146, 69), p1_name, font=font_hdr, fill=p1_col, anchor="lm")
    draw.rectangle([(width // 2 + 30, 62), (width // 2 + 46, 76)], fill=p2_col)
    draw.text((width // 2 + 54, 69), p2_name, font=font_hdr, fill=p2_col, anchor="lm")

    center_x = width // 2
    max_bar_w = 240
    start_y = 108
    row_h = 42

    for idx, (label, v1, v2, is_float) in enumerate(metrics):
        y = start_y + idx * row_h
        max_v = max(v1, v2, 1.0)
        w1 = int((v1 / max_v) * max_bar_w) if v1 > 0 else 4
        w2 = int((v2 / max_v) * max_bar_w) if v2 > 0 else 4

        # Center label pill
        draw.rounded_rectangle([(center_x - 56, y - 13), (center_x + 56, y + 13)], radius=6, fill=(36, 44, 68))
        draw.text((center_x, y), label, font=font_lbl, fill=text_col, anchor="mm")

        # Left bar (Player 1 extends leftwards)
        bar_l_end = center_x - 66
        draw.rounded_rectangle([(bar_l_end - max_bar_w, y - 9), (bar_l_end, y + 9)], radius=5, fill=(34, 41, 62))
        draw.rounded_rectangle([(bar_l_end - w1, y - 9), (bar_l_end, y + 9)], radius=5, fill=p1_col)
        s1 = f"{v1:.1f}" if label in ("Win Rate %", "Rating") else (f"{v1:.2f}" if is_float else f"{int(v1)}")
        draw.text((bar_l_end - max_bar_w - 10, y), s1, font=font_val, fill=p1_col if v1 >= v2 else muted_col, anchor="rm")

        # Right bar (Player 2 extends rightwards)
        bar_r_start = center_x + 66
        draw.rounded_rectangle([(bar_r_start, y - 9), (bar_r_start + max_bar_w, y + 9)], radius=5, fill=(34, 41, 62))
        draw.rounded_rectangle([(bar_r_start, y - 9), (bar_r_start + w2, y + 9)], radius=5, fill=p2_col)
        s2 = f"{v2:.1f}" if label in ("Win Rate %", "Rating") else (f"{v2:.2f}" if is_float else f"{int(v2)}")
        draw.text((bar_r_start + max_bar_w + 10, y), s2, font=font_val, fill=p2_col if v2 >= v1 else muted_col, anchor="lm")

    out = io.BytesIO()
    img.save(out, format="PNG")
    out.seek(0)
    return out
