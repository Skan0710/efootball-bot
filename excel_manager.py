"""Excel persistence manager for eFootball match and player statistics.

Manages the local workbook data/matches.xlsx containing Matches, PlayerStats,
and dynamic Leaderboard sheets with concurrency protection and automatic backups.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from models import MatchRecord, MatchResult, PlayerAggregateRecord, PlayerStat, canonicalize_player_name

LOGGER = logging.getLogger("efootball_bot.excel_manager")

SHEET_MATCHES = "Matches"
SHEET_PLAYER_STATS = "PlayerStats"
SHEET_LEADERBOARD = "Leaderboard"

MATCHES_HEADERS = [
    "MatchID",
    "Date",
    "Result",
    "SubmittedBy",
    "ScreenshotReference",
    "Notes",
    "PlayerStatsJSON",
]

PLAYER_STATS_HEADERS = [
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

LEADERBOARD_HEADERS = [
    "Player",
    "MatchesPlayed",
    "Goals",
    "Assists",
    "G/A",
    "Wins",
    "Losses",
    "WinRate%",
    "LossRate%",
    "GPG",
    "APG",
    "GAPG",
]

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
        """Initializes workbook with required sheets, headers, and formatting if missing, or refreshes existing structure."""
        if not self.workbook_path.exists():
            self.workbook_path.parent.mkdir(parents=True, exist_ok=True)
            LOGGER.info("Initializing new Excel workbook at %s", self.workbook_path)

            wb = openpyxl.Workbook()
            wb.remove(wb.active)

            ws_matches = wb.create_sheet(title=SHEET_MATCHES)
            self._format_header(ws_matches, MATCHES_HEADERS)

            ws_players = wb.create_sheet(title=SHEET_PLAYER_STATS)
            self._format_header(ws_players, PLAYER_STATS_HEADERS)

            ws_lb = wb.create_sheet(title=SHEET_LEADERBOARD)
            self._format_header(ws_lb, LEADERBOARD_HEADERS)

            wb.save(self.workbook_path)
            wb.close()
            LOGGER.info("Workbook initialized successfully with headers and filters.")
            return

        try:
            wb = openpyxl.load_workbook(self.workbook_path)
            try:
                # Ensure all 3 sheets exist
                if SHEET_MATCHES not in wb.sheetnames:
                    ws_m = wb.create_sheet(title=SHEET_MATCHES)
                    self._format_header(ws_m, MATCHES_HEADERS)
                if SHEET_PLAYER_STATS not in wb.sheetnames:
                    ws_p = wb.create_sheet(title=SHEET_PLAYER_STATS)
                    self._format_header(ws_p, PLAYER_STATS_HEADERS)
                if SHEET_LEADERBOARD not in wb.sheetnames:
                    ws_l = wb.create_sheet(title=SHEET_LEADERBOARD)
                    self._format_header(ws_l, LEADERBOARD_HEADERS)

                self._sync_recalculate_all_sheets(wb)
                wb.save(self.workbook_path)
            finally:
                wb.close()
        except PermissionError:
            LOGGER.warning("Workbook %s is open in Excel during startup initialization.", self.workbook_path)
        except Exception as exc:
            LOGGER.error("Failed to refresh workbook headers on startup: %s", exc)

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

    def _sync_recalculate_all_sheets(self, wb: openpyxl.Workbook) -> None:
        """Auto-recalculates PlayerStats (clubbed by player) and Leaderboard from all matches.

        Guarantees that redundant rows are merged into 1 row per player and all Excel
        formulas/headers remain perfectly in sync after any save or deletion.
        """
        ws_matches = wb[SHEET_MATCHES]
        matches_info: List[dict] = []

        # Index legacy player rows from PlayerStats if any exist before wipe
        legacy_player_map: Dict[str, List[PlayerStat]] = {}
        if SHEET_PLAYER_STATS in wb.sheetnames:
            ws_p_old = wb[SHEET_PLAYER_STATS]
            for row in ws_p_old.iter_rows(min_row=2, values_only=True):
                if not row or not row[0]:
                    continue
                old_m_id = str(row[0]).strip()
                p_name = canonicalize_player_name(str(row[1]).strip() if len(row) > 1 and row[1] else "")
                if not p_name or p_name.lower() in ("unknown", "player", ""):
                    continue
                # Support both new (col 4 goals, col 5 assists) and legacy (col 3 goals, col 4 assists)
                try:
                    g = int(row[3]) if len(row) > 3 and row[3] is not None else 0
                    a = int(row[4]) if len(row) > 4 and row[4] is not None else 0
                except (ValueError, TypeError):
                    g = 0
                    a = 0
                legacy_player_map.setdefault(old_m_id, []).append(
                    PlayerStat(player=p_name, goals=g, assists=a, result=MatchResult.WIN)
                )

        # Parse matches
        for row_idx, row in enumerate(ws_matches.iter_rows(min_row=2, values_only=True), start=2):
            if not row or not row[0]:
                continue
            m_id = str(row[0]).strip()
            date_val = str(row[1]) if len(row) > 1 and row[1] else ""
            res_str = str(row[2]) if len(row) > 2 and row[2] else "Win"
            sub_by = str(row[3]) if len(row) > 3 and row[3] else "Unknown"
            screen = str(row[4]) if len(row) > 4 and row[4] else ""
            notes = str(row[5]) if len(row) > 5 and row[5] else ""
            p_json = str(row[6]).strip() if len(row) > 6 and row[6] else ""

            p_stats: List[PlayerStat] = []
            if p_json:
                try:
                    items = json.loads(p_json)
                    for it in items:
                        p_stats.append(
                            PlayerStat(
                                player=it["player"],
                                goals=int(it["goals"]),
                                assists=int(it["assists"]),
                                result=MatchResult.from_str(it.get("result", res_str)),
                            )
                        )
                except Exception:
                    p_stats = []

            # If Col 7 was empty, try legacy player rows or notes
            if not p_stats and m_id in legacy_player_map:
                p_stats = legacy_player_map[m_id]
                # Cache serialized stats to Col 7
                ws_matches.cell(
                    row=row_idx,
                    column=7,
                    value=json.dumps([{"player": s.player, "goals": s.goals, "assists": s.assists, "result": s.result.value} for s in p_stats]),
                )

            matches_info.append({
                "match_id": m_id,
                "date": date_val,
                "result": MatchResult.from_str(res_str),
                "submitted_by": sub_by,
                "screenshot_ref": screen,
                "notes": notes,
                "player_stats": p_stats,
            })

        # Club player stats by canonical player name across all matches
        player_data: Dict[str, dict] = {}
        for m in matches_info:
            for stat in m["player_stats"]:
                p_name = canonicalize_player_name(stat.player)
                if not p_name:
                    continue
                if p_name not in player_data:
                    player_data[p_name] = {
                        "player": p_name,
                        "matches": 0,
                        "goals": 0,
                        "assists": 0,
                        "wins": 0,
                        "losses": 0,
                        "latest_match": m["match_id"],
                    }
                data = player_data[p_name]
                data["matches"] += 1
                data["goals"] += stat.goals
                data["assists"] += stat.assists
                data["latest_match"] = m["match_id"]
                if stat.result in (MatchResult.HOME_WIN, MatchResult.WIN):
                    data["wins"] += 1
                elif stat.result in (MatchResult.AWAY_WIN, MatchResult.LOSS):
                    data["losses"] += 1

        # Preserve any manual player entries from Leaderboard sheet if present
        if SHEET_LEADERBOARD in wb.sheetnames:
            ws_lb_old = wb[SHEET_LEADERBOARD]
            for row in ws_lb_old.iter_rows(min_row=2, values_only=True):
                if row and row[0]:
                    old_p = canonicalize_player_name(str(row[0]).strip())
                    if old_p and old_p not in player_data:
                        try:
                            old_mp = int(row[1]) if len(row) > 1 and row[1] is not None else 0
                            old_g = int(row[2]) if len(row) > 2 and row[2] is not None else 0
                            old_a = int(row[3]) if len(row) > 3 and row[3] is not None else 0
                            old_w = int(row[5]) if len(row) > 5 and row[5] is not None else 0
                            old_l = int(row[6]) if len(row) > 6 and row[6] is not None else 0
                            player_data[old_p] = {
                                "player": old_p,
                                "matches": old_mp,
                                "goals": old_g,
                                "assists": old_a,
                                "wins": old_w,
                                "losses": old_l,
                                "latest_match": "Manual",
                            }
                        except Exception:
                            pass

        # 1. Completely rebuild PlayerStats sheet (clubbed, 1 row per player)
        if SHEET_PLAYER_STATS in wb.sheetnames:
            del wb[SHEET_PLAYER_STATS]
        ws_players = wb.create_sheet(title=SHEET_PLAYER_STATS)
        self._format_header(ws_players, PLAYER_STATS_HEADERS)

        for p_name, d in sorted(player_data.items(), key=lambda it: it[0].lower()):
            r = ws_players.max_row + 1
            row_data = [
                d["latest_match"],
                d["player"],
                d["matches"],
                d["goals"],
                d["assists"],
                f"=D{r}+E{r}",
                f"=IF(C{r}>0, D{r}/C{r}, 0)",
                f"=IF(C{r}>0, E{r}/C{r}, 0)",
                f"=G{r}+H{r}",
            ]
            ws_players.append(row_data)
            for c_idx in range(1, len(row_data) + 1):
                cell = ws_players.cell(row=r, column=c_idx)
                cell.font = DATA_FONT
                cell.border = BORDER_THIN
                if c_idx in (1, 3, 4, 5, 6, 7, 8, 9):
                    cell.alignment = Alignment(horizontal="center")
                if c_idx in (7, 8, 9):
                    cell.number_format = "0.00"

        # 2. Completely rebuild Leaderboard sheet (all 12 columns with formulas)
        if SHEET_LEADERBOARD in wb.sheetnames:
            del wb[SHEET_LEADERBOARD]
        ws_lb = wb.create_sheet(title=SHEET_LEADERBOARD)
        self._format_header(ws_lb, LEADERBOARD_HEADERS)

        sorted_players = sorted(
            player_data.values(),
            key=lambda it: (it["goals"], it["goals"] + it["assists"], it["assists"], it["matches"]),
            reverse=True,
        )
        for d in sorted_players:
            r = ws_lb.max_row + 1
            row_data = [
                d["player"],
                d["matches"],
                d["goals"],
                d["assists"],
                f"=C{r}+D{r}",
                d["wins"],
                d["losses"],
                f"=IF(B{r}>0, (F{r}/B{r})*100, 0)",
                f"=IF(B{r}>0, (G{r}/B{r})*100, 0)",
                f"=IF(B{r}>0, C{r}/B{r}, 0)",
                f"=IF(B{r}>0, D{r}/B{r}, 0)",
                f"=IF(B{r}>0, E{r}/B{r}, 0)",
            ]
            ws_lb.append(row_data)
            for c_idx in range(1, len(row_data) + 1):
                cell = ws_lb.cell(row=r, column=c_idx)
                cell.font = DATA_FONT
                cell.border = BORDER_THIN
                if c_idx >= 2:
                    cell.alignment = Alignment(horizontal="center")
                if c_idx in (8, 9):
                    cell.number_format = '0.0"%"'
                elif c_idx in (10, 11, 12):
                    cell.number_format = "0.00"

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
            ws_matches = wb[SHEET_MATCHES]
            for row in ws_matches.iter_rows(min_row=2, max_col=1, values_only=True):
                if row and row[0] == match.match_id:
                    raise ValueError(f"Match ID '{match.match_id}' already exists in the workbook.")

            p_json = json.dumps([
                {
                    "player": p.player,
                    "goals": int(p.goals),
                    "assists": int(p.assists),
                    "result": p.result.value,
                }
                for p in match.player_stats
            ])

            match_row = [
                match.match_id,
                match.date,
                match.result.value,
                match.submitted_by,
                match.screenshot_ref,
                match.notes,
                p_json,
            ]
            ws_matches.append(match_row)
            new_r = ws_matches.max_row
            for col_idx in range(1, len(match_row) + 1):
                cell = ws_matches.cell(row=new_r, column=col_idx)
                cell.font = DATA_FONT
                cell.border = BORDER_THIN
                if col_idx in (1, 2, 3):
                    cell.alignment = Alignment(horizontal="center")

            # Auto-recalculate PlayerStats (clubbed) & Leaderboard (12 columns)
            self._sync_recalculate_all_sheets(wb)

            wb.save(self.workbook_path)
            LOGGER.info("Saved match %s and auto-recalculated stats", match.match_id)
            return match
        finally:
            wb.close()

    async def delete_match(self, match_id: str) -> bool:
        """Removes a match record and auto-recalculates all statistics atomically."""
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
            target_row = -1
            for row_idx in range(2, ws_matches.max_row + 1):
                val = ws_matches.cell(row=row_idx, column=1).value
                if val and str(val).strip() == match_id.strip():
                    target_row = row_idx
                    break

            if target_row == -1:
                LOGGER.warning("Attempted to delete non-existent match ID: %s", match_id)
                return False

            ws_matches.delete_rows(target_row, 1)

            # Auto-recalculate all sheets immediately from remaining matches!
            self._sync_recalculate_all_sheets(wb)

            wb.save(self.workbook_path)
            LOGGER.info("Deleted match %s and auto-recalculated all sheets", match_id)
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
            for row in ws_matches.iter_rows(min_row=2, values_only=True):
                if row and row[0] and str(row[0]).strip() == match_id.strip():
                    m_id = str(row[0]).strip()
                    date_val = str(row[1]) if len(row) > 1 and row[1] else ""
                    res = MatchResult.from_str(str(row[2])) if len(row) > 2 and row[2] else MatchResult.WIN
                    sub_by = str(row[3]) if len(row) > 3 and row[3] else "Unknown"
                    screen = str(row[4]) if len(row) > 4 and row[4] else ""
                    notes = str(row[5]) if len(row) > 5 and row[5] else ""
                    p_json = str(row[6]).strip() if len(row) > 6 and row[6] else ""

                    p_stats: List[PlayerStat] = []
                    if p_json:
                        try:
                            items = json.loads(p_json)
                            p_stats = [
                                PlayerStat(
                                    player=it["player"],
                                    goals=int(it["goals"]),
                                    assists=int(it["assists"]),
                                    result=MatchResult.from_str(it.get("result", res.value)),
                                )
                                for it in items
                            ]
                        except Exception:
                            p_stats = []

                    return MatchRecord(
                        match_id=m_id,
                        date=date_val,
                        result=res,
                        submitted_by=sub_by,
                        screenshot_ref=screen,
                        notes=notes,
                        player_stats=p_stats,
                    )
            return None
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
            matches: List[MatchRecord] = []
            for row in ws_matches.iter_rows(min_row=2, values_only=True):
                if not row or not row[0]:
                    continue
                m_id = str(row[0]).strip()
                date_val = str(row[1]) if len(row) > 1 and row[1] else ""
                res = MatchResult.from_str(str(row[2])) if len(row) > 2 and row[2] else MatchResult.WIN
                sub_by = str(row[3]) if len(row) > 3 and row[3] else "Unknown"
                screen = str(row[4]) if len(row) > 4 and row[4] else ""
                notes = str(row[5]) if len(row) > 5 and row[5] else ""
                p_json = str(row[6]).strip() if len(row) > 6 and row[6] else ""

                p_stats: List[PlayerStat] = []
                if p_json:
                    try:
                        items = json.loads(p_json)
                        p_stats = [
                            PlayerStat(
                                player=it["player"],
                                goals=int(it["goals"]),
                                assists=int(it["assists"]),
                                result=MatchResult.from_str(it.get("result", res.value)),
                            )
                            for it in items
                        ]
                    except Exception:
                        p_stats = []

                matches.append(
                    MatchRecord(
                        match_id=m_id,
                        date=date_val,
                        result=res,
                        submitted_by=sub_by,
                        screenshot_ref=screen,
                        notes=notes,
                        player_stats=p_stats,
                    )
                )
            return matches
        finally:
            wb.close()

    async def get_all_player_stats(self) -> List[PlayerStat]:
        """Loads all raw player stat entries across matches."""
        async with self._lock:
            return self._sync_get_all_player_stats()

    def _sync_get_all_player_stats(self) -> List[PlayerStat]:
        matches = self._sync_get_all_matches()
        return [stat for m in matches for stat in m.player_stats]

    async def get_leaderboard_from_sheet(self) -> List[dict]:
        """Loads player leaderboard rankings directly from the Excel Leaderboard sheet."""
        async with self._lock:
            return self._sync_get_leaderboard_from_sheet()

    def _sync_get_leaderboard_from_sheet(self) -> List[dict]:
        if not self.workbook_path.exists():
            return []

        wb = openpyxl.load_workbook(self.workbook_path, data_only=True)
        try:
            if SHEET_LEADERBOARD not in wb.sheetnames:
                return []
            ws = wb[SHEET_LEADERBOARD]
            records = []
            for r in range(2, ws.max_row + 1):
                p_val = ws.cell(row=r, column=1).value
                if not p_val:
                    continue
                player = str(p_val).strip()

                def _safe_int(c: int) -> int:
                    val = ws.cell(row=r, column=c).value
                    try:
                        return int(float(val)) if val is not None else 0
                    except (ValueError, TypeError):
                        return 0

                def _safe_float(c: int) -> float:
                    val = ws.cell(row=r, column=c).value
                    try:
                        return float(val) if val is not None else 0.0
                    except (ValueError, TypeError):
                        return 0.0

                mp = _safe_int(2)
                goals = _safe_int(3)
                assists = _safe_int(4)
                ga_raw = ws.cell(row=r, column=5).value
                ga = int(float(ga_raw)) if isinstance(ga_raw, (int, float)) else (goals + assists)

                wins = _safe_int(6)
                losses = _safe_int(7)
                draws = max(0, mp - wins - losses)

                wr_raw = ws.cell(row=r, column=8).value
                win_rate = float(wr_raw) if isinstance(wr_raw, (int, float)) else (round((wins / mp) * 100, 1) if mp > 0 else 0.0)

                lr_raw = ws.cell(row=r, column=9).value
                loss_rate = float(lr_raw) if isinstance(lr_raw, (int, float)) else (round((losses / mp) * 100, 1) if mp > 0 else 0.0)

                gpg_raw = ws.cell(row=r, column=10).value
                gpg = float(gpg_raw) if isinstance(gpg_raw, (int, float)) else (round(goals / mp, 2) if mp > 0 else 0.0)

                apg_raw = ws.cell(row=r, column=11).value
                apg = float(apg_raw) if isinstance(apg_raw, (int, float)) else (round(assists / mp, 2) if mp > 0 else 0.0)

                gapg_raw = ws.cell(row=r, column=12).value
                gapg = float(gapg_raw) if isinstance(gapg_raw, (int, float)) else (round(ga / mp, 2) if mp > 0 else 0.0)

                records.append({
                    "player": player,
                    "matches_played": mp,
                    "goals": goals,
                    "assists": assists,
                    "ga": ga,
                    "wins": wins,
                    "losses": losses,
                    "draws": draws,
                    "win_rate": win_rate,
                    "loss_rate": loss_rate,
                    "gpg": gpg,
                    "apg": apg,
                    "gapg": gapg,
                })
            return records
        finally:
            wb.close()

    async def update_leaderboard_sheet(self, aggregates: List[PlayerAggregateRecord]) -> None:
        """Legacy helper maintained for backward compatibility."""
        async with self._lock:
            wb = openpyxl.load_workbook(self.workbook_path)
            try:
                self._sync_recalculate_all_sheets(wb)
                wb.save(self.workbook_path)
            finally:
                wb.close()
