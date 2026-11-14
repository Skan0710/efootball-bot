"""Discord slash commands, interactive views, and modals for eFootball bot."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from config import BotConfig
from excel_manager import ExcelManager
from models import (
    PLAYER_CANONICAL_MAP,
    LeaderboardCategory,
    MatchRecord,
    MatchResult,
    OCRPlayerCandidate,
    PlayerStat,
    canonicalize_player_name,
)
from ocr import extract_player_stats_from_screenshot, validate_image_file
from stats import (
    calculate_best_teams,
    calculate_hall_of_fame,
    calculate_head_to_head,
    calculate_match_motm_and_milestones,
    calculate_player_aggregates,
    calculate_player_card_stats,
    calculate_player_chemistry,
    calculate_player_form,
    calculate_player_tierlist,
    calculate_player_trophies,
    calculate_potm_rankings,
    calculate_potw_rankings,
    compute_player_card_ovr,
    calculate_session_recap,
    compute_player_rating,
    extract_match_scores,
    generate_fair_teams,
    get_all_teammates,
    get_leaderboard,
    get_player_record,
)
from visuals import (
    render_compare_chart,
    render_fc27_card,
    render_form_chart,
    render_match_summary_card,
    render_tierlist_card,
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
    return [canonicalize_player_name(p.strip()) for p in parts if p.strip()]


def format_match_scoreline(match: MatchRecord) -> str:
    """Formats a match record showing only team players and score (no match ID or metadata)."""
    win_stats = [p for p in match.player_stats if p.result in (MatchResult.WIN, MatchResult.HOME_WIN)]
    loss_stats = [p for p in match.player_stats if p.result in (MatchResult.LOSS, MatchResult.AWAY_WIN)]
    draw_stats = [p for p in match.player_stats if p.result == MatchResult.DRAW]

    win_players = [canonicalize_player_name(p.player) for p in win_stats]
    loss_players = [canonicalize_player_name(p.player) for p in loss_stats]
    win_goals = sum(p.goals for p in win_stats)
    loss_goals = sum(p.goals for p in loss_stats)

    note_left: Optional[str] = None
    note_right: Optional[str] = None
    h_score: Optional[int] = None
    a_score: Optional[int] = None

    if match.notes:
        score_match = re.match(
            r"^\s*(?:Score:\s*)?(?P<left>.+?)\s+(?P<hs>\d+)\s*-\s*(?P<as>\d+)\s+(?P<right>.+?)(?:\s*\([^)]*\))?\s*$",
            match.notes.strip(),
            flags=re.IGNORECASE,
        )
        if score_match:
            note_left = score_match.group("left").strip()
            note_right = score_match.group("right").strip()
            h_score = int(score_match.group("hs"))
            a_score = int(score_match.group("as"))

    if match.result in (MatchResult.AWAY_WIN, MatchResult.LOSS):
        home_players = loss_players
        away_players = win_players
        if h_score is None or a_score is None:
            h_score, a_score = loss_goals, win_goals
    else:
        home_players = win_players
        away_players = loss_players
        if h_score is None or a_score is None:
            if draw_stats and not win_stats and not loss_stats:
                h_score, a_score = 0, 0
            else:
                h_score, a_score = win_goals, loss_goals

    parsed_left = parse_players_from_team_param(note_left)
    parsed_right = parse_players_from_team_param(note_right)

    home_str = " & ".join(home_players or parsed_left) or (note_left or "Home")
    away_str = " & ".join(away_players or parsed_right) or (note_right or "Away")

    if draw_stats and not home_players and not away_players and not parsed_left and not parsed_right:
        all_draw = [canonicalize_player_name(p.player) for p in draw_stats]
        mid = max(1, len(all_draw) // 2)
        home_str = " & ".join(all_draw[:mid]) or "Home"
        away_str = " & ".join(all_draw[mid:]) or "Away"

    return f"**{home_str}** {h_score} - {a_score} **{away_str}**"


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
        name = canonicalize_player_name(name)
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
            all_matches = await self.excel_manager.get_all_matches()
            aggregates = calculate_player_aggregates(all_stats, matches=all_matches)
            await self.excel_manager.update_leaderboard_sheet(list(aggregates.values()))

            highlights = calculate_match_motm_and_milestones(record, all_matches)

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

            motm = highlights.get("motm")
            milestones = highlights.get("milestones", [])
            if motm:
                success_embed.add_field(
                    name="⭐ Man of the Match",
                    value=f"**{motm['player']}** ({motm['goals']}G, {motm['assists']}A • Rating Score: {motm['score']})",
                    inline=False,
                )
            if milestones:
                success_embed.add_field(
                    name="🏆 Milestones & Highlights",
                    value="\n".join(milestones),
                    inline=False,
                )

            success_embed.set_footer(text="Saved to data/matches.xlsx. Use /leaderboard or /record to view updated stats.")

            await interaction.edit_original_response(embed=success_embed, view=self)

            # Announce MOTM & Milestones publicly in the channel if available
            if isinstance(getattr(interaction, "channel", None), discord.abc.Messageable):
                try:
                    announce_embed = discord.Embed(
                        title=f"📣 Match Saved — {format_match_scoreline(record)}",
                        color=discord.Color.gold(),
                        timestamp=datetime.now(),
                    )
                    if motm:
                        announce_embed.add_field(
                            name="⭐ Man of the Match",
                            value=f"👑 **{motm['player']}** — **{motm['goals']}G, {motm['assists']}A**",
                            inline=False,
                        )
                    if milestones:
                        announce_embed.add_field(
                            name="🏆 Milestones Unlocked",
                            value="\n".join(milestones),
                            inline=False,
                        )

                    banner_buf = render_match_summary_card(
                        record,
                        self.home_team,
                        self.away_team,
                        h_val,
                        a_val,
                        self.result,
                    )
                    banner_filename = f"match_summary_{record.match_id}.png"
                    banner_file = discord.File(fp=banner_buf, filename=banner_filename)
                    announce_embed.set_image(url=f"attachment://{banner_filename}")
                    await interaction.channel.send(embed=announce_embed, file=banner_file)
                except Exception as pub_exc:
                    LOGGER.debug("Skipping public match announcement: %s", pub_exc)

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

        try:
            h_score, a_score, res = _parse_scores_and_result(
                self.scores_input.value.strip(),
                self.result_input.value.strip(),
                new_home_c,
                new_away_c,
            )
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

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


def _parse_scores_and_result(
    scores_val: str,
    res_val: str,
    home_c: List[OCRPlayerCandidate],
    away_c: List[OCRPlayerCandidate],
) -> Tuple[int, int, MatchResult]:
    """Parses 'home, away' score text and match result with goal-sum fallback."""
    if scores_val and "," in scores_val:
        try:
            hs_str, as_str = [s.strip() for s in scores_val.split(",", 1)]
            h_score = int(hs_str)
            a_score = int(as_str)
        except ValueError:
            h_score = sum(c.goals for c in home_c)
            a_score = sum(a.goals for a in away_c)
    else:
        h_score = sum(c.goals for c in home_c)
        a_score = sum(a.goals for a in away_c)

    if res_val:
        res = MatchResult.from_str(res_val)
    elif h_score > a_score:
        res = MatchResult.HOME_WIN
    elif h_score < a_score:
        res = MatchResult.AWAY_WIN
    else:
        res = MatchResult.DRAW

    return h_score, a_score, res


class EditSavedMatchModal(discord.ui.Modal, title="Edit Saved Match"):
    """Modal pre-populated with a saved match's stats to fix typos without deleting."""

    home_stats_input = discord.ui.TextInput(
        label="Home Players: Name, Goals, Assists",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500,
    )

    away_stats_input = discord.ui.TextInput(
        label="Away Players: Name, Goals, Assists",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
    )

    scores_input = discord.ui.TextInput(
        label="Home Score, Away Score (e.g. 3, 2)",
        style=discord.TextStyle.short,
        required=False,
        max_length=50,
    )

    result_input = discord.ui.TextInput(
        label="Result: Home Win, Away Win, or Draw",
        style=discord.TextStyle.short,
        required=False,
        max_length=20,
    )

    def __init__(self, match_record: MatchRecord, excel_manager: ExcelManager) -> None:
        super().__init__(title=f"Edit {match_record.match_id}")
        self.match_record = match_record
        self.excel_manager = excel_manager

        if match_record.result == MatchResult.AWAY_WIN:
            home_list = [p for p in match_record.player_stats if p.result in (MatchResult.LOSS, MatchResult.AWAY_WIN)]
            away_list = [p for p in match_record.player_stats if p.result in (MatchResult.WIN, MatchResult.HOME_WIN)]
        elif match_record.result == MatchResult.DRAW:
            half = max(1, len(match_record.player_stats) // 2)
            home_list = match_record.player_stats[:half]
            away_list = match_record.player_stats[half:]
        else:
            home_list = [p for p in match_record.player_stats if p.result in (MatchResult.WIN, MatchResult.HOME_WIN)]
            away_list = [p for p in match_record.player_stats if p.result in (MatchResult.LOSS, MatchResult.AWAY_WIN)]

        hs, a_s = extract_match_scores(match_record)
        self.home_stats_input.default = "\n".join(f"{p.player}, {p.goals}, {p.assists}" for p in home_list)
        self.away_stats_input.default = "\n".join(f"{p.player}, {p.goals}, {p.assists}" for p in away_list)
        self.scores_input.default = f"{hs}, {a_s}"
        self.result_input.default = match_record.result.value

    async def on_submit(self, interaction: discord.Interaction) -> None:
        new_home_c, home_errs = parse_modal_candidate_lines(self.home_stats_input.value, prefix="Home")
        new_away_c, away_errs = parse_modal_candidate_lines(self.away_stats_input.value, prefix="Away")
        all_errors = home_errs + away_errs
        if all_errors:
            await interaction.response.send_message(
                "❌ **Validation Errors:**\n" + "\n".join(all_errors[:5]),
                ephemeral=True,
            )
            return

        try:
            h_score, a_score, res = _parse_scores_and_result(
                self.scores_input.value.strip(),
                self.result_input.value.strip(),
                new_home_c,
                new_away_c,
            )
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        if res in (MatchResult.HOME_WIN, MatchResult.WIN):
            h_outcome, a_outcome = MatchResult.WIN, MatchResult.LOSS
        elif res in (MatchResult.AWAY_WIN, MatchResult.LOSS):
            h_outcome, a_outcome = MatchResult.LOSS, MatchResult.WIN
        else:
            h_outcome = a_outcome = MatchResult.DRAW

        updated_stats = [
            PlayerStat(player=c.player, goals=c.goals, assists=c.assists, result=h_outcome)
            for c in new_home_c
        ] + [
            PlayerStat(player=a.player, goals=a.goals, assists=a.assists, result=a_outcome)
            for a in new_away_c
        ]

        h_names = " & ".join(c.player for c in new_home_c) or "Home"
        a_names = " & ".join(a.player for a in new_away_c) or "Away"
        updated_record = MatchRecord(
            match_id=self.match_record.match_id,
            date=self.match_record.date,
            result=res,
            submitted_by=self.match_record.submitted_by,
            screenshot_ref=self.match_record.screenshot_ref,
            notes=f"Score: {h_names} {h_score} - {a_score} {a_names} ({res.value})",
            player_stats=updated_stats,
        )

        ok = await self.excel_manager.update_match(updated_record)
        if not ok:
            await interaction.response.send_message(
                f"❌ Match **{self.match_record.match_id}** was not found in the workbook.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"✅ Match Updated — {updated_record.match_id}",
            description=f"{format_match_scoreline(updated_record)}\n\nResult: **{res.value}** • Leaderboard recalculated.",
            color=discord.Color.green(),
            timestamp=datetime.now(),
        )
        await interaction.response.send_message(embed=embed)


def parse_fair_team_names(raw_names: str) -> List[str]:
    """Splits comma, newline, or ampersand separated player names cleanly."""
    if not raw_names:
        return []
    parts = re.split(r"[\n,]+|\s+(?:and|&|\+)\s*", raw_names.strip(), flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


def build_fair_teams_embed(split: dict, player_count: int) -> discord.Embed:
    """Builds the Discord embed displaying balanced co-op teams."""
    mode_label = {4: "2v2", 5: "3v2", 6: "3v3"}.get(player_count, f"{len(split['team_a'])}v{len(split['team_b'])}")
    embed = discord.Embed(
        title=f"⚖️ Fair Teams ({mode_label} — {player_count} Players)",
        description=f"Power Difference: **{split['rating_diff']:.2f}** pts\n",
        color=discord.Color.blue(),
        timestamp=datetime.now(),
    )

    def _fmt_side(players: List[dict]) -> str:
        lines = []
        for p in players:
            tag = " (Unranked)" if p.get("is_unranked") else ""
            lines.append(f"• **{p['player']}** — {int(round(p['rating']))} OVR{tag}")
        return "\n\n".join(lines)

    embed.add_field(
        name=f"🔵 Team A (Avg OVR: {round(split['team_a_rating'] / len(split['team_a']), 1)})",
        value=_fmt_side(split["team_a"]),
        inline=True,
    )
    embed.add_field(
        name=f"🔴 Team B (Avg OVR: {round(split['team_b_rating'] / len(split['team_b']), 1)})",
        value=_fmt_side(split["team_b"]),
        inline=True,
    )
    embed.set_footer(text="OVR = Confidence-Weighted FC 27 Card Rating (60–99) • Balanced from Excel history")
    return embed


class FairTeamsModal(discord.ui.Modal):
    """Modal prompting user to enter 4, 5, or 6 player names for team balancing."""

    def __init__(self, expected_count: int, excel_manager: ExcelManager) -> None:
        super().__init__(title=f"Fair Teams — Enter {expected_count} Players")
        self.expected_count = expected_count
        self.excel_manager = excel_manager

        sample_names = ["Suri", "Skan", "Ashy", "Annie", "Azlan", "Jonny"][:expected_count]
        self.names_input = discord.ui.TextInput(
            label=f"Enter {expected_count} Player Names (comma or new line)",
            style=discord.TextStyle.paragraph,
            placeholder=", ".join(sample_names),
            required=True,
            max_length=300,
        )
        self.add_item(self.names_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw_names = parse_fair_team_names(self.names_input.value)
        if len(raw_names) != self.expected_count:
            await interaction.response.send_message(
                f"❌ You selected **{self.expected_count}** players, but entered **{len(raw_names)}**. Please enter exactly {self.expected_count} player names.",
                ephemeral=True,
            )
            return

        records = await self.excel_manager.get_leaderboard_from_sheet()
        try:
            split = generate_fair_teams(raw_names, records)
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        embed = build_fair_teams_embed(split, self.expected_count)
        await interaction.response.send_message(embed=embed)


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
            embed = discord.Embed(
                title="🗑️ Match Deleted",
                description=f"Match **{self.match_id}** was permanently removed. All player stats and leaderboard were automatically recalculated in Excel.",
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
    """Registers eFootball slash commands to the bot command tree."""

    async def player_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> List[app_commands.Choice[str]]:
        """Provides clickable dropdown list of registered players from Excel and alias map."""
        candidates: Dict[str, str] = {}
        for canonical_name in set(PLAYER_CANONICAL_MAP.values()):
            candidates[canonical_name.lower()] = canonical_name
        try:
            records = await excel_manager.get_leaderboard_from_sheet()
            for r in records:
                p = str(r.get("player", "")).strip()
                if p:
                    candidates[p.lower()] = p
        except Exception:
            pass

        query = current.strip().lower()
        sorted_names = sorted(candidates.values(), key=lambda n: n.lower())
        return [
            app_commands.Choice(name=name, value=name)
            for name in sorted_names
            if not query or query in name.lower()
        ][:25]

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
            app_commands.Choice(name="G/A (Goals + Assists)", value=LeaderboardCategory.GA.value),
            app_commands.Choice(name="Wins", value=LeaderboardCategory.WINS.value),
            app_commands.Choice(name="Losses", value=LeaderboardCategory.LOSSES.value),
            app_commands.Choice(name="Win rate", value=LeaderboardCategory.WIN_RATE.value),
            app_commands.Choice(name="Matches played", value=LeaderboardCategory.MATCHES_PLAYED.value),
            app_commands.Choice(name="GPG (Goals Per Game)", value=LeaderboardCategory.GPG.value),
            app_commands.Choice(name="APG (Assists Per Game)", value=LeaderboardCategory.APG.value),
            app_commands.Choice(name="GAPG (G/A Per Game)", value=LeaderboardCategory.GAPG.value),
            app_commands.Choice(name="Hattrick (3+ goals in a game)", value=LeaderboardCategory.HATTRICK.value),
            app_commands.Choice(name="Clean Sheets (0 goals conceded)", value=LeaderboardCategory.CLEAN_SHEETS.value),
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

        records = await excel_manager.get_leaderboard_from_sheet()
        if not records:
            await interaction.followup.send(
                "📋 No match statistics have been recorded yet in Excel. Use `/add-match-image` or `/add-match-text` to record a match!",
            )
            return

        raw_val = category.value if hasattr(category, "value") else str(category)
        cat_enum = LeaderboardCategory.from_str(raw_val)

        if cat_enum == LeaderboardCategory.GOALS:
            records.sort(key=lambda r: (r["goals"], r["ga"], r["assists"], r["matches_played"]), reverse=True)
        elif cat_enum == LeaderboardCategory.ASSISTS:
            records.sort(key=lambda r: (r["assists"], r["ga"], r["goals"], r["matches_played"]), reverse=True)
        elif cat_enum == LeaderboardCategory.GA:
            records.sort(key=lambda r: (r["ga"], r["goals"], r["assists"], r["matches_played"]), reverse=True)
        elif cat_enum == LeaderboardCategory.WINS:
            records.sort(key=lambda r: (r["wins"], r["win_rate"], r["matches_played"]), reverse=True)
        elif cat_enum == LeaderboardCategory.LOSSES:
            records.sort(key=lambda r: (r["losses"], r["loss_rate"], r["matches_played"]), reverse=True)
        elif cat_enum == LeaderboardCategory.DRAWS:
            records.sort(key=lambda r: (r["draws"], r["matches_played"]), reverse=True)
        elif cat_enum == LeaderboardCategory.WIN_RATE:
            records.sort(key=lambda r: (r["win_rate"], r["wins"], r["matches_played"]), reverse=True)
        elif cat_enum == LeaderboardCategory.MATCHES_PLAYED:
            records.sort(key=lambda r: (r["matches_played"], r["wins"], r["goals"]), reverse=True)
        elif cat_enum == LeaderboardCategory.GPG:
            records.sort(key=lambda r: (r["gpg"], r["goals"], r["matches_played"]), reverse=True)
        elif cat_enum == LeaderboardCategory.APG:
            records.sort(key=lambda r: (r["apg"], r["assists"], r["matches_played"]), reverse=True)
        elif cat_enum == LeaderboardCategory.GAPG:
            records.sort(key=lambda r: (r["gapg"], r["ga"], r["matches_played"]), reverse=True)
        elif cat_enum == LeaderboardCategory.HATTRICK:
            records.sort(key=lambda r: (r.get("hattricks", 0), r["goals"], r["matches_played"]), reverse=True)
        elif cat_enum == LeaderboardCategory.CLEAN_SHEETS:
            records.sort(
                key=lambda r: (r.get("clean_sheets", 0), r["wins"], r["win_rate"], r["matches_played"]),
                reverse=True,
            )
        else:
            records.sort(key=lambda r: (r["goals"], r["assists"]), reverse=True)

        embed = discord.Embed(
            title=f"📊 Co-op Leaderboard — {cat_enum.value}",
            color=discord.Color.gold(),
            timestamp=datetime.now(),
        )

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for rank, r in enumerate(records[:20], start=1):
            badge = medals[rank - 1] if rank <= 3 else f"**#{rank}**"

            if cat_enum == LeaderboardCategory.GOALS:
                stat_str = f"**{r['goals']}** goals"
            elif cat_enum == LeaderboardCategory.ASSISTS:
                stat_str = f"**{r['assists']}** assists"
            elif cat_enum == LeaderboardCategory.GA:
                stat_str = f"**{r['ga']}** G/A"
            elif cat_enum == LeaderboardCategory.WINS:
                stat_str = f"**{r['wins']}** wins"
            elif cat_enum == LeaderboardCategory.LOSSES:
                stat_str = f"**{r['losses']}** losses"
            elif cat_enum == LeaderboardCategory.DRAWS:
                stat_str = f"**{r['draws']}** draws"
            elif cat_enum == LeaderboardCategory.WIN_RATE:
                stat_str = f"**{r['win_rate']:.1f}%**"
            elif cat_enum == LeaderboardCategory.MATCHES_PLAYED:
                stat_str = f"**{r['matches_played']}** matches"
            elif cat_enum == LeaderboardCategory.GPG:
                stat_str = f"**{r['gpg']:.2f}** GPG"
            elif cat_enum == LeaderboardCategory.APG:
                stat_str = f"**{r['apg']:.2f}** APG"
            elif cat_enum == LeaderboardCategory.GAPG:
                stat_str = f"**{r['gapg']:.2f}** GAPG"
            elif cat_enum == LeaderboardCategory.HATTRICK:
                ht = r.get("hattricks", 0)
                stat_str = f"**{ht}** {'hattrick' if ht == 1 else 'hattricks'}"
            elif cat_enum == LeaderboardCategory.CLEAN_SHEETS:
                cs = r.get("clean_sheets", 0)
                stat_str = f"**{cs}** {'clean sheet' if cs == 1 else 'clean sheets'}"
            else:
                stat_str = f"**{r['goals']}** goals"

            lines.append(f"{badge} **{r['player']}** — {stat_str}")

        embed.description = "\n\n".join(lines)
        embed.set_footer(text=f"Total registered teammates: {len(records)} • Sorted by {cat_enum.value}")
        await interaction.followup.send(embed=embed)

    # 4. /record
    @tree.command(
        name="record",
        description="View career statistics and performance rates for a specific player.",
    )
    @app_commands.describe(player="Select player from list")
    @app_commands.autocomplete(player=player_autocomplete)
    async def record(interaction: discord.Interaction, player: str) -> None:
        err = check_guild_and_channel(interaction, config)
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return

        records = await excel_manager.get_leaderboard_from_sheet()
        canonical = canonicalize_player_name(player)
        p_record = next(
            (r for r in records if r["player"].lower() == canonical.lower() or r["player"].lower() == player.lower()),
            None,
        )

        if not p_record:
            await interaction.response.send_message(
                f"❌ Player **{player}** was not found in Excel leaderboard. Use `/teammates` to see registered players.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"📊 Career Record — {p_record['player']}",
            color=discord.Color.blue(),
            timestamp=datetime.now(),
        )
        embed.add_field(name="Matches Played", value=str(p_record["matches_played"]), inline=True)
        embed.add_field(name="Goals", value=str(p_record["goals"]), inline=True)
        embed.add_field(name="Assists", value=str(p_record["assists"]), inline=True)
        embed.add_field(name="G/A (Goals + Assists)", value=str(p_record["ga"]), inline=True)
        embed.add_field(name="Hattricks (3+ Goals)", value=str(p_record.get("hattricks", 0)), inline=True)
        embed.add_field(name="Clean Sheets", value=str(p_record.get("clean_sheets", 0)), inline=True)
        embed.add_field(name="Goals Per Game (GPG)", value=f"{p_record['gpg']:.2f}", inline=True)
        embed.add_field(name="Assists Per Game (APG)", value=f"{p_record['apg']:.2f}", inline=True)
        embed.add_field(name="G/A Per Game (GAPG)", value=f"{p_record['gapg']:.2f}", inline=True)
        embed.add_field(
            name="Match Results",
            value=f"**Wins:** {p_record['wins']} | **Losses:** {p_record['losses']}",
            inline=True,
        )
        embed.add_field(
            name="Performance Rates",
            value=(
                f"🟢 **Win Rate:** {p_record['win_rate']:.1f}%\n"
                f"🔴 **Loss Rate:** {p_record['loss_rate']:.1f}%"
            ),
            inline=True,
        )
        embed.set_footer(text="eFootball Co-op Statistics • Fetched from Excel")
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

        lines = [
            f"{idx}. {format_match_scoreline(m)}"
            for idx, m in enumerate(recent, start=1)
        ]
        embed.description = "\n\n".join(lines)
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

        records = await excel_manager.get_leaderboard_from_sheet()
        sorted_teammates = sorted(records, key=lambda r: r["player"].lower())

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
                f"• **{t['player']}**: {t['matches_played']} matches | {t['goals']} goals | {t['assists']} assists | G/A: {t['ga']} | {t['wins']}W / {t['losses']}L ({t['win_rate']:.1f}% WR)"
            )

        embed.description = "\n\n".join(lines)
        embed.set_footer(text="Alphabetical order by name • Fetched from Excel")
        await interaction.response.send_message(embed=embed)

    def _build_weighted_award_embed(
        title: str,
        field_name: str,
        footer_text: str,
        rankings: List[dict],
        color: discord.Color,
    ) -> discord.Embed:
        winner = rankings[0]
        embed = discord.Embed(
            title=title,
            description=(
                f"👑 **{winner['player']}** leads with **{winner['score']:.2f}** pts "
                f"({winner['goals']}G, {winner['assists']}A, {winner['wins']}W in {winner['matches_played']} MP)\n"
            ),
            color=color,
            timestamp=datetime.now(),
        )

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for rank, r in enumerate(rankings[:20], start=1):
            badge = medals[rank - 1] if rank <= 3 else f"**#{rank}**"
            lines.append(
                f"{badge} **{r['player']}** — **{r['score']:.2f}** pts "
                f"({r['goals']}G · {r['assists']}A · {r['wins']}W · {r['matches_played']} MP)"
            )

        embed.add_field(name=field_name, value="\n\n".join(lines), inline=False)
        embed.set_footer(text=footer_text)
        return embed

    # 7. /potw
    @tree.command(
        name="potw",
        description="Show Player of the Week ranked by 0.4*Goals + 0.4*Assists + 0.2*Wins (Mon–Sun).",
    )
    @app_commands.describe(weeks_ago="0 for current week (default), 1 for last week, etc.")
    async def potw(interaction: discord.Interaction, weeks_ago: Optional[int] = 0) -> None:
        err = check_guild_and_channel(interaction, config)
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return

        await interaction.response.defer()

        bounded_weeks = max(0, weeks_ago or 0)
        all_matches = await excel_manager.get_all_matches()
        rankings, week_start, week_end = calculate_potw_rankings(
            all_matches, weeks_ago=bounded_weeks
        )
        week_range = f"{week_start.strftime('%d %b')} – {week_end.strftime('%d %b %Y')}"

        if not rankings:
            await interaction.followup.send(
                f"📋 No matches recorded for week **{week_range}**.",
            )
            return

        embed = _build_weighted_award_embed(
            title=f"🏆 Player of the Week — {week_range}",
            field_name="Weekly Standings",
            footer_text="Score = 0.4×Goals + 0.4×Assists + 0.2×Wins • Mon–Sun Calendar Week",
            rankings=rankings,
            color=discord.Color.gold(),
        )
        await interaction.followup.send(embed=embed)

    # 8. /potm
    @tree.command(
        name="potm",
        description="Show Player of the Month ranked by 0.4*Goals + 0.4*Assists + 0.2*Wins.",
    )
    @app_commands.describe(months_ago="0 for current month (default), 1 for last month, etc.")
    async def potm(interaction: discord.Interaction, months_ago: Optional[int] = 0) -> None:
        err = check_guild_and_channel(interaction, config)
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return

        await interaction.response.defer()

        bounded_months = max(0, months_ago or 0)
        all_matches = await excel_manager.get_all_matches()
        rankings, month_start, _ = calculate_potm_rankings(
            all_matches, months_ago=bounded_months
        )
        month_label = month_start.strftime("%B %Y")

        if not rankings:
            await interaction.followup.send(
                f"📋 No matches recorded for **{month_label}**.",
            )
            return

        embed = _build_weighted_award_embed(
            title=f"🏅 Player of the Month — {month_label}",
            field_name="Monthly Standings",
            footer_text="Score = 0.4×Goals + 0.4×Assists + 0.2×Wins • Calendar Month",
            rankings=rankings,
            color=discord.Color.orange(),
        )
        await interaction.followup.send(embed=embed)

    # 8. /best-team
    @tree.command(
        name="best-team",
        description="Show teammates ranked by most co-op wins played together.",
    )
    async def best_team(interaction: discord.Interaction) -> None:
        err = check_guild_and_channel(interaction, config)
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return

        await interaction.response.defer()

        all_matches = await excel_manager.get_all_matches()
        teams = calculate_best_teams(all_matches)
        winning_teams = [t for t in teams if t["wins"] > 0]

        if not winning_teams:
            await interaction.followup.send(
                "📋 No co-op teammate wins have been recorded yet.",
            )
            return

        top = winning_teams[0]
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for rank, t in enumerate(teams[:20], start=1):
            badge = medals[rank - 1] if rank <= 3 else f"**#{rank}**"
            win_word = "win" if t["wins"] == 1 else "wins"
            lines.append(
                f"{badge} **{t['team_name']}** — **{t['wins']}** {win_word} "
                f"({t['wins']}W / {t['losses']}L · {t['win_rate']:.1f}% WR · {t['goals']}G)"
            )

        embed = discord.Embed(
            title="🤝 Best Co-op Teams — Most Wins Together",
            description=(
                f"🏆 **{top['team_name']}** lead with **{top['wins']}** "
                f"{'win' if top['wins'] == 1 else 'wins'} together "
                f"({top['wins']}W / {top['losses']}L · {top['win_rate']:.1f}% WR)\n\n"
                + "\n\n".join(lines)
            ),
            color=discord.Color.green(),
            timestamp=datetime.now(),
        )
        embed.set_footer(text=f"Total teammate pairings: {len(teams)} • Sorted by wins together")
        await interaction.followup.send(embed=embed)

    # 10. /fair-teams
    @tree.command(
        name="fair-teams",
        description="Split 4, 5, or 6 players into the fairest possible co-op teams.",
    )
    @app_commands.describe(
        player_count="Choose how many players are playing (4, 5, or 6)",
        players="Optional: enter names right here (comma separated), or leave blank for popup",
    )
    @app_commands.choices(
        player_count=[
            app_commands.Choice(name="4 players (2v2)", value=4),
            app_commands.Choice(name="5 players (3v2)", value=5),
            app_commands.Choice(name="6 players (3v3)", value=6),
        ]
    )
    async def fair_teams(
        interaction: discord.Interaction,
        player_count: app_commands.Choice[int],
        players: Optional[str] = None,
    ) -> None:
        err = check_guild_and_channel(interaction, config)
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return

        expected_count = int(player_count.value if hasattr(player_count, "value") else player_count)
        if not players or not players.strip():
            await interaction.response.send_modal(
                FairTeamsModal(expected_count=expected_count, excel_manager=excel_manager)
            )
            return

        raw_names = parse_fair_team_names(players)
        if len(raw_names) != expected_count:
            await interaction.response.send_message(
                f"❌ You selected **{expected_count}** players, but entered **{len(raw_names)}**. Please enter exactly {expected_count} player names.",
                ephemeral=True,
            )
            return

        records = await excel_manager.get_leaderboard_from_sheet()
        try:
            split = generate_fair_teams(raw_names, records)
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        embed = build_fair_teams_embed(split, expected_count)
        await interaction.response.send_message(embed=embed)

    # 11. /form
    @tree.command(
        name="form",
        description="Show a player's recent 5-match form, streaks, and recent match performances.",
    )
    @app_commands.describe(player="Select player from list")
    @app_commands.autocomplete(player=player_autocomplete)
    async def form(interaction: discord.Interaction, player: str) -> None:
        err = check_guild_and_channel(interaction, config)
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return

        await interaction.response.defer()

        all_matches = await excel_manager.get_all_matches()
        form_data = calculate_player_form(player, all_matches, window=5)

        if form_data["total_matches"] == 0:
            await interaction.followup.send(
                f"📋 No match appearances recorded yet for **{form_data['player']}**.",
            )
            return

        badge_map = {"W": "🟢 W", "L": "🔴 L", "D": "⚪ D"}
        form_display = "  ·  ".join(badge_map[o] for o in form_data["form_list"])

        streak_type = form_data["current_streak_type"]
        streak_cnt = form_data["current_streak_count"]
        streak_word = {"W": "Win", "L": "Loss", "D": "Draw"}.get(streak_type, "")
        streak_str = f"{streak_cnt} {streak_word} Streak" if streak_cnt else "None"

        recent_lines = []
        for idx, item in enumerate(form_data["recent"], start=1):
            res_badge = badge_map[item["outcome"]]
            score_line = format_match_scoreline(item["match"])
            recent_lines.append(
                f"{idx}. {res_badge} — {score_line} ({item['goals']}G, {item['assists']}A)"
            )

        embed = discord.Embed(
            title=f"📈 Recent Form — {form_data['player']}",
            description=(
                f"**Last {len(form_data['recent'])} (Latest first):** {form_display}\n\n"
                f"**Current Streak:** {streak_str} · **Best Win Streak:** {form_data['longest_win_streak']} · **Unbeaten Streak:** {form_data['longest_unbeaten_streak']}\n\n"
                + "\n\n".join(recent_lines)
            ),
            color=discord.Color.blue(),
            timestamp=datetime.now(),
        )
        chart_buf = render_form_chart(form_data["player"], form_data["history"])
        slug = re.sub(r"[^a-zA-Z0-9_-]", "", form_data["player"].lower()) or "player"
        chart_name = f"{slug}_form_chart.png"
        chart_file = discord.File(fp=chart_buf, filename=chart_name)
        embed.set_image(url=f"attachment://{chart_name}")
        embed.set_footer(text=f"Total matches played: {form_data['total_matches']}")
        await interaction.followup.send(embed=embed, file=chart_file)

    # 12. /h2h
    @tree.command(
        name="h2h",
        description="View head-to-head rivalry and duo chemistry stats between two players.",
    )
    @app_commands.describe(
        player1="Select first player",
        player2="Select second player",
    )
    @app_commands.autocomplete(player1=player_autocomplete, player2=player_autocomplete)
    async def h2h(interaction: discord.Interaction, player1: str, player2: str) -> None:
        err = check_guild_and_channel(interaction, config)
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return

        all_matches = await excel_manager.get_all_matches()
        try:
            h2h_data = calculate_head_to_head(player1, player2, all_matches)
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        p1 = h2h_data["player1"]
        p2 = h2h_data["player2"]
        tog = h2h_data["together"]
        aga = h2h_data["against"]

        if tog["matches"] == 0 and aga["matches"] == 0:
            await interaction.response.send_message(
                f"📋 **{p1}** and **{p2}** have not shared any recorded matches yet.",
            )
            return

        embed = discord.Embed(
            title=f"⚔️ Head-to-Head & Chemistry — {p1} vs {p2}",
            color=discord.Color.magenta(),
            timestamp=datetime.now(),
        )

        if tog["matches"] > 0:
            tog_val = (
                f"**Matches Together:** {tog['matches']}\n\n"
                f"**Record:** {tog['wins']}W / {tog['losses']}L ({tog['win_rate']:.1f}% WR)\n\n"
                f"**{p1}:** {tog['p1_goals']}G, {tog['p1_assists']}A · "
                f"**{p2}:** {tog['p2_goals']}G, {tog['p2_assists']}A"
            )
        else:
            tog_val = "No matches played on the same team yet."

        if aga["matches"] > 0:
            aga_val = (
                f"**Matches Against:** {aga['matches']}\n\n"
                f"**Wins:** {p1} {aga['p1_wins']} — {aga['p2_wins']} {p2}\n\n"
                f"**{p1}:** {aga['p1_goals']}G, {aga['p1_assists']}A ({aga['p1_win_rate']:.1f}% WR)\n\n"
                f"**{p2}:** {aga['p2_goals']}G, {aga['p2_assists']}A ({aga['p2_win_rate']:.1f}% WR)"
            )
        else:
            aga_val = "No matches played on opposing teams yet."

        embed.add_field(name="🤝 Playing Together", value=tog_val, inline=False)
        embed.add_field(name="🥊 Playing Against Each Other", value=aga_val, inline=False)
        embed.set_footer(text=f"Total shared matches: {tog['matches'] + aga['matches']}")
        await interaction.response.send_message(embed=embed)

    # 13. /compare
    @tree.command(
        name="compare",
        description="Side-by-side career statistics comparison of two players.",
    )
    @app_commands.describe(
        player1="Select first player",
        player2="Select second player",
    )
    @app_commands.autocomplete(player1=player_autocomplete, player2=player_autocomplete)
    async def compare(interaction: discord.Interaction, player1: str, player2: str) -> None:
        err = check_guild_and_channel(interaction, config)
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return

        records = await excel_manager.get_leaderboard_from_sheet()
        c1 = canonicalize_player_name(player1)
        c2 = canonicalize_player_name(player2)
        if c1.lower() == c2.lower():
            await interaction.response.send_message(
                f"❌ Please select two different players to compare (both resolved to **{c1}**).",
                ephemeral=True,
            )
            return

        r1 = next((r for r in records if r["player"].lower() == c1.lower()), None)
        r2 = next((r for r in records if r["player"].lower() == c2.lower()), None)
        if not r1 or not r2:
            missing = c1 if not r1 else c2
            await interaction.response.send_message(
                f"❌ Player **{missing}** was not found in Excel leaderboard. Use `/teammates` to see registered players.",
                ephemeral=True,
            )
            return

        rating1 = compute_player_card_ovr(r1)
        rating2 = compute_player_card_ovr(r2)

        def _cmp_line(label: str, v1: float | int, v2: float | int, fmt: str = "d") -> str:
            s1 = f"{v1:{fmt}}"
            s2 = f"{v2:{fmt}}"
            if v1 > v2:
                return f"**{label}:** ◀️ **{s1}**  vs  {s2}"
            if v2 > v1:
                return f"**{label}:** {s1}  vs  **{s2}** ▶️"
            return f"**{label}:** {s1}  vs  {s2}"

        rows = [
            _cmp_line("Matches Played", r1["matches_played"], r2["matches_played"]),
            _cmp_line("Goals", r1["goals"], r2["goals"]),
            _cmp_line("Assists", r1["assists"], r2["assists"]),
            _cmp_line("G/A", r1["ga"], r2["ga"]),
            _cmp_line("Wins", r1["wins"], r2["wins"]),
            _cmp_line("Win Rate%", r1["win_rate"], r2["win_rate"], ".1f"),
            _cmp_line("GPG", r1["gpg"], r2["gpg"], ".2f"),
            _cmp_line("APG", r1["apg"], r2["apg"], ".2f"),
            _cmp_line("GAPG", r1["gapg"], r2["gapg"], ".2f"),
            _cmp_line("Hattricks", r1.get("hattricks", 0), r2.get("hattricks", 0)),
            _cmp_line("Card OVR", rating1, rating2, "d"),
        ]

        embed = discord.Embed(
            title=f"⚖️ Player Comparison — {r1['player']} vs {r2['player']}",
            description="\n\n".join(rows),
            color=discord.Color.teal(),
            timestamp=datetime.now(),
        )
        chart_buf = render_compare_chart(r1, rating1, r2, rating2)
        chart_name = "player_compare_chart.png"
        chart_file = discord.File(fp=chart_buf, filename=chart_name)
        embed.set_image(url=f"attachment://{chart_name}")
        embed.set_footer(text="◀️ / ▶️ highlights the leader in each stat • Fetched from Excel")
        await interaction.response.send_message(embed=embed, file=chart_file)

    # 14. /hall-of-fame
    @tree.command(
        name="hall-of-fame",
        description="View all-time club records, biggest wins, single-match highs, and milestones.",
    )
    async def hall_of_fame(interaction: discord.Interaction) -> None:
        err = check_guild_and_channel(interaction, config)
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return

        await interaction.response.defer()

        all_matches = await excel_manager.get_all_matches()
        hof = calculate_hall_of_fame(all_matches)
        if not hof:
            await interaction.followup.send(
                "📋 No matches recorded yet. Once matches are saved, the Hall of Fame will appear here!",
            )
            return

        margin_lines = [format_match_scoreline(m) for m, _, _ in hof["biggest_margin_matches"][:3]]
        total_lines = [format_match_scoreline(m) for m, _, _ in hof["highest_total_matches"][:3]]
        clean_lines = [format_match_scoreline(m) for m, _, _ in reversed(hof["clean_sheets"])][:3]

        sections = [
            f"🔥 **Biggest Win Margin (+{hof['biggest_margin']} goals)**\n" + "\n".join(margin_lines),
            f"🎯 **Highest-Scoring Match ({hof['highest_total']} total goals)**\n" + "\n".join(total_lines),
            f"⚽ **Most Goals in One Match ({hof['max_single_goals']} goals)**\n"
            + ", ".join(f"**{p}**" for p in hof["max_single_goals_players"]),
            f"🅰️ **Most Assists in One Match ({hof['max_single_assists']} assists)**\n"
            + ", ".join(f"**{p}**" for p in hof["max_single_assists_players"]),
            f"🌟 **Most G/A in One Match ({hof['max_single_ga']} G/A)**\n"
            + ", ".join(f"**{p}**" for p in hof["max_single_ga_players"]),
        ]

        if hof["best_win_streak"] > 0:
            sections.append(
                f"🚀 **Longest Win Streak ({hof['best_win_streak']} wins in a row)**\n"
                + ", ".join(f"**{p}**" for p in hof["best_win_streak_players"])
            )

        if clean_lines:
            sections.append(
                f"🧤 **Clean Sheet Victories ({len(hof['clean_sheets'])} total)**\n" + "\n".join(clean_lines)
            )

        career_bits = []
        if hof["top_scorer"]:
            career_bits.append(f"Top Scorer: **{hof['top_scorer'].player}** ({hof['top_scorer'].goals}G)")
        if hof["top_assister"]:
            career_bits.append(f"Playmaker: **{hof['top_assister'].player}** ({hof['top_assister'].assists}A)")
        if hof["hattrick_king"] and hof["hattrick_king"].hattricks > 0:
            career_bits.append(f"Hattrick King: **{hof['hattrick_king'].player}** ({hof['hattrick_king'].hattricks})")
        if career_bits:
            sections.append("👑 **All-Time Legends**\n" + " · ".join(career_bits))

        embed = discord.Embed(
            title="🏛️ eFootball Co-op Hall of Fame",
            description="\n\n".join(sections),
            color=discord.Color.gold(),
            timestamp=datetime.now(),
        )
        embed.set_footer(text=f"Computed across {hof['total_matches']} all-time matches")
        await interaction.followup.send(embed=embed)

    # 15. /card
    @tree.command(
        name="card",
        description="Generate an FC 27-style Ultimate Team player card image from career stats.",
    )
    @app_commands.describe(player="Select player from list")
    @app_commands.autocomplete(player=player_autocomplete)
    async def card(interaction: discord.Interaction, player: str) -> None:
        err = check_guild_and_channel(interaction, config)
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return

        await interaction.response.defer()

        records = await excel_manager.get_leaderboard_from_sheet()
        all_matches = await excel_manager.get_all_matches()
        card_stats = calculate_player_card_stats(player, records, all_matches)

        if not card_stats:
            await interaction.followup.send(
                f"❌ Player **{player}** was not found in Excel leaderboard. Use `/teammates` to see registered players.",
            )
            return

        png_buf = render_fc27_card(card_stats)
        safe_slug = re.sub(r"[^a-zA-Z0-9_-]", "", card_stats["player"].lower()) or "player"
        filename = f"{safe_slug}_fc27_card.png"
        file = discord.File(fp=png_buf, filename=filename)

        embed = discord.Embed(
            title=f"🃏 FC 27 Player Card — {card_stats['player']} ({card_stats['rating']} OVR)",
            description=(
                f"**PAC:** {card_stats['pace']} · **SHO:** {card_stats['shooting']} · **PAS:** {card_stats['passing']} · "
                f"**DRI:** {card_stats['dribbling']} · **DEF:** {card_stats['defending']} · **PHY:** {card_stats['physical']}"
            ),
            color=discord.Color.gold(),
            timestamp=datetime.now(),
        )
        embed.set_image(url=f"attachment://{filename}")
        embed.set_footer(text="OVR=60–99 · PAC=71–99 · SHO=Goals · PAS=Assists · DRI=GAPG · DEF=Wins/GC/CS · PHY=Avg(OVR,PAC,DRI)")
        await interaction.followup.send(embed=embed, file=file)

    # 16. /chemistry
    @tree.command(
        name="chemistry",
        description="View a player's best partner, cursed duo, kryptonite, and favorite opponent.",
    )
    @app_commands.describe(player="Select player from list")
    @app_commands.autocomplete(player=player_autocomplete)
    async def chemistry(interaction: discord.Interaction, player: str) -> None:
        err = check_guild_and_channel(interaction, config)
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return

        await interaction.response.defer()

        all_matches = await excel_manager.get_all_matches()
        try:
            chem = calculate_player_chemistry(player, all_matches)
        except ValueError as exc:
            await interaction.followup.send(f"❌ {exc}")
            return

        if chem["total_matches"] == 0:
            await interaction.followup.send(
                f"📋 No match appearances recorded yet for **{chem['player']}**.",
            )
            return

        sections = []
        if chem["best_partner"]:
            bp = chem["best_partner"]
            sections.append(
                f"💚 **Best Partner:** **{bp['player']}** — {bp['wins']}W / {bp['losses']}L ({bp['win_rate']:.1f}% WR in {bp['matches']} matches)"
            )
        if chem["cursed_duo"]:
            cd = chem["cursed_duo"]
            sections.append(
                f"💀 **Cursed Duo:** **{cd['player']}** — {cd['wins']}W / {cd['losses']}L ({cd['win_rate']:.1f}% WR in {cd['matches']} matches)"
            )
        if chem["most_frequent_partner"]:
            fp = chem["most_frequent_partner"]
            sections.append(
                f"🤝 **Most Frequent Teammate:** **{fp['player']}** — {fp['matches']} matches together ({fp['win_rate']:.1f}% WR)"
            )
        if chem["favorite_victim"]:
            fv = chem["favorite_victim"]
            sections.append(
                f"🎯 **Favorite Victim:** **{fv['player']}** — {fv['my_wins']}W / {fv['opp_wins']}L against them ({fv['my_win_rate']:.1f}% WR)"
            )
        if chem["kryptonite"]:
            kr = chem["kryptonite"]
            sections.append(
                f"🧲 **Kryptonite:** **{kr['player']}** — lost {kr['opp_wins']} of {kr['matches']} against them ({kr['opp_win_rate']:.1f}% opponent WR)"
            )

        embed = discord.Embed(
            title=f"🧪 Synergy & Rivalry Matrix — {chem['player']}",
            description="\n\n".join(sections) if sections else "Not enough teammate/opponent data yet.",
            color=discord.Color.purple(),
            timestamp=datetime.now(),
        )
        embed.set_footer(text=f"Computed across {chem['total_matches']} matches played • Prefers 2+ match sample when available")
        await interaction.followup.send(embed=embed)

    async def match_id_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> List[app_commands.Choice[str]]:
        """Dropdown list of recent matches showing players and score for easy selection."""
        query = current.strip().lower()
        try:
            all_matches = await excel_manager.get_all_matches()
        except Exception:
            return []

        choices: List[app_commands.Choice[str]] = []
        for m in reversed(all_matches):
            plain_score = format_match_scoreline(m).replace("**", "")
            label = f"{m.match_id} — {plain_score}"
            if len(label) > 100:
                label = label[:97] + "..."
            if not query or query in label.lower():
                choices.append(app_commands.Choice(name=label, value=m.match_id))
            if len(choices) >= 25:
                break
        return choices

    # 17. /edit-match
    @tree.command(
        name="edit-match",
        description="Edit a saved match's players, goals, assists, scores, or result.",
    )
    @app_commands.describe(match_id="Select the match to edit")
    @app_commands.autocomplete(match_id=match_id_autocomplete)
    async def edit_match(interaction: discord.Interaction, match_id: str) -> None:
        err = check_guild_and_channel(interaction, config)
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return

        match_rec = await excel_manager.get_match(match_id.strip())
        if not match_rec:
            await interaction.response.send_message(
                f"❌ Match **{match_id}** was not found.",
                ephemeral=True,
            )
            return

        modal = EditSavedMatchModal(match_record=match_rec, excel_manager=excel_manager)
        await interaction.response.send_modal(modal)

    # 18. /leaders
    @tree.command(
        name="leaders",
        description="View the #1 leader(s) for each stat category at a glance.",
    )
    async def leaders(interaction: discord.Interaction) -> None:
        err = check_guild_and_channel(interaction, config)
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return

        await interaction.response.defer()

        records = await excel_manager.get_leaderboard_from_sheet()
        if not records:
            await interaction.followup.send(
                "📋 No match statistics have been recorded yet in Excel. Use `/add-match-image` or `/add-match-text` to record a match!",
            )
            return

        categories = [
            ("⚽", "Goals", "goals", "d"),
            ("🅰️", "Assists", "assists", "d"),
            ("🔥", "G/A", "ga", "d"),
            ("🎯", "GPG", "gpg", ".2f"),
            ("🎁", "APG", "apg", ".2f"),
            ("⚡", "GAPG", "gapg", ".2f"),
            ("🏆", "Wins", "wins", "d"),
            ("📈", "Win Rate", "win_rate", ".1f%"),
            ("🎩", "Hattricks", "hattricks", "d"),
            ("🧤", "Clean Sheets", "clean_sheets", "d"),
        ]

        lines: List[str] = []
        for emoji, label, key, fmt in categories:
            top_val = max(r.get(key, 0) for r in records)
            if top_val <= 0:
                lines.append(f"{emoji} **{label}:** None yet")
                continue

            holders = [r["player"] for r in records if r.get(key, 0) == top_val]
            holders_str = ", ".join(holders)
            if fmt == ".1f%":
                val_str = f"{float(top_val):.1f}%"
            elif fmt == ".2f":
                val_str = f"{float(top_val):.2f}"
            else:
                val_str = str(int(top_val))

            lines.append(f"{emoji} **{label}:** **{holders_str}** — {val_str}")

        embed = discord.Embed(
            title="👑 Stat Leaders — Current #1s",
            description="\n\n".join(lines),
            color=discord.Color.gold(),
            timestamp=datetime.now(),
        )
        embed.set_footer(text=f"Across {len(records)} registered players • Fetched from Excel")
        await interaction.followup.send(embed=embed)

    # 19. /recap
    @tree.command(
        name="recap",
        description="View a session recap of today's matches, session MVP, and highlights.",
    )
    @app_commands.describe(date="Specific date in YYYY-MM-DD format (optional, defaults to latest session)")
    async def recap(interaction: discord.Interaction, date: Optional[str] = None) -> None:
        err = check_guild_and_channel(interaction, config)
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return

        await interaction.response.defer()

        all_matches = await excel_manager.get_all_matches()
        summary = calculate_session_recap(all_matches, target_date=date)

        if summary["total_matches"] == 0:
            date_label = date or "the latest match day"
            await interaction.followup.send(f"📋 No matches found for **{date_label}**.")
            return

        embed = discord.Embed(
            title=f"📋 Matchday Session Recap — {summary['date']}",
            color=discord.Color.blue(),
            timestamp=datetime.now(),
        )

        overview_lines = [
            f"**Matches Played:** {summary['total_matches']}",
            f"**Total Goals Scored:** {summary['total_goals']}",
            f"**Clean Sheet Victories:** {summary['clean_sheets']}",
        ]
        if summary["biggest_win_match"]:
            bw = summary["biggest_win_match"]
            overview_lines.append(f"**Biggest Blowout (+{summary['biggest_win_margin']} goals):** {format_match_scoreline(bw)}")

        embed.add_field(name="📊 Session Overview", value="\n".join(overview_lines), inline=False)

        mvp = summary.get("mvp")
        if mvp:
            embed.add_field(
                name="⭐ Session MVP",
                value=f"👑 **{mvp['player']}** — {mvp['goals']}G, {mvp['assists']}A ({mvp['wins']}W - {mvp['losses']}L • Impact: {mvp['score']})",
                inline=False,
            )

        leader_bits = []
        if summary.get("top_scorer") and summary["top_scorer"]["goals"] > 0:
            ts = summary["top_scorer"]
            leader_bits.append(f"⚽ **Top Scorer:** {ts['player']} ({ts['goals']}G)")
        if summary.get("top_playmaker") and summary["top_playmaker"]["assists"] > 0:
            tp = summary["top_playmaker"]
            leader_bits.append(f"🎯 **Playmaker:** {tp['player']} ({tp['assists']}A)")
        if leader_bits:
            embed.add_field(name="🔥 Top Performers", value="\n".join(leader_bits), inline=False)

        match_lines = [f"{idx}. {format_match_scoreline(m)}" for idx, m in enumerate(reversed(summary["matches"][:8]), start=1)]
        embed.add_field(name="📜 Match Results (Latest first)", value="\n\n".join(match_lines), inline=False)
        embed.set_footer(text=f"Total session matches: {summary['total_matches']} • Use /recap date:YYYY-MM-DD for past sessions")
        await interaction.followup.send(embed=embed)

    # 20. /trophies
    @tree.command(
        name="trophies",
        description="View unlockable career achievements, badges, and milestones for a player.",
    )
    @app_commands.describe(player="Select player from list")
    @app_commands.autocomplete(player=player_autocomplete)
    async def trophies(interaction: discord.Interaction, player: str) -> None:
        err = check_guild_and_channel(interaction, config)
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return

        await interaction.response.defer()

        all_matches = await excel_manager.get_all_matches()
        all_stats = await excel_manager.get_all_player_stats()
        aggs = calculate_player_aggregates(all_stats, matches=all_matches)
        t_data = calculate_player_trophies(player, all_matches, aggs)

        embed = discord.Embed(
            title=f"🏆 Trophy Room & Badges — {t_data['player']}",
            description=f"Unlocked **{t_data['total_unlocked']} of {t_data['total_badges']}** Achievements",
            color=discord.Color.gold(),
            timestamp=datetime.now(),
        )

        if t_data["unlocked"]:
            unlocked_lines = [
                f"{emoji} **{name}** — *{desc}*"
                for emoji, name, desc, _ in t_data["unlocked"]
            ]
            embed.add_field(name="✨ Unlocked Achievements", value="\n\n".join(unlocked_lines), inline=False)

        if t_data["locked"]:
            locked_lines = [
                f"🔒 **{name}** — *{desc}*"
                for _, name, desc, _ in t_data["locked"]
            ]
            embed.add_field(name="⏳ In Progress / Locked", value="\n\n".join(locked_lines), inline=False)

        embed.set_footer(text="Earn badges dynamically by playing matches and setting club records!")
        await interaction.followup.send(embed=embed)

    # 21. /tierlist
    @tree.command(
        name="tierlist",
        description="Display an S/A/B/C visual tier list ranking all server players.",
    )
    async def tierlist(interaction: discord.Interaction) -> None:
        err = check_guild_and_channel(interaction, config)
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return

        await interaction.response.defer()

        records = await excel_manager.get_leaderboard_from_sheet()
        all_matches = await excel_manager.get_all_matches()
        if not records:
            await interaction.followup.send("📋 No player records found to generate a tier list.")
            return

        tiers = calculate_player_tierlist(records, all_matches)
        chart_buf = render_tierlist_card(tiers)
        chart_file = discord.File(fp=chart_buf, filename="server_tierlist.png")

        s_names = ", ".join(f"{p['player']} ({p['ovr']})" for p in tiers["S"]) or "None"
        a_names = ", ".join(f"{p['player']} ({p['ovr']})" for p in tiers["A"]) or "None"
        b_names = ", ".join(f"{p['player']} ({p['ovr']})" for p in tiers["B"]) or "None"
        c_names = ", ".join(f"{p['player']} ({p['ovr']})" for p in tiers["C"]) or "None"

        embed = discord.Embed(
            title="🏅 Co-op Squad Tier List",
            description=(
                f"🔴 **S Tier (88+ OVR):** {s_names}\n\n"
                f"🟠 **A Tier (78-87 OVR):** {a_names}\n\n"
                f"🔵 **B Tier (70-77 OVR):** {b_names}\n\n"
                f"⚪ **C Tier (<70 OVR):** {c_names}"
            ),
            color=discord.Color.dark_purple(),
            timestamp=datetime.now(),
        )
        embed.set_image(url="attachment://server_tierlist.png")
        embed.set_footer(text="Rankings calibrated by FC 27 OVR ratings and career statistics")
        await interaction.followup.send(embed=embed, file=chart_file)
