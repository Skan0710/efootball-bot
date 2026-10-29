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

    # Relative coordinates (0.0 - 1.0) for regions of interest in normalized resolution
    player_names_top: float = 0.18
    player_names_bottom: float = 0.35
    stats_table_top: float = 0.35
    stats_table_bottom: float = 0.85
    player_cols_left: float = 0.28
    player_cols_right: float = 0.95


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
    # Convert to grayscale
    gray = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2GRAY)

    # Resize to normalized width (1920px standard) if different, maintaining aspect ratio
    target_width = 1920
    h, w = gray.shape
    if w != target_width:
        scaling_factor = target_width / float(w)
        target_height = int(h * scaling_factor)
        gray = cv2.resize(gray, (target_width, target_height), interpolation=cv2.INTER_CUBIC)

    # Contrast Limited Adaptive Histogram Equalization (CLAHE)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    # Bilateral filter to reduce noise while keeping font edges crisp
    denoised = cv2.bilateralFilter(enhanced, d=5, sigmaColor=75, sigmaSpace=75)

    # Adaptive thresholding
    thresh = cv2.adaptiveThreshold(
        denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, 4
    )

    return thresh


def _configure_tesseract(tesseract_cmd: Optional[str] = None) -> None:
    """Sets pytesseract binary path if specified or present in standard location."""
    if tesseract_cmd and Path(tesseract_cmd).exists():
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    elif not pytesseract.pytesseract.tesseract_cmd or pytesseract.pytesseract.tesseract_cmd == "tesseract":
        # Check standard Windows path
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
    """Identifies player names and their horizontal column centers.

    Returns list of tuples: (player_name, center_x, width) sorted left to right.
    """
    n_boxes = len(data.get("text", []))
    candidates: List[Tuple[str, int, int]] = []

    for i in range(n_boxes):
        text = str(data["text"][i]).strip()
        conf = int(data["conf"][i]) if "conf" in data else 0

        # Filter out empty or noise tokens
        if not text or len(text) < 2 or conf < 30:
            continue

        # Clean noise characters
        cleaned = re.sub(r"[^\w\-\.]", "", text)
        if not cleaned:
            continue

        x = int(data["left"][i])
        y = int(data["top"][i])
        w = int(data["width"][i])
        h = int(data["height"][i])

        if x_min <= x <= x_max and y_min <= y <= y_max:
            center_x = x + (w // 2)
            candidates.append((cleaned, center_x, w))

    # Sort candidates by x coordinate
    candidates.sort(key=lambda c: c[1])

    # Merge words that are very close horizontally (same player name consisting of multiple words)
    merged: List[Tuple[str, int, int]] = []
    for name, cx, w in candidates:
        if not merged:
            merged.append((name, cx, w))
        else:
            prev_name, prev_cx, prev_w = merged[-1]
            # If horizontal distance is small (less than 120px in 1080p), merge into one player name
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
        if not text or not text.isdigit():
            # Check if text is like "0" or "1" with punctuation
            digits = re.findall(r"\d+", text)
            if not digits:
                continue
            text = digits[0]

        x = int(data["left"][i])
        y = int(data["top"][i])
        w = int(data["width"][i])

        if row_y_min <= y <= row_y_max:
            item_center_x = x + (w // 2)
            val = int(text)

            # Find closest player column
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

    # Preprocess
    try:
        processed = preprocess_image_for_ocr(bgr)
    except Exception as exc:
        LOGGER.error("Image preprocessing error: %s", exc)
        return OCRResult(success=False, error_reason=f"Image preprocessing failed: {exc}")

    # Scaled dimensions (normalized reference width = 1920)
    proc_h, proc_w = processed.shape

    # Bounding boxes for player name band and statistics area
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
        # Run Tesseract with data dictionary
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
                "Tesseract OCR executable was not found. Please install Tesseract "
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

    # 1. Detect Player Columns
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
    num_players = len(player_cols)

    # 2. Locate Goals and Assists rows
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

    row_tolerance = 45  # vertical tolerance around detected row label

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

    diagnostics["goals_by_col"] = goals_by_col
    diagnostics["assists_by_col"] = assists_by_col

    # 3. Validate consistency across all columns
    candidates: List[OCRPlayerCandidate] = []
    missing_data = False

    for idx, (player_name, cx, _) in enumerate(player_cols):
        g = goals_by_col.get(idx, 0)
        a = assists_by_col.get(idx, 0)
        # If we failed to find row positions at all, note confidence
        conf_note = "High" if (goals_row_y is not None and assists_row_y is not None) else "Low"
        candidates.append(OCRPlayerCandidate(player=player_name, goals=g, assists=a, confidence_note=conf_note))

    # Consistency check: If neither goals nor assists row was found, fail safely
    if goals_row_y is None and assists_row_y is None:
        return OCRResult(
            success=False,
            candidates=candidates,
            error_reason=(
                "Could not locate 'Goals' or 'Assists' rows in screenshot. Please verify or manually edit stats."
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
