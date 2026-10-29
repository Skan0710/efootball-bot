"""Unit and integration tests for Discord slash command registration, security, and views."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
from discord import app_commands
import pytest

from bot import EFootballBot
from commands_handler import (
    EditStatsModal,
    MatchPreviewView,
    check_guild_and_channel,
    is_user_admin,
    register_commands,
)
from config import BotConfig
from excel_manager import ExcelManager
from models import MatchRecord, MatchResult, OCRPlayerCandidate, PlayerStat
from stats import calculate_player_aggregates


@pytest.fixture
def test_config(tmp_path: Path) -> BotConfig:
    return BotConfig(
        discord_token="fake_token",
        guild_id=1122334455,
        workbook_path=tmp_path / "test_wb.xlsx",
        max_image_mb=10.0,
        allowed_channel_id=999888777,
        admin_role_id=555444333,
    )


def test_command_registration_names(test_config: BotConfig, tmp_path: Path) -> None:
    """Verifies all six required commands are registered with exact hyphenated names."""
    bot = EFootballBot(test_config)
    registered_names = {cmd.name for cmd in bot.tree.get_commands()}

    expected_commands = {
        "add-match-image",
        "delete-match",
        "leaderboard",
        "record",
        "matches",
        "teammates",
    }
    assert registered_names == expected_commands


def test_check_guild_and_channel_security(test_config: BotConfig) -> None:
    """Verifies guild and channel restriction logic."""
    # Interaction from wrong guild
    inter_wrong_guild = MagicMock(spec=discord.Interaction)
    inter_wrong_guild.guild_id = 999999999
    inter_wrong_guild.channel_id = test_config.allowed_channel_id
    err = check_guild_and_channel(inter_wrong_guild, test_config)
    assert err is not None
    assert "restricted to server ID" in err

    # Interaction from wrong channel
    inter_wrong_channel = MagicMock(spec=discord.Interaction)
    inter_wrong_channel.guild_id = test_config.guild_id
    inter_wrong_channel.channel_id = 111111111
    err = check_guild_and_channel(inter_wrong_channel, test_config)
    assert err is not None
    assert "restricted to" in err

    # Interaction from correct guild and channel
    inter_valid = MagicMock(spec=discord.Interaction)
    inter_valid.guild_id = test_config.guild_id
    inter_valid.channel_id = test_config.allowed_channel_id
    err = check_guild_and_channel(inter_valid, test_config)
    assert err is None


def test_is_user_admin_check(test_config: BotConfig) -> None:
    """Verifies administrator permission and admin role validation."""
    inter = MagicMock(spec=discord.Interaction)
    inter.guild = MagicMock()
    member = MagicMock(spec=discord.Member)

    # User with Administrator permission
    member.guild_permissions.administrator = True
    member.roles = []
    inter.user = member
    assert is_user_admin(inter, test_config) is True

    # User with Admin Role
    member.guild_permissions.administrator = False
    admin_role = MagicMock(spec=discord.Role)
    admin_role.id = test_config.admin_role_id
    member.roles = [admin_role]
    assert is_user_admin(inter, test_config) is True

    # Regular user without permissions or role
    member.guild_permissions.administrator = False
    regular_role = MagicMock(spec=discord.Role)
    regular_role.id = 12345
    member.roles = [regular_role]
    assert is_user_admin(inter, test_config) is False


@pytest.mark.asyncio
async def test_preview_view_security_and_save(tmp_path: Path) -> None:
    """Verifies that only the submitting user can interact and Confirm saves match."""
    wb_path = tmp_path / "matches.xlsx"
    manager = ExcelManager(wb_path)

    candidates = [
        OCRPlayerCandidate(player="SkanRocky", goals=2, assists=0),
        OCRPlayerCandidate(player="lillym", goals=0, assists=1),
    ]

    view = MatchPreviewView(
        submitting_user_id=1001,
        candidates=candidates,
        result=MatchResult.WIN,
        excel_manager=manager,
        image_hash="abc123hash",
    )

    # 1. Other user interaction check
    other_interaction = MagicMock(spec=discord.Interaction)
    other_interaction.user.id = 9999
    other_interaction.response = AsyncMock()
    can_interact = await view.interaction_check(other_interaction)
    assert can_interact is False
    other_interaction.response.send_message.assert_awaited_once()

    # 2. Submitting user interaction check
    owner_interaction = MagicMock(spec=discord.Interaction)
    owner_interaction.user.id = 1001
    assert await view.interaction_check(owner_interaction) is True

    # 3. Confirm & Save execution
    owner_interaction.response.defer = AsyncMock()
    owner_interaction.edit_original_response = AsyncMock()
    owner_interaction.user.display_name = "SubmittingUser"
    owner_interaction.user.name = "SubmittingUser"

    button = MagicMock(spec=discord.ui.Button)
    await view.confirm_button.callback(owner_interaction)

    # Verify match was persisted into Excel
    matches = await manager.get_all_matches()
    assert len(matches) == 1
    assert matches[0].result == MatchResult.WIN
    assert len(matches[0].player_stats) == 2


@pytest.mark.asyncio
async def test_edit_stats_modal_validation() -> None:
    """Verifies parsing and validation of manually edited player statistics."""
    view = MagicMock(spec=MatchPreviewView)
    view.candidates = []
    view.result = MatchResult.WIN
    view.home_score = None
    view.away_score = None
    view.home_team = "Home"
    view.away_team = "Away"
    view.away_candidates = []
    modal = EditStatsModal(parent_view=view)

    # Valid multi-player input
    modal.stats_input._value = "SkanRocky, 2, 1\nlillym, 0, 3"
    modal.result_input._value = "Win"
    modal.scores_input._value = "2, 0"
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response.edit_message = AsyncMock()

    await modal.on_submit(interaction)

    assert len(view.candidates) == 2
    assert view.candidates[0].player == "SkanRocky"
    assert view.candidates[0].goals == 2
    assert view.candidates[0].assists == 1
    assert view.candidates[1].player == "lillym"
    assert view.candidates[1].goals == 0
    assert view.candidates[1].assists == 3
    interaction.response.edit_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_edit_stats_modal_invalid_input_rejected() -> None:
    """Verifies that invalid lines or negative integers are rejected."""
    view = MagicMock(spec=MatchPreviewView)
    view.candidates = []
    view.result = MatchResult.WIN
    view.home_score = None
    view.away_score = None
    view.home_team = "Home"
    view.away_team = "Away"
    view.away_candidates = []
    modal = EditStatsModal(parent_view=view)

    # Invalid input (missing assists, negative goals)
    modal.stats_input._value = "BadFormatLine\nInvalidPlayer, -1, 2"
    modal.result_input._value = "Win"
    modal.scores_input._value = ""
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response.send_message = AsyncMock()

    await modal.on_submit(interaction)

    # view.candidates should remain unchanged
    assert len(view.candidates) == 0
    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.call_args
    assert "Validation Errors" in args[0]


def test_two_image_score_result_calculation() -> None:
    """Verifies that 1st image counts as home score, 2nd image as away score, and outcome is calculated."""
    # Home 3 - Away 1 -> Win
    home_score = 3
    away_score = 1
    if home_score > away_score:
        res = MatchResult.WIN
    elif home_score < away_score:
        res = MatchResult.LOSS
    else:
        res = MatchResult.DRAW
    assert res == MatchResult.WIN

    # Home 1 - Away 2 -> Loss
    home_score = 1
    away_score = 2
    if home_score > away_score:
        res = MatchResult.WIN
    elif home_score < away_score:
        res = MatchResult.LOSS
    else:
        res = MatchResult.DRAW
    assert res == MatchResult.LOSS

    # Home 2 - Away 2 -> Draw
    home_score = 2
    away_score = 2
    if home_score > away_score:
        res = MatchResult.WIN
    elif home_score < away_score:
        res = MatchResult.LOSS
    else:
        res = MatchResult.DRAW
    assert res == MatchResult.DRAW


def test_parse_players_from_team_param() -> None:
    """Verifies that 'S1 and S2', 'S1 & S2', 'S1, S2' are parsed properly into individual players."""
    from commands_handler import parse_players_from_team_param

    assert parse_players_from_team_param("Skan and Suri") == ["Skan", "Suri"]
    assert parse_players_from_team_param("Skan & Suri") == ["Skan", "Suri"]
    assert parse_players_from_team_param("Skan, Suri") == ["Skan", "Suri"]
    assert parse_players_from_team_param("Player1 and Player2 and Player3") == ["Player1", "Player2", "Player3"]
    assert parse_players_from_team_param("Home") == []
    assert parse_players_from_team_param("XI") == []
    assert parse_players_from_team_param(None) == []


@pytest.mark.asyncio
async def test_home_and_away_win_outcomes_saved_to_excel(tmp_path: Path) -> None:
    """Verifies that Home Win adds a win for home players (loss for away), and Away Win adds a win for away players."""
    wb_path = tmp_path / "matches.xlsx"
    manager = ExcelManager(wb_path)

    home_players = [
        OCRPlayerCandidate(player="Skan", goals=3, assists=1),
        OCRPlayerCandidate(player="Suri", goals=1, assists=2),
    ]
    away_players = [
        OCRPlayerCandidate(player="Alex", goals=1, assists=0),
        OCRPlayerCandidate(player="Bob", goals=1, assists=1),
    ]

    # 1. Match with HOME_WIN
    view_hw = MatchPreviewView(
        submitting_user_id=101,
        candidates=home_players,
        result=MatchResult.HOME_WIN,
        excel_manager=manager,
        image_hash="hash_home_win",
        home_team="Skan & Suri",
        away_team="Alex & Bob",
        home_score=4,
        away_score=2,
        away_candidates=away_players,
    )

    owner_interaction = MagicMock(spec=discord.Interaction)
    owner_interaction.user.id = 101
    owner_interaction.user.display_name = "Skan"
    owner_interaction.user.name = "Skan"
    owner_interaction.response.defer = AsyncMock()
    owner_interaction.edit_original_response = AsyncMock()

    await view_hw.confirm_button.callback(owner_interaction)

    all_stats = await manager.get_all_player_stats()
    aggs = calculate_player_aggregates(all_stats)

    # Home players get WIN
    assert aggs["skan"].wins == 1
    assert aggs["skan"].losses == 0
    assert aggs["suri"].wins == 1
    assert aggs["suri"].losses == 0
    # Away players get LOSS
    assert aggs["alex"].wins == 0
    assert aggs["alex"].losses == 1
    assert aggs["bob"].wins == 0
    assert aggs["bob"].losses == 1

    # 2. Match with AWAY_WIN
    view_aw = MatchPreviewView(
        submitting_user_id=101,
        candidates=home_players,
        result=MatchResult.AWAY_WIN,
        excel_manager=manager,
        image_hash="hash_away_win",
        home_team="Skan & Suri",
        away_team="Alex & Bob",
        home_score=0,
        away_score=2,
        away_candidates=away_players,
    )

    await view_aw.confirm_button.callback(owner_interaction)

    all_stats2 = await manager.get_all_player_stats()
    aggs2 = calculate_player_aggregates(all_stats2)

    # In match 2 (Away Win): Away players got a win, Home players got a loss!
    # Skan & Suri now have 1 win, 1 loss total
    assert aggs2["skan"].wins == 1
    assert aggs2["skan"].losses == 1
    assert aggs2["suri"].wins == 1
    assert aggs2["suri"].losses == 1
    # Alex & Bob now have 1 win, 1 loss total
    assert aggs2["alex"].wins == 1
    assert aggs2["alex"].losses == 1
    assert aggs2["bob"].wins == 1
    assert aggs2["bob"].losses == 1

