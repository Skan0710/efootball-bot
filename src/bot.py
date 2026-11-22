"""Main entry point and Discord client initialization for the eFootball bot."""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import logging
import sys
from pathlib import Path
from typing import Optional

# Ensure src/ directory is in sys.path
_SRC_DIR = str(Path(__file__).resolve().parent)
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

import discord
from discord import app_commands
from discord.ext import commands, tasks

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
        self._last_announced_potw_week: Optional[str] = None

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

        # Start periodic POTW gameweek completion automation
        if not self.auto_potw_task.is_running():
            self.auto_potw_task.start()

    def _get_potw_state_file(self) -> Path:
        return self.bot_config.workbook_path.parent / "potw_state.json"

    def _load_potw_state(self) -> Optional[str]:
        p = self._get_potw_state_file()
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                return data.get("last_announced_week")
            except Exception:
                return None
        return None

    def _save_potw_state(self, week_key: str) -> None:
        p = self._get_potw_state_file()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"last_announced_week": week_key}), encoding="utf-8")
        except Exception as exc:
            LOGGER.warning("Could not save POTW state: %s", exc)

    async def check_and_announce_potw(self) -> bool:
        """Checks if the completed gameweek has ended and automatically announces POTW with a special 90+ TOTW card."""
        try:
            target_channel_id = self.bot_config.allowed_channel_id
            if not target_channel_id:
                return False

            all_matches = await self.excel_manager.get_all_matches()
            if not all_matches:
                return False

            from stats import calculate_potw_rankings, calculate_special_card_stats
            from visuals import render_fc27_card

            # Check last completed calendar week (weeks_ago=1)
            rankings, week_start, week_end = calculate_potw_rankings(all_matches, weeks_ago=1)
            if not rankings:
                return False

            week_key = f"{week_start.strftime('%Y-W%U')}"
            last_announced = self._load_potw_state() or self._last_announced_potw_week
            if last_announced == week_key:
                return False

            channel = self.get_channel(target_channel_id)
            if not channel:
                try:
                    channel = await self.fetch_channel(target_channel_id)
                except Exception:
                    channel = None

            if not isinstance(channel, discord.abc.Messageable):
                return False

            winner = rankings[0]
            week_range = f"{week_start.strftime('%d %b')} – {week_end.strftime('%d %b %Y')}"

            records = await self.excel_manager.get_leaderboard_from_sheet()
            totw_card = calculate_special_card_stats(winner["player"], records, all_matches, card_type="TOTW")

            card_file: Optional[discord.File] = None
            card_filename = f"totw_{winner['player'].lower()}_card.png"
            if totw_card:
                try:
                    card_buf = render_fc27_card(totw_card)
                    card_file = discord.File(fp=card_buf, filename=card_filename)
                except Exception as c_exc:
                    LOGGER.warning("Failed to render automated TOTW card: %s", c_exc)

            buff_ovr = totw_card["rating"] if totw_card else 90
            embed = discord.Embed(
                title=f"🌟 Player of the Week — {week_range}",
                description=(
                    f"👑 Congratulations to **{winner['player']}** for winning **Player of the Week**!\n\n"
                    f"**Gameweek Stats:** {winner['goals']} Goals, {winner['assists']} Assists, "
                    f"{winner['wins']} Wins in {winner['matches_played']} matches ({winner['score']:.2f} pts).\n\n"
                    f"⚡ Awarded a special **{buff_ovr} OVR Team of the Week (TOTW) Card** (90+ Buff)!"
                ),
                color=discord.Color.gold(),
                timestamp=datetime.now(),
            )
            if card_file:
                embed.set_image(url=f"attachment://{card_filename}")
            embed.set_footer(text="Official eFootball Weekly Honors • TOTW Special Card (Base Card Unchanged)")

            if card_file:
                await channel.send(
                    content="📢 **The Gameweek is complete! All hail our latest Player of the Week!**",
                    embed=embed,
                    file=card_file,
                )
            else:
                await channel.send(
                    content="📢 **The Gameweek is complete! All hail our latest Player of the Week!**",
                    embed=embed,
                )

            self._last_announced_potw_week = week_key
            self._save_potw_state(week_key)
            LOGGER.info("Successfully announced POTW for week %s to channel %s", week_key, target_channel_id)
            return True
        except Exception as exc:
            LOGGER.warning("Error in check_and_announce_potw: %s", exc)
            return False

    @tasks.loop(hours=1)
    async def auto_potw_task(self) -> None:
        """Periodically checks if the gameweek is over and automatically announces POTW."""
        await self.check_and_announce_potw()

    @auto_potw_task.before_loop
    async def before_potw_task(self) -> None:
        await self.wait_until_ready()

    async def on_ready(self) -> None:
        """Invoked when the bot has connected to Discord and finished internal cache setup."""
        if self.user:
            LOGGER.info("Logged in as %s (ID: %s)", self.user.name, self.user.id)
            LOGGER.info("Bot is active and listening in target guild: %s", self.bot_config.guild_id)
            await self.change_presence(
                activity=discord.Game(name="eFootball Co-op | /add-match-image")
            )
            # Announce completed POTW the moment the bot is on
            await self.check_and_announce_potw()
            # Announce code updates if a new update/commit has been deployed
            await self.check_and_announce_code_update()

    async def check_and_announce_code_update(self) -> bool:
        """Checks if a new code update was deployed and announces it to the updates channel."""
        try:
            from update_notifier import check_and_announce_update
            return await check_and_announce_update(self, self.bot_config)
        except Exception as exc:
            LOGGER.warning("Could not execute code update announcement: %s", exc)
            return False

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
