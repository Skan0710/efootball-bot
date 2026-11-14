"""Tests for new commands: /squad, /totw, /duo-card, /rivalry, /predict, and FC 27 OVR ratings."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from PIL import Image

from bot import EFootballBot
from config import BotConfig
from models import MatchRecord, MatchResult, PlayerStat
from predictions_manager import PredictionsManager
from stats import (
    calculate_duo_card_stats,
    calculate_player_rivalry,
    calculate_squad_lineup,
    compute_player_card_ovr,
    generate_fair_teams,
)
from visuals import render_duo_card, render_squad_pitch


@pytest.fixture
def test_config(tmp_path: Path) -> BotConfig:
    return BotConfig(
        discord_token="fake_token",
        guild_id=1122334455,
        workbook_path=tmp_path / "test_wb.xlsx",
        max_image_mb=10.0,
        allowed_channel_id=99887766,
    )


def test_compute_player_card_ovr() -> None:
    """Verifies Bayesian sample-damped card OVR calculation."""
    # Top tier record
    top_rec = {"player": "Suri", "matches_played": 12, "win_rate": 83.3, "gapg": 3.0, "gpg": 2.0}
    ovr_top = compute_player_card_ovr(top_rec)
    assert 80 <= ovr_top <= 99

    # Unranked / 0 matches
    zero_rec = {"player": "Newbie", "matches_played": 0, "win_rate": 0.0, "gapg": 0.0, "gpg": 0.0}
    ovr_zero = compute_player_card_ovr(zero_rec)
    assert ovr_zero == 60


def test_calculate_player_rivalry() -> None:
    """Verifies head-to-head clash history and bragging rights calculation."""
    matches = [
        MatchRecord(
            match_id="M-RIV-1",
            date="2026-11-10 12:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Suri", goals=3, assists=1, result=MatchResult.WIN),
                PlayerStat(player="Ashy", goals=1, assists=1, result=MatchResult.WIN),
                PlayerStat(player="Skan", goals=1, assists=0, result=MatchResult.LOSS),
                PlayerStat(player="Annie", goals=0, assists=1, result=MatchResult.LOSS),
            ],
        ),
        MatchRecord(
            match_id="M-RIV-2",
            date="2026-11-10 13:00:00",
            result=MatchResult.AWAY_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Suri", goals=0, assists=0, result=MatchResult.LOSS),
                PlayerStat(player="Lilly", goals=1, assists=0, result=MatchResult.LOSS),
                PlayerStat(player="Skan", goals=2, assists=1, result=MatchResult.WIN),
                PlayerStat(player="Azlan", goals=1, assists=1, result=MatchResult.WIN),
            ],
        ),
        MatchRecord(
            match_id="M-RIV-3",
            date="2026-11-10 14:00:00",
            result=MatchResult.DRAW,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Suri", goals=2, assists=0, result=MatchResult.DRAW),
                PlayerStat(player="Ashy", goals=0, assists=1, result=MatchResult.DRAW),
                PlayerStat(player="Skan", goals=1, assists=1, result=MatchResult.DRAW),
                PlayerStat(player="Annie", goals=1, assists=0, result=MatchResult.DRAW),
            ],
        ),
    ]

    riv = calculate_player_rivalry("Suri", "Skan", matches)
    assert riv["total_clashes"] == 3
    assert riv["p1_wins"] == 1
    assert riv["p2_wins"] == 1
    assert riv["draws"] == 1
    assert riv["p1_goals"] == 5
    assert riv["p2_goals"] == 4
    assert riv["leader"] == "Tied"
    assert "Dead Heat" in riv["bragging_rights"]

    with pytest.raises(ValueError, match="Cannot calculate rivalry against the same player"):
        calculate_player_rivalry("Suri", "suri", matches)


def test_calculate_duo_card_and_render() -> None:
    """Verifies partnership stats and dynamic duo card image rendering."""
    records = [
        {"player": "Suri", "matches_played": 5, "win_rate": 80.0, "gapg": 2.5, "gpg": 1.5, "cleansheet": 1, "wins": 4},
        {"player": "Ashy", "matches_played": 5, "win_rate": 80.0, "gapg": 2.0, "gpg": 1.0, "cleansheet": 1, "wins": 4},
    ]
    matches = [
        MatchRecord(
            match_id="M-DUO-1",
            date="2026-11-11 12:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Suri", goals=2, assists=1, result=MatchResult.WIN),
                PlayerStat(player="Ashy", goals=1, assists=2, result=MatchResult.WIN),
                PlayerStat(player="Skan", goals=0, assists=0, result=MatchResult.LOSS),
                PlayerStat(player="Annie", goals=0, assists=0, result=MatchResult.LOSS),
            ],
        ),
        MatchRecord(
            match_id="M-DUO-2",
            date="2026-11-11 13:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Suri", goals=3, assists=0, result=MatchResult.WIN),
                PlayerStat(player="Ashy", goals=0, assists=2, result=MatchResult.WIN),
                PlayerStat(player="Skan", goals=1, assists=0, result=MatchResult.LOSS),
                PlayerStat(player="Annie", goals=0, assists=1, result=MatchResult.LOSS),
            ],
        ),
    ]

    duo = calculate_duo_card_stats("Suri", "Ashy", records, matches)
    assert duo["matches_together"] == 2
    assert duo["wins_together"] == 2
    assert duo["win_rate"] == 100.0
    assert duo["combined_goals"] == 6
    assert duo["combined_assists"] == 5
    assert duo["synergy_tier"] in ("GOLD", "DIAMOND")

    buf = render_duo_card(duo)
    assert isinstance(buf, io.BytesIO)
    img = Image.open(buf)
    assert img.size == (860, 480)


def test_calculate_squad_lineup_and_render() -> None:
    """Verifies squad formation positioning and tactical pitch image rendering."""
    records = [
        {"player": "Suri", "matches_played": 10, "win_rate": 80.0, "gapg": 3.0, "gpg": 2.0, "cleansheet": 2, "wins": 8},
        {"player": "Ashy", "matches_played": 10, "win_rate": 70.0, "gapg": 2.2, "gpg": 0.8, "cleansheet": 2, "wins": 7},
        {"player": "Skan", "matches_played": 10, "win_rate": 60.0, "gapg": 1.5, "gpg": 0.6, "cleansheet": 2, "wins": 6},
        {"player": "Annie", "matches_played": 10, "win_rate": 50.0, "gapg": 1.0, "gpg": 0.4, "cleansheet": 1, "wins": 5},
    ]
    matches = [
        MatchRecord(
            match_id="M-SQ-1",
            date="2026-11-11 15:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Suri", goals=2, assists=1, result=MatchResult.WIN),
                PlayerStat(player="Ashy", goals=1, assists=1, result=MatchResult.WIN),
            ],
        )
    ]

    lineup = calculate_squad_lineup(records, matches)
    assert lineup["squad_size"] == 4
    assert 60 <= lineup["avg_ovr"] <= 99
    assert len(lineup["attackers"]) + len(lineup["midfielders"]) + len(lineup["defenders"]) == 4

    pitch_buf = render_squad_pitch(lineup, title="TEST PITCH")
    assert isinstance(pitch_buf, io.BytesIO)
    p_img = Image.open(pitch_buf)
    assert p_img.size == (820, 620)


def test_predictions_manager_lifecycle(tmp_path: Path) -> None:
    """Verifies matchday prediction storage, resolution, and leaderboard scoring."""
    storage = tmp_path / "predictions.json"
    mgr = PredictionsManager(storage_path=storage)

    # 1. Set predictions
    p1 = mgr.set_prediction(user_id=101, user_name="UserA", outcome="home", score="3-1")
    p2 = mgr.set_prediction(user_id=102, user_name="UserB", outcome="home", score="2-0")
    p3 = mgr.set_prediction(user_id=103, user_name="UserC", outcome="away", score="1-2")

    assert p1["outcome"] == "Home Win"
    assert p1["score"] == "3-1"
    assert len(mgr.get_all_pending()) == 3

    # 2. Resolve match with Home Win 3-1
    # UserA: correct outcome (+1) + exact score (+3) = 4 pts
    # UserB: correct outcome (+1) = 1 pt
    # UserC: 0 pts
    awarded = mgr.resolve_predictions("M-PRED-1", MatchResult.HOME_WIN, home_score=3, away_score=1)
    assert len(awarded) == 2
    assert awarded[0]["user_name"] == "UserA"
    assert awarded[0]["points"] == 4
    assert awarded[0]["exact_score"] is True

    assert awarded[1]["user_name"] == "UserB"
    assert awarded[1]["points"] == 1
    assert awarded[1]["exact_score"] is False

    # 3. Check leaderboard
    lb = mgr.get_leaderboard()
    assert len(lb) == 3
    assert lb[0]["user_name"] == "UserA"
    assert lb[0]["points"] == 4
    assert lb[1]["user_name"] == "UserB"
    assert lb[1]["points"] == 1
    assert lb[2]["user_name"] == "UserC"
    assert lb[2]["points"] == 0

    # Pending should now be empty
    assert len(mgr.get_all_pending()) == 0


@pytest.mark.asyncio
async def test_new_slash_commands_execution(test_config: BotConfig) -> None:
    """Verifies /squad, /totw, /duo-card, /rivalry, /predict, and /predictions-leaderboard commands."""
    bot = EFootballBot(test_config)
    await bot.excel_manager.save_match(
        MatchRecord(
            match_id="M-SLASH-1",
            date="2026-11-12 18:00:00",
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

    # Test /squad
    squad_cmd = bot.tree.get_command("squad")
    assert squad_cmd is not None
    inter_squad = MagicMock(spec=discord.Interaction)
    inter_squad.guild_id = test_config.guild_id
    inter_squad.channel_id = test_config.allowed_channel_id
    inter_squad.response = MagicMock()
    inter_squad.response.defer = AsyncMock()
    inter_squad.followup = MagicMock()
    inter_squad.followup.send = AsyncMock()

    await squad_cmd.callback(inter_squad)
    inter_squad.followup.send.assert_called_once()
    assert "Co-op Squad Starting Lineup" in inter_squad.followup.send.call_args[1]["embed"].title

    # Test /duo-card
    duo_cmd = bot.tree.get_command("duo-card")
    assert duo_cmd is not None
    inter_duo = MagicMock(spec=discord.Interaction)
    inter_duo.guild_id = test_config.guild_id
    inter_duo.channel_id = test_config.allowed_channel_id
    inter_duo.response = MagicMock()
    inter_duo.response.defer = AsyncMock()
    inter_duo.followup = MagicMock()
    inter_duo.followup.send = AsyncMock()

    await duo_cmd.callback(inter_duo, "Suri", "Ashy")
    inter_duo.followup.send.assert_called_once()
    assert "Dynamic Duo" in inter_duo.followup.send.call_args[1]["embed"].title

    # Test /rivalry
    riv_cmd = bot.tree.get_command("rivalry")
    assert riv_cmd is not None
    inter_riv = MagicMock(spec=discord.Interaction)
    inter_riv.guild_id = test_config.guild_id
    inter_riv.channel_id = test_config.allowed_channel_id
    inter_riv.response = MagicMock()
    inter_riv.response.defer = AsyncMock()
    inter_riv.followup = MagicMock()
    inter_riv.followup.send = AsyncMock()

    await riv_cmd.callback(inter_riv, "Suri", "Skan")
    inter_riv.followup.send.assert_called_once()
    assert "Head-to-Head Rivalry" in inter_riv.followup.send.call_args[1]["embed"].title

    # Test /predict
    pred_cmd = bot.tree.get_command("predict")
    assert pred_cmd is not None
    inter_pred = MagicMock(spec=discord.Interaction)
    inter_pred.guild_id = test_config.guild_id
    inter_pred.channel_id = test_config.allowed_channel_id
    inter_pred.user = MagicMock()
    inter_pred.user.id = 555
    inter_pred.user.display_name = "TestBettor"
    inter_pred.response = MagicMock()
    inter_pred.response.send_message = AsyncMock()

    choice_mock = MagicMock()
    choice_mock.value = "Home Win"
    await pred_cmd.callback(inter_pred, outcome=choice_mock, score="2-1")
    inter_pred.response.send_message.assert_called_once()
    assert "Prediction Locked In!" in inter_pred.response.send_message.call_args[1]["embed"].title
