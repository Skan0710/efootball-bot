"""AI Pundit commentary and roast engine for eFootball match results.

Generates performance-based match commentary featuring:
- Peter Drury: Poetic hype praising standout winners and clutch plays.
- Roy Keane: Brutal roast tearing into underperformers and chokers.
- Locker Room: Irrespective banter regarding team chemistry, lag, and controller excuses.

Includes token-efficient caching and resilient fallback generation.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

import discord

from models import MatchRecord, MatchResult, PlayerStat

try:
    from groq import Groq
except ImportError:
    Groq = None

LOGGER = logging.getLogger("efootball_bot.pundit")

# In-memory review cache to avoid redundant API token consumption
PUNDIT_CACHE: Dict[str, PunditReview] = {}


def resolve_api_keys(api_key: Optional[str] = None) -> List[str]:
    """Resolves primary and secondary API keys from arguments and environment."""
    keys: List[str] = []
    if api_key:
        for k in api_key.split(","):
            c = k.strip()
            if c and c not in keys:
                keys.append(c)
    env_primary = os.getenv("GROQ_API_KEY", "").strip()
    if env_primary:
        for k in env_primary.split(","):
            c = k.strip()
            if c and c not in keys:
                keys.append(c)
    env_sec = (
        os.getenv("GROQ_API_KEY_SECONDARY", "").strip()
        or os.getenv("GROQ_API_KEY_BACKUP", "").strip()
    )
    if env_sec:
        for k in env_sec.split(","):
            c = k.strip()
            if c and c not in keys:
                keys.append(c)
    return keys


@dataclass
class PunditReview:
    """Structured three-mode pundit breakdown for a match."""

    match_id: str
    scoreline: str
    drury_commentary: str
    keane_roast: str
    locker_room_banter: str
    is_ai_generated: bool = True


def _rule_based_fallback_review(match: MatchRecord, scoreline: str) -> PunditReview:
    """Deterministic, zero-token fallback review tailored to player performances."""
    win_stats = [p for p in match.player_stats if p.result in (MatchResult.WIN, MatchResult.HOME_WIN)]
    loss_stats = [p for p in match.player_stats if p.result in (MatchResult.LOSS, MatchResult.AWAY_WIN)]
    draw_stats = [p for p in match.player_stats if p.result == MatchResult.DRAW]

    # Best performer
    best_player: Optional[PlayerStat] = None
    all_players = match.player_stats or []
    if all_players:
        best_player = max(all_players, key=lambda p: (p.goals * 2 + p.assists, p.goals))

    # Worst performer (0 goals, 0 assists, or on losing side)
    worst_player: Optional[PlayerStat] = None
    zero_contributors = [p for p in (loss_stats or all_players) if p.goals == 0 and p.assists == 0]
    if zero_contributors:
        worst_player = zero_contributors[0]
    elif loss_stats:
        worst_player = min(loss_stats, key=lambda p: (p.goals * 2 + p.assists, p.goals))
    elif all_players:
        worst_player = min(all_players, key=lambda p: (p.goals * 2 + p.assists, p.goals))

    # 1. Drury commentary
    if best_player and (best_player.goals > 0 or best_player.assists > 0):
        drury = (
            f"Breathtaking! Under the glittering floodlights, {best_player.player} stepped forward "
            f"as the architect of destiny. With {best_player.goals} goal(s) and {best_player.assists} assist(s), "
            f"they didn't just contest this tie; they commanded it. A sweeping, triumphant exhibition!"
        )
    elif win_stats:
        winners_str = " and ".join(p.player for p in win_stats[:2])
        drury = (
            f"An irresistible surge! {winners_str} seized their moment with courage and conviction, "
            f"turning pressure into pure digital poetry as the final whistle blew."
        )
    else:
        drury = (
            "A grueling stalemate of attrition! Neither side yielded an inch, leaving us with "
            "a spectacle forged in tension and desperate defending."
        )

    # 2. Keane roast
    if worst_player and worst_player.goals == 0 and worst_player.assists == 0:
        keane = (
            f"{worst_player.player}? Absolutely anonymous. Zero goals, zero assists, zero appetite. "
            f"You're out there jogging around while your teammates are doing the heavy lifting. "
            f"It's embarrassing. Go into the dressing room, take a look in the mirror, and sort yourself out."
        )
    elif worst_player:
        keane = (
            f"{worst_player.player} went completely missing when the pressure dialed up. "
            f"At this level, you can't be giving away possession and hiding on the pitch. "
            f"Basic footballing standards—do your job!"
        )
    else:
        keane = (
            "Both sides looked lethargic. Defending was non-existent at times, and no one took "
            "the game by the scruff of the neck. Serious lack of character."
        )

    # 3. Locker room banter
    if worst_player and best_player and worst_player.player != best_player.player:
        locker = (
            f'"Someone check {worst_player.player}\'s controller, the batteries must have died in the 10th minute! '
            f'Meanwhile {best_player.player} is literally carrying the entire server on their back. '
            f'No excuses about script or ping today boys—the scoreboard doesn\'t lie."'
        )
    else:
        locker = (
            '"Already hearing the classic excuses in voice chat—input delay, script lag, and sweaty tactics. '
            'Time to hit the training ground and run it back."'
        )

    return PunditReview(
        match_id=match.match_id,
        scoreline=scoreline,
        drury_commentary=drury,
        keane_roast=keane,
        locker_room_banter=locker,
        is_ai_generated=False,
    )


async def generate_pundit_review(
    match: MatchRecord,
    scoreline: str,
    api_key: Optional[str] = None,
    model: str = "qwen/qwen3.8-27b",
    timeout_seconds: float = 15.0,
) -> PunditReview:
    """Generates or retrieves cached pundit commentary for a match record."""
    if match.match_id in PUNDIT_CACHE:
        return PUNDIT_CACHE[match.match_id]

    resolved_keys = resolve_api_keys(api_key)
    if not resolved_keys:
        review = _rule_based_fallback_review(match, scoreline)
        PUNDIT_CACHE[match.match_id] = review
        return review

    # Prepare structured match briefing for low token prompt
    win_stats = [p for p in match.player_stats if p.result in (MatchResult.WIN, MatchResult.HOME_WIN)]
    loss_stats = [p for p in match.player_stats if p.result in (MatchResult.LOSS, MatchResult.AWAY_WIN)]
    draw_stats = [p for p in match.player_stats if p.result == MatchResult.DRAW]

    stats_summary: List[str] = []
    if win_stats:
        stats_summary.append("Winning Side: " + ", ".join(f"{p.player} ({p.goals}G, {p.assists}A)" for p in win_stats))
    if loss_stats:
        stats_summary.append("Losing Side: " + ", ".join(f"{p.player} ({p.goals}G, {p.assists}A)" for p in loss_stats))
    if draw_stats:
        stats_summary.append("Draw Side: " + ", ".join(f"{p.player} ({p.goals}G, {p.assists}A)" for p in draw_stats))
    if not win_stats and not loss_stats and not draw_stats and match.player_stats:
        stats_summary.append("Players: " + ", ".join(f"{p.player} ({p.goals}G, {p.assists}A)" for p in match.player_stats))

    prompt = (
        "You are an expert football pundit panel reviewing an eFootball co-op match between friends.\n"
        f"Match ID: {match.match_id}\n"
        f"Scoreline & Outcome: {scoreline} ({match.result.value})\n"
        f"Stats: {' | '.join(stats_summary)}\n\n"
        "Generate a JSON object with EXACTLY three string keys (keep each under 60 words for concise token efficiency):\n"
        "- 'drury': Poetic, dramatic Peter Drury commentary praising the standout winners or clutch drama.\n"
        "- 'keane': Roy Keane brutal roast tearing into the worst performer, chokers, or players with 0G/0A.\n"
        "- 'locker_room': Witty locker room / Discord voice chat banter about lag, script, controllers, and who carried whom.\n"
        "Output valid JSON only."
    )

    try:
        content_str: Optional[str] = None
        for attempt_idx, key in enumerate(resolved_keys):
            if Groq is not None:
                try:
                    client = Groq(api_key=key, timeout=timeout_seconds)
                    response = client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        response_format={"type": "json_object"},
                        max_tokens=350,
                        temperature=0.7,
                    )
                    content_str = response.choices[0].message.content
                    if content_str:
                        break
                except Exception as exc:
                    LOGGER.warning("Pundit key #%d failed via SDK: %s; trying HTTP/failover", attempt_idx + 1, exc)

            if not content_str:
                try:
                    import httpx

                    headers = {
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    }
                    body = {
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "response_format": {"type": "json_object"},
                        "max_tokens": 350,
                        "temperature": 0.7,
                    }
                    with httpx.Client(timeout=timeout_seconds) as http_client:
                        r = http_client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=body)
                        if r.status_code == 200:
                            data = r.json()
                            content_str = data["choices"][0]["message"]["content"]
                            if content_str:
                                break
                        else:
                            LOGGER.warning("Pundit API key #%d returned HTTP %d: %s", attempt_idx + 1, r.status_code, r.text[:200])
                except Exception as exc:
                    LOGGER.warning("Pundit key #%d failed via HTTP: %s", attempt_idx + 1, exc)

        if content_str:
            clean_json = content_str.strip()
            if clean_json.startswith("```json"):
                clean_json = clean_json[7:]
            if clean_json.startswith("```"):
                clean_json = clean_json[3:]
            if clean_json.endswith("```"):
                clean_json = clean_json[:-3]
            parsed = json.loads(clean_json.strip())

            drury = str(parsed.get("drury", "")).strip()
            keane = str(parsed.get("keane", "")).strip()
            locker = str(parsed.get("locker_room", "")).strip()

            if drury and keane and locker:
                review = PunditReview(
                    match_id=match.match_id,
                    scoreline=scoreline,
                    drury_commentary=drury,
                    keane_roast=keane,
                    locker_room_banter=locker,
                    is_ai_generated=True,
                )
                PUNDIT_CACHE[match.match_id] = review
                return review
    except Exception as exc:
        LOGGER.warning("Error generating AI pundit review: %s; falling back to template", exc)

    # Fallback if AI generation failed or gave unexpected structure
    review = _rule_based_fallback_review(match, scoreline)
    PUNDIT_CACHE[match.match_id] = review
    return review


def build_pundit_embed(review: PunditReview, match: MatchRecord) -> discord.Embed:
    """Builds a rich presentation embed for the three-mode Pundit Desk."""
    embed = discord.Embed(
        title=f"🎙️ The Pundit Desk — Match ID: {match.match_id}",
        description=f"### {review.scoreline}\n*Match recorded on `{match.date}` by `{match.submitted_by}`*",
        color=discord.Color.dark_gold(),
    )

    embed.add_field(
        name="✨ 🎙️ Peter Drury (Poetic Praise)",
        value=review.drury_commentary,
        inline=False,
    )
    embed.add_field(
        name="🔥 😡 Roy Keane (The Brutal Roast)",
        value=review.keane_roast,
        inline=False,
    )
    embed.add_field(
        name="💬 🍻 Locker Room Banter",
        value=review.locker_room_banter,
        inline=False,
    )

    footer_text = "eFootball Co-op Punditry Studio"
    if review.is_ai_generated:
        footer_text += " • AI Powered"
    else:
        footer_text += " • Performance Studio"
    footer_text += " • /pundit [match_id]"

    embed.set_footer(text=footer_text)
    return embed
