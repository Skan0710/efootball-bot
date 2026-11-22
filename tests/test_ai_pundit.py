"""Unit tests for the AI Pundit Desk commentary engine."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from ai_pundit import (
    PUNDIT_CACHE,
    PunditReview,
    _rule_based_fallback_review,
    build_pundit_embed,
    generate_pundit_review,
)
from bot import EFootballBot
from config import BotConfig
from excel_manager import ExcelManager
from models import MatchRecord, MatchResult, PlayerStat


@pytest.fixture
def sample_match() -> MatchRecord:
    return MatchRecord(
        match_id="M-TEST-PUNDIT-01",
        date="2026-11-17 12:00:00",
        result=MatchResult.HOME_WIN,
        submitted_by="AdminUser",
        notes="Score: Home 3 - 2 Away (Home Win)",
        player_stats=[
            PlayerStat(player="Lilly", goals=1, assists=2, result=MatchResult.HOME_WIN),
            PlayerStat(player="Annie", goals=2, assists=0, result=MatchResult.HOME_WIN),
            PlayerStat(player="Son", goals=0, assists=0, result=MatchResult.HOME_WIN),
            PlayerStat(player="Skan", goals=0, assists=0, result=MatchResult.AWAY_WIN),
            PlayerStat(player="Jonny", goals=1, assists=1, result=MatchResult.AWAY_WIN),
            PlayerStat(player="Azlan", goals=1, assists=0, result=MatchResult.AWAY_WIN),
        ],
    )


def test_rule_based_fallback_performance_assignment(sample_match: MatchRecord) -> None:
    """Verifies Drury praises best performer and Keane roasts 0G/0A player."""
    review = _rule_based_fallback_review(sample_match, "Home 3 - 2 Away")
    assert review.match_id == "M-TEST-PUNDIT-01"
    assert review.is_ai_generated is False
    # Drury should praise Lilly or Annie (best performers)
    assert "Lilly" in review.drury_commentary or "Annie" in review.drury_commentary
    # Keane should roast Skan (0G 0A on losing side)
    assert "Skan" in review.keane_roast
    assert "Zero goals, zero assists" in review.keane_roast
    # Locker room banter should mention controller/ping
    assert "controller" in review.locker_room_banter or "excuses" in review.locker_room_banter


def test_rule_based_fallback_draw_match() -> None:
    """Verifies draw match generates balanced commentary."""
    draw_match = MatchRecord(
        match_id="M-TEST-DRAW-01",
        date="2026-11-17 13:00:00",
        result=MatchResult.DRAW,
        submitted_by="Skan",
        player_stats=[
            PlayerStat(player="Lilly", goals=1, assists=0, result=MatchResult.DRAW),
            PlayerStat(player="Suri", goals=1, assists=0, result=MatchResult.DRAW),
        ],
    )
    review = _rule_based_fallback_review(draw_match, "Lilly 1 - 1 Suri")
    assert "stalemate" in review.drury_commentary.lower() or "draw" in review.drury_commentary.lower() or "Lilly" in review.drury_commentary


@pytest.mark.asyncio
async def test_generate_pundit_review_caching(sample_match: MatchRecord) -> None:
    """Verifies reviews are cached by match_id to eliminate redundant token usage."""
    PUNDIT_CACHE.clear()
    rev1 = await generate_pundit_review(sample_match, "Home 3 - 2 Away", api_key=None)
    assert sample_match.match_id in PUNDIT_CACHE

    # Second call should return exact cached instance
    rev2 = await generate_pundit_review(sample_match, "Home 3 - 2 Away", api_key=None)
    assert rev1 is rev2


@pytest.mark.asyncio
async def test_generate_pundit_review_mocked_ai(sample_match: MatchRecord) -> None:
    """Verifies AI response JSON parsing into PunditReview."""
    PUNDIT_CACHE.clear()
    fake_json = json.dumps({
        "drury": "Lilly was pure digital majesty in motion!",
        "keane": "Skan had zero impact. Completely out of his depth.",
        "locker_room": "Check Skan's wifi router, bro was lagging into next week.",
    })

    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = fake_json
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    mock_client.chat.completions.create.return_value = mock_resp

    with patch("ai_pundit.Groq", return_value=mock_client):
        review = await generate_pundit_review(
            sample_match,
            "Home 3 - 2 Away",
            api_key="fake_key",
        )

    assert review.is_ai_generated is True
    assert "pure digital majesty" in review.drury_commentary
    assert "zero impact" in review.keane_roast
    assert "wifi router" in review.locker_room_banter


def test_build_pundit_embed(sample_match: MatchRecord) -> None:
    """Verifies embed formatting and fields."""
    review = PunditReview(
        match_id=sample_match.match_id,
        scoreline="Home 3 - 2 Away",
        drury_commentary="Poetic Drury",
        keane_roast="Brutal Keane",
        locker_room_banter="Funny Locker",
        is_ai_generated=True,
    )
    embed = build_pundit_embed(review, sample_match)
    assert sample_match.match_id in embed.title
    assert len(embed.fields) == 3
    assert embed.fields[0].value == "Poetic Drury"
    assert embed.fields[1].value == "Brutal Keane"
    assert embed.fields[2].value == "Funny Locker"
    assert "AI Powered" in embed.footer.text


@pytest.mark.asyncio
async def test_pundit_slash_command(sample_match: MatchRecord, tmp_path: Path) -> None:
    """Verifies the /pundit slash command execution."""
    cfg = BotConfig(
        discord_token="fake_token",
        guild_id=1122334455,
        workbook_path=tmp_path / "test_wb.xlsx",
        allowed_channel_id=999888777,
    )
    em = ExcelManager(cfg.workbook_path)
    await em.save_match(sample_match)

    bot = EFootballBot(cfg)
    bot.excel_manager = em
    cmd_map = {cmd.name: cmd for cmd in bot.tree.get_commands()}
    pundit_cmd = cmd_map["pundit"]

    inter = MagicMock(spec=discord.Interaction)
    inter.guild_id = cfg.guild_id
    inter.channel_id = cfg.allowed_channel_id
    inter.response = MagicMock()
    inter.response.defer = AsyncMock()
    inter.followup = MagicMock()
    inter.followup.send = AsyncMock()

    # Call /pundit with default latest match
    await pundit_cmd.callback(inter, match_id=None)
    inter.response.defer.assert_called_once()
    inter.followup.send.assert_called_once()
    called_embed = inter.followup.send.call_args[1]["embed"]
    assert sample_match.match_id in called_embed.title
