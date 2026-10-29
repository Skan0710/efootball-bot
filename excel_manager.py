"""Excel persistence manager for eFootball match and player statistics.

Manages the local workbook data/matches.xlsx containing Matches, PlayerStats,
and dynamic Leaderboard sheets with concurrency protection and automatic backups.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from models import MatchRecord, MatchResult, PlayerAggregateRecord, PlayerStat

LOGGER = logging.getLogger("efootball_bot.excel_manager")

SHEET_MATCHES = "Matches"
SHEET_PLAYER_STATS = "PlayerStats"
SHEET_LEADERBOARD = "Leaderboard"

HEADER_FILL = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
HEADER_FONT = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
DATA_FONT = Font(name="Segoe UI", size=10)
BORDER_THIN = Border(
    left=Side(style="thin", color="D9D9D9"),
    right=Side(style="thin", color="D9D9D9"),
    top=Side(style="thin", color="D9D9D9"),
    bottom=Side(style="thin", color="D9D9D9"),
)


class ExcelManager:
    """Thread-safe and async-safe manager for Excel workbook storage."""

    def __init__(self, workbook_path: Path | str) -> None:
        self.workbook_path = Path(workbook_path)
        self._lock = asyncio.Lock()
        self._ensure_workbook_exists()

    def _ensure_workbook_exists(self) -> None:
        """Initializes workbook with required sheets, headers, and formatting if missing."""
        if self.workbook_path.exists():
            return

        self.workbook_path.parent.mkdir(parents=True, exist_ok=True)
        LOGGER.info("Initializing new Excel workbook at %s", self.workbook_path)

        wb = openpyxl.Workbook()
        # Remove default sheet
        wb.remove(wb.active)

        # 1. Matches Sheet
        ws_matches = wb.create_sheet(title=SHEET_MATCHES)
        matches_headers = ["MatchID", "Date", "Result", "SubmittedBy", "ScreenshotReference", "Notes"]
        self._format_header(ws_matches, matches_headers)

        # 2. PlayerStats Sheet
        ws_players = wb.create_sheet(title=SHEET_PLAYER_STATS)
        players_headers = [
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
        self._format_header(ws_players, players_headers)

        # 3. Dynamic Leaderboard Sheet
        ws_lb = wb.create_sheet(title=SHEET_LEADERBOARD)
        lb_headers = [
            "Player",
            "MatchesPlayed",
            "Goals",
            "Assists",
            "Wins",
            "Losses",
            "WinRate%",
            "LossRate%",
        ]
        self._format_header(ws_lb, lb_headers)

        wb.save(self.workbook_path)
        wb.close()
        LOGGER.info("Workbook initialized successfully with headers and filters.")

    def _format_header(self, ws: openpyxl.worksheet.worksheet.Worksheet, headers: List[str]) -> None:
        """Applies styling, auto-filter, and freezes the top row."""
        ws.append(headers)
        for col_idx, header in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = BORDER_THIN

        ws.freeze_panes = "A2"
        max_col_letter = get_column_letter(len(headers))
        ws.auto_filter.ref = f"A1:{max_col_letter}1"

        # Apply default sensible column widths
        for col_idx, header in enumerate(headers, start=1):
            col_letter = get_column_letter(col_idx)
            ws.column_dimensions[col_letter].width = max(len(header) + 5, 14)

    def create_backup(self, backup_dir: Optional[Path] = None) -> Optional[Path]:
        """Creates a timestamped backup of the current workbook."""
        if not self.workbook_path.exists():
            return None

        dest_dir = backup_dir or (self.workbook_path.parent / "backups")
        dest_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = dest_dir / f"{self.workbook_path.stem}_{timestamp}.xlsx"
        try:
            shutil.copy2(self.workbook_path, backup_file)
            LOGGER.info("Created workbook backup at %s", backup_file)

            # Keep only the 10 most recent backups to save disk space
            backups = sorted(dest_dir.glob(f"{self.workbook_path.stem}_*.xlsx"), key=os.path.getmtime)
            if len(backups) > 10:
                for old in backups[:-10]:
                    try:
                        old.unlink()
                    except OSError:
                        pass
            return backup_file
        except Exception as exc:
            LOGGER.error("Failed to create workbook backup: %s", exc)
            return None

    def generate_match_id(self) -> str:
        """Generates a human-friendly unique match identifier."""
        date_str = datetime.now().strftime("%Y%m%d")
        short_id = uuid.uuid4().hex[:6].upper()
        return f"M-{date_str}-{short_id}"

    async def save_match(self, match: MatchRecord) -> MatchRecord:
        """Appends a validated match and all associated player statistics atomically."""
        async with self._lock:
            return self._sync_save_match(match)

    def _sync_save_match(self, match: MatchRecord) -> MatchRecord:
        """Synchronous implementation of save_match."""
        self._ensure_workbook_exists()
        self.create_backup()

        wb = openpyxl.load_workbook(self.workbook_path)
        try:
            # Validate unique match ID
            ws_matches = wb[SHEET_MATCHES]
            for row in ws_matches.iter_rows(min_row=2, max_col=1, values_only=True):
                if row and row[0] == match.match_id:
                    raise ValueError(f"Match ID '{match.match_id}' already exists in the workbook.")

            # Append to Matches sheet
            match_row = [
                match.match_id,
                match.date,
                match.result.value,
                match.submitted_by,
                match.screenshot_ref,
                match.notes,
            ]
            ws_matches.append(match_row)
            new_match_row_idx = ws_matches.max_row
            for col_idx in range(1, len(match_row) + 1):
                cell = ws_matches.cell(row=new_match_row_idx, column=col_idx)
                cell.font = DATA_FONT
                cell.border = BORDER_THIN
                if col_idx in (1, 2, 3):
                    cell.alignment = Alignment(horizontal="center")

            # Append to PlayerStats sheet
            ws_players = wb[SHEET_PLAYER_STATS]
            for stat in match.player_stats:
                new_p_row_idx = ws_players.max_row + 1
                p_row = [
                    match.match_id,
                    stat.player,
                    1,
                    int(stat.goals),
                    int(stat.assists),
                    f"=D{new_p_row_idx}+E{new_p_row_idx}",
                    f"=IF(C{new_p_row_idx}>0, D{new_p_row_idx}/C{new_p_row_idx}, 0)",
                    f"=IF(C{new_p_row_idx}>0, E{new_p_row_idx}/C{new_p_row_idx}, 0)",
                    f"=G{new_p_row_idx}+H{new_p_row_idx}",
                ]
                ws_players.append(p_row)
                for col_idx in range(1, len(p_row) + 1):
                    cell = ws_players.cell(row=new_p_row_idx, column=col_idx)
                    cell.font = DATA_FONT
                    cell.border = BORDER_THIN
                    if col_idx in (1, 3, 4, 5, 6):
                        cell.alignment = Alignment(horizontal="center")
                    elif col_idx in (7, 8, 9):
                        cell.alignment = Alignment(horizontal="center")
                        cell.number_format = "0.00"

            wb.save(self.workbook_path)
            LOGGER.info("Saved match %s with %d player rows", match.match_id, len(match.player_stats))
            return match
        finally:
            wb.close()

    async def delete_match(self, match_id: str) -> bool:
        """Removes a match record and all associated player statistics atomically."""
        async with self._lock:
            return self._sync_delete_match(match_id)

    def _sync_delete_match(self, match_id: str) -> bool:
        """Synchronous implementation of delete_match."""
        if not self.workbook_path.exists():
            return False

        self.create_backup()
        wb = openpyxl.load_workbook(self.workbook_path)
        try:
            ws_matches = wb[SHEET_MATCHES]
            match_found = False
            target_match_row = -1

            for row_idx in range(2, ws_matches.max_row + 1):
                val = ws_matches.cell(row=row_idx, column=1).value
                if val and str(val).strip() == match_id.strip():
                    match_found = True
                    target_match_row = row_idx
                    break

            if not match_found:
                LOGGER.warning("Attempted to delete non-existent match ID: %s", match_id)
                return False

            ws_matches.delete_rows(target_match_row, 1)

            # Delete corresponding PlayerStats rows (in reverse to preserve indices)
            ws_players = wb[SHEET_PLAYER_STATS]
            deleted_player_rows = 0
            for row_idx in range(ws_players.max_row, 1, -1):
                val = ws_players.cell(row=row_idx, column=1).value
                if val and str(val).strip() == match_id.strip():
                    ws_players.delete_rows(row_idx, 1)
                    deleted_player_rows += 1

            wb.save(self.workbook_path)
            LOGGER.info(
                "Deleted match %s and %d associated player stat rows",
                match_id,
                deleted_player_rows,
            )
            return True
        finally:
            wb.close()

    async def delete_all_matches(self) -> int:
        """Deletes all matches, player stats, and leaderboard rows atomically."""
        async with self._lock:
            return self._sync_delete_all_matches()

    def _sync_delete_all_matches(self) -> int:
        """Synchronous implementation of delete_all_matches."""
        if not self.workbook_path.exists():
            return 0

        self.create_backup()
        wb = openpyxl.load_workbook(self.workbook_path)
        try:
            ws_matches = wb[SHEET_MATCHES]
            match_count = max(0, ws_matches.max_row - 1)

            if ws_matches.max_row > 1:
                ws_matches.delete_rows(2, ws_matches.max_row)

            if SHEET_PLAYER_STATS in wb.sheetnames:
                ws_players = wb[SHEET_PLAYER_STATS]
                if ws_players.max_row > 1:
                    ws_players.delete_rows(2, ws_players.max_row)

            if SHEET_LEADERBOARD in wb.sheetnames:
                ws_lb = wb[SHEET_LEADERBOARD]
                if ws_lb.max_row > 1:
                    ws_lb.delete_rows(2, ws_lb.max_row)

            wb.save(self.workbook_path)
            LOGGER.info("Deleted all %d matches from workbook.", match_count)
            return match_count
        finally:
            wb.close()

    async def get_match(self, match_id: str) -> Optional[MatchRecord]:
        """Retrieves a specific match record and its associated player stats."""
        async with self._lock:
            return self._sync_get_match(match_id)

    def _sync_get_match(self, match_id: str) -> Optional[MatchRecord]:
        """Synchronous get_match implementation."""
        if not self.workbook_path.exists():
            return None

        wb = openpyxl.load_workbook(self.workbook_path, data_only=True)
        try:
            ws_matches = wb[SHEET_MATCHES]
            match_data: Optional[dict] = None

            for row in ws_matches.iter_rows(min_row=2, values_only=True):
                if not row or not row[0]:
                    continue
                if str(row[0]).strip() == match_id.strip():
                    match_data = {
                        "match_id": str(row[0]).strip(),
                        "date": str(row[1]) if len(row) > 1 and row[1] else "",
                        "result": MatchResult.from_str(str(row[2])) if len(row) > 2 and row[2] else MatchResult.WIN,
                        "submitted_by": str(row[3]) if len(row) > 3 and row[3] else "Unknown",
                        "screenshot_ref": str(row[4]) if len(row) > 4 and row[4] else "",
                        "notes": str(row[5]) if len(row) > 5 and row[5] else "",
                    }
                    break

            if not match_data:
                return None

            ws_players = wb[SHEET_PLAYER_STATS]
            player_stats: List[PlayerStat] = []
            for row in ws_players.iter_rows(min_row=2, values_only=True):
                if not row or not row[0]:
                    continue
                if str(row[0]).strip() == match_id.strip():
                    parsed = self._parse_player_stat_row(row, match_data["result"], match_data["notes"])
                    if parsed:
                        p_name, goals, assists, res = parsed
                        player_stats.append(
                            PlayerStat(
                                player=p_name,
                                goals=goals,
                                assists=assists,
                                result=res,
                            )
                        )

            return MatchRecord(
                match_id=match_data["match_id"],
                date=match_data["date"],
                result=match_data["result"],
                submitted_by=match_data["submitted_by"],
                screenshot_ref=match_data["screenshot_ref"],
                notes=match_data["notes"],
                player_stats=player_stats,
            )
        finally:
            wb.close()

    async def get_all_matches(self) -> List[MatchRecord]:
        """Loads all matches with their respective player stats."""
        async with self._lock:
            return self._sync_get_all_matches()

    def _sync_get_all_matches(self) -> List[MatchRecord]:
        if not self.workbook_path.exists():
            return []

        wb = openpyxl.load_workbook(self.workbook_path, data_only=True)
        try:
            ws_matches = wb[SHEET_MATCHES]
            ws_players = wb[SHEET_PLAYER_STATS]

            # Index matches by match_id
            matches_info: Dict[str, dict] = {}
            for row in ws_matches.iter_rows(min_row=2, values_only=True):
                if not row or not row[0]:
                    continue
                m_id = str(row[0]).strip()
                date_val = str(row[1]) if len(row) > 1 and row[1] else ""
                res = MatchResult.from_str(str(row[2])) if len(row) > 2 and row[2] else MatchResult.WIN
                sub_by = str(row[3]) if len(row) > 3 and row[3] else "Unknown"
                screen = str(row[4]) if len(row) > 4 and row[4] else ""
                notes = str(row[5]) if len(row) > 5 and row[5] else ""
                matches_info[m_id] = {
                    "date": date_val,
                    "result": res,
                    "submitted_by": sub_by,
                    "screenshot_ref": screen,
                    "notes": notes,
                }

            # Index player stats by match_id
            player_stats_map: Dict[str, List[PlayerStat]] = {}
            for row in ws_players.iter_rows(min_row=2, values_only=True):
                if not row or not row[0]:
                    continue
                m_id = str(row[0]).strip()
                m_info = matches_info.get(m_id, {"result": MatchResult.WIN, "notes": ""})
                parsed = self._parse_player_stat_row(row, m_info["result"], m_info["notes"])
                if parsed:
                    p_name, goals, assists, res = parsed
                    player_stats_map.setdefault(m_id, []).append(
                        PlayerStat(player=p_name, goals=goals, assists=assists, result=res)
                    )

            matches: List[MatchRecord] = []
            for m_id, info in matches_info.items():
                matches.append(
                    MatchRecord(
                        match_id=m_id,
                        date=info["date"],
                        result=info["result"],
                        submitted_by=info["submitted_by"],
                        screenshot_ref=info["screenshot_ref"],
                        notes=info["notes"],
                        player_stats=player_stats_map.get(m_id, []),
                    )
                )

            return matches
        finally:
            wb.close()

    async def get_all_player_stats(self) -> List[PlayerStat]:
        """Loads all raw player stat entries for statistical aggregation."""
        async with self._lock:
            return self._sync_get_all_player_stats()

    @staticmethod
    def _parse_player_stat_row(
        row: tuple, default_result: MatchResult, notes: str = ""
    ) -> Optional[Tuple[str, int, int, MatchResult]]:
        """Safely parses player stat row supporting both new format and legacy format."""
        if not row or not row[0]:
            return None
        p_name = str(row[1]).strip() if len(row) > 1 and row[1] else "Unknown"
        if not p_name or p_name.lower() in ("unknown", ""):
            return None

        # Check if legacy format (Col 4 was Result string)
        is_legacy = False
        if len(row) >= 5 and isinstance(row[4], str):
            try:
                MatchResult.from_str(str(row[4]))
                is_legacy = True
            except ValueError:
                is_legacy = False

        if is_legacy:
            goals = int(row[2]) if len(row) > 2 and row[2] is not None else 0
            assists = int(row[3]) if len(row) > 3 and row[3] is not None else 0
            res = MatchResult.from_str(str(row[4])) if len(row) > 4 and row[4] else default_result
        else:
            # New format: Col 2 is MatchesPlayed (1), Col 3 is Goals, Col 4 is Assists
            goals = int(row[3]) if len(row) > 3 and row[3] is not None else 0
            assists = int(row[4]) if len(row) > 4 and row[4] is not None else 0

            # Determine player's individual result from match outcome & team notes
            is_away = False
            if notes:
                notes_lower = notes.lower()
                p_lower = p_name.lower()
                if "vs" in notes_lower:
                    _, away_part = notes_lower.split("vs", 1)
                    is_away = p_lower in away_part
                elif "-" in notes_lower:
                    _, away_part = notes_lower.split("-", 1)
                    is_away = p_lower in away_part
                elif "away" in notes_lower or "opponent" in notes_lower:
                    is_away = True

            if default_result in (MatchResult.HOME_WIN, MatchResult.WIN):
                res = MatchResult.LOSS if is_away else MatchResult.WIN
            elif default_result in (MatchResult.AWAY_WIN, MatchResult.LOSS):
                res = MatchResult.WIN if is_away else MatchResult.LOSS
            else:
                res = MatchResult.DRAW

        return p_name, goals, assists, res

    def _sync_get_all_player_stats(self) -> List[PlayerStat]:
        if not self.workbook_path.exists():
            return []

        wb = openpyxl.load_workbook(self.workbook_path, data_only=True)
        try:
            ws_matches = wb[SHEET_MATCHES]
            ws_players = wb[SHEET_PLAYER_STATS]

            matches_map: Dict[str, Tuple[MatchResult, str]] = {}
            for row in ws_matches.iter_rows(min_row=2, values_only=True):
                if row and row[0]:
                    m_id = str(row[0]).strip()
                    res = MatchResult.from_str(str(row[2])) if len(row) > 2 and row[2] else MatchResult.WIN
                    notes = str(row[5]) if len(row) > 5 and row[5] else ""
                    matches_map[m_id] = (res, notes)

            stats: List[PlayerStat] = []
            for row in ws_players.iter_rows(min_row=2, values_only=True):
                if not row or not row[0]:
                    continue
                m_id = str(row[0]).strip()
                default_res, notes = matches_map.get(m_id, (MatchResult.WIN, ""))
                parsed = self._parse_player_stat_row(row, default_res, notes)
                if parsed:
                    p_name, goals, assists, res = parsed
                    stats.append(
                        PlayerStat(
                            player=p_name,
                            goals=goals,
                            assists=assists,
                            result=res,
                        )
                    )
            return stats
        finally:
            wb.close()

    async def update_leaderboard_sheet(self, aggregates: List[PlayerAggregateRecord]) -> None:
        """Updates the Excel Leaderboard sheet so opening it in Excel reflects latest stats."""
        async with self._lock:
            self._sync_update_leaderboard_sheet(aggregates)

    def _sync_update_leaderboard_sheet(self, aggregates: List[PlayerAggregateRecord]) -> None:
        if not self.workbook_path.exists():
            return

        wb = openpyxl.load_workbook(self.workbook_path)
        try:
            if SHEET_LEADERBOARD in wb.sheetnames:
                del wb[SHEET_LEADERBOARD]

            ws_lb = wb.create_sheet(title=SHEET_LEADERBOARD)
            lb_headers = [
                "Player",
                "MatchesPlayed",
                "Goals",
                "Assists",
                "Wins",
                "Losses",
                "WinRate%",
                "LossRate%",
            ]
            self._format_header(ws_lb, lb_headers)

            for agg in aggregates:
                curr_row = ws_lb.max_row + 1
                row_data = [
                    agg.player,
                    agg.matches_played,
                    agg.goals,
                    agg.assists,
                    agg.wins,
                    agg.losses,
                    f"=IF(B{curr_row}>0, (E{curr_row}/B{curr_row})*100, 0)",
                    f"=IF(B{curr_row}>0, (F{curr_row}/B{curr_row})*100, 0)",
                ]
                ws_lb.append(row_data)
                for col_idx in range(1, len(row_data) + 1):
                    cell = ws_lb.cell(row=curr_row, column=col_idx)
                    cell.font = DATA_FONT
                    cell.border = BORDER_THIN
                    if col_idx >= 2:
                        cell.alignment = Alignment(horizontal="center")
                    if col_idx in (7, 8):
                        cell.number_format = '0.0"%"'

            wb.save(self.workbook_path)
        finally:
            wb.close()
