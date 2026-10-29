"""Optical Character Recognition pipeline for eFootball post-match screenshots.

Extracts player names, goals, and assists using layout-aware region matching,
OpenCV image preprocessing, and Tesseract OCR with safe failure handling.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image
import pytesseract

from models import OCRPlayerCandidate

LOGGER = logging.getLogger("efootball_bot.ocr")


@dataclass
class LayoutConfig:
    """Configurable layout ratios for eFootball match details screen."""

    player_names_top: float = 0.05
    player_names_bottom: float = 0.25
    stats_table_top: float = 0.20
    stats_table_bottom: float = 0.88
    player_cols_left: float = 0.20
    player_cols_right: float = 0.90


@dataclass
class OCRResult:
    """Structured response from the screenshot OCR pipeline."""

    success: bool
    candidates: List[OCRPlayerCandidate] = field(default_factory=list)
    error_reason: Optional[str] = None
    raw_text: str = ""
    diagnostics: Dict[str, Any] = field(default_factory=dict)


def validate_image_file(
    image_path: Path | str, max_bytes: int = 10 * 1024 * 1024
) -> Tuple[bool, Optional[str]]:
    """Validates that a file is a readable, within-size, non-corrupted image."""
    path = Path(image_path)
    if not path.exists():
        return False, f"File does not exist: {path}"

    try:
        file_size = path.stat().st_size
        if file_size <= 0:
            return False, "Image file is empty (0 bytes)."
        if file_size > max_bytes:
            mb_limit = max_bytes / (1024 * 1024)
            return False, f"Image size exceeds {mb_limit:.1f} MB limit."

        # Verify decoding with Pillow
        with Image.open(path) as img:
            img.verify()
            format_name = (img.format or "").upper()
            if format_name not in ("PNG", "JPEG", "JPG", "WEBP"):
                return False, f"Unsupported image format: {format_name}. Supported: PNG, JPEG, WEBP."

            width, height = img.size
            if width < 300 or height < 200:
                return False, f"Image resolution too small ({width}x{height}) for reliable OCR."

        return True, None
    except Exception as exc:
        LOGGER.warning("Image validation error for %s: %s", path, exc)
        return False, f"Corrupted or invalid image file: {exc}"


def preprocess_image_for_ocr(bgr_image: np.ndarray) -> np.ndarray:
    """Preprocesses image using OpenCV to improve text contrast and OCR legibility."""
    gray = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2GRAY)
    target_width = 1920
    h, w = gray.shape
    if w != target_width:
        scaling_factor = target_width / float(w)
        target_height = int(h * scaling_factor)
        gray = cv2.resize(gray, (target_width, target_height), interpolation=cv2.INTER_CUBIC)

    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    denoised = cv2.bilateralFilter(enhanced, d=5, sigmaColor=75, sigmaSpace=75)
    thresh = cv2.adaptiveThreshold(
        denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, 4
    )
    return thresh


def _configure_tesseract(tesseract_cmd: Optional[str] = None) -> None:
    """Sets pytesseract binary path if specified or present in standard location."""
    if tesseract_cmd and Path(tesseract_cmd).exists():
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    elif not pytesseract.pytesseract.tesseract_cmd or pytesseract.pytesseract.tesseract_cmd == "tesseract":
        win_default = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        if win_default.exists():
            pytesseract.pytesseract.tesseract_cmd = str(win_default)


def parse_player_columns_from_data(
    data: Dict[str, list],
    x_min: int,
    x_max: int,
    y_min: int,
    y_max: int,
) -> List[Tuple[str, int, int]]:
    """Identifies player names and their horizontal column centers."""
    n_boxes = len(data.get("text", []))
    candidates: List[Tuple[str, int, int]] = []
    excluded = {"next", "opposition", "match", "details", "team", "stats", "home", "away", "xi"}

    for i in range(n_boxes):
        text = str(data["text"][i]).strip()
        conf = int(data["conf"][i]) if "conf" in data else 0

        if not text or len(text) < 2 or conf < 30:
            continue

        cleaned = re.sub(r"[^\w\-\.]", "", text)
        if not cleaned or cleaned.lower() in excluded or cleaned.isdigit():
            continue

        x = int(data["left"][i])
        y = int(data["top"][i])
        w = int(data["width"][i])

        if x_min <= x <= x_max and y_min <= y <= y_max:
            center_x = x + (w // 2)
            candidates.append((cleaned, center_x, w))

    candidates.sort(key=lambda c: c[1])

    merged: List[Tuple[str, int, int]] = []
    for name, cx, w in candidates:
        if not merged:
            merged.append((name, cx, w))
        else:
            prev_name, prev_cx, prev_w = merged[-1]
            if abs(cx - prev_cx) < 120:
                merged[-1] = (f"{prev_name} {name}", (prev_cx + cx) // 2, prev_w + w)
            else:
                merged.append((name, cx, w))

    return merged


def find_row_numbers_by_columns(
    data: Dict[str, list],
    row_y_min: int,
    row_y_max: int,
    column_centers: List[int],
    max_col_dist: int = 150,
) -> Dict[int, int]:
    """Matches detected numbers in a horizontal row strip to closest player column center."""
    n_boxes = len(data.get("text", []))
    col_values: Dict[int, int] = {}

    for i in range(n_boxes):
        text = str(data["text"][i]).strip()
        m = re.match(r"^(\d+)", text)
        if not m:
            continue
        val = int(m.group(1))

        x = int(data["left"][i])
        y = int(data["top"][i])
        w = int(data["width"][i])

        if row_y_min <= y <= row_y_max:
            item_center_x = x + (w // 2)
            best_col = -1
            best_dist = float("inf")
            for col_idx, col_cx in enumerate(column_centers):
                dist = abs(item_center_x - col_cx)
                if dist < best_dist and dist <= max_col_dist:
                    best_dist = dist
                    best_col = col_idx

            if best_col >= 0:
                col_values[best_col] = val

    return col_values


def _extract_via_targeted_regions(
    bgr: np.ndarray,
    h: int,
    w: int,
) -> Optional[OCRResult]:
    """Extracts player stats by isolating the upper player bar and table rows."""
    # 1. Player Bar: y = 0.05 to 0.23
    pb_crop = bgr[int(h * 0.05):int(h * 0.23), :]
    pb_gray = cv2.cvtColor(pb_crop, cv2.COLOR_BGR2GRAY)
    pb_2x = cv2.resize(pb_gray, (0, 0), fx=2, fy=2, interpolation=cv2.INTER_CUBIC)

    try:
        p_data = pytesseract.image_to_data(pb_2x, output_type=pytesseract.Output.DICT, config="--psm 6")
    except Exception:
        return None

    excluded_words = {"next", "opposition", "match", "details", "team", "stats", "home", "away", "xi", "efootball"}
    players: List[Tuple[str, int]] = []
    scaled_w = w * 2

    for i in range(len(p_data.get("text", []))):
        t = str(p_data["text"][i]).strip()
        c = int(p_data["conf"][i]) if "conf" in p_data else 0
        if len(t) >= 3 and c > 35:
            clean = re.sub(r"[^\w\-\.]", "", t)
            if len(clean) >= 3 and clean.lower() not in excluded_words and not clean.isdigit():
                cx = int(p_data["left"][i]) + (int(p_data["width"][i]) // 2)
                # Ensure it's in the central band (avoid left logos and right edge bars)
                if int(scaled_w * 0.18) <= cx <= int(scaled_w * 0.90):
                    players.append((clean, cx))

    # Deduplicate/merge very close horizontal tokens
    merged_players: List[Tuple[str, int]] = []
    for name, cx in sorted(players, key=lambda p: p[1]):
        if not merged_players:
            merged_players.append((name, cx))
        else:
            prev_name, prev_cx = merged_players[-1]
            if abs(cx - prev_cx) < 140:
                merged_players[-1] = (f"{prev_name} {name}", (prev_cx + cx) // 2)
            else:
                merged_players.append((name, cx))

    if not merged_players:
        return None

    # 2. Table Area: Multi-offset search
    best_t_data: Optional[Dict[str, list]] = None
    for offset in [0.22, 0.23, 0.21, 0.24, 0.20, 0.25]:
        ta = bgr[int(h * offset):int(h * 0.86), :]
        ta_gray = cv2.cvtColor(ta, cv2.COLOR_BGR2GRAY)
        ta_2x = cv2.resize(ta_gray, (0, 0), fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        try:
            t_data = pytesseract.image_to_data(ta_2x, output_type=pytesseract.Output.DICT, config="--psm 6")
        except Exception:
            continue

        texts = [str(t_data["text"][j]).strip().lower() for j in range(len(t_data.get("text", [])))]
        has_g = any("goal" in txt for txt in texts)
        has_a = any("assist" in txt for txt in texts)
        if has_g and has_a:
            best_t_data = t_data
            break
        elif has_g and best_t_data is None:
            best_t_data = t_data

    if not best_t_data:
        return None

    goals_y: Optional[int] = None
    assists_y: Optional[int] = None

    for i in range(len(best_t_data.get("text", []))):
        t = str(best_t_data["text"][i]).strip().lower()
        y = int(best_t_data["top"][i])
        if "goal" in t and goals_y is None:
            goals_y = y
        if "assist" in t and assists_y is None:
            assists_y = y

    if goals_y is None and assists_y is None:
        return None

    def get_row_numbers(target_y: Optional[int], tol: int = 50) -> List[Tuple[int, int]]:
        nums: List[Tuple[int, int]] = []
        if target_y is None:
            return nums
        for j in range(len(best_t_data.get("text", []))):
            t = str(best_t_data["text"][j]).strip()
            y = int(best_t_data["top"][j])
            x = int(best_t_data["left"][j])
            w_b = int(best_t_data["width"][j])
            cx = x + (w_b // 2)
            m = re.match(r"^(\d+)", t)
            if abs(y - target_y) <= tol and m:
                nums.append((int(m.group(1)), cx))
        return nums

    g_nums = get_row_numbers(goals_y)
    a_nums = get_row_numbers(assists_y)

    candidates: List[OCRPlayerCandidate] = []
    for name, pcx in merged_players:
        g = min(g_nums, key=lambda it: abs(it[1] - pcx))[0] if g_nums else 0
        a = min(a_nums, key=lambda it: abs(it[1] - pcx))[0] if a_nums else 0
        candidates.append(OCRPlayerCandidate(player=name, goals=g, assists=a, confidence_note="Targeted Crop"))

    return OCRResult(
        success=True,
        candidates=candidates,
        diagnostics={
            "method": "targeted_regions",
            "detected_players": [p[0] for p in merged_players],
            "goals_detected": [gn[0] for gn in g_nums],
            "assists_detected": [an[0] for an in a_nums],
        },
    )


def extract_player_stats_from_screenshot(
    image_path: Path | str,
    tesseract_cmd: Optional[str] = None,
    layout: Optional[LayoutConfig] = None,
    diagnostic: bool = False,
) -> OCRResult:
    """Performs full layout-aware extraction of player statistics from screenshot.

    Does NOT save data. Returns structured OCRResult with candidates and diagnostics.
    """
    path = Path(image_path)
    valid, err = validate_image_file(path)
    if not valid:
        return OCRResult(success=False, error_reason=err or "Invalid image file")

    _configure_tesseract(tesseract_cmd)
    cfg = layout or LayoutConfig()

    bgr = cv2.imread(str(path))
    if bgr is None:
        return OCRResult(success=False, error_reason="OpenCV could not decode image file.")

    height, width, _ = bgr.shape

    # 1. First try high-accuracy targeted regions (matches real eFootball mobile/console Details screens)
    try:
        targeted_res = _extract_via_targeted_regions(bgr, height, width)
        if targeted_res and targeted_res.success and len(targeted_res.candidates) > 0:
            LOGGER.info("Successfully extracted %d players via targeted regions", len(targeted_res.candidates))
            return targeted_res
    except Exception as exc:
        LOGGER.warning("Targeted extraction encountered error, attempting fallback: %s", exc)

    # 2. Fallback to full-image preprocessing and column matching
    try:
        processed = preprocess_image_for_ocr(bgr)
    except Exception as exc:
        LOGGER.error("Image preprocessing error: %s", exc)
        return OCRResult(success=False, error_reason=f"Image preprocessing failed: {exc}")

    proc_h, proc_w = processed.shape
    p_ymin = int(proc_h * cfg.player_names_top)
    p_ymax = int(proc_h * cfg.player_names_bottom)
    s_ymin = int(proc_h * cfg.stats_table_top)
    s_ymax = int(proc_h * cfg.stats_table_bottom)
    x_min = int(proc_w * cfg.player_cols_left)
    x_max = int(proc_w * cfg.player_cols_right)

    diagnostics: Dict[str, Any] = {
        "image_size": f"{width}x{height}",
        "processed_size": f"{proc_w}x{proc_h}",
        "regions": {
            "player_band": (x_min, p_ymin, x_max, p_ymax),
            "stats_band": (0, s_ymin, proc_w, s_ymax),
        },
    }

    try:
        ocr_data = pytesseract.image_to_data(
            processed,
            output_type=pytesseract.Output.DICT,
            config="--psm 11",
        )
        raw_full_text = pytesseract.image_to_string(processed)
        diagnostics["raw_full_text_sample"] = raw_full_text[:500]
    except pytesseract.TesseractNotFoundError:
        return OCRResult(
            success=False,
            error_reason=(
                "Tesseract OCR executable was not found. Please verify Tesseract is installed "
                "or specify TESSERACT_CMD in your .env file."
            ),
            diagnostics=diagnostics,
        )
    except Exception as exc:
        LOGGER.warning("Tesseract execution failure: %s", exc)
        return OCRResult(
            success=False,
            error_reason=f"Tesseract OCR processing failed: {exc}",
            diagnostics=diagnostics,
        )

    player_cols = parse_player_columns_from_data(ocr_data, x_min, x_max, p_ymin, p_ymax)
    diagnostics["detected_player_columns"] = [
        {"player": p[0], "center_x": p[1]} for p in player_cols
    ]

    if not player_cols:
        return OCRResult(
            success=False,
            error_reason=(
                "Could not detect player name columns in screenshot. Please ensure the screenshot "
                "shows the eFootball co-op post-match Details screen."
            ),
            raw_text=raw_full_text,
            diagnostics=diagnostics,
        )

    column_centers = [p[1] for p in player_cols]
    n_boxes = len(ocr_data.get("text", []))
    goals_row_y: Optional[int] = None
    assists_row_y: Optional[int] = None

    for i in range(n_boxes):
        text = str(ocr_data["text"][i]).strip().lower()
        y = int(ocr_data["top"][i])
        if s_ymin <= y <= s_ymax:
            if re.search(r"\bgoal(s)?\b", text):
                goals_row_y = y
            elif re.search(r"\bassist(s)?\b", text):
                assists_row_y = y

    diagnostics["detected_goals_row_y"] = goals_row_y
    diagnostics["detected_assists_row_y"] = assists_row_y

    goals_by_col: Dict[int, int] = {}
    assists_by_col: Dict[int, int] = {}
    row_tolerance = 50

    if goals_row_y is not None:
        goals_by_col = find_row_numbers_by_columns(
            ocr_data,
            goals_row_y - row_tolerance,
            goals_row_y + row_tolerance,
            column_centers,
        )
    if assists_row_y is not None:
        assists_by_col = find_row_numbers_by_columns(
            ocr_data,
            assists_row_y - row_tolerance,
            assists_row_y + row_tolerance,
            column_centers,
        )

    candidates: List[OCRPlayerCandidate] = []
    for idx, (player_name, cx, _) in enumerate(player_cols):
        g = goals_by_col.get(idx, 0)
        a = assists_by_col.get(idx, 0)
        conf_note = "High" if (goals_row_y is not None and assists_row_y is not None) else "Low"
        candidates.append(OCRPlayerCandidate(player=player_name, goals=g, assists=a, confidence_note=conf_note))

    if goals_row_y is None and assists_row_y is None:
        return OCRResult(
            success=False,
            candidates=candidates,
            error_reason=(
                "Could not locate 'Goals' or 'Assist' rows in screenshot. Please verify or manually edit stats."
            ),
            raw_text=raw_full_text,
            diagnostics=diagnostics,
        )

    return OCRResult(
        success=True,
        candidates=candidates,
        raw_text=raw_full_text,
        diagnostics=diagnostics,
    )
