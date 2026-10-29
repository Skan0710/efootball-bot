"""Discord slash commands, interactive views, and modals for eFootball bot."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from config import BotConfig
from excel_manager import ExcelManager
from models import (
    LeaderboardCategory,
    MatchRecord,
    MatchResult,
    OCRPlayerCandidate,
    PlayerStat,
)
from ocr import extract_player_stats_from_screenshot, validate_image_file
from stats import (
    calculate_player_aggregates,
    get_all_teammates,
    get_leaderboard,
    get_player_record,
)

LOGGER = logging.getLogger("efootball_bot.commands")

# In-memory store of recent image hashes to prevent accidental duplicate submissions
RECENT_IMAGE_HASHES: set[str] = set()


def compute_file_sha256(file_path: Path) -> str:
    """Computes SHA-256 hex digest of a file."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


async def download_image_source(
    attachment: Optional[discord.Attachment],
    url: Optional[str],
    dest_path: Path,
    max_bytes: int,
) -> Tuple[bool, Optional[str]]:
    """Saves an uploaded/pasted attachment or downloads an image from a URL."""
    if attachment is not None:
        if attachment.size > max_bytes:
            return False, f"Image size exceeds limit of {max_bytes / (1024 * 1024):.1f} MB."
        await attachment.save(dest_path)
        return validate_image_file(dest_path, max_bytes=max_bytes)

    if url is not None and url.strip():
        clean_url = url.strip()
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(clean_url) as resp:
                    if resp.status != 200:
                        return False, f"Failed to download image from URL (HTTP status {resp.status})."
                    content_length = resp.headers.get("Content-Length")
                    if content_length and int(content_length) > max_bytes:
                        return False, f"Remote image exceeds limit of {max_bytes / (1024 * 1024):.1f} MB."
                    data = await resp.read()
                    if len(data) > max_bytes:
                        return False, f"Remote image exceeds limit of {max_bytes / (1024 * 1024):.1f} MB."
                    dest_path.write_bytes(data)
            return validate_image_file(dest_path, max_bytes=max_bytes)
        except Exception as exc:
            LOGGER.error("Error downloading image from URL %s: %s", clean_url, exc)
            return False, f"Could not download image from URL: {exc}"

    return False, "No image attachment or URL was provided."


def check_guild_and_channel(interaction: discord.Interaction, config: BotConfig) -> Optional[str]:
    """Validates that interaction originated from configured guild and allowed channel."""
    if interaction.guild_id != config.guild_id:
        return f"This bot is restricted to server ID `{config.guild_id}`."
    if config.allowed_channel_id and interaction.channel_id != config.allowed_channel_id:
        return f"Commands are restricted to <#{config.allowed_channel_id}>."
    return None


def is_user_admin(interaction: discord.Interaction, config: BotConfig) -> bool:
    """Checks whether the user has administrator permission or the configured admin role."""
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return False

    # Check Discord Administrator permission
    if interaction.user.guild_permissions.administrator:
        return True

    # Check configured admin role
    if config.admin_role_id:
        return any(role.id == config.admin_role_id for role in interaction.user.roles)

    return False


def parse_players_from_team_param(team_param: Optional[str]) -> List[str]:
    """Parses player names if user provided a string like 'S1 and S2', 'S1 & S2', 'S1, S2'."""
    if not team_param:
        return []
    s = team_param.strip()
    if s.lower() in ("home", "away", "opponent", "opponents", "none", "xi", "team"):
        return []
    parts = re.split(r"\s+(?:and|&|\+)\s*|,\s*", s, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


def build_preview_embed(
    candidates: List[OCRPlayerCandidate],
    result: MatchResult,
    home_team: str = "Home",
    away_team: str = "Away",
    home_score: Optional[int] = None,
    away_score: Optional[int] = None,
    away_candidates: Optional[List[OCRPlayerCandidate]] = None,
    error_note: Optional[str] = None,
) -> discord.Embed:
    """Builds the ephemeral verification embed for post-match statistics."""
    color = (
        discord.Color.green()
        if result in (MatchResult.HOME_WIN, MatchResult.WIN)
        else (discord.Color.red() if result in (MatchResult.AWAY_WIN, MatchResult.LOSS) else discord.Color.gold())
    )

    h_display = home_score if home_score is not None else (sum(c.goals for c in candidates) if candidates else 0)
    a_display = (
        away_score
        if away_score is not None
        else (sum(a.goals for a in (away_candidates or [])) if away_candidates else 0)
    )

    title_score = f" ({h_display} - {a_display})"
    embed = discord.Embed(
        title=f"Match Preview — Result: {result.value}{title_score}",
        description=(
            "Please review the extracted player stats below.\n"
            "Click **Confirm & Save** to persist, **Edit Stats** to make corrections, or **Cancel**."
        ),
        color=color,
        timestamp=datetime.now(),
    )

    # Determine team outcome tags
    if result in (MatchResult.HOME_WIN, MatchResult.WIN):
        h_tag = "WIN 🏆"
        a_tag = "LOSS ❌"
    elif result in (MatchResult.AWAY_WIN, MatchResult.LOSS):
        h_tag = "LOSS ❌"
        a_tag = "WIN 🏆"
    else:
        h_tag = "DRAW 🤝"
        a_tag = "DRAW 🤝"

    embed.add_field(
        name="🏟️ Match Overview",
        value=(
            f"🏠 **{home_team}** ({h_tag}): `{h_display}`\n"
            f"✈️ **{away_team}** ({a_tag}): `{a_display}`\n"
            f"🏆 **Overall Outcome:** **{result.value}**"
        ),
        inline=False,
    )

    # 1. Home Team Players
    if candidates:
        home_lines = [f"⚽ **{c.player}** — Goals: `{c.goals}` | Assists: `{c.assists}`" for c in candidates]
        embed.add_field(
            name=f"🏠 {home_team} Players ({h_tag})",
            value="\n".join(home_lines),
            inline=False,
        )
    else:
        embed.add_field(
            name=f"🏠 {home_team} Players ({h_tag})",
            value="*No players detected. Click **Edit Stats** to add manually.*",
            inline=False,
        )

    # 2. Away Team Players
    if away_candidates:
        away_lines = [f"✈️ **{a.player}** — Goals: `{a.goals}` | Assists: `{a.assists}`" for a in away_candidates]
        embed.add_field(
            name=f"✈️ {away_team} Players ({a_tag})",
            value="\n".join(away_lines),
            inline=False,
        )

    if error_note:
        embed.add_field(
            name="⚠️ OCR Notice",
            value=error_note[:1024],
            inline=False,
        )

    embed.set_footer(text="Only the submitting user can interact with these controls.")
    return embed


def parse_modal_candidate_lines(
    text: str, prefix: str = ""
) -> Tuple[List[OCRPlayerCandidate], List[str]]:
    """Helper to parse multi-line comma-separated player, goals, assists text."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    candidates: List[OCRPlayerCandidate] = []
    errors: List[str] = []

    for line_num, line in enumerate(lines, start=1):
        parts = [p.strip() for p in line.split(",")]
        label = f"{prefix} Line {line_num}" if prefix else f"Line {line_num}"
        if len(parts) != 3:
            errors.append(f"{label}: Expected 'Player, Goals, Assists', got '{line}'")
            continue

        name, goals_str, assists_str = parts
        if not name:
            errors.append(f"{label}: Player name cannot be empty.")
            continue

        try:
            goals = int(goals_str)
            assists = int(assists_str)
            if goals < 0 or assists < 0:
                errors.append(f"{label}: Goals and assists cannot be negative.")
                continue
        except ValueError:
            errors.append(f"{label}: Goals and assists must be whole numbers.")
            continue

        candidates.append(
            OCRPlayerCandidate(player=name, goals=goals, assists=assists, confidence_note="Manual entry")
        )

    return candidates, errors


class EditStatsModal(discord.ui.Modal, title="Edit Match Players, Stats & Result"):
    """Modal allowing manual correction of player names, goals, assists, scores, and result."""

    home_stats_input = discord.ui.TextInput(
        label="Home Players: Name, Goals, Assists",
        style=discord.TextStyle.paragraph,
        placeholder="Skan, 3, 1\nSuri, 1, 2",
        required=True,
        max_length=500,
    )

    away_stats_input = discord.ui.TextInput(
        label="Away Players: Name, Goals, Assists",
        style=discord.TextStyle.paragraph,
        placeholder="Alex, 1, 0\nBob, 0, 1",
        required=False,
        max_length=500,
    )

    scores_input = discord.ui.TextInput(
        label="Home Score, Away Score (e.g. 4, 2)",
        style=discord.TextStyle.short,
        placeholder="4, 2",
        required=False,
        max_length=50,
    )

    teams_input = discord.ui.TextInput(
        label="Home Team, Away Team (optional)",
        style=discord.TextStyle.short,
        placeholder="Home, Away",
        required=False,
        max_length=100,
    )

    result_input = discord.ui.TextInput(
        label="Result: Home Win, Away Win, or Draw",
        style=discord.TextStyle.short,
        placeholder="Home Win",
        required=False,
        max_length=20,
    )

    def __init__(self, parent_view: MatchPreviewView) -> None:
        super().__init__()
        self.parent_view = parent_view
        self.stats_input = self.home_stats_input

        h_lines = [f"{c.player}, {c.goals}, {c.assists}" for c in parent_view.candidates]
        self.home_stats_input.default = "\n".join(h_lines) if h_lines else ""

        a_lines = [f"{a.player}, {a.goals}, {a.assists}" for a in (parent_view.away_candidates or [])]
        self.away_stats_input.default = "\n".join(a_lines) if a_lines else ""

        self.result_input.default = parent_view.result.value

        h_score = getattr(parent_view, "home_score", None)
        a_score = getattr(parent_view, "away_score", None)
        if h_score is not None and a_score is not None:
            self.scores_input.default = f"{h_score}, {a_score}"

        h_team = getattr(parent_view, "home_team", "Home")
        a_team = getattr(parent_view, "away_team", "Away")
        self.teams_input.default = f"{h_team}, {a_team}"

    def _parse_candidate_lines(
        self, text: str, prefix: str = ""
    ) -> Tuple[List[OCRPlayerCandidate], List[str]]:
        return parse_modal_candidate_lines(text, prefix=prefix)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        new_home_c, home_errs = self._parse_candidate_lines(self.home_stats_input.value, prefix="Home")
        new_away_c, away_errs = self._parse_candidate_lines(self.away_stats_input.value, prefix="Away")
        all_errors = home_errs + away_errs

        if all_errors:
            error_msg = "\n".join(all_errors[:5])
            await interaction.response.send_message(
                f"❌ **Validation Errors in Edited Stats:**\n{error_msg}\n\nPlease click **Edit Stats** and try again.",
                ephemeral=True,
            )
            return

        # Update Teams if provided
        teams_val = self.teams_input.value.strip()
        if teams_val and "," in teams_val:
            h_t, a_t = [t.strip() for t in teams_val.split(",", 1)]
            if h_t:
                self.parent_view.home_team = h_t
            if a_t:
                self.parent_view.away_team = a_t

        # Update Scores if provided
        scores_val = self.scores_input.value.strip()
        if scores_val and "," in scores_val:
            try:
                hs_str, as_str = [s.strip() for s in scores_val.split(",", 1)]
                self.parent_view.home_score = int(hs_str)
                self.parent_view.away_score = int(as_str)
            except ValueError:
                pass
        else:
            self.parent_view.home_score = sum(c.goals for c in new_home_c)
            self.parent_view.away_score = sum(a.goals for a in new_away_c)

        # Update Result if provided
        res_val = self.result_input.value.strip()
        if res_val:
            try:
                self.parent_view.result = MatchResult.from_str(res_val)
            except ValueError:
                await interaction.response.send_message(
                    f"❌ Invalid result '{res_val}'. Must be Home Win, Away Win, or Draw.",
                    ephemeral=True,
                )
                return
        else:
            # Auto-calculate result from scores
            h_s = self.parent_view.home_score or 0
            a_s = self.parent_view.away_score or 0
            if h_s > a_s:
                self.parent_view.result = MatchResult.HOME_WIN
            elif h_s < a_s:
                self.parent_view.result = MatchResult.AWAY_WIN
            else:
                self.parent_view.result = MatchResult.DRAW

        self.parent_view.candidates = new_home_c
        self.parent_view.away_candidates = new_away_c
        self.parent_view.error_note = None

        new_embed = build_preview_embed(
            candidates=self.parent_view.candidates,
            result=self.parent_view.result,
            home_team=self.parent_view.home_team,
            away_team=self.parent_view.away_team,
            home_score=self.parent_view.home_score,
            away_score=self.parent_view.away_score,
            away_candidates=self.parent_view.away_candidates,
        )
        await interaction.response.edit_message(embed=new_embed, view=self.parent_view)


class MatchPreviewView(discord.ui.View):
    """Interactive preview controls for verifying and persisting extracted stats."""

    def __init__(
        self,
        submitting_user_id: int,
        candidates: List[OCRPlayerCandidate],
        result: MatchResult,
        excel_manager: ExcelManager,
        image_hash: str,
        home_team: str = "Home",
        away_team: str = "Away",
        home_score: Optional[int] = None,
        away_score: Optional[int] = None,
        away_candidates: Optional[List[OCRPlayerCandidate]] = None,
        notes: str = "",
        error_note: Optional[str] = None,
        timeout: float = 300.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.submitting_user_id = submitting_user_id
        self.candidates = candidates
        self.result = result
        self.excel_manager = excel_manager
        self.image_hash = image_hash
        self.home_team = home_team
        self.away_team = away_team
        self.home_score = home_score
        self.away_score = away_score
        self.away_candidates = away_candidates or []
        self.notes = notes
        self.error_note = error_note

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.submitting_user_id:
            await interaction.response.send_message(
                "❌ You cannot interact with another player's match preview.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Confirm & Save", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self.candidates and not self.away_candidates:
            await interaction.response.send_message(
                "❌ Cannot save a match with 0 players. Use **Edit Stats** to add players first.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        match_id = self.excel_manager.generate_match_id()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Determine individual player result for Home and Away players:
        # Home Win -> Home players get WIN, Away players get LOSS
        # Away Win -> Home players get LOSS, Away players get WIN
        # Draw -> Both get DRAW
        if self.result in (MatchResult.HOME_WIN, MatchResult.WIN):
            h_outcome = MatchResult.WIN
            a_outcome = MatchResult.LOSS
        elif self.result in (MatchResult.AWAY_WIN, MatchResult.LOSS):
            h_outcome = MatchResult.LOSS
            a_outcome = MatchResult.WIN
        else:
            h_outcome = MatchResult.DRAW
            a_outcome = MatchResult.DRAW

        player_stats: List[PlayerStat] = []
        for c in self.candidates:
            player_stats.append(
                PlayerStat(
                    player=c.player,
                    goals=c.goals,
                    assists=c.assists,
                    result=h_outcome,
                )
            )

        for a in self.away_candidates:
            player_stats.append(
                PlayerStat(
                    player=a.player,
                    goals=a.goals,
                    assists=a.assists,
                    result=a_outcome,
                )
            )

        h_val = self.home_score if self.home_score is not None else sum(c.goals for c in self.candidates)
        a_val = self.away_score if self.away_score is not None else sum(a.goals for a in self.away_candidates)
        final_notes = self.notes or f"Score: {self.home_team} {h_val} - {a_val} {self.away_team} ({self.result.value})"

        record = MatchRecord(
            match_id=match_id,
            date=now_str,
            result=self.result,
            submitted_by=interaction.user.display_name,
            screenshot_ref=f"sha256:{self.image_hash[:16]}",
            notes=final_notes,
            player_stats=player_stats,
        )

        try:
            await self.excel_manager.save_match(record)
            RECENT_IMAGE_HASHES.add(self.image_hash)

            # Recalculate aggregates and update Leaderboard sheet in workbook
            all_stats = await self.excel_manager.get_all_player_stats()
            aggregates = calculate_player_aggregates(all_stats)
            await self.excel_manager.update_leaderboard_sheet(list(aggregates.values()))

            # Disable buttons
            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    child.disabled = True

            score_line = f" ({self.home_team} **{h_val} - {a_val}** {self.away_team})"
            success_embed = discord.Embed(
                title=f"✅ Match Saved — ID: {match_id}",
                description=f"Match recorded for **{len(player_stats)}** player(s) as a **{self.result.value}**{score_line}.",
                color=discord.Color.green(),
                timestamp=datetime.now(),
            )
            for ps in player_stats:
                is_win = ps.result in (MatchResult.WIN, MatchResult.HOME_WIN)
                res_tag = "🏆 Win" if is_win else ("❌ Loss" if ps.result in (MatchResult.LOSS, MatchResult.AWAY_WIN) else "🤝 Draw")
                success_embed.add_field(
                    name=f"⚽ {ps.player} ({res_tag})",
                    value=f"Goals: **{ps.goals}** | Assists: **{ps.assists}**",
                    inline=True,
                )
            success_embed.set_footer(text="Saved to data/matches.xlsx. Use /leaderboard or /record to view updated stats.")

            await interaction.edit_original_response(embed=success_embed, view=self)
            LOGGER.info("Match %s saved by user %s (%s)", match_id, interaction.user.name, interaction.user.id)
        except PermissionError:
            LOGGER.warning("Excel workbook locked during match save: %s", match_id)
            await interaction.followup.send(
                "⚠️ **Excel File Locked:** `data/matches.xlsx` is currently open in Microsoft Excel or another application. Please close the file and click **Confirm & Save** again.",
                ephemeral=True,
            )
        except Exception as exc:
            LOGGER.error("Failed to persist match %s: %s", match_id, exc)
            await interaction.followup.send(
                f"❌ An error occurred while saving the match to Excel: {exc}",
                ephemeral=True,
            )

    @discord.ui.button(label="Edit Stats", style=discord.ButtonStyle.primary, emoji="✏️")
    async def edit_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        modal = EditStatsModal(parent_view=self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="❌")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

        cancel_embed = discord.Embed(
            title="🚫 Match Submission Cancelled",
            description="The match preview was discarded. No data was saved.",
            color=discord.Color.light_grey(),
        )
        await interaction.response.edit_message(embed=cancel_embed, view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True


class AddMatchTextModal(discord.ui.Modal, title="Edit Match Players, Stats & Result"):
    """Modal allowing direct match entry via text without screenshot uploads."""

    home_stats_input = discord.ui.TextInput(
        label="Home Players: Name, Goals, Assists",
        style=discord.TextStyle.paragraph,
        placeholder="Skan, 2, 0\nLilly, 0, 1",
        required=True,
        max_length=500,
    )

    away_stats_input = discord.ui.TextInput(
        label="Away Players: Name, Goals, Assists",
        style=discord.TextStyle.paragraph,
        placeholder="Suri, 1, 2\nAnnie, 2, 1",
        required=False,
        max_length=500,
    )

    scores_input = discord.ui.TextInput(
        label="Home Score, Away Score (e.g. 4, 2)",
        style=discord.TextStyle.short,
        placeholder="2, 3",
        required=False,
        max_length=50,
    )

    teams_input = discord.ui.TextInput(
        label="Home Team, Away Team (optional)",
        style=discord.TextStyle.short,
        placeholder="Skan & Lilly, Suri & Annie",
        required=False,
        max_length=100,
    )

    result_input = discord.ui.TextInput(
        label="Result: Home Win, Away Win, or Draw",
        style=discord.TextStyle.short,
        placeholder="Away Win",
        required=False,
        max_length=20,
    )

    def __init__(self, excel_manager: ExcelManager) -> None:
        super().__init__()
        self.excel_manager = excel_manager

    async def on_submit(self, interaction: discord.Interaction) -> None:
        new_home_c, home_errs = parse_modal_candidate_lines(self.home_stats_input.value, prefix="Home")
        new_away_c, away_errs = parse_modal_candidate_lines(self.away_stats_input.value, prefix="Away")
        all_errors = home_errs + away_errs

        if all_errors:
            error_msg = "\n".join(all_errors[:5])
            await interaction.response.send_message(
                f"❌ **Validation Errors in Stats:**\n{error_msg}\n\nPlease run `/add-match-text` and try again.",
                ephemeral=True,
            )
            return

        h_team = "Home"
        a_team = "Away"
        teams_val = self.teams_input.value.strip()
        if teams_val:
            if "," in teams_val:
                h_t, a_t = [t.strip() for t in teams_val.split(",", 1)]
                if h_t:
                    h_team = h_t
                if a_t:
                    a_team = a_t
            else:
                h_team = teams_val

        scores_val = self.scores_input.value.strip()
        h_score: Optional[int] = None
        a_score: Optional[int] = None
        if scores_val and "," in scores_val:
            try:
                hs_str, as_str = [s.strip() for s in scores_val.split(",", 1)]
                h_score = int(hs_str)
                a_score = int(as_str)
            except ValueError:
                h_score = sum(c.goals for c in new_home_c)
                a_score = sum(a.goals for a in new_away_c)
        else:
            h_score = sum(c.goals for c in new_home_c)
            a_score = sum(a.goals for a in new_away_c)

        res_val = self.result_input.value.strip()
        if res_val:
            try:
                res = MatchResult.from_str(res_val)
            except ValueError:
                await interaction.response.send_message(
                    f"❌ Invalid result '{res_val}'. Must be Home Win, Away Win, or Draw.",
                    ephemeral=True,
                )
                return
        else:
            if (h_score or 0) > (a_score or 0):
                res = MatchResult.HOME_WIN
            elif (h_score or 0) < (a_score or 0):
                res = MatchResult.AWAY_WIN
            else:
                res = MatchResult.DRAW

        preview_embed = build_preview_embed(
            candidates=new_home_c,
            result=res,
            home_team=h_team,
            away_team=a_team,
            home_score=h_score,
            away_score=a_score,
            away_candidates=new_away_c,
        )

        score_note = f"Score: {h_team} {h_score} - {a_score} {a_team} ({res.value})"
        view = MatchPreviewView(
            submitting_user_id=interaction.user.id,
            candidates=new_home_c,
            result=res,
            excel_manager=self.excel_manager,
            image_hash="",
            home_team=h_team,
            away_team=a_team,
            home_score=h_score,
            away_score=a_score,
            away_candidates=new_away_c,
            notes=score_note,
        )

        await interaction.response.send_message(embed=preview_embed, view=view, ephemeral=True)


class DeleteConfirmView(discord.ui.View):
    """Confirmation dialog before executing destructive match deletion."""

    def __init__(
        self,
        submitting_user_id: int,
        match_id: str,
        excel_manager: ExcelManager,
        timeout: float = 60.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.submitting_user_id = submitting_user_id
        self.match_id = match_id
        self.excel_manager = excel_manager

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.submitting_user_id:
            await interaction.response.send_message("❌ You cannot authorize this deletion.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm Deletion", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm_delete(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        deleted = await self.excel_manager.delete_match(self.match_id)

        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

        if deleted:
            # Refresh leaderboard sheet in workbook
            all_stats = await self.excel_manager.get_all_player_stats()
            aggregates = calculate_player_aggregates(all_stats)
            await self.excel_manager.update_leaderboard_sheet(list(aggregates.values()))

            embed = discord.Embed(
                title="🗑️ Match Deleted",
                description=f"Match **{self.match_id}** and all associated player statistics were permanently removed.",
                color=discord.Color.red(),
            )
            LOGGER.info("Admin %s deleted match %s", interaction.user.name, self.match_id)
            await interaction.edit_original_response(embed=embed, view=self)
        else:
            await interaction.edit_original_response(
                content=f"❌ Match **{self.match_id}** could not be found or was already deleted.",
                view=self,
            )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="↩️")
    async def cancel_delete(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

        await interaction.response.edit_message(
            content="Deletion cancelled. Match remains intact.", embed=None, view=self
        )


class DeleteAllConfirmView(discord.ui.View):
    """Confirmation dialog before wiping all match data."""

    def __init__(
        self,
        submitting_user_id: int,
        excel_manager: ExcelManager,
        timeout: float = 60.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.submitting_user_id = submitting_user_id
        self.excel_manager = excel_manager

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.submitting_user_id:
            await interaction.response.send_message("❌ You cannot authorize this action.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm Delete All", style=discord.ButtonStyle.danger, emoji="💥")
    async def confirm_delete_all(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        count = await self.excel_manager.delete_all_matches()

        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

        embed = discord.Embed(
            title="💥 All Match Data Deleted",
            description=f"Successfully wiped all **{count}** match records and associated statistics from the workbook.",
            color=discord.Color.red(),
        )
        LOGGER.info("Admin %s executed delete_all_matches (%d matches wiped)", interaction.user.name, count)
        await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="↩️")
    async def cancel_delete(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

        await interaction.response.edit_message(
            content="Action cancelled. All match records remain intact.", embed=None, view=self
        )


async def handle_pasted_match_message(
    message: discord.Message,
    attachments: List[discord.Attachment],
    config: BotConfig,
    excel_manager: ExcelManager,
) -> None:
    """Handles screenshots pasted directly into the channel via Ctrl+V."""
    if not attachments:
        return

    temp_dir = Path(tempfile.gettempdir()) / "efootball_ocr"
    temp_dir.mkdir(parents=True, exist_ok=True)

    file_1 = temp_dir / f"paste_1_{message.id}_{attachments[0].id}.png"
    file_2: Optional[Path] = None

    try:
        ok1, err1 = await download_image_source(attachments[0], None, file_1, config.max_image_bytes)
        if not ok1:
            await message.reply(f"❌ Could not process 1st image: {err1}", delete_after=15)
            return

        ocr_1 = extract_player_stats_from_screenshot(file_1, tesseract_cmd=config.tesseract_cmd)
        home_candidates = list(ocr_1.candidates)
        home_score = sum(c.goals for c in home_candidates)
        image_hash = compute_file_sha256(file_1)

        away_score: Optional[int] = None
        away_candidates: List[OCRPlayerCandidate] = []
        if len(attachments) > 1:
            file_2 = temp_dir / f"paste_2_{message.id}_{attachments[1].id}.png"
            ok2, err2 = await download_image_source(attachments[1], None, file_2, config.max_image_bytes)
            if ok2:
                ocr_2 = extract_player_stats_from_screenshot(file_2, tesseract_cmd=config.tesseract_cmd)
                away_candidates = list(ocr_2.candidates)
                away_score = sum(c.goals for c in away_candidates)

        a_score = away_score if away_score is not None else 0
        if home_score > a_score:
            computed_res = MatchResult.HOME_WIN
        elif home_score < a_score:
            computed_res = MatchResult.AWAY_WIN
        else:
            computed_res = MatchResult.DRAW

        embed = build_preview_embed(
            candidates=home_candidates,
            result=computed_res,
            home_team="Home",
            away_team="Away",
            home_score=home_score,
            away_score=away_score,
            away_candidates=away_candidates,
            error_note=ocr_1.error_reason if not ocr_1.success else None,
        )

        view = MatchPreviewView(
            submitting_user_id=message.author.id,
            candidates=home_candidates,
            result=computed_res,
            excel_manager=excel_manager,
            image_hash=image_hash,
            home_team="Home",
            away_team="Away",
            home_score=home_score,
            away_score=away_score,
            away_candidates=away_candidates,
            error_note=ocr_1.error_reason if not ocr_1.success else None,
        )

        await message.reply(
            content=f"📸 **Post-Match Screenshots Detected** from {message.author.mention}!",
            embed=embed,
            view=view,
        )
    finally:
        if file_1.exists():
            try:
                file_1.unlink()
            except OSError:
                pass
        if file_2 and file_2.exists():
            try:
                file_2.unlink()
            except OSError:
                pass


def register_commands(
    tree: app_commands.CommandTree,
    config: BotConfig,
    excel_manager: ExcelManager,
) -> None:
    """Registers the six required eFootball slash commands to the bot command tree."""

    # 1. /add-match-image
    @tree.command(
        name="add-match-image",
        description="Submit eFootball post-match screenshot(s) to record player stats.",
    )
    @app_commands.describe(
        image="1st Image / Home team screenshot (paste Ctrl+V or upload)",
        image2="2nd Image / Away team screenshot (paste Ctrl+V or upload) - optional",
        home_team="Home team name (optional, default: Home)",
        away_team="Away team name (optional, default: Away)",
        home_score="Home score (optional; auto-set to extracted goals from 1st image)",
        away_score="Away score (optional; auto-set to extracted goals from 2nd image if provided)",
        result="Match outcome (Home Win, Away Win, or Draw; auto-calculated from scores if omitted)",
        image_url="Or paste direct image URL for Home team (optional)",
        image2_url="Or paste direct image URL for Away team (optional)",
    )
    @app_commands.choices(
        result=[
            app_commands.Choice(name="Home Win", value="Home Win"),
            app_commands.Choice(name="Away Win", value="Away Win"),
            app_commands.Choice(name="Draw", value="Draw"),
        ]
    )
    async def add_match_image(
        interaction: discord.Interaction,
        image: Optional[discord.Attachment] = None,
        image2: Optional[discord.Attachment] = None,
        home_team: Optional[str] = "Home",
        away_team: Optional[str] = "Away",
        home_score: Optional[int] = None,
        away_score: Optional[int] = None,
        result: Optional[app_commands.Choice[str]] = None,
        image_url: Optional[str] = None,
        image2_url: Optional[str] = None,
    ) -> None:
        err = check_guild_and_channel(interaction, config)
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return

        # Check that at least one image source was provided
        if image is None and not image_url:
            await interaction.response.send_message(
                "❌ Please attach an image or provide an `image_url` for the 1st image (Home team). You can paste directly with Ctrl+V into Discord!",
                ephemeral=True,
            )
            return

        # Immediate deferral to ensure Discord interaction does not time out during processing
        await interaction.response.defer(ephemeral=True)

        temp_dir = Path(tempfile.gettempdir()) / "efootball_ocr"
        temp_dir.mkdir(parents=True, exist_ok=True)

        suffix_1 = Path(image.filename).suffix if image else ".png"
        temp_file_1 = temp_dir / f"upload_1_{interaction.id}{suffix_1}"
        temp_file_2: Optional[Path] = None

        try:
            # 1. Download & Validate Image 1 (Home Team)
            valid1, val_err1 = await download_image_source(
                image, image_url, temp_file_1, config.max_image_bytes
            )
            if not valid1:
                await interaction.followup.send(f"❌ Invalid 1st image (Home): {val_err1}", ephemeral=True)
                return

            image_hash_1 = compute_file_sha256(temp_file_1)
            is_duplicate = image_hash_1 in RECENT_IMAGE_HASHES

            ocr_1 = extract_player_stats_from_screenshot(
                temp_file_1,
                tesseract_cmd=config.tesseract_cmd,
            )
            home_candidates = list(ocr_1.candidates)

            # 2. Download & Process Image 2 (Away Team, if provided)
            away_candidates: List[OCRPlayerCandidate] = []
            has_image_2 = (image2 is not None) or bool(image2_url and image2_url.strip())
            ocr_2_success = True
            ocr_2_err = None

            if has_image_2:
                suffix_2 = Path(image2.filename).suffix if image2 else ".png"
                temp_file_2 = temp_dir / f"upload_2_{interaction.id}{suffix_2}"
                valid2, val_err2 = await download_image_source(
                    image2, image2_url, temp_file_2, config.max_image_bytes
                )
                if valid2:
                    ocr_2 = extract_player_stats_from_screenshot(
                        temp_file_2,
                        tesseract_cmd=config.tesseract_cmd,
                    )
                    away_candidates = list(ocr_2.candidates)
                    ocr_2_success = ocr_2.success
                    ocr_2_err = ocr_2.error_reason

            # 3. Parse and map player names from team parameters (e.g. "S1 and S2")
            parsed_home = parse_players_from_team_param(home_team)
            parsed_away = parse_players_from_team_param(away_team)

            # If only 1 image provided and user put their players in away_team, map image 1 to away team
            if not has_image_2 and parsed_away and (not parsed_home or (home_team and home_team.strip().lower() in ("home", "xi"))):
                away_candidates = home_candidates
                home_candidates = []

            # Assign parsed home player names
            if parsed_home:
                for idx, p_name in enumerate(parsed_home):
                    if idx < len(home_candidates):
                        home_candidates[idx].player = p_name
                    else:
                        home_candidates.append(
                            OCRPlayerCandidate(player=p_name, goals=0, assists=0, confidence_note="From command parameter")
                        )

            # Assign parsed away player names
            if parsed_away:
                for idx, p_name in enumerate(parsed_away):
                    if idx < len(away_candidates):
                        away_candidates[idx].player = p_name
                    else:
                        away_candidates.append(
                            OCRPlayerCandidate(player=p_name, goals=0, assists=0, confidence_note="From command parameter")
                        )

            extracted_home_goals = sum(c.goals for c in home_candidates)
            extracted_away_goals = sum(c.goals for c in away_candidates)

            final_home_score = home_score if home_score is not None else extracted_home_goals
            final_away_score = away_score if away_score is not None else extracted_away_goals

            # 4. Determine Result:
            # Home Win, Away Win, or Draw
            if result and result.value:
                final_result = MatchResult.from_str(result.value)
            else:
                if final_home_score > final_away_score:
                    final_result = MatchResult.HOME_WIN
                elif final_home_score < final_away_score:
                    final_result = MatchResult.AWAY_WIN
                else:
                    final_result = MatchResult.DRAW

            error_notice = None
            if not ocr_1.success:
                error_notice = ocr_1.error_reason or "OCR could not detect all statistics in Image 1."
            if has_image_2 and not ocr_2_success:
                msg2 = f"Image 2: {ocr_2_err or 'OCR issues'}"
                error_notice = f"{error_notice}\n{msg2}" if error_notice else msg2

            if is_duplicate:
                duplicate_msg = "⚠️ This exact screenshot appears to have already been submitted recently."
                error_notice = f"{duplicate_msg}\n{error_notice}" if error_notice else duplicate_msg

            # 5. Display interactive private preview
            h_team_name = " & ".join(parsed_home) if parsed_home else (home_team.strip() if home_team else "Home")
            a_team_name = " & ".join(parsed_away) if parsed_away else (away_team.strip() if away_team else "Away")

            preview_embed = build_preview_embed(
                candidates=home_candidates,
                result=final_result,
                home_team=h_team_name,
                away_team=a_team_name,
                home_score=final_home_score,
                away_score=final_away_score,
                away_candidates=away_candidates,
                error_note=error_notice,
            )

            score_note = f"Score: {h_team_name} {final_home_score} - {final_away_score} {a_team_name} ({final_result.value})"
            view = MatchPreviewView(
                submitting_user_id=interaction.user.id,
                candidates=home_candidates,
                result=final_result,
                excel_manager=excel_manager,
                image_hash=image_hash_1,
                home_team=h_team_name,
                away_team=a_team_name,
                home_score=final_home_score,
                away_score=final_away_score,
                away_candidates=away_candidates,
                notes=score_note,
                error_note=error_notice,
            )

            await interaction.followup.send(embed=preview_embed, view=view, ephemeral=True)

        finally:
            if temp_file_1.exists():
                try:
                    temp_file_1.unlink()
                except OSError:
                    pass
            if temp_file_2 and temp_file_2.exists():
                try:
                    temp_file_2.unlink()
                except OSError:
                    pass

    # 1b. /add-match-text
    @tree.command(
        name="add-match-text",
        description="Add match data and player statistics directly via text input.",
    )
    async def add_match_text(interaction: discord.Interaction) -> None:
        err = check_guild_and_channel(interaction, config)
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return

        modal = AddMatchTextModal(excel_manager=excel_manager)
        await interaction.response.send_modal(modal)

    # 2. /delete-match
    @tree.command(
        name="delete-match",
        description="Permanently delete a match record and associated player statistics.",
    )
    @app_commands.describe(
        match_id="The unique Match ID to delete (e.g. M-20261029-XXXXXX)",
        password="Admin authorization password",
    )
    async def delete_match(interaction: discord.Interaction, match_id: str, password: str) -> None:
        err = check_guild_and_channel(interaction, config)
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return

        if password.strip() != config.delete_password:
            await interaction.response.send_message(
                "❌ Invalid password. Action unauthorized.",
                ephemeral=True,
            )
            return

        if not is_user_admin(interaction, config):
            await interaction.response.send_message(
                "❌ You do not have permission to delete match records. Only server admins or the designated admin role can perform deletions.",
                ephemeral=True,
            )
            return

        clean_id = match_id.strip()
        existing = await excel_manager.get_match(clean_id)
        if not existing:
            await interaction.response.send_message(
                f"❌ Match **{clean_id}** does not exist in the workbook.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="⚠️ Confirm Match Deletion",
            description=(
                f"Are you sure you want to permanently delete match **{clean_id}**?\n"
                f"**Date:** {existing.date}\n"
                f"**Result:** {existing.result.value}\n"
                f"**Players:** {', '.join(p.player for p in existing.player_stats)}\n\n"
                "This action cannot be undone and will recalculate all player career aggregates."
            ),
            color=discord.Color.red(),
        )

        view = DeleteConfirmView(
            submitting_user_id=interaction.user.id,
            match_id=clean_id,
            excel_manager=excel_manager,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # 2b. /delete-all
    @tree.command(
        name="delete-all",
        description="Permanently delete all matches and wipe stats from the workbook (Password protected).",
    )
    @app_commands.describe(password="Admin authorization password")
    async def delete_all(interaction: discord.Interaction, password: str) -> None:
        err = check_guild_and_channel(interaction, config)
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return

        if password.strip() != config.delete_password:
            await interaction.response.send_message(
                "❌ Invalid password. Action unauthorized.",
                ephemeral=True,
            )
            return

        if not is_user_admin(interaction, config):
            await interaction.response.send_message(
                "❌ You do not have permission to delete match records. Only server admins or the designated admin role can perform deletions.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="⚠️ Confirm Wipe All Match Data",
            description=(
                "**DANGER:** Are you sure you want to permanently delete **ALL** match records and player statistics from the workbook?\n\n"
                "This action cannot be undone. All career statistics, goals, assists, and leaderboards will be wiped clean."
            ),
            color=discord.Color.red(),
        )

        view = DeleteAllConfirmView(
            submitting_user_id=interaction.user.id,
            excel_manager=excel_manager,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # 3. /leaderboard
    @tree.command(
        name="leaderboard",
        description="Display group leaderboard rankings for a selected performance category.",
    )
    @app_commands.describe(category="Ranking category to sort players by")
    @app_commands.choices(
        category=[
            app_commands.Choice(name="Goals", value=LeaderboardCategory.GOALS.value),
            app_commands.Choice(name="Assists", value=LeaderboardCategory.ASSISTS.value),
            app_commands.Choice(name="Wins", value=LeaderboardCategory.WINS.value),
            app_commands.Choice(name="Losses", value=LeaderboardCategory.LOSSES.value),
            app_commands.Choice(name="Draws", value=LeaderboardCategory.DRAWS.value),
            app_commands.Choice(name="Win rate", value=LeaderboardCategory.WIN_RATE.value),
            app_commands.Choice(name="Matches played", value=LeaderboardCategory.MATCHES_PLAYED.value),
        ]
    )
    async def leaderboard(
        interaction: discord.Interaction,
        category: app_commands.Choice[str],
    ) -> None:
        err = check_guild_and_channel(interaction, config)
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return

        await interaction.response.defer()

        all_stats = await excel_manager.get_all_player_stats()
        aggregates = calculate_player_aggregates(all_stats)

        cat_enum = LeaderboardCategory(category.value)
        ranked = get_leaderboard(aggregates, cat_enum)

        if not ranked:
            await interaction.followup.send(
                "📋 No match statistics have been recorded yet. Use `/add-match-image` to record a match!",
            )
            return

        embed = discord.Embed(
            title=f"🏆 Co-op Leaderboard — {cat_enum.value}",
            color=discord.Color.gold(),
            timestamp=datetime.now(),
        )

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for rank, p in enumerate(ranked[:20], start=1):
            badge = medals[rank - 1] if rank <= 3 else f"`#{rank}`"

            if cat_enum == LeaderboardCategory.GOALS:
                stat_str = f"**{p.goals}** goals ({p.matches_played} matches)"
            elif cat_enum == LeaderboardCategory.ASSISTS:
                stat_str = f"**{p.assists}** assists ({p.matches_played} matches)"
            elif cat_enum == LeaderboardCategory.WINS:
                stat_str = f"**{p.wins}** wins ({p.win_rate}%)"
            elif cat_enum == LeaderboardCategory.LOSSES:
                stat_str = f"**{p.losses}** losses ({p.loss_rate}%)"
            elif cat_enum == LeaderboardCategory.DRAWS:
                stat_str = f"**{p.draws}** draws ({p.draw_rate}%)"
            elif cat_enum == LeaderboardCategory.WIN_RATE:
                stat_str = f"**{p.win_rate}%** ({p.wins}W / {p.losses}L / {p.draws}D)"
            elif cat_enum == LeaderboardCategory.MATCHES_PLAYED:
                stat_str = f"**{p.matches_played}** matches ({p.wins}W - {p.win_rate}%)"
            else:
                stat_str = f"**{p.goals}** goals"

            lines.append(f"{badge} **{p.player}** — {stat_str}")

        embed.description = "\n".join(lines)
        embed.set_footer(text=f"Total registered teammates: {len(ranked)}")
        await interaction.followup.send(embed=embed)

    # 4. /record
    @tree.command(
        name="record",
        description="View career statistics and performance rates for a specific player.",
    )
    @app_commands.describe(player="Player name (case-insensitive)")
    async def record(interaction: discord.Interaction, player: str) -> None:
        err = check_guild_and_channel(interaction, config)
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return

        all_stats = await excel_manager.get_all_player_stats()
        aggregates = calculate_player_aggregates(all_stats)
        p_record = get_player_record(player, aggregates)

        if not p_record:
            await interaction.response.send_message(
                f"❌ Player **{player}** was not found in recorded statistics. Use `/teammates` to see registered players.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"📊 Career Record — {p_record.player}",
            color=discord.Color.blue(),
            timestamp=datetime.now(),
        )
        embed.add_field(name="Matches Played", value=str(p_record.matches_played), inline=True)
        embed.add_field(name="Goals", value=str(p_record.goals), inline=True)
        embed.add_field(name="Assists", value=str(p_record.assists), inline=True)
        embed.add_field(
            name="Match Results",
            value=f"**Wins:** {p_record.wins} | **Losses:** {p_record.losses} | **Draws:** {p_record.draws}",
            inline=False,
        )
        embed.add_field(
            name="Performance Rates",
            value=(
                f"🟢 **Win Rate:** {p_record.win_rate}%\n"
                f"🔴 **Loss Rate:** {p_record.loss_rate}%\n"
                f"⚪ **Draw Rate:** {p_record.draw_rate}%"
            ),
            inline=False,
        )
        embed.set_footer(text="eFootball Co-op Statistics")
        await interaction.response.send_message(embed=embed)

    # 5. /matches
    @tree.command(
        name="matches",
        description="List recent saved co-op match records and participating players.",
    )
    @app_commands.describe(limit="Number of matches to display (1 - 25, default 10)")
    async def matches(interaction: discord.Interaction, limit: Optional[int] = 10) -> None:
        err = check_guild_and_channel(interaction, config)
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return

        bounded_limit = max(1, min(limit or 10, 25))
        all_matches = await excel_manager.get_all_matches()

        if not all_matches:
            await interaction.response.send_message(
                "📋 No matches recorded yet. Submit your first screenshot with `/add-match-image`!",
                ephemeral=True,
            )
            return

        recent = list(reversed(all_matches))[:bounded_limit]

        embed = discord.Embed(
            title=f"📜 Recent Matches (Last {len(recent)})",
            color=discord.Color.purple(),
            timestamp=datetime.now(),
        )

        for m in recent:
            res_emoji = (
                "🟢"
                if m.result in (MatchResult.HOME_WIN, MatchResult.WIN)
                else ("🔴" if m.result in (MatchResult.AWAY_WIN, MatchResult.LOSS) else "⚪")
            )
            player_summary = ", ".join(
                f"{p.player} ({p.goals}G, {p.assists}A)" for p in m.player_stats
            ) or "No player stats"

            embed.add_field(
                name=f"{res_emoji} {m.match_id} — {m.result.value} ({m.date})",
                value=f"**Players:** {player_summary}\n*Submitted by: {m.submitted_by}*",
                inline=False,
            )

        embed.set_footer(text=f"Showing {len(recent)} of {len(all_matches)} total matches.")
        await interaction.response.send_message(embed=embed)

    # 6. /teammates
    @tree.command(
        name="teammates",
        description="List all registered teammates and their aggregate totals.",
    )
    async def teammates(interaction: discord.Interaction) -> None:
        err = check_guild_and_channel(interaction, config)
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return

        all_stats = await excel_manager.get_all_player_stats()
        aggregates = calculate_player_aggregates(all_stats)
        sorted_teammates = get_all_teammates(aggregates)

        if not sorted_teammates:
            await interaction.response.send_message(
                "👥 No teammates registered yet. Once matches are recorded, teammates will appear here.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"👥 Registered Teammates ({len(sorted_teammates)})",
            color=discord.Color.teal(),
            timestamp=datetime.now(),
        )

        lines = []
        for t in sorted_teammates:
            lines.append(
                f"• **{t.player}**: {t.matches_played} matches | {t.goals} goals | {t.assists} assists | {t.wins}W / {t.losses}L / {t.draws}D ({t.win_rate}% WR)"
            )

        embed.description = "\n".join(lines)
        embed.set_footer(text="Alphabetical order by name")
        await interaction.response.send_message(embed=embed)
