"""Unit tests for OCR image validation, preprocessing, and layout parsing."""

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

from ocr import (
    find_row_numbers_by_columns,
    parse_player_columns_from_data,
    preprocess_image_for_ocr,
    validate_image_file,
    extract_player_stats_from_screenshot,
)


def test_validate_image_valid_png(tmp_path: Path) -> None:
    """Verifies that a genuine PNG within size limits passes validation."""
    img_path = tmp_path / "valid.png"
    img = Image.new("RGB", (1280, 720), color=(50, 50, 50))
    img.save(img_path, format="PNG")

    valid, err = validate_image_file(img_path, max_bytes=5 * 1024 * 1024)
    assert valid is True
    assert err is None


def test_validate_image_non_existent(tmp_path: Path) -> None:
    """Verifies that a nonexistent file fails validation cleanly."""
    valid, err = validate_image_file(tmp_path / "does_not_exist.jpg")
    assert valid is False
    assert "File does not exist" in str(err)


def test_validate_image_oversized(tmp_path: Path) -> None:
    """Verifies that files exceeding maximum size are rejected."""
    img_path = tmp_path / "large.png"
    img = Image.new("RGB", (1920, 1080), color=(255, 255, 255))
    img.save(img_path, format="PNG")

    # Set threshold very low (100 bytes)
    valid, err = validate_image_file(img_path, max_bytes=100)
    assert valid is False
    assert "exceeds" in str(err)


def test_validate_image_corrupted(tmp_path: Path) -> None:
    """Verifies that invalid/corrupt image bytes fail validation."""
    corrupt_path = tmp_path / "corrupt.png"
    corrupt_path.write_bytes(b"NOT_A_REAL_IMAGE_DATA")

    valid, err = validate_image_file(corrupt_path)
    assert valid is False
    assert "Corrupted or invalid" in str(err)


def test_preprocess_image_for_ocr() -> None:
    """Verifies OpenCV preprocessing pipeline produces binary normalized image."""
    dummy_bgr = np.zeros((720, 1280, 3), dtype=np.uint8)
    processed = preprocess_image_for_ocr(dummy_bgr)

    assert processed.ndim == 2
    assert processed.shape[1] == 1920  # Normalized reference width
    assert processed.dtype == np.uint8


def test_parse_player_columns_from_data() -> None:
    """Verifies detecting player columns and sorting them left-to-right."""
    # Synthetic OCR data representing two players: SkanRocky at x=700, lillym at x=1200
    synthetic_data = {
        "text": ["", "Match", "Details", "SkanRocky", "lillym", "Goals"],
        "conf": [0, 90, 85, 95, 92, 88],
        "left": [0, 100, 250, 700, 1200, 200],
        "top": [0, 50, 50, 250, 250, 450],
        "width": [0, 80, 100, 120, 90, 80],
        "height": [0, 30, 30, 40, 40, 35],
    }

    cols = parse_player_columns_from_data(
        synthetic_data, x_min=500, x_max=1500, y_min=200, y_max=350
    )

    assert len(cols) == 2
    assert cols[0][0] == "SkanRocky"
    assert cols[0][1] == 760
    assert cols[1][0] == "lillym"
    assert cols[1][1] == 1245


def test_find_row_numbers_by_columns() -> None:
    """Verifies matching numeric statistics to column centers by horizontal distance."""
    column_centers = [760, 1245]

    synthetic_row_data = {
        "text": ["Goals", "2", "0"],
        "conf": [90, 95, 95],
        "left": [200, 755, 1240],
        "top": [450, 450, 450],
        "width": [80, 20, 20],
        "height": [30, 30, 30],
    }

    matched = find_row_numbers_by_columns(
        synthetic_row_data,
        row_y_min=420,
        row_y_max=480,
        column_centers=column_centers,
    )

    assert matched[0] == 2
    assert matched[1] == 0


def test_ocr_pipeline_with_mocked_tesseract(tmp_path: Path) -> None:
    """Tests the full OCR pipeline using mock Tesseract output for the example screenshot."""
    img_path = tmp_path / "efootball_details.png"
    img = Image.new("RGB", (1920, 1080), color=(20, 25, 30))
    img.save(img_path, format="PNG")

    mock_ocr_data = {
        "text": ["SkanRocky", "lillym", "Goals", "2", "0", "Assists", "0", "1"],
        "conf": [95, 95, 90, 95, 95, 90, 95, 95],
        "left": [700, 1200, 200, 750, 1240, 200, 750, 1240],
        "top": [260, 260, 450, 450, 450, 520, 520, 520],
        "width": [120, 90, 80, 25, 25, 90, 25, 25],
        "height": [35, 35, 30, 30, 30, 30, 30, 30],
    }

    with patch("ocr._extract_via_targeted_regions", return_value=None), \
         patch("ocr.pytesseract.image_to_data", return_value=mock_ocr_data), \
         patch("ocr.pytesseract.image_to_string", return_value="Mocked OCR Text"):
        result = extract_player_stats_from_screenshot(img_path)

    assert result.success is True
    assert len(result.candidates) == 2

    skan = next(c for c in result.candidates if c.player == "Skan")
    assert skan.goals == 2
    assert skan.assists == 0

    lilly = next(c for c in result.candidates if c.player == "Lilly")
    assert lilly.goals == 0
    assert lilly.assists == 1


def test_ocr_fails_safely_when_tesseract_missing(tmp_path: Path) -> None:
    """Verifies that missing Tesseract binary fails safely without crashing."""
    import pytesseract
    img_path = tmp_path / "test.png"
    img = Image.new("RGB", (800, 600), color=(0, 0, 0))
    img.save(img_path, format="PNG")

    with patch("ocr._extract_via_targeted_regions", return_value=None), \
         patch("ocr.pytesseract.image_to_data", side_effect=pytesseract.TesseractNotFoundError()):
        result = extract_player_stats_from_screenshot(img_path)

    assert result.success is False
    assert "Tesseract OCR executable was not found" in (result.error_reason or "")


def test_real_efootball_details_screenshot_extraction() -> None:
    """Tests OCR extraction on the real eFootball post-match Details screenshot fixture."""
    fixture_path = Path(__file__).parent / "fixtures" / "sample_efootball_details.jpg"
    if not fixture_path.exists():
        pytest.skip("Fixture image not found.")

    result = extract_player_stats_from_screenshot(fixture_path)
    assert result.success is True
    names = [c.player for c in result.candidates]
    assert "Skan" in names
    assert "Suri" in names

    skan = next(c for c in result.candidates if c.player == "Skan")
    assert skan.goals == 3
    assert skan.assists == 1

    suri = next(c for c in result.candidates if c.player == "Suri")
    assert suri.goals == 1
    assert suri.assists == 2


def test_extract_via_vision_ai_success(tmp_path: Path) -> None:
    """Verifies that multimodal AI Vision extracts and canonicalizes player stats cleanly."""
    from ocr import extract_via_vision_ai
    from unittest.mock import MagicMock

    img_path = tmp_path / "vision_test.jpg"
    img = Image.new("RGB", (1280, 720), color=(20, 20, 20))
    img.save(img_path, format="JPEG")

    mock_json_content = (
        '{\n'
        '  "team": "JFC",\n'
        '  "players": [\n'
        '    {"player": "itsJonaldinhoH", "goals": 0, "assists": 0},\n'
        '    {"player": "lillym", "goals": 0, "assists": 2},\n'
        '    {"player": "Azlan19Siuu", "goals": 3, "assists": 0}\n'
        '  ]\n'
        '}'
    )

    mock_choice = MagicMock()
    mock_choice.message.content = mock_json_content
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]

    with patch("ocr.Groq") as mock_groq_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_completion
        mock_groq_cls.return_value = mock_client

        res = extract_via_vision_ai(img_path, api_key="fake-key-12345")

    assert res is not None
    assert res.success is True
    assert len(res.candidates) == 3
    # Player canonicalization check
    assert res.candidates[0].player == "Jonny"
    assert res.candidates[0].goals == 0
    assert res.candidates[0].assists == 0

    assert res.candidates[1].player == "Lilly"
    assert res.candidates[1].goals == 0
    assert res.candidates[1].assists == 2

    assert res.candidates[2].player == "Azlan"
    assert res.candidates[2].goals == 3
    assert res.candidates[2].assists == 0
    assert "AI Vision" in res.candidates[2].confidence_note


def test_extract_via_vision_ai_no_key(tmp_path: Path) -> None:
    """Verifies that vision AI extraction returns None safely when no key is configured."""
    from ocr import extract_via_vision_ai

    img_path = tmp_path / "no_key.jpg"
    img = Image.new("RGB", (800, 600), color=(0, 0, 0))
    img.save(img_path, format="JPEG")

    with patch.dict("os.environ", {}, clear=True):
        res = extract_via_vision_ai(img_path, api_key=None)
    assert res is None


def test_extract_player_stats_prioritizes_ai_and_falls_back(tmp_path: Path) -> None:
    """Verifies that AI Vision is prioritized when key is present, and falls back to local OCR."""
    from models import OCRPlayerCandidate
    from ocr import OCRResult, extract_player_stats_from_screenshot

    img_path = tmp_path / "fallback_test.jpg"
    img = Image.new("RGB", (1280, 720), color=(10, 10, 10))
    img.save(img_path, format="JPEG")

    ai_candidates = [
        OCRPlayerCandidate(player="Suri", goals=4, assists=2, confidence_note="AI Vision (High Accuracy)"),
        OCRPlayerCandidate(player="Roshan", goals=0, assists=1, confidence_note="AI Vision (High Accuracy)"),
    ]
    mock_ai_result = OCRResult(success=True, candidates=ai_candidates)

    # 1. When AI succeeds, extract_player_stats_from_screenshot returns AI result directly
    with patch("ocr.extract_via_vision_ai", return_value=mock_ai_result):
        res1 = extract_player_stats_from_screenshot(img_path, groq_api_key="valid-key")
        assert res1.success is True
        assert [c.player for c in res1.candidates] == ["Suri", "Roshan"]

    # 2. When AI returns None (or fails), falls back to local pipeline
    with patch("ocr.extract_via_vision_ai", return_value=None), \
         patch("ocr._extract_via_targeted_regions", return_value=OCRResult(success=True, candidates=[
             OCRPlayerCandidate(player="LocalPlayer", goals=1, assists=0)
         ])):
        res2 = extract_player_stats_from_screenshot(img_path, groq_api_key="valid-key")
        assert res2.success is True
        assert res2.candidates[0].player == "LocalPlayer"
