"""Main entry point and Discord client initialization for the eFootball bot."""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from commands_handler import handle_pasted_match_message, register_commands
from config import BotConfig, load_config, setup_logging
from excel_manager import ExcelManager

LOGGER = logging.getLogger("efootball_bot.bot")


class EFootballBot(commands.Bot):
    """Custom bot client with guild-specific slash command synchronization and direct paste support."""

    def __init__(self, config: BotConfig) -> None:
        intents = discord.Intents.default()
        intents.message_content = False

        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
        )
        self.bot_config = config
        self.guild_object = discord.Object(id=self.bot_config.guild_id)
        self.excel_manager = ExcelManager(self.bot_config.workbook_path)

        # Register the 6 required slash commands
        register_commands(self.tree, self.bot_config, self.excel_manager)

        # Configure tree error handler
        self.tree.on_error = self.on_tree_error

    async def on_tree_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        """Global slash command error handling."""
        LOGGER.error("Slash command error in %s: %s", interaction.command.name if interaction.command else "unknown", error)
        msg = "❌ An unexpected error occurred while executing this command."
        if isinstance(error, app_commands.CheckFailure):
            msg = "❌ You do not have permission or are in the wrong channel for this command."
        elif isinstance(error, app_commands.CommandOnCooldown):
            msg = f"⏳ Command is on cooldown. Try again in {error.retry_after:.1f}s."

        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass

    async def setup_hook(self) -> None:
        """Executes before bot starts listening. Synchronizes commands to target guild."""
        LOGGER.info("Running setup hook. Ensuring target storage directory exists...")
        self.bot_config.workbook_path.parent.mkdir(parents=True, exist_ok=True)

        LOGGER.info(
            "Syncing slash commands exclusively to Guild ID: %s", self.bot_config.guild_id
        )
        # Copy global app_commands to guild tree for instant sync without 1-hour Discord global cache delay
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

    async def on_message(self, message: discord.Message) -> None:
        """Listens for screenshots pasted directly (Ctrl+V) into authorized channel."""
        if message.author.bot:
            return

        if message.guild is None or message.guild.id != self.bot_config.guild_id:
            return

        if self.bot_config.allowed_channel_id and message.channel.id != self.bot_config.allowed_channel_id:
            return

        # Check if user pasted images directly into the channel
        image_attachments = [
            att for att in message.attachments
            if att.content_type and att.content_type.startswith("image/")
        ]

        if image_attachments:
            LOGGER.info(
                "Detected %d pasted screenshot(s) from user %s in channel %s",
                len(image_attachments),
                message.author.name,
                message.channel.id,
            )
            await handle_pasted_match_message(
                message=message,
                attachments=image_attachments,
                config=self.bot_config,
                excel_manager=self.excel_manager,
            )

        await self.process_commands(message)


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
