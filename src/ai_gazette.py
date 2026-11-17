"""The Co-op Gazette: Satirical weekly newspaper engine for eFootball statistics.

Generates entertaining weekly sports gazette editions featuring:
- Sensationalist front-page headlines
- The Week in Review / Matchday digest
- Locker Room whispers & fictional transfer rumors
- Stat of the week
- Quote of the week

Features token-efficient caching and resilient fallback generation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
import re
from typing import Dict, List, Optional, Tuple

import discord

from models import MatchRecord, MatchResult

try:
    from groq import Groq
except ImportError:
    Groq = None

LOGGER = logging.getLogger("efootball_bot.gazette")

# In-memory edition cache: week_key -> GazetteEdition
GAZETTE_CACHE: Dict[str, GazetteEdition] = {}

# Tracks the last week auto-published to avoid duplicate announcements
LAST_PUBLISHED_WEEK_KEY: Optional[str] = None


def resolve_api_keys(api_key: Optional[str] = None) -> List[str]:
    """Resolves primary and fallback API keys from arguments and environment."""
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
class GazetteEdition:
    """Structured weekly sports newspaper issue."""

    week_key: str
    issue_number: int
    headline: str
    lead_story: str
    matchday_digest: str
    locker_room_rumor: str
    stat_of_the_week: str
    quote_of_the_week: str
    is_ai_generated: bool = True


def _get_current_week_key() -> str:
    """Returns ISO calendar week string, e.g. '2026-W47'."""
    now = datetime.now(timezone.utc)
    iso_year, iso_week, _ = now.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _filter_matches_for_week(
    matches: List[MatchRecord],
    week_key: Optional[str] = None,
) -> Tuple[str, List[MatchRecord]]:
    """Filters matches by ISO week or falls back to recent matches if insufficient data."""
    target_key = week_key or _get_current_week_key()
    week_matches: List[MatchRecord] = []

    for m in matches:
        if not m.date:
            continue
        try:
            # Parse 'YYYY-MM-DD HH:MM:SS' or ISO format
            dt = datetime.fromisoformat(m.date.replace("Z", "+00:00"))
        except Exception:
            try:
                dt = datetime.strptime(m.date[:19], "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue

        iso_y, iso_w, _ = dt.isocalendar()
        if f"{iso_y}-W{iso_w:02d}" == target_key:
            week_matches.append(m)

    # If target week has fewer than 2 matches, use the last 5-10 matches
    if len(week_matches) < 2 and len(matches) >= 2:
        week_matches = matches[-8:]

    return target_key, week_matches


def _summarize_match_week(week_matches: List[MatchRecord]) -> dict:
    """Aggregates player and match statistics for the target period."""
    player_goals: Dict[str, int] = {}
    player_assists: Dict[str, int] = {}
    player_wins: Dict[str, int] = {}
    player_matches: Dict[str, int] = {}

    for m in week_matches:
        for ps in m.player_stats:
            p = ps.player
            player_goals[p] = player_goals.get(p, 0) + ps.goals
            player_assists[p] = player_assists.get(p, 0) + ps.assists
            player_matches[p] = player_matches.get(p, 0) + 1
            if ps.result in (MatchResult.WIN, MatchResult.HOME_WIN):
                player_wins[p] = player_wins.get(p, 0) + 1

    top_scorer = max(player_goals.items(), key=lambda x: x[1]) if player_goals else ("None", 0)
    top_assister = max(player_assists.items(), key=lambda x: x[1]) if player_assists else ("None", 0)
    most_active = max(player_matches.items(), key=lambda x: x[1]) if player_matches else ("None", 0)

    # Find biggest scoreline
    biggest_match = week_matches[-1] if week_matches else None

    return {
        "match_count": len(week_matches),
        "top_scorer": top_scorer,
        "top_assister": top_assister,
        "most_active": most_active,
        "active_players": list(player_matches.keys()),
        "biggest_match": biggest_match,
    }


def _rule_based_fallback_edition(week_key: str, summary: dict, issue_num: int) -> GazetteEdition:
    """Deterministic, zero-token newspaper edition."""
    scorer_name, scorer_goals = summary["top_scorer"]
    playmaker_name, playmaker_assists = summary["top_assister"]
    active = summary["active_players"]

    second_player = active[1] if len(active) > 1 else "The Squad"

    headline = f"WEEKLY MAYHEM: {scorer_name.upper()} RUNS THE PITCH AS RIVALS SEEK ANSWERS!"
    lead_story = (
        f"A pulsating week of co-op action saw {summary['match_count']} fixtures contested across the lobby. "
        f"{scorer_name} spearheaded the attack with {scorer_goals} goal(s), leaving defenders clutching at shadows. "
        f"Meanwhile, {playmaker_name} pulled the strings from the midfield, dishing out {playmaker_assists} assist(s)."
    )
    digest = (
        f"• **Fixtures Played:** {summary['match_count']}\n"
        f"• **Golden Boot of the Week:** {scorer_name} ({scorer_goals} Goals)\n"
        f"• **Playmaker of the Week:** {playmaker_name} ({playmaker_assists} Assists)\n"
        f"• **Active Lineup:** {', '.join(active[:6])}"
    )
    rumor = (
        f"Insiders claim {second_player} was spotted testing third-party controllers in practice after "
        f"complaining of 'unprecedented script delay' during Friday's late fixture."
    )
    stat = (
        f"{scorer_name} scored more goals this week ({scorer_goals}) than the bottom three players combined."
    )
    quote = (
        f'"{scorer_name} has the finishing touch right now, but wait until the ping spikes next weekend." — Anonymous Teammate'
    )

    return GazetteEdition(
        week_key=week_key,
        issue_number=issue_num,
        headline=headline,
        lead_story=lead_story,
        matchday_digest=digest,
        locker_room_rumor=rumor,
        stat_of_the_week=stat,
        quote_of_the_week=quote,
        is_ai_generated=False,
    )


async def generate_gazette_edition(
    matches: List[MatchRecord],
    week_key: Optional[str] = None,
    api_key: Optional[str] = None,
    model: str = "qwen/qwen3.8-27b",
    timeout_seconds: float = 20.0,
) -> GazetteEdition:
    """Generates or retrieves a cached weekly Gazette edition."""
    resolved_week, week_matches = _filter_matches_for_week(matches, week_key)

    if resolved_week in GAZETTE_CACHE:
        return GAZETTE_CACHE[resolved_week]

    summary = _summarize_match_week(week_matches)
    issue_num = len(matches) // 5 + 1

    resolved_keys = resolve_api_keys(api_key)
    if not resolved_keys or not week_matches:
        edition = _rule_based_fallback_edition(resolved_week, summary, issue_num)
        GAZETTE_CACHE[resolved_week] = edition
        return edition

    scorer_name, scorer_goals = summary["top_scorer"]
    playmaker_name, playmaker_assists = summary["top_assister"]
    active_str = ", ".join(summary["active_players"])

    prompt = (
        "You are the chief sports editor of 'The Co-op Gazette', a satirical sports newspaper for an eFootball co-op friend group.\n"
        f"Week: {resolved_week} (Issue #{issue_num})\n"
        f"Fixtures Contested: {summary['match_count']}\n"
        f"Top Scorer: {scorer_name} ({scorer_goals} goals)\n"
        f"Top Playmaker: {playmaker_name} ({playmaker_assists} assists)\n"
        f"Active Players: {active_str}\n\n"
        "Generate a JSON object with EXACTLY five concise string fields (keep total length under 250 words to minimize tokens):\n"
        "- 'headline': Punchy, sensationalist front-page sports tabloid headline (ALL CAPS).\n"
        "- 'lead_story': 2-3 sentence humorous breakdown of this week's top performer and biggest clash.\n"
        "- 'locker_room_rumor': 1-2 sentence satirical transfer rumor, contract dispute, or locker room gossip.\n"
        "- 'stat_of_the_week': 1 funny or exaggerated statistical comparison featuring one of the players.\n"
        "- 'quote_of_the_week': A fake post-match quote from a player offering excuses (lag, script, controller drift).\n"
        "CRITICAL: Do NOT use internal double quotes (\") inside any field values. Use single quotes (') instead. Output valid JSON only."
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
                        temperature=0.75,
                    )
                    content_str = response.choices[0].message.content
                    if content_str:
                        break
                except Exception as exc:
                    LOGGER.warning("Gazette key #%d failed via SDK: %s; trying HTTP/failover", attempt_idx + 1, exc)
                    body = getattr(exc, "body", None)
                    if isinstance(body, dict) and "failed_generation" in body and body["failed_generation"]:
                        content_str = body["failed_generation"]
                        break

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
                        "temperature": 0.75,
                    }
                    with httpx.Client(timeout=timeout_seconds) as http_client:
                        r = http_client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=body)
                        if r.status_code == 200:
                            data = r.json()
                            content_str = data["choices"][0]["message"]["content"]
                            if content_str:
                                break
                        else:
                            LOGGER.warning("Gazette API key #%d returned HTTP %d: %s", attempt_idx + 1, r.status_code, r.text[:200])
                except Exception as exc:
                    LOGGER.warning("Gazette key #%d failed via HTTP: %s", attempt_idx + 1, exc)

        if content_str:
            clean_json = content_str.strip()
            if clean_json.startswith("```json"):
                clean_json = clean_json[7:]
            if clean_json.startswith("```"):
                clean_json = clean_json[3:]
            if clean_json.endswith("```"):
                clean_json = clean_json[:-3]
            try:
                parsed = json.loads(clean_json.strip())
            except Exception:
                parsed = {}
                for fkey, fpat in [
                    ("headline", r'"headline"\s*:\s*"((?:[^"\\]|\\.)*)"'),
                    ("lead_story", r'"lead_story"\s*:\s*"((?:[^"\\]|\\.)*)"'),
                    ("locker_room_rumor", r'"locker_room_rumor"\s*:\s*"((?:[^"\\]|\\.)*)"'),
                    ("stat_of_the_week", r'"stat_of_the_week"\s*:\s*"((?:[^"\\]|\\.)*)"'),
                    ("quote_of_the_week", r'"quote_of_the_week"\s*:\s*"((?:[^"\\]|\\.)*)"'),
                ]:
                    m = re.search(fpat, clean_json, re.DOTALL)
                    if m:
                        parsed[fkey] = m.group(1).replace('\\"', '"')

            headline = str(parsed.get("headline", "")).strip()
            lead = str(parsed.get("lead_story", "")).strip()
            rumor = str(parsed.get("locker_room_rumor", "")).strip()
            stat = str(parsed.get("stat_of_the_week", "")).strip()
            quote = str(parsed.get("quote_of_the_week", "")).strip()

            digest = (
                f"• **Fixtures Contested:** {summary['match_count']}\n"
                f"• **Golden Boot:** {scorer_name} ({scorer_goals} Goals)\n"
                f"• **Chief Playmaker:** {playmaker_name} ({playmaker_assists} Assists)\n"
                f"• **Roster:** {', '.join(summary['active_players'][:6])}"
            )

            if headline and lead:
                edition = GazetteEdition(
                    week_key=resolved_week,
                    issue_number=issue_num,
                    headline=headline,
                    lead_story=lead,
                    matchday_digest=digest,
                    locker_room_rumor=rumor or "Transfer windows remain active as managers scout fresh co-op talent.",
                    stat_of_the_week=stat or f"{scorer_name} recorded {scorer_goals} goals this week.",
                    quote_of_the_week=quote or '"The controller simply lost connection during the penalty." — Anonymous',
                    is_ai_generated=True,
                )
                GAZETTE_CACHE[resolved_week] = edition
                return edition
    except Exception as exc:
        LOGGER.warning("Error generating AI Gazette edition: %s; falling back to template", exc)

    edition = _rule_based_fallback_edition(resolved_week, summary, issue_num)
    GAZETTE_CACHE[resolved_week] = edition
    return edition


def _rule_based_fallback_match_gazette(
    match: MatchRecord, scoreline: str, issue_num: int = 1
) -> GazetteEdition:
    """Deterministic, zero-token matchday special tabloid edition."""
    all_players = match.player_stats or []
    best_player = max(all_players, key=lambda p: (p.goals * 2 + p.assists, p.goals)) if all_players else None

    loss_stats = [p for p in all_players if p.result in (MatchResult.LOSS, MatchResult.AWAY_WIN)]
    worst_player = None
    if loss_stats:
        worst_player = min(loss_stats, key=lambda p: (p.goals * 2 + p.assists, p.goals))
    elif all_players:
        worst_player = min(all_players, key=lambda p: (p.goals * 2 + p.assists, p.goals))

    star = best_player.player if best_player else "The Squad"
    star_goals = best_player.goals if best_player else 0
    star_assists = best_player.assists if best_player else 0
    victim = worst_player.player if worst_player else "The Opposition"

    headline = f"MATCHDAY SPECIAL: {star.upper()} STEALS THE SHOW IN {match.match_id} THRILLER!"
    lead_story = (
        f"In a fiercely contested clash ({scoreline}), {star} took center stage with {star_goals} goal(s) and {star_assists} assist(s), "
        f"dictating the tempo from start to finish. Meanwhile, questions mount over {victim}'s tactical discipline as the defense crumbled."
    )
    digest = (
        f"• **Scoreline:** {scoreline}\n"
        f"• **Outcome:** {match.result.value}\n"
        f"• **Star Performer:** {star} ({star_goals}G, {star_assists}A)\n"
        f"• **Recorded By:** {match.submitted_by or 'Unknown'} ({match.date or 'Recently'})"
    )
    rumor = (
        f"Paddock insiders report heated exchanges in voice chat immediately following the final whistle, with rumors that {victim} "
        f"threatened to swap controllers after experiencing 'unbelievable input delay'."
    )
    stat = (
        f"{star} was directly involved in {star_goals + star_assists} goal(s) during this single fixture."
    )
    quote = (
        f'"{victim} looked like he was playing with inverted controls out there." — Anonymous Teammate'
    )

    return GazetteEdition(
        week_key=f"Match {match.match_id}",
        issue_number=issue_num,
        headline=headline,
        lead_story=lead_story,
        matchday_digest=digest,
        locker_room_rumor=rumor,
        stat_of_the_week=stat,
        quote_of_the_week=quote,
        is_ai_generated=False,
    )


async def generate_match_gazette(
    match: MatchRecord,
    scoreline: str,
    api_key: Optional[str] = None,
    model: str = "qwen/qwen3.8-27b",
    timeout_seconds: float = 20.0,
) -> GazetteEdition:
    """Generates or retrieves a cached single-match Gazette edition."""
    cache_key = f"MATCH_{match.match_id}"
    if cache_key in GAZETTE_CACHE:
        return GAZETTE_CACHE[cache_key]

    resolved_keys = resolve_api_keys(api_key)
    if not resolved_keys or not match.player_stats:
        edition = _rule_based_fallback_match_gazette(match, scoreline)
        GAZETTE_CACHE[cache_key] = edition
        return edition

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
    if not stats_summary and match.player_stats:
        stats_summary.append("Players: " + ", ".join(f"{p.player} ({p.goals}G, {p.assists}A)" for p in match.player_stats))

    prompt = (
        "You are the chief sports editor of 'The Co-op Gazette', a satirical sports tabloid for an eFootball co-op friend group.\n"
        f"Match ID: {match.match_id}\n"
        f"Scoreline: {scoreline} ({match.result.value})\n"
        f"Match Stats: {' | '.join(stats_summary)}\n\n"
        "Generate a JSON object with EXACTLY five concise string fields reviewing THIS SPECIFIC MATCH ONLY (keep total length under 250 words):\n"
        "- 'headline': Punchy, sensationalist front-page sports tabloid headline (ALL CAPS) specifically about this match's winner/loser.\n"
        "- 'lead_story': 2-3 sentence humorous breakdown of this match's pivotal moment, clutch play, or tactical collapse.\n"
        "- 'locker_room_rumor': 1-2 sentence satirical post-match drama, voice chat excuse, or fake controller malfunction rumor.\n"
        "- 'stat_of_the_match': 1 funny or exaggerated statistical comparison about a player in this match.\n"
        "- 'quote_of_the_match': A fake post-match excuse quote from a player (lag, script, controller drift).\n"
        "CRITICAL: Do NOT use internal double quotes (\") inside any field values. Use single quotes (') instead. Output valid JSON only."
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
                        temperature=0.75,
                    )
                    content_str = response.choices[0].message.content
                    if content_str:
                        break
                except Exception as exc:
                    LOGGER.warning("Match Gazette key #%d failed via SDK: %s; trying HTTP/failover", attempt_idx + 1, exc)
                    body = getattr(exc, "body", None)
                    if isinstance(body, dict) and "failed_generation" in body and body["failed_generation"]:
                        content_str = body["failed_generation"]
                        break

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
                        "temperature": 0.75,
                    }
                    with httpx.Client(timeout=timeout_seconds) as http_client:
                        r = http_client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=body)
                        if r.status_code == 200:
                            data = r.json()
                            content_str = data["choices"][0]["message"]["content"]
                            if content_str:
                                break
                        else:
                            LOGGER.warning("Match Gazette API key #%d returned HTTP %d: %s", attempt_idx + 1, r.status_code, r.text[:200])
                except Exception as exc:
                    LOGGER.warning("Match Gazette key #%d failed via HTTP: %s", attempt_idx + 1, exc)

        if content_str:
            clean_json = content_str.strip()
            if clean_json.startswith("```json"):
                clean_json = clean_json[7:]
            if clean_json.startswith("```"):
                clean_json = clean_json[3:]
            if clean_json.endswith("```"):
                clean_json = clean_json[:-3]
            try:
                parsed = json.loads(clean_json.strip())
            except Exception:
                parsed = {}
                for fkey, fpat in [
                    ("headline", r'"headline"\s*:\s*"((?:[^"\\]|\\.)*)"'),
                    ("lead_story", r'"lead_story"\s*:\s*"((?:[^"\\]|\\.)*)"'),
                    ("locker_room_rumor", r'"locker_room_rumor"\s*:\s*"((?:[^"\\]|\\.)*)"'),
                    ("stat_of_the_match", r'"stat_of_the_match"\s*:\s*"((?:[^"\\]|\\.)*)"'),
                    ("quote_of_the_match", r'"quote_of_the_match"\s*:\s*"((?:[^"\\]|\\.)*)"'),
                ]:
                    m = re.search(fpat, clean_json, re.DOTALL)
                    if m:
                        parsed[fkey] = m.group(1).replace('\\"', '"')

            headline = str(parsed.get("headline", "")).strip()
            lead = str(parsed.get("lead_story", "")).strip()
            rumor = str(parsed.get("locker_room_rumor", "")).strip()
            stat = str(parsed.get("stat_of_the_match", parsed.get("stat_of_the_week", ""))).strip()
            quote = str(parsed.get("quote_of_the_match", parsed.get("quote_of_the_week", ""))).strip()

            digest = (
                f"• **Scoreline:** {scoreline}\n"
                f"• **Outcome:** {match.result.value}\n"
                f"• **Lineup:** {', '.join(p.player for p in match.player_stats[:6])}\n"
                f"• **Recorded By:** {match.submitted_by or 'Unknown'} ({match.date or 'Recently'})"
            )

            if headline and lead:
                edition = GazetteEdition(
                    week_key=f"Match {match.match_id}",
                    issue_number=1,
                    headline=headline,
                    lead_story=lead,
                    matchday_digest=digest,
                    locker_room_rumor=rumor or "Voice chat tensions simmered as post-match analysis got underway.",
                    stat_of_the_week=stat or f"Total shots and stats logged for match {match.match_id}.",
                    quote_of_the_week=quote or '"My controller simply stopped responding in the box." — Anonymous',
                    is_ai_generated=True,
                )
                GAZETTE_CACHE[cache_key] = edition
                return edition
    except Exception as exc:
        LOGGER.warning("Error generating AI Match Gazette edition: %s; falling back to template", exc)

    edition = _rule_based_fallback_match_gazette(match, scoreline)
    GAZETTE_CACHE[cache_key] = edition
    return edition


def build_gazette_embed(edition: GazetteEdition, match: Optional[MatchRecord] = None) -> discord.Embed:
    """Builds a newspaper-style Discord embed for The Co-op Gazette."""
    is_match_edition = match is not None or edition.week_key.startswith("Match")

    if is_match_edition:
        m_id = match.match_id if match else edition.week_key
        title = f"🗞️ THE CO-OP GAZETTE — Matchday Special: {m_id}"
        field_digest_name = "📊 Matchday Summary"
        field_rumor_name = "🕵️ Locker Room Whispers & Post-Match Drama"
        field_stat_name = "📈 Stat of the Match"
        field_quote_name = "💬 Quote of the Match"
    else:
        title = f"🗞️ THE CO-OP GAZETTE — Issue #{edition.issue_number}"
        field_digest_name = "📊 The Matchday Digest"
        field_rumor_name = "🕵️ Locker Room Whispers & Transfer Talk"
        field_stat_name = "📈 Stat of the Week"
        field_quote_name = "💬 Quote of the Week"

    embed = discord.Embed(
        title=title,
        description=f"# 📢 {edition.headline}\n\n{edition.lead_story}",
        color=discord.Color.from_rgb(44, 47, 51),
        timestamp=datetime.now(),
    )

    embed.add_field(
        name=field_digest_name,
        value=edition.matchday_digest,
        inline=False,
    )
    embed.add_field(
        name=field_rumor_name,
        value=edition.locker_room_rumor,
        inline=False,
    )
    embed.add_field(
        name=field_stat_name,
        value=edition.stat_of_the_week,
        inline=True,
    )
    embed.add_field(
        name=field_quote_name,
        value=edition.quote_of_the_week,
        inline=True,
    )

    footer = f"The Co-op Gazette • Edition {edition.week_key}"
    if edition.is_ai_generated:
        footer += " • AI Press Room"
    else:
        footer += " • Editorial Desk"
    footer += " • /gazette"

    embed.set_footer(text=footer)
    return embed
