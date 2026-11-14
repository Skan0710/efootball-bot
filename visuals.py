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

    # Stylized Avatar / Crest Emblem or Player PFP on center-right (Clean Square Display)
    cx, cy = 266, 198
    box_size = 172
    half_box = box_size // 2
    x0, y0 = cx - half_box, cy - half_box
    x1, y1 = cx + half_box, cy + half_box

    # Outer metallic square border & background frame
    draw.rectangle(
        [(x0 - 5, y0 - 5), (x1 + 5, y1 + 5)],
        fill=(*theme["accent_soft"], 70),
        outline=(*theme["accent"], 220),
        width=3,
    )

    pfp_path = _find_player_pfp(str(card["player"]))
    rendered_pfp = False

    if pfp_path:
        try:
            with Image.open(pfp_path) as pfp_img:
                pfp_rgba = pfp_img.convert("RGBA")
                w, h = pfp_rgba.size

                # Proportionally scale down if larger than frame; do not enlarge or zoom if smaller
                scale = min(box_size / max(w, 1), box_size / max(h, 1))
                if w <= box_size and h <= box_size:
                    scale = 1.0  # Prevent enlargement/zooming
                else:
                    scale = min(1.0, scale)

                new_w = max(1, int(round(w * scale)))
                new_h = max(1, int(round(h * scale)))
                resized = pfp_rgba.resize((new_w, new_h), Image.Resampling.LANCZOS)

                # Inner square frame fill
                draw.rectangle([(x0, y0), (x1, y1)], fill=(*theme["bg_bot"], 240))

                # Center the fitted image perfectly inside the square without cropping or zooming
                paste_x = x0 + (box_size - new_w) // 2
                paste_y = y0 + (box_size - new_h) // 2
                canvas.paste(resized, (paste_x, paste_y), mask=resized)

                # Inner metallic frame outline
                draw.rectangle([(x0, y0), (x1, y1)], outline=(*theme["divider"], 220), width=2)
                rendered_pfp = True
        except Exception:
            rendered_pfp = False

    if not rendered_pfp:
        draw.rectangle([(x0, y0), (x1, y1)], fill=(*theme["bg_bot"], 180), outline=(*theme["divider"], 180), width=2)
        # Stylized footballer shoulders & head silhouette fallback
        draw.ellipse([(cx - 24, cy - 44), (cx + 24, cy + 4)], fill=theme["accent"])
        draw.chord([(cx - 50, cy + 6), (cx + 50, cy + 82)], start=180, end=360, fill=theme["accent"])
        initials = "".join(ch for ch in str(card["player"])[:2]).upper() or "FC"
        draw.text((cx, cy + 36), initials, font=_load_font(22, bold=True), fill=theme["bg_bot"], anchor="mm")

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


def render_match_summary_card(
    match: MatchRecord,
    home_team: str,
    away_team: str,
    home_score: int,
    away_score: int,
    result: MatchResult,
) -> io.BytesIO:
    """Renders a visual matchday score banner card with scores, result badge, and goalscorers."""
    width, height = 760, 360
    bg = (15, 20, 35)
    card_bg = (22, 28, 48)
    gold = (236, 193, 92)
    cyan = (56, 189, 248)
    text_white = (248, 250, 252)
    text_muted = (160, 172, 198)

    img = Image.new("RGBA", (width, height), bg)
    draw = ImageDraw.Draw(img)

    # Rounded container
    draw.rounded_rectangle([(14, 14), (width - 14, height - 14)], radius=18, fill=card_bg, outline=(42, 54, 88), width=2)

    # Result Banner at Top
    res_label = "HOME WIN" if result in (MatchResult.HOME_WIN, MatchResult.WIN) else ("AWAY WIN" if result in (MatchResult.AWAY_WIN, MatchResult.LOSS) else "DRAW")
    res_col = (34, 197, 94) if result != MatchResult.DRAW else (234, 179, 8)
    draw.rounded_rectangle([(width // 2 - 70, 22), (width // 2 + 70, 48)], radius=13, fill=(*res_col, 40), outline=res_col, width=2)
    draw.text((width // 2, 35), res_label, font=_load_font(13, bold=True), fill=res_col, anchor="mm")

    # Team Names
    font_team = _load_font(26, bold=True)
    draw.text((width // 4, 90), home_team.upper(), font=font_team, fill=text_white, anchor="mm")
    draw.text((width * 3 // 4, 90), away_team.upper(), font=font_team, fill=text_white, anchor="mm")

    # Center Scoreboard
    draw.rounded_rectangle([(width // 2 - 75, 70), (width // 2 + 75, 140)], radius=12, fill=(12, 16, 28), outline=(50, 64, 102), width=2)
    score_text = f"{home_score} - {away_score}"
    draw.text((width // 2, 105), score_text, font=_load_font(42, bold=True), fill=gold, anchor="mm")

    # Divider line
    draw.line([(35, 158), (width - 35, 158)], fill=(38, 48, 78), width=1)

    # Home and Away Player Performance lists
    home_players = [p for p in match.player_stats if p.result in (MatchResult.WIN, MatchResult.HOME_WIN)] if result in (MatchResult.HOME_WIN, MatchResult.WIN) else [p for p in match.player_stats if p.result in (MatchResult.LOSS, MatchResult.AWAY_WIN)]
    away_players = [p for p in match.player_stats if p not in home_players]

    font_stat = _load_font(14, bold=False)
    font_stat_b = _load_font(14, bold=True)

    def _draw_side_players(players: List[PlayerStat], start_x: int, align_right: bool = False):
        y = 180
        for p in players[:4]:
            pfp_path = _find_player_pfp(p.player)
            # Draw player avatar box (Square display, fitted without crop/zoom)
            box_x = start_x - 12 if align_right else start_x + 12
            draw.rounded_rectangle([(box_x - 10, y - 2), (box_x + 10, y + 18)], radius=3, fill=(38, 50, 80), outline=cyan, width=1)
            if pfp_path:
                try:
                    with Image.open(pfp_path) as im:
                        im_rgba = im.convert("RGBA")
                        iw, ih = im_rgba.size
                        scale = min(20 / max(iw, 1), 20 / max(ih, 1))
                        nw, nh = max(1, int(round(iw * scale))), max(1, int(round(ih * scale)))
                        resized = im_rgba.resize((nw, nh), Image.Resampling.LANCZOS)
                        px = (box_x - 10) + (20 - nw) // 2
                        py_pos = (y - 2) + (20 - nh) // 2
                        img.paste(resized, (px, py_pos), mask=resized)
                except Exception:
                    pass

            text_x = start_x - 30 if align_right else start_x + 30
            text_anc = "rm" if align_right else "lm"
            stat_txt = f"{p.player} ({p.goals}G, {p.assists}A)"
            draw.text((text_x, y + 8), stat_txt, font=font_stat_b if (p.goals or p.assists) else font_stat, fill=text_white if (p.goals or p.assists) else text_muted, anchor=text_anc)
            y += 32

    _draw_side_players(home_players, width // 4, align_right=False)
    _draw_side_players(away_players, width * 3 // 4, align_right=True)

    # Footer note
    draw.text((width // 2, height - 28), f"ID: {match.match_id} • {match.date}", font=_load_font(12, bold=False), fill=text_muted, anchor="mm")

    out = io.BytesIO()
    img.save(out, format="PNG")
    out.seek(0)
    return out


def render_tierlist_card(tiers: Dict[str, List[dict]]) -> io.BytesIO:
    """Renders an S/A/B/C tier list graphic of server players."""
    width, height = 780, 520
    bg = (16, 20, 34)
    panel = (24, 30, 50)

    img = Image.new("RGBA", (width, height), bg)
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle([(14, 14), (width - 14, height - 14)], radius=18, fill=panel, outline=(42, 54, 88), width=2)
    draw.text((width // 2, 40), "SERVER PLAYER TIER LIST", font=_load_font(22, bold=True), fill=(248, 250, 252), anchor="mm")
    draw.text((width // 2, 64), "Calibrated by Career OVR & Matchday Form", font=_load_font(13, bold=False), fill=(160, 172, 198), anchor="mm")

    tier_configs = [
        ("S", (239, 68, 68), "ELITE / TOTY (88+ OVR)"),
        ("A", (249, 115, 22), "GOLD RARE (78-87 OVR)"),
        ("B", (59, 130, 246), "SILVER STAR (70-77 OVR)"),
        ("C", (148, 163, 184), "DEVELOPING (<70 OVR)"),
    ]

    start_y = 96
    row_h = 92
    box_w = 68

    for idx, (letter, col, label) in enumerate(tier_configs):
        y = start_y + idx * (row_h + 8)
        # Tier letter block
        draw.rounded_rectangle([(30, y), (30 + box_w, y + row_h)], radius=10, fill=col)
        draw.text((30 + box_w // 2, y + row_h // 2), letter, font=_load_font(32, bold=True), fill=(255, 255, 255), anchor="mm")

        # Tier row background
        draw.rounded_rectangle([(30 + box_w + 10, y), (width - 30, y + row_h)], radius=10, fill=(18, 22, 38), outline=(42, 52, 82), width=1)

        members = tiers.get(letter, [])
        if not members:
            draw.text((30 + box_w + 24, y + row_h // 2), f"No players in this tier ({label})", font=_load_font(14, bold=False), fill=(120, 132, 158), anchor="lm")
            continue

        item_x = 30 + box_w + 24
        for m in members[:6]:
            pfp_path = _find_player_pfp(m["player"])
            cx, cy = item_x + 22, y + 34
            draw.rounded_rectangle([(cx - 20, cy - 20), (cx + 20, cy + 20)], radius=6, fill=(34, 44, 70), outline=col, width=2)
            if pfp_path:
                try:
                    with Image.open(pfp_path) as im:
                        im_rgba = im.convert("RGBA")
                        iw, ih = im_rgba.size
                        scale = min(38 / max(iw, 1), 38 / max(ih, 1))
                        nw, nh = max(1, int(round(iw * scale))), max(1, int(round(ih * scale)))
                        resized = im_rgba.resize((nw, nh), Image.Resampling.LANCZOS)
                        px = (cx - 19) + (38 - nw) // 2
                        py_pos = (cy - 19) + (38 - nh) // 2
                        img.paste(resized, (px, py_pos), mask=resized)
                except Exception:
                    pass
            # OVR pill badge on top of avatar
            draw.rounded_rectangle([(cx - 16, cy + 12), (cx + 16, cy + 26)], radius=4, fill=(12, 16, 26), outline=col, width=1)
            draw.text((cx, cy + 19), str(m["ovr"]), font=_load_font(10, bold=True), fill=(250, 204, 21), anchor="mm")

            draw.text((cx, y + 74), m["player"], font=_load_font(12, bold=True), fill=(240, 244, 252), anchor="mm")
            item_x += 88

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
        ("Card OVR", float(rating1), float(rating2), False),
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


def render_duo_card(duo: dict) -> io.BytesIO:
    """Renders a dynamic EA FC style dual chemistry card for two teammates."""
    width, height = 860, 480
    bg = (14, 18, 30)
    card_bg = (22, 28, 46)
    accent_gold = (236, 193, 92)
    syn_col = duo.get("synergy_color", (45, 212, 191))
    text_white = (248, 250, 252)
    text_muted = (160, 172, 198)

    img = Image.new("RGBA", (width, height), bg)
    draw = ImageDraw.Draw(img)

    # Outer border
    draw.rounded_rectangle([(14, 14), (width - 14, height - 14)], radius=20, fill=card_bg, outline=(42, 54, 88), width=3)

    # Header banner
    draw.text((width // 2, 42), "⚡ DYNAMIC DUOS ⚡", font=_load_font(24, bold=True), fill=accent_gold, anchor="mm")
    title_text = f"{duo['title'].upper()} • {duo['synergy_tier']} SYNERGY"
    draw.text((width // 2, 68), title_text, font=_load_font(13, bold=True), fill=syn_col, anchor="mm")

    # Helper to render a player side-card panel
    def _draw_player_panel(card: dict, x_center: int):
        pw, ph = 260, 350
        px0 = x_center - pw // 2
        py0 = 100
        px1 = px0 + pw
        py1 = py0 + ph

        # Sub-panel
        draw.rounded_rectangle([(px0, py0), (px1, py1)], radius=16, fill=(18, 24, 40), outline=accent_gold, width=2)

        # Rating & Position
        draw.text((px0 + 44, py0 + 38), str(card["rating"]), font=_load_font(38, bold=True), fill=text_white, anchor="mm")
        draw.line([(px0 + 24, py0 + 60), (px0 + 64, py0 + 60)], fill=accent_gold, width=2)
        draw.text((px0 + 44, py0 + 78), str(card["position"]), font=_load_font(18, bold=True), fill=accent_gold, anchor="mm")

        # PFP Square
        pfp_cx, pfp_cy = px0 + 170, py0 + 80
        pfp_size = 96
        half_p = pfp_size // 2
        draw.rectangle([(pfp_cx - half_p - 3, pfp_cy - half_p - 3), (pfp_cx + half_p + 3, pfp_cy + half_p + 3)], fill=(28, 36, 60), outline=(*syn_col, 220), width=2)

        pfp_path = _find_player_pfp(str(card["player"]))
        rendered_pfp = False
        if pfp_path:
            try:
                with Image.open(pfp_path) as pfp_img:
                    pfp_rgba = pfp_img.convert("RGBA")
                    w, h = pfp_rgba.size
                    scale = min(pfp_size / max(w, 1), pfp_size / max(h, 1))
                    if w <= pfp_size and h <= pfp_size:
                        scale = 1.0
                    else:
                        scale = min(1.0, scale)
                    nw = max(1, int(round(w * scale)))
                    nh = max(1, int(round(h * scale)))
                    resized = pfp_rgba.resize((nw, nh), Image.Resampling.LANCZOS)
                    draw.rectangle([(pfp_cx - half_p, pfp_cy - half_p), (pfp_cx + half_p, pfp_cy + half_p)], fill=(12, 16, 26))
                    img.paste(resized, (pfp_cx - half_p + (pfp_size - nw) // 2, pfp_cy - half_p + (pfp_size - nh) // 2), mask=resized)
                    rendered_pfp = True
            except Exception:
                rendered_pfp = False

        if not rendered_pfp:
            draw.rectangle([(pfp_cx - half_p, pfp_cy - half_p), (pfp_cx + half_p, pfp_cy + half_p)], fill=(12, 16, 26))
            draw.text((pfp_cx, pfp_cy), str(card["player"])[:2].upper(), font=_load_font(24, bold=True), fill=accent_gold, anchor="mm")

        # Name line
        name_y = py0 + 160
        draw.line([(px0 + 20, name_y), (px1 - 20, name_y)], fill=(42, 54, 88), width=1)
        draw.text((x_center, name_y + 20), str(card["player"]).upper(), font=_load_font(20, bold=True), fill=text_white, anchor="mm")
        draw.line([(px0 + 20, name_y + 40), (px1 - 20, name_y + 40)], fill=(42, 54, 88), width=1)

        # 6 Stats grid
        stats = [
            (card["pace"], "PAC"),
            (card["shooting"], "SHO"),
            (card["passing"], "PAS"),
            (card["dribbling"], "DRI"),
            (card["defending"], "DEF"),
            (card["physical"], "PHY"),
        ]
        s_y = name_y + 60
        for i in range(3):
            v_l, l_l = stats[i]
            draw.text((px0 + 44, s_y + i * 26), str(v_l), font=_load_font(15, bold=True), fill=text_white, anchor="lm")
            draw.text((px0 + 80, s_y + i * 26), l_l, font=_load_font(13, bold=False), fill=text_muted, anchor="lm")
            v_r, l_r = stats[i + 3]
            draw.text((px0 + 145, s_y + i * 26), str(v_r), font=_load_font(15, bold=True), fill=text_white, anchor="lm")
            draw.text((px0 + 182, s_y + i * 26), l_r, font=_load_font(13, bold=False), fill=text_muted, anchor="lm")

    _draw_player_panel(duo["card1"], 175)
    _draw_player_panel(duo["card2"], width - 175)

    cx = width // 2
    draw.line([(290, 260), (cx - 70, 260)], fill=syn_col, width=3)
    draw.line([(cx + 70, 260), (width - 290, 260)], fill=syn_col, width=3)

    draw.rounded_rectangle([(cx - 80, 120), (cx + 80, 420)], radius=16, fill=(16, 22, 38), outline=syn_col, width=2)

    draw.ellipse([(cx - 40, 140), (cx + 40, 220)], fill=(24, 32, 54), outline=accent_gold, width=2)
    draw.text((cx, 172), f"{duo['win_rate']:.0f}%", font=_load_font(22, bold=True), fill=accent_gold, anchor="mm")
    draw.text((cx, 194), "WIN RATE", font=_load_font(9, bold=True), fill=text_muted, anchor="mm")

    cy_list = [240, 275, 310, 345, 380]
    metrics = [
        ("Matches", f"{duo['matches_together']}"),
        ("Wins", f"{duo['wins_together']}"),
        ("Combined G/A", f"{duo['combined_ga']}"),
        ("Clean Sheets", f"{duo['clean_sheets_together']}"),
        ("Avg OVR", f"{duo['avg_ovr']}"),
    ]
    for y_pos, (lbl, val) in zip(cy_list, metrics):
        draw.text((cx - 65, y_pos), lbl, font=_load_font(11, bold=False), fill=text_muted, anchor="lm")
        draw.text((cx + 65, y_pos), val, font=_load_font(13, bold=True), fill=text_white, anchor="rm")
        draw.line([(cx - 65, y_pos + 14), (cx + 65, y_pos + 14)], fill=(36, 46, 72), width=1)

    out = io.BytesIO()
    img.save(out, format="PNG")
    out.seek(0)
    return out


def render_squad_pitch(lineup: dict, title: str = "SERVER STARTING LINEUP") -> io.BytesIO:
    """Renders a tactical pitch visualizer showing starting lineup positioning and player badges."""
    width, height = 820, 620
    img = Image.new("RGBA", (width, height), (15, 20, 32))
    draw = ImageDraw.Draw(img)

    px0, py0 = 30, 80
    px1, py1 = width - 30, height - 25
    pw = px1 - px0
    ph = py1 - py0

    stripe_h = 36
    num_stripes = (ph // stripe_h) + 1
    for i in range(num_stripes):
        sy0 = py0 + i * stripe_h
        sy1 = min(py1, sy0 + stripe_h)
        col = (24, 76, 42) if (i % 2 == 0) else (20, 66, 36)
        draw.rectangle([(px0, sy0), (px1, sy1)], fill=col)

    white_line = (255, 255, 255, 175)
    draw.rectangle([(px0, py0), (px1, py1)], outline=white_line, width=2)

    mid_y = (py0 + py1) // 2
    draw.line([(px0, mid_y), (px1, mid_y)], fill=white_line, width=2)

    cx = (px0 + px1) // 2
    draw.ellipse([(cx - 55, mid_y - 55), (cx + 55, mid_y + 55)], outline=white_line, width=2)
    draw.ellipse([(cx - 4, mid_y - 4), (cx + 4, mid_y + 4)], fill=white_line)

    draw.rectangle([(cx - 100, py0), (cx + 100, py0 + 65)], outline=white_line, width=2)
    draw.rectangle([(cx - 50, py0), (cx + 50, py0 + 25)], outline=white_line, width=2)

    draw.rectangle([(cx - 100, py1 - 65), (cx + 100, py1)], outline=white_line, width=2)
    draw.rectangle([(cx - 50, py1 - 25), (cx + 50, py1)], outline=white_line, width=2)

    draw.rounded_rectangle([(px0, 14), (px1, 68)], radius=12, fill=(18, 24, 40), outline=(42, 56, 88), width=2)
    draw.text((width // 2, 33), title.upper(), font=_load_font(20, bold=True), fill=(236, 193, 92), anchor="mm")
    sub_title = f"Squad Average: {lineup.get('avg_ovr', 75)} OVR • {lineup.get('squad_size', 0)} Active Teammates"
    draw.text((width // 2, 54), sub_title, font=_load_font(12, bold=False), fill=(160, 172, 198), anchor="mm")

    def _draw_player_node(player_card: dict, node_x: int, node_y: int):
        box_s = 48
        half_s = box_s // 2

        draw.rectangle(
            [(node_x - half_s - 2, node_y - half_s - 2), (node_x + half_s + 2, node_y + half_s + 2)],
            fill=(18, 24, 40),
            outline=(236, 193, 92),
            width=2,
        )

        pfp_path = _find_player_pfp(str(player_card["player"]))
        rendered = False
        if pfp_path:
            try:
                with Image.open(pfp_path) as p_img:
                    p_rgba = p_img.convert("RGBA")
                    w, h = p_rgba.size
                    scale = min(box_s / max(w, 1), box_s / max(h, 1))
                    nw = max(1, int(round(w * scale)))
                    nh = max(1, int(round(h * scale)))
                    resized = p_rgba.resize((nw, nh), Image.Resampling.LANCZOS)
                    draw.rectangle([(node_x - half_s, node_y - half_s), (node_x + half_s, node_y + half_s)], fill=(10, 14, 24))
                    img.paste(resized, (node_x - half_s + (box_s - nw) // 2, node_y - half_s + (box_s - nh) // 2), mask=resized)
                    rendered = True
            except Exception:
                rendered = False

        if not rendered:
            draw.rectangle([(node_x - half_s, node_y - half_s), (node_x + half_s, node_y + half_s)], fill=(14, 20, 34))
            draw.text((node_x, node_y), str(player_card["player"])[:2].upper(), font=_load_font(18, bold=True), fill=(236, 193, 92), anchor="mm")

        draw.rounded_rectangle([(node_x - half_s - 14, node_y - half_s - 8), (node_x - half_s + 8, node_y - half_s + 10)], radius=4, fill=(15, 23, 42), outline=(56, 189, 248), width=1)
        draw.text((node_x - half_s - 3, node_y - half_s + 1), str(player_card["position"]), font=_load_font(9, bold=True), fill=(56, 189, 248), anchor="mm")

        draw.rounded_rectangle([(node_x + half_s - 8, node_y - half_s - 8), (node_x + half_s + 14, node_y - half_s + 10)], radius=4, fill=(15, 23, 42), outline=(236, 193, 92), width=1)
        draw.text((node_x + half_s + 3, node_y - half_s + 1), str(player_card["rating"]), font=_load_font(10, bold=True), fill=(250, 204, 21), anchor="mm")

        p_name = str(player_card["player"])
        nw_pill = max(60, len(p_name) * 8 + 14)
        draw.rounded_rectangle([(node_x - nw_pill // 2, node_y + half_s + 4), (node_x + nw_pill // 2, node_y + half_s + 20)], radius=5, fill=(10, 14, 24), outline=(42, 54, 88), width=1)
        draw.text((node_x, node_y + half_s + 12), p_name, font=_load_font(11, bold=True), fill=(248, 250, 252), anchor="mm")

    attackers = lineup.get("attackers", [])
    midfielders = lineup.get("midfielders", [])
    defenders = lineup.get("defenders", [])

    def _distribute_row(players: List[dict], y_row: int):
        count = len(players)
        if count == 0:
            return
        step = pw // (count + 1)
        for idx, p in enumerate(players, start=1):
            _draw_player_node(p, px0 + idx * step, y_row)

    _distribute_row(attackers, py0 + int(ph * 0.22))
    _distribute_row(midfielders, py0 + int(ph * 0.52))
    _distribute_row(defenders, py0 + int(ph * 0.80))

    out = io.BytesIO()
    img.save(out, format="PNG")
    out.seek(0)
    return out
