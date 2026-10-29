"""Hardening tests: concurrency, invalid inputs, duplicate submissions, and edge cases."""

import asyncio
from pathlib import Path

import pytest

from commands_handler import RECENT_IMAGE_HASHES
from excel_manager import ExcelManager
from models import MatchRecord, MatchResult, OCRPlayerCandidate, PlayerStat
from ocr import (
    LayoutConfig,
    extract_player_stats_from_screenshot,
    find_row_numbers_by_columns,
    parse_player_columns_from_data,
)
from stats import calculate_player_aggregates


@pytest.mark.asyncio
async def test_concurrent_workbook_writes(tmp_path: Path) -> None:
    """Verifies that concurrent save_match operations are safely serialized without corruption."""
    wb_path = tmp_path / "concurrent_matches.xlsx"
    manager = ExcelManager(wb_path)

    async def worker_save(idx: int) -> None:
        rec = MatchRecord(
            match_id=f"M-CONCURRENT-{idx:03d}",
            date="2026-10-29 12:00:00",
            result=MatchResult.WIN if idx % 2 == 0 else MatchResult.LOSS,
            submitted_by=f"User{idx}",
            player_stats=[
                PlayerStat(player=f"Player{idx}", goals=idx, assists=1, result=MatchResult.WIN)
            ],
        )
        await manager.save_match(rec)

    # Launch 10 simultaneous writes
    tasks = [worker_save(i) for i in range(10)]
    await asyncio.gather(*tasks)

    all_matches = await manager.get_all_matches()
    assert len(all_matches) == 10

    # Ensure all 10 unique IDs exist
    ids = {m.match_id for m in all_matches}
    assert len(ids) == 10


def test_invalid_match_result_enum_rejection() -> None:
    """Verifies that unapproved match result strings raise ValueError."""
    with pytest.raises(ValueError, match="Invalid match result"):
        MatchResult.from_str("InvalidOutcome")

    with pytest.raises(ValueError, match="Invalid match result"):
        MatchResult.from_str("Forfeit")


def test_player_stat_negative_validation() -> None:
    """Verifies that negative goals or assists are rejected."""
    with pytest.raises(ValueError, match="Goals cannot be negative"):
        PlayerStat(player="Test", goals=-1, assists=0, result=MatchResult.WIN)

    with pytest.raises(ValueError, match="Assists cannot be negative"):
        PlayerStat(player="Test", goals=0, assists=-5, result=MatchResult.WIN)

    with pytest.raises(ValueError, match="Player name cannot be empty"):
        PlayerStat(player="   ", goals=0, assists=0, result=MatchResult.WIN)


def test_duplicate_player_in_single_match_handling() -> None:
    """Verifies aggregation correctly aggregates if same player name appears multiple times."""
    raw_stats = [
        PlayerStat(player="DualEntry", goals=1, assists=1, result=MatchResult.WIN),
        PlayerStat(player="DualEntry", goals=2, assists=0, result=MatchResult.WIN),
    ]
    aggs = calculate_player_aggregates(raw_stats)
    record = aggs["dualentry"]
    assert record.goals == 3
    assert record.assists == 1
    assert record.matches_played == 2


def test_ocr_mismatched_column_and_number_counts() -> None:
    """Verifies OCR column matching when detected numbers do not align with player columns."""
    column_centers = [760, 1245]  # 2 columns

    # Only 1 number detected in row
    partial_row_data = {
        "text": ["Goals", "3"],
        "conf": [95, 95],
        "left": [200, 755],
        "top": [450, 450],
        "width": [80, 25],
        "height": [30, 30],
    }

    matched = find_row_numbers_by_columns(
        partial_row_data,
        row_y_min=420,
        row_y_max=480,
        column_centers=column_centers,
    )

    # Column 0 found, column 1 not found
    assert 0 in matched
    assert 1 not in matched
    assert matched[0] == 3


def test_duplicate_submission_safeguard() -> None:
    """Verifies recent hash cache stores and identifies repeated screenshot hashes."""
    test_hash = "mock_sha256_hash_12345"
    assert test_hash not in RECENT_IMAGE_HASHES

    RECENT_IMAGE_HASHES.add(test_hash)
    assert test_hash in RECENT_IMAGE_HASHES
