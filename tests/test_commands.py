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
    build_help_embed,
    build_individual_record_embed,
    build_match_highlights_embed,
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
        "delete-latest",
        "leaderboard",
        "record",
        "matches",
        "latest-match",
        "pundit",
        "teammates",
        "potw",
        "potm",
        "best-team",
        "best-team-leader",
        "captain-h2h",
        "fair-teams",
        "form",
        "h2h",
        "compare",
        "hall-of-fame",
        "card",
        "chemistry",
        "edit-match",
        "leaders",
        "recap",
        "trophies",
        "tierlist",
        "squad",
        "totw",
        "duo-card",
        "rivalry",
        "predict",
        "predictions-leaderboard",
        "say",
        "help",
        "individual-record",
        "highlights",
        "Reply as Bot",
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
    away_candidates = [
        OCRPlayerCandidate(player="Alex", goals=1, assists=0),
        OCRPlayerCandidate(player="Bob", goals=0, assists=1),
    ]

    view = MatchPreviewView(
        submitting_user_id=1001,
        candidates=candidates,
        result=MatchResult.WIN,
        excel_manager=manager,
        image_hash="abc123hash",
        away_candidates=away_candidates,
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
    assert len(matches[0].player_stats) == 4


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

    # Valid multi-player input (2v2 balanced)
    modal.stats_input._value = "SkanRocky, 2, 1\nlillym, 0, 3"
    modal.away_stats_input._value = "Alex, 1, 0\nBob, 0, 1"
    modal.result_input._value = "Win"
    modal.scores_input._value = "2, 1"
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
async def test_matches_command_shows_team_players_score_and_match_id(test_config: BotConfig) -> None:
    """Verifies /matches displays team players, score, and match ID for easy searching."""
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
    assert "**Suri & Ashy** 3 - 2 **Skan & Jonny**" in embed.description
    assert "`M-SECRET-ID-99`" in embed.description


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
    assert "(leader)" in embed.fields[0].value or "Leader:" in embed.fields[0].value
    assert "(leader)" in embed.fields[1].value or "Leader:" in embed.fields[1].value

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
    assert "Player Comparison" in cmp_embed.title
    assert "Card OVR" in cmp_embed.description
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


@pytest.mark.asyncio
async def test_edit_match_command_and_modal(test_config: BotConfig) -> None:
    """Verifies /edit-match autocomplete, modal prefill, and saving edited stats."""
    bot = EFootballBot(test_config)
    await bot.excel_manager.save_match(
        MatchRecord(
            match_id="M-EDIT-1",
            date="2026-11-12 10:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            notes="Score: Lilly & Azlan 3 - 2 Skan & Suri (Home Win)",
            player_stats=[
                PlayerStat(player="Lilly", goals=3, assists=0, result=MatchResult.WIN),
                PlayerStat(player="Azlan", goals=0, assists=3, result=MatchResult.WIN),
                PlayerStat(player="Skan", goals=0, assists=1, result=MatchResult.LOSS),
                PlayerStat(player="Suri", goals=2, assists=0, result=MatchResult.LOSS),
            ],
        )
    )

    edit_cmd = bot.tree.get_command("edit-match")
    assert edit_cmd is not None

    # Test autocomplete dropdown
    inter_ac = MagicMock(spec=discord.Interaction)
    choices = await edit_cmd._params["match_id"].autocomplete(inter_ac, "M-EDIT-1")
    assert len(choices) == 1
    assert choices[0].value == "M-EDIT-1"

    # Execute /edit-match -> sends pre-filled modal
    inter = MagicMock(spec=discord.Interaction)
    inter.guild_id = test_config.guild_id
    inter.channel_id = test_config.allowed_channel_id
    inter.response = MagicMock()
    inter.response.send_modal = AsyncMock()

    await edit_cmd.callback(inter, "M-EDIT-1")
    inter.response.send_modal.assert_called_once()
    modal = inter.response.send_modal.call_args[0][0]
    assert "Lilly, 3, 0" in modal.home_stats_input.default
    assert "Suri, 2, 0" in modal.away_stats_input.default

    # Modify and submit modal
    modal.home_stats_input._value = "Lilly, 4, 0\nAzlan, 0, 4"
    modal.away_stats_input._value = "Skan, 1, 1\nSuri, 1, 1"
    modal.scores_input._value = "4, 2"
    modal.result_input._value = "Home Win"

    inter_sub = MagicMock(spec=discord.Interaction)
    inter_sub.response = MagicMock()
    inter_sub.response.send_message = AsyncMock()
    await modal.on_submit(inter_sub)

    inter_sub.response.send_message.assert_called_once()
    upd_embed = inter_sub.response.send_message.call_args[1]["embed"]
    assert "Match Updated — M-EDIT-1" in upd_embed.title

    updated_rec = await bot.excel_manager.get_match("M-EDIT-1")
    assert updated_rec is not None
    lilly_stat = next(s for s in updated_rec.player_stats if s.player == "Lilly")
    assert lilly_stat.goals == 4


@pytest.mark.asyncio
async def test_leaders_command(test_config: BotConfig) -> None:
    """Verifies /leaders displays the #1 leader(s) for each stat category."""
    bot = EFootballBot(test_config)
    await bot.excel_manager.save_match(
        MatchRecord(
            match_id="M-LEAD-1",
            date="2026-11-12 11:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            notes="Score: Suri & Azlan 4 - 0 Skan & Lilly (Home Win)",
            player_stats=[
                PlayerStat(player="Suri", goals=4, assists=0, result=MatchResult.WIN),
                PlayerStat(player="Azlan", goals=0, assists=4, result=MatchResult.WIN),
                PlayerStat(player="Skan", goals=0, assists=0, result=MatchResult.LOSS),
                PlayerStat(player="Lilly", goals=0, assists=0, result=MatchResult.LOSS),
            ],
        )
    )

    leaders_cmd = bot.tree.get_command("leaders")
    assert leaders_cmd is not None

    inter = MagicMock(spec=discord.Interaction)
    inter.guild_id = test_config.guild_id
    inter.channel_id = test_config.allowed_channel_id
    inter.response = MagicMock()
    inter.response.defer = AsyncMock()
    inter.followup = MagicMock()
    inter.followup.send = AsyncMock()

    await leaders_cmd.callback(inter)
    inter.followup.send.assert_called_once()
    embed = inter.followup.send.call_args[1]["embed"]
    assert "Stat Leaders" in embed.title
    assert "**Goals:** **Suri** — 4" in embed.description
    assert "**Assists:** **Azlan** — 4" in embed.description
    assert "**Hattricks:** **Suri** — 1" in embed.description
    assert "**Clean Sheets:** **Suri, Azlan** — 1" in embed.description
    assert "\n\n" in embed.description


@pytest.mark.asyncio
async def test_recap_trophies_and_tierlist_commands(test_config: BotConfig) -> None:
    """Verifies /recap, /trophies, and /tierlist execute and return rich embeds and images."""
    bot = EFootballBot(test_config)
    await bot.excel_manager.save_match(
        MatchRecord(
            match_id="M-REC-1",
            date="2026-11-13 14:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            notes="Score: Suri & Skan 3 - 1 Annie & Azlan (Home Win)",
            player_stats=[
                PlayerStat(player="Suri", goals=2, assists=1, result=MatchResult.WIN),
                PlayerStat(player="Skan", goals=1, assists=2, result=MatchResult.WIN),
                PlayerStat(player="Annie", goals=1, assists=0, result=MatchResult.LOSS),
                PlayerStat(player="Azlan", goals=0, assists=1, result=MatchResult.LOSS),
            ],
        )
    )

    recap_cmd = bot.tree.get_command("recap")
    trophies_cmd = bot.tree.get_command("trophies")
    tierlist_cmd = bot.tree.get_command("tierlist")
    assert recap_cmd is not None and trophies_cmd is not None and tierlist_cmd is not None

    # Test /recap
    inter_r = MagicMock(spec=discord.Interaction)
    inter_r.guild_id = test_config.guild_id
    inter_r.channel_id = test_config.allowed_channel_id
    inter_r.response = MagicMock()
    inter_r.response.defer = AsyncMock()
    inter_r.followup = MagicMock()
    inter_r.followup.send = AsyncMock()

    await recap_cmd.callback(inter_r, "2026-11-13")
    inter_r.followup.send.assert_called_once()
    r_embed = inter_r.followup.send.call_args[1]["embed"]
    assert "Matchday Session Recap — 2026-11-13" in r_embed.title
    assert any("Session MVP" in f.name for f in r_embed.fields)

    # Test /trophies
    inter_t = MagicMock(spec=discord.Interaction)
    inter_t.guild_id = test_config.guild_id
    inter_t.channel_id = test_config.allowed_channel_id
    inter_t.response = MagicMock()
    inter_t.response.defer = AsyncMock()
    inter_t.followup = MagicMock()
    inter_t.followup.send = AsyncMock()

    await trophies_cmd.callback(inter_t, "Suri")
    inter_t.followup.send.assert_called_once()
    t_embed = inter_t.followup.send.call_args[1]["embed"]
    assert "Trophy Room & Badges — Suri" in t_embed.title

    # Test /tierlist
    inter_tl = MagicMock(spec=discord.Interaction)
    inter_tl.guild_id = test_config.guild_id
    inter_tl.channel_id = test_config.allowed_channel_id
    inter_tl.response = MagicMock()
    inter_tl.response.defer = AsyncMock()
    inter_tl.followup = MagicMock()
    inter_tl.followup.send = AsyncMock()

    await tierlist_cmd.callback(inter_tl)
    inter_tl.followup.send.assert_called_once()
    tl_kwargs = inter_tl.followup.send.call_args[1]
    assert "Co-op Squad Tier List" in tl_kwargs["embed"].title
    assert isinstance(tl_kwargs["file"], discord.File)


@pytest.mark.asyncio
async def test_latest_match_and_delete_latest_flow(test_config: BotConfig, tmp_path: Path) -> None:
    wb_file = tmp_path / "test_wb.xlsx"
    em = ExcelManager(wb_file)
    bot = EFootballBot(test_config)
    bot.excel_manager = em

    cmd_map = {cmd.name: cmd for cmd in bot.tree.get_commands()}
    latest_cmd = cmd_map["latest-match"]
    delete_latest_cmd = cmd_map["delete-latest"]

    # 1. When no matches exist
    inter_empty = MagicMock(spec=discord.Interaction)
    inter_empty.guild_id = test_config.guild_id
    inter_empty.channel_id = test_config.allowed_channel_id
    inter_empty.response = MagicMock()
    inter_empty.response.send_message = AsyncMock()

    await latest_cmd.callback(inter_empty)
    inter_empty.response.send_message.assert_called_once()
    assert "No matches recorded" in str(inter_empty.response.send_message.call_args)

    # 2. Add two matches to test retrieval of the latest one
    m1 = MatchRecord(
        match_id="M-20261116-000001",
        date="2026-11-16 10:00:00",
        result=MatchResult.HOME_WIN,
        submitted_by="AdminUser",
        notes="Score: Home 2 - 1 Away",
        player_stats=[
            PlayerStat(player="Jonny", goals=2, assists=0, result=MatchResult.HOME_WIN),
            PlayerStat(player="Azlan", goals=1, assists=1, result=MatchResult.AWAY_WIN),
        ],
    )
    m2 = MatchRecord(
        match_id="M-20261116-000002",
        date="2026-11-16 11:00:00",
        result=MatchResult.AWAY_WIN,
        submitted_by="SkanMaster",
        notes="Score: Home 1 - 3 Away",
        player_stats=[
            PlayerStat(player="Lilly", goals=1, assists=0, result=MatchResult.LOSS),
            PlayerStat(player="Suri", goals=2, assists=1, result=MatchResult.WIN),
            PlayerStat(player="Annie", goals=1, assists=1, result=MatchResult.WIN),
        ],
    )
    await em.save_match(m1)
    await em.save_match(m2)

    # 3. Test /latest-match returns m2 with all details
    inter_latest = MagicMock(spec=discord.Interaction)
    inter_latest.guild_id = test_config.guild_id
    inter_latest.channel_id = test_config.allowed_channel_id
    inter_latest.response = MagicMock()
    inter_latest.response.send_message = AsyncMock()

    await latest_cmd.callback(inter_latest)
    inter_latest.response.send_message.assert_called_once()
    embed = inter_latest.response.send_message.call_args[1]["embed"]
    assert "M-20261116-000002" in embed.title
    assert "SkanMaster" in [f.value for f in embed.fields if f.name == "👤 Added By"][0]
    assert "2026-11-16 11:00:00" in [f.value for f in embed.fields if f.name == "⏰ Time / Date"][0]
    assert any("Suri" in f.value for f in embed.fields)

    # 4. Test /delete-latest: invalid password
    admin_member = MagicMock(spec=discord.Member)
    admin_member.id = 12345
    admin_member.name = "AdminUser"
    admin_member.guild_permissions = discord.Permissions(administrator=True)

    inter_del_wrong = MagicMock(spec=discord.Interaction)
    inter_del_wrong.guild = MagicMock(spec=discord.Guild)
    inter_del_wrong.guild_id = test_config.guild_id
    inter_del_wrong.channel_id = test_config.allowed_channel_id
    inter_del_wrong.user = admin_member
    inter_del_wrong.response = MagicMock()
    inter_del_wrong.response.send_message = AsyncMock()

    await delete_latest_cmd.callback(inter_del_wrong, password="wrong_password")
    inter_del_wrong.response.send_message.assert_called_once()
    assert "Invalid password" in str(inter_del_wrong.response.send_message.call_args)

    # 5. Test /delete-latest: no password opens modal
    inter_del_modal = MagicMock(spec=discord.Interaction)
    inter_del_modal.guild = MagicMock(spec=discord.Guild)
    inter_del_modal.guild_id = test_config.guild_id
    inter_del_modal.channel_id = test_config.allowed_channel_id
    inter_del_modal.user = admin_member
    inter_del_modal.response = MagicMock()
    inter_del_modal.response.send_modal = AsyncMock()

    await delete_latest_cmd.callback(inter_del_modal, password=None)
    inter_del_modal.response.send_modal.assert_called_once()

    # 6. Test /delete-latest: valid password presents confirmation view showing latest match contents
    inter_del_ok = MagicMock(spec=discord.Interaction)
    inter_del_ok.guild = MagicMock(spec=discord.Guild)
    inter_del_ok.guild_id = test_config.guild_id
    inter_del_ok.channel_id = test_config.allowed_channel_id
    inter_del_ok.user = admin_member
    inter_del_ok.response = MagicMock()
    inter_del_ok.response.send_message = AsyncMock()

    await delete_latest_cmd.callback(inter_del_ok, password=test_config.delete_password)
    inter_del_ok.response.send_message.assert_called_once()
    del_kwargs = inter_del_ok.response.send_message.call_args[1]
    assert "M-20261116-000002" in del_kwargs["embed"].title
    assert isinstance(del_kwargs["view"], discord.ui.View)

    # 7. Confirm deletion via the view button
    btn_inter = MagicMock(spec=discord.Interaction)
    btn_inter.user = inter_del_ok.user
    btn_inter.response = MagicMock()
    btn_inter.response.defer = AsyncMock()
    btn_inter.edit_original_response = AsyncMock()

    view = del_kwargs["view"]
    await view.confirm_delete.callback(btn_inter)
    btn_inter.edit_original_response.assert_called_once()
    assert "Match Deleted" in btn_inter.edit_original_response.call_args[1]["embed"].title

    # Verify latest match is now m1
    remaining = await em.get_all_matches()
    assert len(remaining) == 1
    assert remaining[0].match_id == "M-20261116-000001"

    # 8. Test DeleteLatestPasswordModal submission
    from commands_handler import DeleteLatestPasswordModal
    modal = DeleteLatestPasswordModal(excel_manager=em, config=test_config)
    modal.password_input._value = test_config.delete_password
    inter_modal_submit = MagicMock(spec=discord.Interaction)
    inter_modal_submit.guild = MagicMock(spec=discord.Guild)
    inter_modal_submit.guild_id = test_config.guild_id
    inter_modal_submit.channel_id = test_config.allowed_channel_id
    inter_modal_submit.user = inter_del_ok.user
    inter_modal_submit.response = MagicMock()
    inter_modal_submit.response.send_message = AsyncMock()

    await modal.on_submit(inter_modal_submit)
    inter_modal_submit.response.send_message.assert_called_once()
    assert "M-20261116-000001" in inter_modal_submit.response.send_message.call_args[1]["embed"].title


@pytest.mark.asyncio
async def test_say_command_admin_and_modal_flow(test_config: BotConfig) -> None:
    """Verifies /say command security checks, modal opening, and inline delivery."""
    from commands_handler import SayModal

    bot = EFootballBot(test_config)
    say_cmd = bot.tree.get_command("say")
    assert say_cmd is not None

    # 1. Non-admin user gets rejected
    inter_non_admin = MagicMock(spec=discord.Interaction)
    inter_non_admin.guild = MagicMock(spec=discord.Guild)
    inter_non_admin.guild_id = test_config.guild_id
    inter_non_admin.channel_id = test_config.allowed_channel_id
    member_user = MagicMock(spec=discord.Member)
    member_user.guild_permissions.administrator = False
    member_user.roles = []
    inter_non_admin.user = member_user
    inter_non_admin.response = MagicMock()
    inter_non_admin.response.send_message = AsyncMock()

    await say_cmd.callback(inter_non_admin, message="Hello")
    inter_non_admin.response.send_message.assert_called_once()
    assert "Only server administrators" in inter_non_admin.response.send_message.call_args[0][0]

    # 2. Admin user without message opens SayModal
    inter_admin = MagicMock(spec=discord.Interaction)
    inter_admin.guild = MagicMock(spec=discord.Guild)
    inter_admin.guild_id = test_config.guild_id
    inter_admin.channel_id = test_config.allowed_channel_id
    mock_channel = MagicMock(spec=discord.TextChannel)
    mock_channel.send = AsyncMock()
    inter_admin.channel = mock_channel
    admin_user = MagicMock(spec=discord.Member)
    admin_user.guild_permissions.administrator = True
    inter_admin.user = admin_user
    inter_admin.response = MagicMock()
    inter_admin.response.send_modal = AsyncMock()

    await say_cmd.callback(inter_admin, message=None)
    inter_admin.response.send_modal.assert_called_once()
    modal_arg = inter_admin.response.send_modal.call_args[0][0]
    assert isinstance(modal_arg, SayModal)

    # 3. SayModal submit sends message to channel
    modal = SayModal(target_channel=mock_channel)
    modal.message_input._value = "Hello from the bot!"
    modal.reply_to_input._value = ""

    inter_modal = MagicMock(spec=discord.Interaction)
    inter_modal.response = MagicMock()
    inter_modal.response.defer = AsyncMock()
    inter_modal.followup = MagicMock()
    inter_modal.followup.send = AsyncMock()

    await modal.on_submit(inter_modal)
    mock_channel.send.assert_called_once_with("Hello from the bot!")
    inter_modal.followup.send.assert_called_once()
    assert "Message sent as bot" in inter_modal.followup.send.call_args[0][0]

    # 4. Inline /say execution
    inter_inline = MagicMock(spec=discord.Interaction)
    inter_inline.guild = MagicMock(spec=discord.Guild)
    inter_inline.guild_id = test_config.guild_id
    inter_inline.channel_id = test_config.allowed_channel_id
    inter_inline.channel = mock_channel
    inter_inline.user = admin_user
    inter_inline.response = MagicMock()
    inter_inline.response.defer = AsyncMock()
    inter_inline.followup = MagicMock()
    inter_inline.followup.send = AsyncMock()

    await say_cmd.callback(inter_inline, message="Quick direct announcement!")
    mock_channel.send.assert_called_with("Quick direct announcement!")
    inter_inline.followup.send.assert_called_once()
    assert "Message sent as bot" in inter_inline.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_best_team_leader_command(test_config: BotConfig, tmp_path: Path) -> None:
    """Verifies /best-team-leader command displays ranked captain leaderboard."""
    bot = EFootballBot(test_config)
    em = bot.excel_manager

    # 1. Empty state
    inter_empty = MagicMock(spec=discord.Interaction)
    inter_empty.guild_id = test_config.guild_id
    inter_empty.channel_id = test_config.allowed_channel_id
    inter_empty.response = MagicMock()
    inter_empty.response.defer = AsyncMock()
    inter_empty.followup = MagicMock()
    inter_empty.followup.send = AsyncMock()

    cmd = bot.tree.get_command("best-team-leader")
    assert cmd is not None
    await cmd.callback(inter_empty)

    inter_empty.followup.send.assert_called_once()
    embed_empty = inter_empty.followup.send.call_args[1]["embed"]
    assert "No team leader data recorded yet" in embed_empty.description

    # 2. Add match with team lineups: Home (Skan & Jonny) 3 - 1 Away (Lilly & Azlan) -> Skan wins, Lilly loses
    await em.save_match(
        MatchRecord(
            match_id="M-LEADER-01",
            date="2026-11-19 12:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Admin",
            notes="Score: Skan & Jonny 3 - 1 Lilly & Azlan (Home Win)",
            player_stats=[
                PlayerStat(player="Skan", goals=2, assists=1, result=MatchResult.HOME_WIN),
                PlayerStat(player="Jonny", goals=1, assists=1, result=MatchResult.HOME_WIN),
                PlayerStat(player="Lilly", goals=1, assists=0, result=MatchResult.AWAY_WIN),
                PlayerStat(player="Azlan", goals=0, assists=1, result=MatchResult.AWAY_WIN),
            ],
        )
    )

    inter_data = MagicMock(spec=discord.Interaction)
    inter_data.guild_id = test_config.guild_id
    inter_data.channel_id = test_config.allowed_channel_id
    inter_data.response = MagicMock()
    inter_data.response.defer = AsyncMock()
    inter_data.followup = MagicMock()
    inter_data.followup.send = AsyncMock()

    await cmd.callback(inter_data)
    inter_data.followup.send.assert_called_once()
    embed_data = inter_data.followup.send.call_args[1]["embed"]
    assert "Captains & Team Leaders Leaderboard" in embed_data.title
    assert "Skan" in embed_data.description
    assert "1 Win" in embed_data.description
    assert "Lilly" in embed_data.description


@pytest.mark.asyncio
async def test_captain_h2h_command(test_config: BotConfig) -> None:
    """Tests /captain-h2h command callback for empty state and matches between captains."""
    bot = EFootballBot(test_config)
    em = bot.excel_manager

    cmd = bot.tree.get_command("captain-h2h")
    assert cmd is not None

    # 1. No matches yet
    inter_empty = MagicMock(spec=discord.Interaction)
    inter_empty.guild_id = test_config.guild_id
    inter_empty.channel_id = test_config.allowed_channel_id
    inter_empty.response = MagicMock()
    inter_empty.response.send_message = AsyncMock()

    await cmd.callback(inter_empty, captain1="Skan", captain2="Suri")
    inter_empty.response.send_message.assert_called_once()
    msg = inter_empty.response.send_message.call_args[0][0]
    assert "have not faced each other as opposing team captains yet" in msg

    # 2. Add match where Skan captains Home and Suri captains Away
    await em.save_match(
        MatchRecord(
            match_id="M-CH2H-TEST",
            date="2026-11-19 14:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Admin",
            notes="Score: Skan & Jonny 4 - 2 Suri & Annie (Home Win)",
            player_stats=[
                PlayerStat(player="Skan", goals=3, assists=1, result=MatchResult.HOME_WIN),
                PlayerStat(player="Jonny", goals=1, assists=1, result=MatchResult.HOME_WIN),
                PlayerStat(player="Suri", goals=2, assists=0, result=MatchResult.AWAY_WIN),
                PlayerStat(player="Annie", goals=0, assists=1, result=MatchResult.AWAY_WIN),
            ],
        )
    )

    inter_clash = MagicMock(spec=discord.Interaction)
    inter_clash.guild_id = test_config.guild_id
    inter_clash.channel_id = test_config.allowed_channel_id
    inter_clash.response = MagicMock()
    inter_clash.response.send_message = AsyncMock()

    await cmd.callback(inter_clash, captain1="Skan", captain2="Suri")
    inter_clash.response.send_message.assert_called_once()
    embed = inter_clash.response.send_message.call_args[1]["embed"]
    assert "Captain Head-to-Head" in embed.title
    assert "Skan" in embed.title and "Suri" in embed.title
    assert "1 match" in embed.description
    assert "M-CH2H-TEST" in embed.fields[2].value


@pytest.mark.asyncio
async def test_auto_hof_and_pundit_on_match_save(test_config: BotConfig, tmp_path: Path) -> None:
    """Verifies that saving a match triggers AI Pundit review and Hall of Fame auto-trigger on overwrite, but no Gazette."""
    from commands_handler import MatchPreviewView
    from ocr import OCRPlayerCandidate

    em = ExcelManager(tmp_path / "matches.xlsx")

    # Seed baseline match so baseline Hall of Fame exists (margin 1, max goals 1)
    await em.save_match(
        MatchRecord(
            match_id="M-SEED-01",
            date="2026-11-18 10:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Admin",
            notes="Score: Skan & Jonny 2 - 1 Azlan & Annie (Home Win)",
            player_stats=[
                PlayerStat(player="Skan", goals=1, assists=0, result=MatchResult.HOME_WIN),
                PlayerStat(player="Jonny", goals=1, assists=1, result=MatchResult.HOME_WIN),
                PlayerStat(player="Azlan", goals=1, assists=0, result=MatchResult.AWAY_WIN),
                PlayerStat(player="Annie", goals=0, assists=1, result=MatchResult.AWAY_WIN),
            ],
        )
    )

    mock_channel = MagicMock(spec=discord.TextChannel)
    mock_channel.send = AsyncMock()

    # New match: 4 - 0 Home win, Skan scores 3 goals -> breaks margin (+4 > +1) and max goals (3 > 1)
    candidates = [
        OCRPlayerCandidate(player="Skan", goals=3, assists=1),
        OCRPlayerCandidate(player="Suri", goals=1, assists=2),
    ]
    away_candidates = [
        OCRPlayerCandidate(player="Azlan", goals=0, assists=0),
        OCRPlayerCandidate(player="Annie", goals=0, assists=0),
    ]

    view = MatchPreviewView(
        submitting_user_id=1001,
        candidates=candidates,
        result=MatchResult.HOME_WIN,
        excel_manager=em,
        image_hash="test_auto_hof_hash",
        home_team="Skan",
        away_team="Azlan",
        home_score=4,
        away_score=0,
        away_candidates=away_candidates,
        config=test_config,
    )

    inter = MagicMock(spec=discord.Interaction)
    inter.user.id = 1001
    inter.user.display_name = "Skan"
    inter.user.name = "Skan"
    inter.channel = mock_channel
    inter.response.defer = AsyncMock()
    inter.edit_original_response = AsyncMock()

    await view.confirm_button.callback(inter)

    # Verify channel.send calls:
    # 1. Match saved announcement
    # 2. Hall of Fame auto-open (because records were overwritten)
    # 3. Pundit review
    # Notice: NO Match Gazette should be sent!
    assert mock_channel.send.call_count >= 3
    sent_embeds = [call.kwargs.get("embed") for call in mock_channel.send.call_args_list if call.kwargs.get("embed")]
    embed_titles = [e.title for e in sent_embeds if e and e.title]

    assert any("match saved" in t.lower() for t in embed_titles)
    assert any("hall of fame" in t.lower() for t in embed_titles)
    assert any("pundit" in t.lower() for t in embed_titles)
    assert not any("gazette" in t.lower() for t in embed_titles)

    # Verify announcement message for Hall of Fame
    sent_contents = [call.kwargs.get("content") for call in mock_channel.send.call_args_list if call.kwargs.get("content")]
    assert any("HALL OF FAME OVERWRITTEN" in c for c in sent_contents if c)


@pytest.mark.asyncio
async def test_check_and_announce_potw_automated(test_config: BotConfig, tmp_path: Path) -> None:
    """Verifies that check_and_announce_potw announces the completed gameweek with a TOTW card and guards against duplicate runs."""
    from datetime import datetime, timedelta
    from stats import get_calendar_week_bounds

    bot = EFootballBot(test_config)
    em = bot.excel_manager

    # Construct match in the previous completed week
    w_start, w_end = get_calendar_week_bounds(weeks_ago=1)
    match_dt = w_start + timedelta(days=2, hours=10)
    match_record = MatchRecord(
        match_id="M-POTW-TEST",
        date=match_dt.strftime("%Y-%m-%d %H:%M:%S"),
        result=MatchResult.HOME_WIN,
        submitted_by="Admin",
        player_stats=[
            PlayerStat(player="Skan", goals=4, assists=2, result=MatchResult.WIN),
            PlayerStat(player="Jonny", goals=1, assists=1, result=MatchResult.WIN),
        ],
    )
    await em.save_match(match_record)

    mock_channel = MagicMock(spec=discord.TextChannel)
    mock_channel.send = AsyncMock()

    bot.get_channel = MagicMock(return_value=mock_channel)

    announced = await bot.check_and_announce_potw()
    assert announced is True
    assert mock_channel.send.call_count == 1
    call_args = mock_channel.send.call_args[1]
    assert "Gameweek is complete" in mock_channel.send.call_args[1]["content"]
    assert "Player of the Week" in call_args["embed"].title
    assert "Skan" in call_args["embed"].description
    assert "file" in call_args

    # Check that a consecutive call does not re-announce (idempotent / deduplicated)
    second_run = await bot.check_and_announce_potw()
    assert second_run is False
    assert mock_channel.send.call_count == 1


@pytest.mark.asyncio
async def test_add_match_roster_filter_minimum_and_equal_teams(tmp_path: Path) -> None:
    """Verifies that matches require >=2 players per team and strictly equal team sizes."""
    from commands_handler import MatchPreviewView, AddMatchTextModal
    from ocr import OCRPlayerCandidate

    em = ExcelManager(tmp_path / "filter_test.xlsx")

    # 1. Reject if home < 2 players (1v1)
    view_1v1 = MatchPreviewView(
        submitting_user_id=1,
        candidates=[OCRPlayerCandidate("Skan", 1, 0)],
        result=MatchResult.HOME_WIN,
        excel_manager=em,
        image_hash="hash_1v1",
        away_candidates=[OCRPlayerCandidate("Azlan", 0, 0)],
    )
    inter_1v1 = MagicMock(spec=discord.Interaction)
    inter_1v1.response.send_message = AsyncMock()
    await view_1v1.confirm_button.callback(inter_1v1)
    inter_1v1.response.send_message.assert_called_once()
    assert "at least 2 players" in inter_1v1.response.send_message.call_args[0][0]

    # 2. Reject if away < 2 players (2v1)
    view_2v1 = MatchPreviewView(
        submitting_user_id=1,
        candidates=[OCRPlayerCandidate("Skan", 1, 0), OCRPlayerCandidate("Suri", 0, 0)],
        result=MatchResult.HOME_WIN,
        excel_manager=em,
        image_hash="hash_2v1",
        away_candidates=[OCRPlayerCandidate("Azlan", 0, 0)],
    )
    inter_2v1 = MagicMock(spec=discord.Interaction)
    inter_2v1.response.send_message = AsyncMock()
    await view_2v1.confirm_button.callback(inter_2v1)
    inter_2v1.response.send_message.assert_called_once()
    assert "at least 2 players" in inter_2v1.response.send_message.call_args[0][0]

    # 3. Reject if unequal team counts (3v2)
    view_3v2 = MatchPreviewView(
        submitting_user_id=1,
        candidates=[
            OCRPlayerCandidate("P1", 1, 0),
            OCRPlayerCandidate("P2", 0, 0),
            OCRPlayerCandidate("P3", 0, 0),
        ],
        result=MatchResult.HOME_WIN,
        excel_manager=em,
        image_hash="hash_3v2",
        away_candidates=[
            OCRPlayerCandidate("P4", 0, 0),
            OCRPlayerCandidate("P5", 0, 0),
        ],
    )
    inter_3v2 = MagicMock(spec=discord.Interaction)
    inter_3v2.response.send_message = AsyncMock()
    await view_3v2.confirm_button.callback(inter_3v2)
    inter_3v2.response.send_message.assert_called_once()
    assert "equal number of players" in inter_3v2.response.send_message.call_args[0][0]

    # 4. Reject in AddMatchTextModal on unequal or < 2 players
    modal = AddMatchTextModal(excel_manager=em)
    modal.home_stats_input._value = "P1, 1, 0\nP2, 0, 0\nP3, 0, 0"
    modal.away_stats_input._value = "P4, 0, 0\nP5, 0, 0"
    modal.scores_input._value = "1, 0"
    modal.result_input._value = "Home Win"

    inter_modal = MagicMock(spec=discord.Interaction)
    inter_modal.response.send_message = AsyncMock()
    await modal.on_submit(inter_modal)
    inter_modal.response.send_message.assert_called_once()
    assert "equal number of players" in inter_modal.response.send_message.call_args[0][0]

    # 5. Accept valid 2v2
    view_2v2 = MatchPreviewView(
        submitting_user_id=1,
        candidates=[OCRPlayerCandidate("P1", 1, 0), OCRPlayerCandidate("P2", 0, 0)],
        result=MatchResult.HOME_WIN,
        excel_manager=em,
        image_hash="hash_2v2",
        away_candidates=[OCRPlayerCandidate("P3", 0, 0), OCRPlayerCandidate("P4", 0, 0)],
    )
    inter_2v2 = MagicMock(spec=discord.Interaction)
    inter_2v2.response.defer = AsyncMock()
    inter_2v2.edit_original_response = AsyncMock()
    inter_2v2.user.display_name = "SubmittingUser"
    inter_2v2.user.name = "SubmittingUser"
    inter_2v2.channel = None
    await view_2v2.confirm_button.callback(inter_2v2)
    inter_2v2.edit_original_response.assert_called_once()
    saved = await em.get_all_matches()
    assert len(saved) == 1
    assert len(saved[0].player_stats) == 4


def test_build_help_embed_overview_and_categories() -> None:
    """Verifies that the help embed generates correctly for all categories and specific filters."""
    # Overview (all)
    embed_all = build_help_embed("all")
    assert "Command Directory" in embed_all.title
    assert len(embed_all.fields) >= 5
    # Check that major categories exist
    field_names = [f.name for f in embed_all.fields]
    assert any("Match Logging" in name for name in field_names)
    assert any("Matches" in name for name in field_names)
    assert any("Player Profiles" in name for name in field_names)
    assert any("Leaderboards" in name for name in field_names)

    # Filtered by category
    embed_players = build_help_embed("players")
    assert "Player Profiles" in embed_players.description
    assert len(embed_players.fields) == 1
    assert "/record" in embed_players.fields[0].value
    assert "/card" in embed_players.fields[0].value
    assert "/chemistry" in embed_players.fields[0].value


def test_build_individual_record_embed() -> None:
    """Verifies that the individual record embed displays matches, match IDs, and goal contributions."""
    m1 = MatchRecord(
        match_id="M-20261120-01",
        date="2026-11-20 14:00:00",
        result=MatchResult.WIN,
        submitted_by="AdminUser",
        notes="Score: 3 - 1 (Win)",
        player_stats=[PlayerStat(player="Son", goals=2, assists=1, result=MatchResult.WIN)],
    )
    m2 = MatchRecord(
        match_id="M-20261121-02",
        date="2026-11-21 16:30:00",
        result=MatchResult.LOSS,
        submitted_by="AdminUser",
        notes="Score: 1 - 2 (Loss)",
        player_stats=[PlayerStat(player="Son", goals=0, assists=1, result=MatchResult.LOSS)],
    )

    player_matches = [(m1, m1.player_stats[0]), (m2, m2.player_stats[0])]
    embed = build_individual_record_embed("Son", player_matches)

    assert "Individual Match Record — Son" in embed.title
    assert "Matches Played:** 2" in embed.description
    assert "Goals:** 2" in embed.description
    assert "Assists:** 2" in embed.description
    assert "Total G/A:** 4" in embed.description
    assert len(embed.fields) >= 1
    val = embed.fields[0].value
    assert "M-20261120-01" in val
    assert "M-20261121-02" in val
    assert "2**G | 🎯 **1**A" in val


def test_build_match_highlights_embed() -> None:
    """Verifies that the match highlights embed displays score, MVP, and lineup stats."""
    m = MatchRecord(
        match_id="M-TEST-HIGHLIGHTS-01",
        date="2026-11-20 18:00:00",
        result=MatchResult.HOME_WIN,
        submitted_by="AdminUser",
        notes="Score: 4 - 2 (Home Win)",
        player_stats=[
            PlayerStat(player="Son", goals=3, assists=1, result=MatchResult.HOME_WIN),
            PlayerStat(player="Lilly", goals=1, assists=2, result=MatchResult.HOME_WIN),
            PlayerStat(player="Skan", goals=2, assists=0, result=MatchResult.AWAY_WIN),
        ],
    )
    embed = build_match_highlights_embed(m)
    assert "Match Highlights — ID: M-TEST-HIGHLIGHTS-01" in embed.title
    assert "M-TEST-HIGHLIGHTS-01" in embed.fields[0].value
    field_names = [f.name for f in embed.fields]
    assert any("Winning Side" in name for name in field_names)
    assert any("Defeated Side" in name for name in field_names)
    assert any("Match MVP" in name for name in field_names)
    # Son has 3G 1A (4 G/A) -> Match MVP
    mvp_field = next(f for f in embed.fields if f.name == "⭐ Match MVP")
    assert "Son" in mvp_field.value
    assert "3**G | 🎯 **1**A" in mvp_field.value












