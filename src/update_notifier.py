"""Automatic code update announcement notifier for Discord.

Listens on bot startup, detects new git commits/code updates deployed to the bot,
and automatically sends a brief, punchy announcement in the designated channel,
pinging the updates role so members know what is new.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import discord

from config import BotConfig

LOGGER = logging.getLogger("efootball_bot.update_notifier")

DEFAULT_UPDATE_CHANNEL_ID = 1525228800999428268
DEFAULT_UPDATE_ROLE_ID = 1525397453770063874

IST = timezone(timedelta(hours=5, minutes=30))
ANNOUNCEMENT_TARGET_HOUR_IST = 23  # 11:00 PM IST
ANNOUNCEMENT_TARGET_MINUTE_IST = 0


def get_ist_now() -> datetime:
    """Returns current datetime in Indian Standard Time (UTC+5:30)."""
    return datetime.now(timezone.utc).astimezone(IST)


def is_before_announcement_time(
    target_hour: int = ANNOUNCEMENT_TARGET_HOUR_IST,
    target_minute: int = ANNOUNCEMENT_TARGET_MINUTE_IST,
) -> bool:
    """Returns True if the current IST time is earlier than target announcement time (11:00 PM IST)."""
    now = get_ist_now()
    return now.hour < target_hour or (now.hour == target_hour and now.minute < target_minute)


def seconds_until_announcement_time(
    target_hour: int = ANNOUNCEMENT_TARGET_HOUR_IST,
    target_minute: int = ANNOUNCEMENT_TARGET_MINUTE_IST,
) -> float:
    """Returns seconds remaining until the announcement time today in IST."""
    now = get_ist_now()
    target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    diff = (target - now).total_seconds()
    return max(0.0, diff)


async def _schedule_deferred_announcement(
    bot: discord.Client,
    config: BotConfig,
    repo_dir: Optional[Path],
    delay: float,
) -> None:
    """Waits until 11:00 PM IST and then executes the update announcement."""
    try:
        LOGGER.info("Deferred announcement sleeping for %.1f seconds until 11:00 PM IST...", delay)
        await asyncio.sleep(delay)
        LOGGER.info("11:00 PM IST reached; triggering deferred update announcement now.")
        await check_and_announce_update(bot, config, repo_dir=repo_dir, force_now=True)
    except Exception as exc:
        LOGGER.warning("Error in deferred update announcement: %s", exc)


def get_current_git_commit(repo_dir: Optional[Path] = None) -> Optional[str]:
    """Retrieves the current git commit hash."""
    cwd = repo_dir or Path.cwd()
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        commit = res.stdout.strip()
        if commit:
            return commit
    except Exception as exc:
        LOGGER.debug("Could not get git commit via subprocess: %s", exc)

    # Direct filesystem fallback via .git directory
    git_dir = cwd / ".git"
    if git_dir.exists():
        head_file = git_dir / "HEAD"
        if head_file.exists():
            try:
                head_content = head_file.read_text(encoding="utf-8").strip()
                if head_content.startswith("ref:"):
                    ref_path = git_dir / head_content[4:].strip()
                    if ref_path.exists():
                        return ref_path.read_text(encoding="utf-8").strip()
                elif len(head_content) == 40:
                    return head_content
            except Exception:
                pass
    return None


def get_commit_messages_since(
    since_commit: Optional[str] = None,
    max_count: int = 5,
    repo_dir: Optional[Path] = None,
) -> List[str]:
    """Retrieves concise commit subjects since the last announced commit."""
    cwd = repo_dir or Path.cwd()
    cmd = ["git", "log"]
    if since_commit:
        cmd.append(f"{since_commit}..HEAD")
    else:
        cmd.extend([f"-{max_count}"])
    cmd.append("--pretty=format:%s")

    try:
        res = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        lines = [line.strip() for line in res.stdout.strip().splitlines() if line.strip()]
        clean_lines = []
        for line in lines:
            lower = line.lower()
            if lower.startswith("merge branch") or lower.startswith("merge pull request"):
                continue
            cleaned = clean_commit_for_display(line)
            if cleaned and cleaned not in clean_lines:
                clean_lines.append(cleaned)
        return clean_lines[:max_count]
    except Exception as exc:
        LOGGER.debug("Could not get git log: %s", exc)
        return []


def clean_commit_for_display(subject: str) -> str:
    """Cleans a commit subject into a brief, friendly bullet point without technical jargon."""
    clean = re.sub(r"^(feat|fix|refactor|perf|chore|style|test)(\(.*?\))?:\s*", "", subject, flags=re.IGNORECASE)
    clean = clean.strip()
    if clean:
        clean = clean[0].upper() + clean[1:]
    return clean


def is_silent_commit(subject: str) -> bool:
    """Returns True if the commit is marked to be skipped from public Discord announcements."""
    s = subject.lower()
    return any(marker in s for marker in ["[silent]", "[no update]", "[no-update]", "[no announce]", "[no-announce]"])


def get_pending_update_highlights(data_dir: Path) -> Optional[Tuple[str, List[str]]]:
    """Checks for custom update notes in data/pending_update.json if present."""
    p = data_dir / "pending_update.json"
    if not p.exists():
        return None

    try:
        content = json.loads(p.read_text(encoding="utf-8"))
        title = content.get("title", "🚀 New Update Dropped!")
        highlights = content.get("highlights", [])
        if highlights and isinstance(highlights, list):
            # Archive after reading
            archived = data_dir / "latest_announced_update.json"
            p.replace(archived)
            return title, [str(h) for h in highlights]
    except Exception as exc:
        LOGGER.warning("Could not read pending update file: %s", exc)
    return None


def format_update_message(
    highlights: List[str],
    role_id: int = DEFAULT_UPDATE_ROLE_ID,
    title: Optional[str] = None,
) -> Tuple[str, discord.Embed]:
    """Constructs the role mention and Discord embed for an update announcement."""
    content = f"<@&{role_id}>"
    header_title = title or "🚀 New Update Dropped!"

    embed = discord.Embed(
        title=header_title,
        description="A fresh update has just been deployed to the bot! Here is what's new:",
        color=0x00D26A,  # Energetic emerald green
    )

    if highlights:
        bullet_text = "\n".join(f"• {h}" for h in highlights)
    else:
        bullet_text = "• Fresh improvements and card engine updates are now live."

    embed.add_field(
        name="What's New",
        value=bullet_text,
        inline=False,
    )
    embed.set_footer(text="Check it out with /my-card or /card <player>!")
    return content, embed


async def check_and_announce_update(
    bot: discord.Client,
    config: BotConfig,
    repo_dir: Optional[Path] = None,
    force_now: bool = False,
) -> bool:
    """Checks if a new code update was deployed and automatically announces it.

    Returns True if an announcement was sent, False otherwise.
    """
    state_file = config.workbook_path.parent / "update_state.json"
    root_dir = repo_dir or Path.cwd()
    current_commit = get_current_git_commit(root_dir)

    if not current_commit:
        LOGGER.debug("No current git commit found; skipping update announcement.")
        return False

    last_announced_commit: Optional[str] = None
    if state_file.exists():
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            last_announced_commit = data.get("last_announced_commit")
        except Exception as exc:
            LOGGER.warning("Could not parse update state file: %s", exc)

    # If the commit is identical, no new code update has been added
    if last_announced_commit == current_commit:
        return False

    # Check for pending custom highlights or extract from git commits
    pending = get_pending_update_highlights(config.workbook_path.parent)
    if pending:
        custom_title, highlights = pending
    else:
        custom_title = "🚀 New Update Dropped!"
        # Check if the commits since last announcement are marked silent
        raw_cmd = ["git", "log"]
        if last_announced_commit:
            raw_cmd.append(f"{last_announced_commit}..HEAD")
        else:
            raw_cmd.extend(["-4"])
        raw_cmd.append("--pretty=format:%s")

        try:
            res = subprocess.run(
                raw_cmd,
                cwd=str(root_dir),
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )
            raw_commits = [line.strip() for line in res.stdout.strip().splitlines() if line.strip()]
        except Exception:
            raw_commits = []

        if raw_commits and any(is_silent_commit(c) for c in raw_commits):
            LOGGER.info("Update commit is marked silent; advancing state without sending announcement message.")
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text(
                json.dumps({"last_announced_commit": current_commit}, indent=2),
                encoding="utf-8",
            )
            return False

        commits = get_commit_messages_since(
            since_commit=last_announced_commit,
            max_count=4,
            repo_dir=root_dir,
        )
        highlights = commits if commits else [
            "Player Card Stats & OVR engine updated with realistic co-op progression.",
            "Tactical positions synchronized with card archetypes (ST, CAM, CM, RW, LW, CB).",
            "Special In-Form (TOTW) and POTM boosts refined.",
        ]

    # Timing constraint: Do not send update announcement before 11:00 PM IST
    if not force_now and is_before_announcement_time():
        delay = seconds_until_announcement_time()
        LOGGER.info(
            "Update detected (commit %s) before 11:00 PM IST. Scheduling auto-trigger in %.1f seconds.",
            current_commit[:7],
            delay,
        )
        asyncio.create_task(_schedule_deferred_announcement(bot, config, repo_dir, delay))
        return False

    channel_id = config.update_channel_id
    role_id = config.update_role_id

    channel = bot.get_channel(channel_id)
    if not channel:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception as exc:
            LOGGER.warning("Could not fetch update announcement channel %s: %s", channel_id, exc)
            return False

    if not isinstance(channel, discord.abc.Messageable) and not hasattr(channel, "send"):
        LOGGER.warning("Target update channel %s is not messageable.", channel_id)
        return False

    content, embed = format_update_message(highlights, role_id=role_id, title=custom_title)

    try:
        await channel.send(
            content=content,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(roles=True),
        )
        LOGGER.info(
            "Successfully announced new update (commit %s) to channel %s",
            current_commit[:7],
            channel_id,
        )

        # Save new state
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps({"last_announced_commit": current_commit}, indent=2),
            encoding="utf-8",
        )
        return True
    except Exception as exc:
        LOGGER.error("Failed to send update announcement: %s", exc)
        return False
