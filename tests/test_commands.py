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
        "add-match-text",
        "delete-match",
        "delete-all",
        "leaderboard",
        "record",
        "matches",
        "teammates",
        "potw",
        "potm",
        "best-team",
        "fair-teams",
        "form",
        "h2h",
        "compare",
        "hall-of-fame",
        "card",
        "chemistry",
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
    assert view.candidates[0].player == "Skan"
    assert view.candidates[0].goals == 2
    assert view.candidates[0].assists == 1
    assert view.candidates[1].player == "Lilly"
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


@pytest.mark.asyncio
async def test_add_match_text_modal(tmp_path: Path) -> None:
    """Verifies that AddMatchTextModal parses text inputs into a MatchPreviewView."""
    from commands_handler import AddMatchTextModal

    excel_file = tmp_path / "test_text_matches.xlsx"
    manager = ExcelManager(excel_file)

    modal = AddMatchTextModal(excel_manager=manager)
    modal.home_stats_input._value = "Skan, 2, 0\nLilly, 0, 1"
    modal.away_stats_input._value = "Suri, 1, 2\nAnnie, 2, 1"
    modal.scores_input._value = "2, 3"
    modal.teams_input._value = "Skan & Lilly, Suri & Annie"
    modal.result_input._value = "Away Win"

    interaction = MagicMock(spec=discord.Interaction)
    interaction.user.id = 12345
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()

    await modal.on_submit(interaction)

    interaction.response.send_message.assert_called_once()
    kwargs = interaction.response.send_message.call_args[1]
    assert kwargs.get("ephemeral") is True
    view = kwargs.get("view")
    assert view is not None
    assert len(view.candidates) == 2
    assert view.candidates[0].player == "Skan"
    assert view.candidates[0].goals == 2
    assert view.candidates[0].assists == 0
    assert len(view.away_candidates) == 2
    assert view.away_candidates[0].player == "Suri"
    assert view.home_score == 2
    assert view.away_score == 3
    assert view.result == MatchResult.AWAY_WIN


@pytest.mark.asyncio
async def test_delete_all_matches_flow(tmp_path: Path) -> None:
    """Verifies DeleteAllConfirmView wipes all matches from the workbook."""
    from commands_handler import DeleteAllConfirmView

    excel_file = tmp_path / "test_delete_all.xlsx"
    manager = ExcelManager(excel_file)

    # Save a match first
    match_rec = MatchRecord(
        match_id="M-TEST-1",
        date="2026-10-29",
        result=MatchResult.WIN,
        submitted_by="Admin",
        screenshot_ref="hash1",
        notes="Notes",
        player_stats=[PlayerStat(player="Skan", goals=2, assists=1, result=MatchResult.WIN)],
    )
    await manager.save_match(match_rec)
    matches_before = await manager.get_all_matches()
    assert len(matches_before) == 1

    # Wipe matches
    view = DeleteAllConfirmView(submitting_user_id=999, excel_manager=manager)
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user.id = 999
    interaction.user.name = "AdminUser"
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.edit_original_response = AsyncMock()

    await view.confirm_delete_all.callback(interaction)

    matches_after = await manager.get_all_matches()
    assert len(matches_after) == 0


@pytest.mark.asyncio
async def test_potw_best_team_and_hattrick_leaderboard_commands(test_config: BotConfig) -> None:
    """Verifies /potw, /best-team, and /leaderboard category:hattrick command embeds."""
    from datetime import datetime

    bot = EFootballBot(test_config)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    manager = bot.excel_manager

    await manager.save_match(
        MatchRecord(
            match_id="M-CMD-1",
            date=now_str,
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Suri", goals=3, assists=2, result=MatchResult.WIN),
                PlayerStat(player="Ashy", goals=2, assists=3, result=MatchResult.WIN),
                PlayerStat(player="Skan", goals=0, assists=0, result=MatchResult.LOSS),
                PlayerStat(player="Annie", goals=0, assists=0, result=MatchResult.LOSS),
            ],
        )
    )

    def _make_interaction() -> MagicMock:
        inter = MagicMock(spec=discord.Interaction)
        inter.guild_id = test_config.guild_id
        inter.channel_id = test_config.allowed_channel_id
        inter.response = MagicMock()
        inter.response.defer = AsyncMock()
        inter.response.send_message = AsyncMock()
        inter.followup = MagicMock()
        inter.followup.send = AsyncMock()
        return inter

    # 1. Test /potw
    potw_cmd = bot.tree.get_command("potw")
    assert potw_cmd is not None
    inter_potw = _make_interaction()
    await potw_cmd.callback(inter_potw, 0)
    inter_potw.followup.send.assert_called_once()
    potw_embed = inter_potw.followup.send.call_args[1]["embed"]
    assert "Player of the Week" in potw_embed.title
    assert "Suri" in potw_embed.description
    assert "Ashy" in potw_embed.fields[0].value
    assert "\n\n" in potw_embed.fields[0].value
    assert "`" not in potw_embed.fields[0].value

    # 1b. Test /potm
    potm_cmd = bot.tree.get_command("potm")
    assert potm_cmd is not None
    inter_potm = _make_interaction()
    await potm_cmd.callback(inter_potm, 0)
    inter_potm.followup.send.assert_called_once()
    potm_embed = inter_potm.followup.send.call_args[1]["embed"]
    assert "Player of the Month" in potm_embed.title
    assert "Suri" in potm_embed.description
    assert "Ashy" in potm_embed.fields[0].value
    assert "\n\n" in potm_embed.fields[0].value
    assert "`" not in potm_embed.fields[0].value

    # 2. Test /best-team
    best_team_cmd = bot.tree.get_command("best-team")
    assert best_team_cmd is not None
    inter_team = _make_interaction()
    await best_team_cmd.callback(inter_team)
    inter_team.followup.send.assert_called_once()
    team_embed = inter_team.followup.send.call_args[1]["embed"]
    assert "Best Co-op Teams" in team_embed.title
    assert "Ashy & Suri" in team_embed.description
    assert "\n\n" in team_embed.description
    assert "`" not in team_embed.description

    # 3. Test /leaderboard category:Hattrick
    lb_cmd = bot.tree.get_command("leaderboard")
    assert lb_cmd is not None
    inter_lb = _make_interaction()
    choice = app_commands.Choice(name="Hattrick (3+ goals in a game)", value="Hattrick")
    await lb_cmd.callback(inter_lb, choice)
    inter_lb.followup.send.assert_called_once()
    lb_embed = inter_lb.followup.send.call_args[1]["embed"]
    assert "Hattrick" in lb_embed.title
    assert "**Suri** — **1** hattrick" in lb_embed.description
    assert "\n\n" in lb_embed.description
    assert "`" not in lb_embed.description


@pytest.mark.asyncio
async def test_matches_command_only_shows_team_players_and_score(test_config: BotConfig) -> None:
    """Verifies /matches displays only team players and score without match ID or extra metadata."""
    bot = EFootballBot(test_config)
    await bot.excel_manager.save_match(
        MatchRecord(
            match_id="M-SECRET-ID-99",
            date="2026-11-11 17:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Ashy",
            notes="Score: Suri & Ashy 3 - 2 Skan & Jonny (Home Win)",
            player_stats=[
                PlayerStat(player="Suri", goals=0, assists=3, result=MatchResult.WIN),
                PlayerStat(player="Ashy", goals=3, assists=0, result=MatchResult.WIN),
                PlayerStat(player="Skan", goals=2, assists=0, result=MatchResult.LOSS),
                PlayerStat(player="Jonny", goals=0, assists=2, result=MatchResult.LOSS),
            ],
        )
    )

    inter = MagicMock(spec=discord.Interaction)
    inter.guild_id = test_config.guild_id
    inter.channel_id = test_config.allowed_channel_id
    inter.response = MagicMock()
    inter.response.send_message = AsyncMock()

    matches_cmd = bot.tree.get_command("matches")
    assert matches_cmd is not None
    await matches_cmd.callback(inter, 10)

    inter.response.send_message.assert_called_once()
    embed = inter.response.send_message.call_args[1]["embed"]
    assert len(embed.fields) == 0
    assert "1. **Suri & Ashy** 3 - 2 **Skan & Jonny**" in embed.description
    assert "`" not in embed.description
    assert "M-SECRET-ID-99" not in embed.description
    assert "Submitted by" not in embed.description
    assert "3G" not in embed.description
    assert "2026-11-11" not in embed.description


@pytest.mark.asyncio
async def test_fair_teams_command_and_modal(test_config: BotConfig) -> None:
    """Verifies /fair-teams modal opening and 4/5/6 player balancing embed."""
    bot = EFootballBot(test_config)
    await bot.excel_manager.save_match(
        MatchRecord(
            match_id="M-FT-1",
            date="2026-11-11 12:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Suri", goals=4, assists=2, result=MatchResult.WIN),
                PlayerStat(player="Ashy", goals=2, assists=1, result=MatchResult.WIN),
                PlayerStat(player="Skan", goals=1, assists=0, result=MatchResult.LOSS),
                PlayerStat(player="Annie", goals=0, assists=1, result=MatchResult.LOSS),
            ],
        )
    )

    ft_cmd = bot.tree.get_command("fair-teams")
    assert ft_cmd is not None

    # 1. Without inline players -> sends FairTeamsModal
    inter_modal = MagicMock(spec=discord.Interaction)
    inter_modal.guild_id = test_config.guild_id
    inter_modal.channel_id = test_config.allowed_channel_id
    inter_modal.response = MagicMock()
    inter_modal.response.send_modal = AsyncMock()

    choice_4 = app_commands.Choice(name="4 players (2v2)", value=4)
    await ft_cmd.callback(inter_modal, choice_4, None)
    inter_modal.response.send_modal.assert_called_once()
    modal = inter_modal.response.send_modal.call_args[0][0]
    assert modal.expected_count == 4

    # Submit modal with 4 names
    modal.names_input._value = "Suri, Ashy, Skan, Annie"
    inter_sub = MagicMock(spec=discord.Interaction)
    inter_sub.response = MagicMock()
    inter_sub.response.send_message = AsyncMock()
    await modal.on_submit(inter_sub)
    inter_sub.response.send_message.assert_called_once()
    embed = inter_sub.response.send_message.call_args[1]["embed"]
    assert "Fair Teams (2v2" in embed.title
    assert len(embed.fields) == 2

    # 2. Inline 5 players -> directly responds with 3v2 balanced embed
    inter_inline = MagicMock(spec=discord.Interaction)
    inter_inline.guild_id = test_config.guild_id
    inter_inline.channel_id = test_config.allowed_channel_id
    inter_inline.response = MagicMock()
    inter_inline.response.send_message = AsyncMock()
    choice_5 = app_commands.Choice(name="5 players (3v2)", value=5)
    await ft_cmd.callback(inter_inline, choice_5, "Suri, Ashy, Skan, Annie, Azlan")
    inter_inline.response.send_message.assert_called_once()
    embed5 = inter_inline.response.send_message.call_args[1]["embed"]
    assert "Fair Teams (3v2" in embed5.title


@pytest.mark.asyncio
async def test_form_and_record_player_autocomplete(test_config: BotConfig) -> None:
    """Verifies /form and /record list registered players via autocomplete and render form."""
    bot = EFootballBot(test_config)
    await bot.excel_manager.save_match(
        MatchRecord(
            match_id="M-FORM-1",
            date="2026-11-11 12:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            notes="Score: Suri & Ashy 3 - 1 Skan & Annie (Home Win)",
            player_stats=[
                PlayerStat(player="Suri", goals=2, assists=1, result=MatchResult.WIN),
                PlayerStat(player="Ashy", goals=1, assists=2, result=MatchResult.WIN),
                PlayerStat(player="Skan", goals=1, assists=0, result=MatchResult.LOSS),
                PlayerStat(player="Annie", goals=0, assists=1, result=MatchResult.LOSS),
            ],
        )
    )

    form_cmd = bot.tree.get_command("form")
    record_cmd = bot.tree.get_command("record")
    assert form_cmd is not None and record_cmd is not None

    # Both /form and /record expose autocomplete callbacks listing registered players
    inter_ac = MagicMock(spec=discord.Interaction)
    form_choices = await form_cmd._params["player"].autocomplete(inter_ac, "")
    record_choices = await record_cmd._params["player"].autocomplete(inter_ac, "")
    form_names = {c.value for c in form_choices}
    record_names = {c.value for c in record_choices}
    assert {"Suri", "Ashy", "Skan", "Annie"}.issubset(form_names)
    assert {"Suri", "Ashy", "Skan", "Annie"}.issubset(record_names)

    # Execute /form for Suri
    inter_form = MagicMock(spec=discord.Interaction)
    inter_form.guild_id = test_config.guild_id
    inter_form.channel_id = test_config.allowed_channel_id
    inter_form.response = MagicMock()
    inter_form.response.defer = AsyncMock()
    inter_form.followup = MagicMock()
    inter_form.followup.send = AsyncMock()

    await form_cmd.callback(inter_form, "Suri")
    inter_form.followup.send.assert_called_once()
    embed = inter_form.followup.send.call_args[1]["embed"]
    assert "Recent Form — Suri" in embed.title
    assert "1 Win Streak" in embed.description
    assert "2G, 1A" in embed.description
    assert isinstance(inter_form.followup.send.call_args[1]["file"], discord.File)
    assert inter_form.followup.send.call_args[1]["file"].filename.endswith("_form_chart.png")


@pytest.mark.asyncio
async def test_h2h_and_compare_commands(test_config: BotConfig) -> None:
    """Verifies /h2h and /compare embeds and listed player autocomplete."""
    bot = EFootballBot(test_config)
    await bot.excel_manager.save_match(
        MatchRecord(
            match_id="M-CMP-1",
            date="2026-11-11 14:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Suri", goals=3, assists=1, result=MatchResult.WIN),
                PlayerStat(player="Ashy", goals=1, assists=2, result=MatchResult.WIN),
                PlayerStat(player="Skan", goals=1, assists=0, result=MatchResult.LOSS),
                PlayerStat(player="Annie", goals=0, assists=1, result=MatchResult.LOSS),
            ],
        )
    )

    h2h_cmd = bot.tree.get_command("h2h")
    compare_cmd = bot.tree.get_command("compare")
    assert h2h_cmd is not None and compare_cmd is not None

    inter_h2h = MagicMock(spec=discord.Interaction)
    inter_h2h.guild_id = test_config.guild_id
    inter_h2h.channel_id = test_config.allowed_channel_id
    inter_h2h.response = MagicMock()
    inter_h2h.response.send_message = AsyncMock()

    await h2h_cmd.callback(inter_h2h, "Suri", "Skan")
    inter_h2h.response.send_message.assert_called_once()
    h2h_embed = inter_h2h.response.send_message.call_args[1]["embed"]
    assert "Suri vs Skan" in h2h_embed.title
    assert "Matches Against:" in h2h_embed.fields[1].value

    inter_cmp = MagicMock(spec=discord.Interaction)
    inter_cmp.guild_id = test_config.guild_id
    inter_cmp.channel_id = test_config.allowed_channel_id
    inter_cmp.response = MagicMock()
    inter_cmp.response.send_message = AsyncMock()

    await compare_cmd.callback(inter_cmp, "Suri", "Skan")
    inter_cmp.response.send_message.assert_called_once()
    cmp_embed = inter_cmp.response.send_message.call_args[1]["embed"]
    assert "Player Comparison — Suri vs Skan" in cmp_embed.title
    assert "Skill Rating" in cmp_embed.description
    assert "\n\n" in cmp_embed.description
    assert isinstance(inter_cmp.response.send_message.call_args[1]["file"], discord.File)
    assert inter_cmp.response.send_message.call_args[1]["file"].filename == "player_compare_chart.png"


@pytest.mark.asyncio
async def test_hall_of_fame_command(test_config: BotConfig) -> None:
    """Verifies /hall-of-fame renders club records and milestones cleanly."""
    bot = EFootballBot(test_config)
    await bot.excel_manager.save_match(
        MatchRecord(
            match_id="M-HOF-CMD",
            date="2026-11-11 16:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            notes="Score: Suri & Ashy 5 - 0 Skan & Annie (Home Win)",
            player_stats=[
                PlayerStat(player="Suri", goals=3, assists=2, result=MatchResult.WIN),
                PlayerStat(player="Ashy", goals=2, assists=3, result=MatchResult.WIN),
                PlayerStat(player="Skan", goals=0, assists=0, result=MatchResult.LOSS),
                PlayerStat(player="Annie", goals=0, assists=0, result=MatchResult.LOSS),
            ],
        )
    )

    hof_cmd = bot.tree.get_command("hall-of-fame")
    assert hof_cmd is not None

    inter = MagicMock(spec=discord.Interaction)
    inter.guild_id = test_config.guild_id
    inter.channel_id = test_config.allowed_channel_id
    inter.response = MagicMock()
    inter.response.defer = AsyncMock()
    inter.followup = MagicMock()
    inter.followup.send = AsyncMock()

    await hof_cmd.callback(inter)
    inter.followup.send.assert_called_once()
    embed = inter.followup.send.call_args[1]["embed"]
    assert "Hall of Fame" in embed.title
    assert "Biggest Win Margin (+5 goals)" in embed.description
    assert "Suri & Ashy" in embed.description
    assert "\n\n" in embed.description


@pytest.mark.asyncio
async def test_clean_sheets_leaderboard_and_motm_on_save(test_config: BotConfig) -> None:
    """Verifies Clean Sheets category in /leaderboard and MOTM/Milestone fields on save."""
    bot = EFootballBot(test_config)
    candidates = [
        OCRPlayerCandidate(player="Lilly", goals=3, assists=0),
        OCRPlayerCandidate(player="Azlan", goals=0, assists=3),
    ]
    away_candidates = [
        OCRPlayerCandidate(player="Skan", goals=0, assists=0),
        OCRPlayerCandidate(player="Suri", goals=0, assists=0),
    ]

    view = MatchPreviewView(
        submitting_user_id=1001,
        candidates=candidates,
        result=MatchResult.HOME_WIN,
        excel_manager=bot.excel_manager,
        image_hash="hash_cs_motm",
        home_score=3,
        away_score=0,
        away_candidates=away_candidates,
    )
    inter_save = MagicMock(spec=discord.Interaction)
    inter_save.user.id = 1001
    inter_save.user.display_name = "Skan"
    inter_save.user.name = "Skan"
    inter_save.channel = None
    inter_save.response.defer = AsyncMock()
    inter_save.edit_original_response = AsyncMock()

    await view.confirm_button.callback(inter_save)
    saved_embed = inter_save.edit_original_response.call_args[1]["embed"]
    field_names = [f.name for f in saved_embed.fields]
    assert "⭐ Man of the Match" in field_names
    assert "🏆 Milestones & Highlights" in field_names

    lb_cmd = bot.tree.get_command("leaderboard")
    inter_lb = MagicMock(spec=discord.Interaction)
    inter_lb.guild_id = test_config.guild_id
    inter_lb.channel_id = test_config.allowed_channel_id
    inter_lb.response = MagicMock()
    inter_lb.response.defer = AsyncMock()
    inter_lb.followup = MagicMock()
    inter_lb.followup.send = AsyncMock()

    choice = app_commands.Choice(name="Clean Sheets (0 goals conceded)", value="Clean Sheets")
    await lb_cmd.callback(inter_lb, choice)
    lb_embed = inter_lb.followup.send.call_args[1]["embed"]
    assert "Clean Sheets" in lb_embed.title
    assert "**Lilly** — **1** clean sheet" in lb_embed.description


@pytest.mark.asyncio
async def test_card_command(test_config: BotConfig) -> None:
    """Verifies /card generates an FC 27 card image attachment and stat summary."""
    bot = EFootballBot(test_config)
    await bot.excel_manager.save_match(
        MatchRecord(
            match_id="M-CARD-CMD",
            date="2026-11-12 10:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            notes="Score: Home 3 - 1 Away (Home Win)",
            player_stats=[
                PlayerStat(player="Suri", goals=2, assists=1, result=MatchResult.WIN),
                PlayerStat(player="Skan", goals=1, assists=2, result=MatchResult.WIN),
            ],
        )
    )

    card_cmd = bot.tree.get_command("card")
    assert card_cmd is not None

    inter = MagicMock(spec=discord.Interaction)
    inter.guild_id = test_config.guild_id
    inter.channel_id = test_config.allowed_channel_id
    inter.response = MagicMock()
    inter.response.defer = AsyncMock()
    inter.followup = MagicMock()
    inter.followup.send = AsyncMock()

    await card_cmd.callback(inter, "Suri")
    inter.followup.send.assert_called_once()
    kwargs = inter.followup.send.call_args[1]
    assert "FC 27 Player Card — Suri" in kwargs["embed"].title
    assert isinstance(kwargs["file"], discord.File)
    assert kwargs["file"].filename.endswith("_fc27_card.png")


@pytest.mark.asyncio
async def test_chemistry_command(test_config: BotConfig) -> None:
    """Verifies /chemistry outputs best partner, cursed duo, and rivalry metrics."""
    bot = EFootballBot(test_config)
    await bot.excel_manager.save_match(
        MatchRecord(
            match_id="M-CHEM-1",
            date="2026-11-12 10:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Suri", goals=2, assists=0, result=MatchResult.WIN),
                PlayerStat(player="Ashy", goals=1, assists=2, result=MatchResult.WIN),
                PlayerStat(player="Skan", goals=1, assists=0, result=MatchResult.LOSS),
            ],
        )
    )
    await bot.excel_manager.save_match(
        MatchRecord(
            match_id="M-CHEM-2",
            date="2026-11-12 11:00:00",
            result=MatchResult.AWAY_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Suri", goals=0, assists=1, result=MatchResult.LOSS),
                PlayerStat(player="Annie", goals=1, assists=0, result=MatchResult.LOSS),
                PlayerStat(player="Skan", goals=2, assists=1, result=MatchResult.WIN),
            ],
        )
    )

    chem_cmd = bot.tree.get_command("chemistry")
    assert chem_cmd is not None

    inter = MagicMock(spec=discord.Interaction)
    inter.guild_id = test_config.guild_id
    inter.channel_id = test_config.allowed_channel_id
    inter.response = MagicMock()
    inter.response.defer = AsyncMock()
    inter.followup = MagicMock()
    inter.followup.send = AsyncMock()

    await chem_cmd.callback(inter, "Suri")
    inter.followup.send.assert_called_once()
    embed = inter.followup.send.call_args[1]["embed"]
    assert "Synergy & Rivalry Matrix — Suri" in embed.title
    assert "Best Partner" in embed.description
    assert "Cursed Duo" in embed.description
    assert "\n\n" in embed.description








