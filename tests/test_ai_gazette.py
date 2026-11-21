"""Unit tests for The Co-op Gazette weekly sports newspaper engine."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from ai_gazette import (
    GAZETTE_CACHE,
    GazetteEdition,
    _filter_matches_for_week,
    _rule_based_fallback_edition,
    _rule_based_fallback_match_gazette,
    _summarize_match_week,
    build_gazette_embed,
    generate_gazette_edition,
    generate_match_gazette,
)
from bot import EFootballBot
from config import BotConfig
from excel_manager import ExcelManager
from models import MatchRecord, MatchResult, PlayerStat


@pytest.fixture
def sample_week_matches() -> list[MatchRecord]:
    return [
        MatchRecord(
            match_id="M-W1-01",
            date="2026-11-16 10:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            notes="Score: Home 3 - 2 Away",
            player_stats=[
                PlayerStat(player="Lilly", goals=1, assists=2, result=MatchResult.HOME_WIN),
                PlayerStat(player="Annie", goals=2, assists=0, result=MatchResult.HOME_WIN),
                PlayerStat(player="Suri", goals=2, assists=1, result=MatchResult.AWAY_WIN),
            ],
        ),
        MatchRecord(
            match_id="M-W1-02",
            date="2026-11-16 12:00:00",
            result=MatchResult.AWAY_WIN,
            submitted_by="Jonny",
            notes="Score: Home 1 - 4 Away",
            player_stats=[
                PlayerStat(player="Suri", goals=3, assists=1, result=MatchResult.AWAY_WIN),
                PlayerStat(player="Lilly", goals=1, assists=1, result=MatchResult.AWAY_WIN),
                PlayerStat(player="Jonny", goals=1, assists=0, result=MatchResult.HOME_WIN),
            ],
        ),
    ]


def test_summarize_match_week(sample_week_matches: list[MatchRecord]) -> None:
    """Verifies weekly stats aggregation identifies top scorer and assister."""
    summary = _summarize_match_week(sample_week_matches)
    assert summary["match_count"] == 2
    top_scorer, goals = summary["top_scorer"]
    assert top_scorer == "Suri"
    assert goals == 5

    top_assister, assists = summary["top_assister"]
    assert top_assister == "Lilly"
    assert assists == 3


def test_rule_based_fallback_edition(sample_week_matches: list[MatchRecord]) -> None:
    """Verifies deterministic fallback edition format."""
    summary = _summarize_match_week(sample_week_matches)
    edition = _rule_based_fallback_edition("2026-W47", summary, 1)

    assert edition.issue_number == 1
    assert "SURI" in edition.headline
    assert "5 goal(s)" in edition.lead_story or "5 Goals" in edition.matchday_digest
    assert edition.is_ai_generated is False


@pytest.mark.asyncio
async def test_generate_gazette_edition_caching(sample_week_matches: list[MatchRecord]) -> None:
    """Verifies gazette editions are cached per week to prevent token waste."""
    GAZETTE_CACHE.clear()
    ed1 = await generate_gazette_edition(sample_week_matches, week_key="2026-W47", api_key=None)
    assert "2026-W47" in GAZETTE_CACHE

    ed2 = await generate_gazette_edition(sample_week_matches, week_key="2026-W47", api_key=None)
    assert ed1 is ed2


@pytest.mark.asyncio
async def test_generate_gazette_edition_mocked_ai(sample_week_matches: list[MatchRecord]) -> None:
    """Verifies AI response JSON parsing into GazetteEdition."""
    GAZETTE_CACHE.clear()
    fake_json = json.dumps({
        "headline": "SURI HATTRICK SENDS SHOCKWAVES THROUGH CO-OP LEAGUE",
        "lead_story": "Suri was completely unplayable this week, putting five in the net.",
        "locker_room_rumor": "Jonny has demanded a mid-season roster review after conceding four.",
        "stat_of_the_week": "Lilly generated 3 assists while covering every blade of grass.",
        "quote_of_the_week": "The lag was definitely 200ms on the second goal.",
    })

    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = fake_json
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    mock_client.chat.completions.create.return_value = mock_resp

    with patch("ai_gazette.Groq", return_value=mock_client):
        edition = await generate_gazette_edition(
            sample_week_matches,
            week_key="2026-W47",
            api_key="fake_key",
        )

    assert edition.is_ai_generated is True
    assert "SHOCKWAVES" in edition.headline
    assert "unplayable this week" in edition.lead_story


def test_build_gazette_embed() -> None:
    """Verifies Discord embed presentation."""
    edition = GazetteEdition(
        week_key="2026-W47",
        issue_number=5,
        headline="TABLOID HEADLINE",
        lead_story="Lead story body",
        matchday_digest="Digest text",
        locker_room_rumor="Rumor text",
        stat_of_the_week="Stat text",
        quote_of_the_week="Quote text",
        is_ai_generated=True,
    )
    embed = build_gazette_embed(edition)
    assert "Issue #5" in embed.title
    assert "TABLOID HEADLINE" in embed.description
    assert len(embed.fields) == 4
    assert "AI Press Room" in embed.footer.text


@pytest.mark.asyncio
async def test_generate_match_gazette_and_embed(sample_week_matches: list[MatchRecord]) -> None:
    """Verifies single-match gazette special edition and embed generation."""
    match = sample_week_matches[0]
    edition = await generate_match_gazette(match, "Home 3 - 2 Away", api_key=None)
    assert edition.is_ai_generated is False
    assert "M-W1-01" in edition.headline or "ANNIE" in edition.headline or "LILLY" in edition.headline
    assert "Home 3 - 2 Away" in edition.matchday_digest

    embed = build_gazette_embed(edition, match=match)
    assert "Matchday Special" in embed.title
    assert "M-W1-01" in embed.title
    assert len(embed.fields) == 4
