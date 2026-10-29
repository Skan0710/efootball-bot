"""Main entry point and Discord client initialization for the eFootball bot."""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import BotConfig, load_config, setup_logging

LOGGER = logging.getLogger("efootball_bot.bot")


class EFootballBot(commands.Bot):
    """Custom bot client with guild-specific slash command synchronization."""

    def __init__(self, config: BotConfig) -> None:
        intents = discord.Intents.default()
        # Explicitly configure default intents (message content is not required for slash commands)
        intents.message_content = False

        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
        )
        self.bot_config = config
        self.guild_object = discord.Object(id=self.bot_config.guild_id)

    async def setup_hook(self) -> None:
        """Executes before bot starts listening. Synchronizes commands to target guild."""
        LOGGER.info("Running setup hook. Ensuring target storage directory exists...")
        self.bot_config.workbook_path.parent.mkdir(parents=True, exist_ok=True)

        LOGGER.info(
            "Syncing slash commands exclusively to Guild ID: %s", self.bot_config.guild_id
        )
        # Copy global app_commands to guild tree for instant update without global Discord delay
        self.tree.copy_global_to(guild=self.guild_object)
        synced = await self.tree.sync(guild=self.guild_object)
        LOGGER.info("Synced %d slash commands to guild %s", len(synced), self.bot_config.guild_id)

    async def on_ready(self) -> None:
        """Invoked when the bot has connected to Discord and finished internal cache setup."""
        if self.user:
            LOGGER.info("Logged in as %s (ID: %s)", self.user.name, self.user.id)
            LOGGER.info("Bot is active and listening in target guild: %s", self.bot_config.guild_id)
            await self.change_presence(
                activity=discord.Game(name="eFootball Co-op | /add-match-image")
            )


def build_bot(config: Optional[BotConfig] = None) -> EFootballBot:
    """Factory creating an EFootballBot instance configured with application settings."""
    if config is None:
        config = load_config()
    setup_logging(config.log_level)
    return EFootballBot(config)


def main() -> None:
    """CLI launcher for starting the bot."""
    try:
        config = load_config()
    except Exception as exc:
        LOGGER.critical("Configuration error: %s", exc)
        sys.exit(1)

    setup_logging(config.log_level)
    LOGGER.info("Starting eFootball Discord Bot...")

    bot = build_bot(config)

    try:
        bot.run(config.discord_token, log_handler=None)
    except KeyboardInterrupt:
        LOGGER.info("Bot process stopped by user.")
    except Exception as exc:
        LOGGER.critical("Fatal error running bot: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
