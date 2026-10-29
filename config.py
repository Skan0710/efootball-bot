"""Configuration management for the eFootball Discord Statistics Bot.

Loads settings from environment variables and .env file, performs validation,
and configures application-wide logging.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

LOGGER = logging.getLogger("efootball_bot")


@dataclass(frozen=True)
class BotConfig:
    """Immutable application configuration."""

    discord_token: str
    guild_id: int
    workbook_path: Path
    max_image_mb: float = 10.0
    allowed_channel_id: Optional[int] = None
    admin_role_id: Optional[int] = None
    tesseract_cmd: Optional[str] = None
    log_level: str = "INFO"

    @property
    def max_image_bytes(self) -> int:
        """Maximum allowed file size in bytes."""
        return int(self.max_image_mb * 1024 * 1024)


def setup_logging(level_name: str = "INFO") -> None:
    """Configures structured standard logging for console and error outputs."""
    numeric_level = getattr(logging, level_name.upper(), logging.INFO)
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Avoid duplicate handlers if setup_logging is called multiple times
    if not root_logger.handlers:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
    else:
        for handler in root_logger.handlers:
            handler.setFormatter(formatter)


def load_config(env_path: Optional[str | Path] = None, require_token: bool = True) -> BotConfig:
    """Loads and validates configuration from environment variables.

    Args:
        env_path: Optional custom path to a .env file.
        require_token: If True, validates presence of DISCORD_BOT_TOKEN. Set to
          False during non-interactive testing or maintenance scripts.

    Returns:
        BotConfig instance with validated settings.

    Raises:
        ValueError: If mandatory configuration is missing or malformed.
    """
    if env_path is not None:
        load_dotenv(dotenv_path=env_path)
    else:
        load_dotenv()

    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if require_token and not token:
        raise ValueError(
            "DISCORD_BOT_TOKEN is missing or empty. Please define it in your .env file."
        )

    guild_id_raw = os.getenv("DISCORD_GUILD_ID", "").strip()
    if not guild_id_raw:
        raise ValueError(
            "DISCORD_GUILD_ID is missing. The bot requires a target guild ID to operate."
        )
    try:
        guild_id = int(guild_id_raw)
    except ValueError as exc:
        raise ValueError(f"DISCORD_GUILD_ID must be a numeric integer, got '{guild_id_raw}'") from exc

    workbook_raw = os.getenv("WORKBOOK_PATH", "data/matches.xlsx").strip()
    workbook_path = Path(workbook_raw)

    max_image_mb_raw = os.getenv("MAX_IMAGE_MB", "10").strip()
    try:
        max_image_mb = float(max_image_mb_raw)
        if max_image_mb <= 0:
            raise ValueError
    except ValueError as exc:
        raise ValueError(f"MAX_IMAGE_MB must be a positive number, got '{max_image_mb_raw}'") from exc

    allowed_channel_id: Optional[int] = None
    allowed_channel_raw = os.getenv("ALLOWED_CHANNEL_ID", "").strip()
    if allowed_channel_raw:
        try:
            allowed_channel_id = int(allowed_channel_raw)
        except ValueError as exc:
            raise ValueError(
                f"ALLOWED_CHANNEL_ID must be an integer, got '{allowed_channel_raw}'"
            ) from exc

    admin_role_id: Optional[int] = None
    admin_role_raw = os.getenv("ADMIN_ROLE_ID", "").strip()
    if admin_role_raw:
        try:
            admin_role_id = int(admin_role_raw)
        except ValueError as exc:
            raise ValueError(f"ADMIN_ROLE_ID must be an integer, got '{admin_role_raw}'") from exc

    tesseract_cmd = os.getenv("TESSERACT_CMD", "").strip() or None
    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()

    return BotConfig(
        discord_token=token,
        guild_id=guild_id,
        workbook_path=workbook_path,
        max_image_mb=max_image_mb,
        allowed_channel_id=allowed_channel_id,
        admin_role_id=admin_role_id,
        tesseract_cmd=tesseract_cmd,
        log_level=log_level,
    )
