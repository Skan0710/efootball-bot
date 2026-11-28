"""Tests for the automatic code update announcement notifier."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from config import BotConfig
from update_notifier import (
    DEFAULT_UPDATE_CHANNEL_ID,
    DEFAULT_UPDATE_ROLE_ID,
    check_and_announce_update,
    clean_commit_for_display,
    format_update_message,
    get_commit_messages_since,
    get_current_git_commit,
    get_pending_update_highlights,
    is_before_announcement_time,
    is_silent_commit,
    seconds_until_announcement_time,
)


def test_clean_commit_for_display() -> None:
    """Verifies that conventional prefixes are stripped and text is capitalized."""
    assert clean_commit_for_display("feat: add new card stats") == "Add new card stats"
    assert clean_commit_for_display("fix(ocr): resolve score parsing bug") == "Resolve score parsing bug"
    assert clean_commit_for_display("Revamp 6 face attributes") == "Revamp 6 face attributes"
    assert clean_commit_for_display("") == ""


def test_is_silent_commit() -> None:
    """Verifies silent commit markers detection."""
    assert is_silent_commit("[silent] rename Naka to Son") is True
    assert is_silent_commit("rename Naka to Son [no-announce]") is True
    assert is_silent_commit("feat: [no update] internal refactor") is True
    assert is_silent_commit("feat: update card progression") is False


def test_format_update_message() -> None:
    """Verifies embed formatting and role mention string."""
    highlights = ["New Card Stats are live", "Tactical positions updated"]
    content, embed = format_update_message(highlights, role_id=DEFAULT_UPDATE_ROLE_ID)

    assert content == f"<@&{DEFAULT_UPDATE_ROLE_ID}>"
    assert isinstance(embed, discord.Embed)
    assert embed.title == "🚀 New Update Dropped!"
    assert len(embed.fields) == 1
    assert "• New Card Stats are live" in embed.fields[0].value
    assert "• Tactical positions updated" in embed.fields[0].value


def test_get_current_git_commit() -> None:
    """Verifies that git commit retrieval returns a valid 40-char SHA string."""
    commit = get_current_git_commit()
    assert commit is not None
    assert len(commit) == 40


def test_pending_update_highlights(tmp_path: Path) -> None:
    """Verifies reading and archiving pending_update.json."""
    pending_file = tmp_path / "pending_update.json"
    pending_file.write_text(
        json.dumps({
            "title": "Custom Title",
            "highlights": ["Feature 1", "Feature 2"],
        }),
        encoding="utf-8",
    )

    res = get_pending_update_highlights(tmp_path)
    assert res is not None
    title, highlights = res
    assert title == "Custom Title"
    assert highlights == ["Feature 1", "Feature 2"]
    # File should have been archived
    assert not pending_file.exists()
    assert (tmp_path / "latest_announced_update.json").exists()


@pytest.mark.asyncio
async def test_check_and_announce_update_skips_when_same_commit(tmp_path: Path) -> None:
    """Verifies that if state file already matches current commit, no message is sent."""
    current_commit = get_current_git_commit()
    assert current_commit is not None

    state_file = tmp_path / "update_state.json"
    state_file.write_text(json.dumps({"last_announced_commit": current_commit}), encoding="utf-8")

    dummy_config = BotConfig(
        discord_token="fake_token",
        guild_id=12345,
        workbook_path=tmp_path / "matches.xlsx",
        update_channel_id=DEFAULT_UPDATE_CHANNEL_ID,
        update_role_id=DEFAULT_UPDATE_ROLE_ID,
    )

    mock_bot = MagicMock()
    mock_bot.get_channel = MagicMock()

    announced = await check_and_announce_update(mock_bot, dummy_config)
    assert announced is False
    mock_bot.get_channel.assert_not_called()


@pytest.mark.asyncio
async def test_check_and_announce_update_sends_when_new_commit(tmp_path: Path) -> None:
    """Verifies that a new commit triggers announcement and updates state file."""
    state_file = tmp_path / "update_state.json"
    state_file.write_text(json.dumps({"last_announced_commit": "old_commit_123"}), encoding="utf-8")

    dummy_config = BotConfig(
        discord_token="fake_token",
        guild_id=12345,
        workbook_path=tmp_path / "matches.xlsx",
        update_channel_id=DEFAULT_UPDATE_CHANNEL_ID,
        update_role_id=DEFAULT_UPDATE_ROLE_ID,
    )

    mock_channel = AsyncMock(spec=discord.TextChannel)
    mock_bot = MagicMock()
    mock_bot.get_channel.return_value = mock_channel

    announced = await check_and_announce_update(mock_bot, dummy_config, force_now=True)
    assert announced is True

    # Verify message was sent with role mention allowed
    mock_channel.send.assert_awaited_once()
    call_kwargs = mock_channel.send.call_args.kwargs
    assert call_kwargs["content"] == f"<@&{DEFAULT_UPDATE_ROLE_ID}>"
    assert isinstance(call_kwargs["embed"], discord.Embed)
    assert call_kwargs["allowed_mentions"].roles is True

    # Verify state file was updated
    new_state = json.loads(state_file.read_text(encoding="utf-8"))
    assert new_state["last_announced_commit"] != "old_commit_123"
    assert len(new_state["last_announced_commit"]) == 40


def test_announcement_timing_gate() -> None:
    """Verifies that the 11:00 PM IST timing gate calculates delays accurately."""
    # Target 23:00 (11:00 PM IST)
    before = is_before_announcement_time(target_hour=23, target_minute=0)
    # The return value depends on current IST time, but function should return a boolean
    assert isinstance(before, bool)
    remaining = seconds_until_announcement_time(target_hour=23, target_minute=0)
    assert remaining >= 0.0


@pytest.mark.asyncio
async def test_bot_code_update_announcement_disabled(tmp_path: Path) -> None:
    """Verifies that EFootballBot.check_and_announce_code_update is disabled and returns False."""
    from bot import EFootballBot

    cfg = BotConfig(
        discord_token="fake_token",
        guild_id=12345,
        workbook_path=tmp_path / "matches.xlsx",
        update_channel_id=DEFAULT_UPDATE_CHANNEL_ID,
        update_role_id=DEFAULT_UPDATE_ROLE_ID,
    )
    bot = EFootballBot(cfg)
    assert await bot.check_and_announce_code_update() is False
