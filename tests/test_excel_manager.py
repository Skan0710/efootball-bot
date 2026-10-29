"""Unit tests for ExcelManager persistence, concurrency, and deletion."""

from pathlib import Path

import openpyxl
import pytest

from excel_manager import SHEET_LEADERBOARD, SHEET_MATCHES, SHEET_PLAYER_STATS, ExcelManager
from models import MatchRecord, MatchResult, PlayerStat
from stats import calculate_player_aggregates


@pytest.mark.asyncio
async def test_workbook_initialization(tmp_path: Path) -> None:
    """Verifies that an uninitialized path creates a workbook with required sheets and headers."""
    wb_path = tmp_path / "test_matches.xlsx"
    assert not wb_path.exists()

    manager = ExcelManager(wb_path)
    assert wb_path.exists()

    wb = openpyxl.load_workbook(wb_path)
    assert SHEET_MATCHES in wb.sheetnames
    assert SHEET_PLAYER_STATS in wb.sheetnames
    assert SHEET_LEADERBOARD in wb.sheetnames

    # Verify Matches header row
    ws_matches = wb[SHEET_MATCHES]
    headers_matches = [cell.value for cell in ws_matches[1]]
    assert headers_matches == [
        "MatchID",
        "Date",
        "Result",
        "SubmittedBy",
        "ScreenshotReference",
        "Notes",
    ]

    # Verify PlayerStats header row
    ws_players = wb[SHEET_PLAYER_STATS]
    headers_players = [cell.value for cell in ws_players[1]]
    assert headers_players == [
        "MatchID",
        "Player",
        "MatchesPlayed",
        "Goals",
        "Assists",
        "G/A",
        "GPG",
        "APG",
        "GAPG",
    ]

    # Verify Leaderboard header row (no Draws or DrawRate%)
    ws_lb = wb[SHEET_LEADERBOARD]
    headers_lb = [cell.value for cell in ws_lb[1]]
    assert headers_lb == [
        "Player",
        "MatchesPlayed",
        "Goals",
        "Assists",
        "Wins",
        "Losses",
        "WinRate%",
        "LossRate%",
    ]
    wb.close()


@pytest.mark.asyncio
async def test_save_and_get_match(tmp_path: Path) -> None:
    """Verifies saving a match and retrieving it along with player rows."""
    wb_path = tmp_path / "test_matches.xlsx"
    manager = ExcelManager(wb_path)

    match_id = manager.generate_match_id()
    record = MatchRecord(
        match_id=match_id,
        date="2026-10-29 12:00:00",
        result=MatchResult.WIN,
        submitted_by="AdminUser",
        notes="Clean sheet victory",
        player_stats=[
            PlayerStat(player="SkanRocky", goals=2, assists=0, result=MatchResult.WIN),
            PlayerStat(player="lillym", goals=0, assists=1, result=MatchResult.WIN),
        ],
    )

    await manager.save_match(record)

    fetched = await manager.get_match(match_id)
    assert fetched is not None
    assert fetched.match_id == match_id
    assert fetched.result == MatchResult.WIN
    assert fetched.submitted_by == "AdminUser"
    assert len(fetched.player_stats) == 2

    # Check player row contents
    skan = next(p for p in fetched.player_stats if p.player == "SkanRocky")
    assert skan.goals == 2
    assert skan.assists == 0

    # Verify Excel formulas in PlayerStats sheet
    wb = openpyxl.load_workbook(wb_path)
    ws_p = wb[SHEET_PLAYER_STATS]
    assert ws_p.cell(row=2, column=6).value == "=D2+E2"
    assert ws_p.cell(row=2, column=7).value == "=IF(C2>0, D2/C2, 0)"
    assert ws_p.cell(row=2, column=8).value == "=IF(C2>0, E2/C2, 0)"
    assert ws_p.cell(row=2, column=9).value == "=G2+H2"
    wb.close()


@pytest.mark.asyncio
async def test_delete_match_removes_players_and_updates_aggregates(tmp_path: Path) -> None:
    """Verifies that deleting a match removes player stats and alters aggregate calculations."""
    wb_path = tmp_path / "test_matches.xlsx"
    manager = ExcelManager(wb_path)

    # Save Match 1
    m1 = MatchRecord(
        match_id="M-001",
        date="2026-10-29 10:00:00",
        result=MatchResult.WIN,
        submitted_by="UserA",
        player_stats=[
            PlayerStat(player="PlayerOne", goals=3, assists=1, result=MatchResult.WIN),
        ],
    )
    await manager.save_match(m1)

    # Save Match 2
    m2 = MatchRecord(
        match_id="M-002",
        date="2026-10-29 11:00:00",
        result=MatchResult.LOSS,
        submitted_by="UserA",
        player_stats=[
            PlayerStat(player="PlayerOne", goals=1, assists=0, result=MatchResult.LOSS),
        ],
    )
    await manager.save_match(m2)

    # Verify initial aggregates
    initial_stats = await manager.get_all_player_stats()
    initial_aggs = calculate_player_aggregates(initial_stats)
    p1_initial = initial_aggs["playerone"]
    assert p1_initial.matches_played == 2
    assert p1_initial.goals == 4
    assert p1_initial.wins == 1
    assert p1_initial.losses == 1

    # Delete Match 2
    deleted = await manager.delete_match("M-002")
    assert deleted is True

    # Verify Match 2 no longer exists
    assert await manager.get_match("M-002") is None

    # Verify Match 1 still exists
    assert await manager.get_match("M-001") is not None

    # Verify updated aggregates
    remaining_stats = await manager.get_all_player_stats()
    updated_aggs = calculate_player_aggregates(remaining_stats)
    p1_updated = updated_aggs["playerone"]
    assert p1_updated.matches_played == 1
    assert p1_updated.goals == 3
    assert p1_updated.wins == 1
    assert p1_updated.losses == 0
    assert p1_updated.win_rate == 100.0


@pytest.mark.asyncio
async def test_delete_non_existent_match_returns_false(tmp_path: Path) -> None:
    """Verifies that attempting to delete a nonexistent match returns False without error."""
    wb_path = tmp_path / "test_matches.xlsx"
    manager = ExcelManager(wb_path)
    res = await manager.delete_match("NON-EXISTENT-ID")
    assert res is False


@pytest.mark.asyncio
async def test_save_duplicate_match_id_raises_error(tmp_path: Path) -> None:
    """Verifies duplicate match ID rejection."""
    wb_path = tmp_path / "test_matches.xlsx"
    manager = ExcelManager(wb_path)

    match = MatchRecord(
        match_id="M-UNIQUE-1",
        date="2026-10-29 10:00:00",
        result=MatchResult.WIN,
        submitted_by="UserA",
        player_stats=[PlayerStat(player="Alpha", goals=1, assists=0, result=MatchResult.WIN)],
    )
    await manager.save_match(match)

    with pytest.raises(ValueError, match="already exists"):
        await manager.save_match(match)


@pytest.mark.asyncio
async def test_backup_creation_on_save(tmp_path: Path) -> None:
    """Verifies backup file generation in backups directory."""
    wb_path = tmp_path / "matches.xlsx"
    manager = ExcelManager(wb_path)

    match = MatchRecord(
        match_id="M-BACKUP-TEST",
        date="2026-10-29 10:00:00",
        result=MatchResult.WIN,
        submitted_by="UserA",
        player_stats=[PlayerStat(player="Alpha", goals=1, assists=0, result=MatchResult.WIN)],
    )
    await manager.save_match(match)

    backup_dir = tmp_path / "backups"
    assert backup_dir.exists()
    backups = list(backup_dir.glob("matches_*.xlsx"))
    assert len(backups) >= 1
