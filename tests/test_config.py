"""Tests for configuration validation and environment loading."""

import os
from pathlib import Path

import pytest

from config import BotConfig, load_config


def test_load_config_missing_token_raises_value_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verifies that missing DISCORD_BOT_TOKEN triggers a descriptive ValueError."""
    dummy_env = tmp_path / "empty.env"
    dummy_env.write_text("")
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.setenv("DISCORD_GUILD_ID", "123456789")

    with pytest.raises(ValueError, match="DISCORD_BOT_TOKEN is missing"):
        load_config(env_path=dummy_env, require_token=True)


def test_load_config_missing_guild_id_raises_value_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verifies that missing DISCORD_GUILD_ID triggers a descriptive ValueError."""
    dummy_env = tmp_path / "empty.env"
    dummy_env.write_text("")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fake_test_token")
    monkeypatch.delenv("DISCORD_GUILD_ID", raising=False)

    with pytest.raises(ValueError, match="DISCORD_GUILD_ID is missing"):
        load_config(env_path=dummy_env, require_token=True)


def test_load_config_invalid_guild_id_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verifies that non-numeric DISCORD_GUILD_ID triggers ValueError."""
    dummy_env = tmp_path / "empty.env"
    dummy_env.write_text("")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fake_test_token")
    monkeypatch.setenv("DISCORD_GUILD_ID", "not_a_number")

    with pytest.raises(ValueError, match="DISCORD_GUILD_ID must be a numeric integer"):
        load_config(env_path=dummy_env, require_token=True)


def test_load_config_valid_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verifies default values when optional environment variables are omitted."""
    dummy_env = tmp_path / "empty.env"
    dummy_env.write_text("")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "sample_token_xyz")
    monkeypatch.setenv("DISCORD_GUILD_ID", "9876543210")
    monkeypatch.delenv("WORKBOOK_PATH", raising=False)
    monkeypatch.delenv("MAX_IMAGE_MB", raising=False)
    monkeypatch.delenv("ALLOWED_CHANNEL_ID", raising=False)
    monkeypatch.delenv("ADMIN_ROLE_ID", raising=False)

    cfg = load_config(env_path=dummy_env, require_token=True)
    assert cfg.discord_token == "sample_token_xyz"
    assert cfg.guild_id == 9876543210
    assert cfg.workbook_path == Path("data/matches.xlsx")
    assert cfg.max_image_mb == 10.0
    assert cfg.max_image_bytes == 10 * 1024 * 1024
    assert cfg.allowed_channel_id is None
    assert cfg.admin_role_id is None
    assert cfg.groq_api_key is None
    assert cfg.groq_api_key_secondary is None
    assert cfg.groq_api_keys == []
    assert cfg.groq_vision_model == "qwen/qwen3.8-27b"


def test_multi_api_key_failover_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verifies that primary and secondary API keys are loaded into priority failover list."""
    env_file = tmp_path / "keys.env"
    env_file.write_text(
        "DISCORD_BOT_TOKEN=tok\n"
        "DISCORD_GUILD_ID=12345\n"
        "GROQ_API_KEY=primary_key_1\n"
        "GROQ_API_KEY_SECONDARY=secondary_key_2\n"
    )
    cfg = load_config(env_path=env_file, require_token=True)
    assert cfg.groq_api_key == "primary_key_1"
    assert cfg.groq_api_key_secondary == "secondary_key_2"
    assert cfg.groq_api_keys == ["primary_key_1", "secondary_key_2"]
