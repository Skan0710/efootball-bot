"""Statistical aggregation and leaderboard computation for eFootball co-op."""

from __future__ import annotations

from datetime import datetime, timedelta
from itertools import combinations
from typing import Dict, List, Optional, Tuple

from models import (
    LeaderboardCategory,
    MatchRecord,
    MatchResult,
    PlayerAggregateRecord,
    PlayerStat,
    canonicalize_player_name,
)


def calculate_player_aggregates(player_stats: List[PlayerStat]) -> Dict[str, PlayerAggregateRecord]:
    """Calculates cumulative career statistics for each player from raw match entries.

    Groups case-insensitively while preserving the most recent display name casing.
    Safely calculates rates and handles zero matches.
    """
    player_data: Dict[str, dict] = {}

    for stat in player_stats:
        normalized = stat.player.strip().lower()
        if not normalized:
            continue

        if normalized not in player_data:
            player_data[normalized] = {
                "display_name": stat.player.strip(),
                "matches": 0,
                "goals": 0,
                "assists": 0,
                "wins": 0,
                "losses": 0,
                "draws": 0,
                "hattricks": 0,
            }
        else:
            # Keep latest non-empty casing
            player_data[normalized]["display_name"] = stat.player.strip()

        data = player_data[normalized]
        data["matches"] += 1
        data["goals"] += stat.goals
        data["assists"] += stat.assists
        if stat.goals >= 3:
            data["hattricks"] += 1

        if stat.result in (MatchResult.HOME_WIN, MatchResult.WIN):
            data["wins"] += 1
        elif stat.result in (MatchResult.AWAY_WIN, MatchResult.LOSS):
            data["losses"] += 1
        elif stat.result == MatchResult.DRAW:
            data["draws"] += 1

    records: Dict[str, PlayerAggregateRecord] = {}
    for norm_name, d in player_data.items():
        records[norm_name] = PlayerAggregateRecord.compute(
            player=d["display_name"],
            matches_played=d["matches"],
            goals=d["goals"],
            assists=d["assists"],
            wins=d["wins"],
            losses=d["losses"],
            draws=d["draws"],
            hattricks=d["hattricks"],
        )

    return records


def get_player_record(
    player_name: str, aggregates: Dict[str, PlayerAggregateRecord]
) -> Optional[PlayerAggregateRecord]:
    """Performs case-insensitive lookup of a player's career record."""
    canonical = canonicalize_player_name(player_name)
    normalized = canonical.strip().lower()
    return aggregates.get(normalized) or aggregates.get(player_name.strip().lower())


def get_leaderboard(
    aggregates: Dict[str, PlayerAggregateRecord],
    category: LeaderboardCategory | str,
) -> List[PlayerAggregateRecord]:
    """Ranks players by the requested leaderboard category.

    Deterministic tie-breaking is applied to guarantee consistent rankings.
    """
    if isinstance(category, str):
        category = LeaderboardCategory.from_str(category)

    records = list(aggregates.values())

    if category == LeaderboardCategory.GOALS:
        records.sort(
            key=lambda r: (r.goals, r.assists, r.win_rate, r.matches_played, -len(r.player)),
            reverse=True,
        )
    elif category == LeaderboardCategory.ASSISTS:
        records.sort(
            key=lambda r: (r.assists, r.goals, r.win_rate, r.matches_played, -len(r.player)),
            reverse=True,
        )
    elif category == LeaderboardCategory.WINS:
        records.sort(
            key=lambda r: (r.wins, r.win_rate, r.matches_played, r.goals),
            reverse=True,
        )
    elif category == LeaderboardCategory.LOSSES:
        records.sort(
            key=lambda r: (r.losses, r.loss_rate, r.matches_played),
            reverse=True,
        )
    elif category == LeaderboardCategory.DRAWS:
        records.sort(
            key=lambda r: (r.draws, r.matches_played),
            reverse=True,
        )
    elif category == LeaderboardCategory.WIN_RATE:
        records.sort(
            key=lambda r: (r.win_rate, r.wins, r.matches_played, r.goals),
            reverse=True,
        )
    elif category == LeaderboardCategory.MATCHES_PLAYED:
        records.sort(
            key=lambda r: (r.matches_played, r.wins, r.goals, r.win_rate),
            reverse=True,
        )
    elif category == LeaderboardCategory.GA:
        records.sort(
            key=lambda r: (r.ga, r.goals, r.assists, r.matches_played),
            reverse=True,
        )
    elif category == LeaderboardCategory.GPG:
        records.sort(
            key=lambda r: (r.gpg, r.goals, r.matches_played),
            reverse=True,
        )
    elif category == LeaderboardCategory.APG:
        records.sort(
            key=lambda r: (r.apg, r.assists, r.matches_played),
            reverse=True,
        )
    elif category == LeaderboardCategory.GAPG:
        records.sort(
            key=lambda r: (r.gapg, r.ga, r.goals, r.matches_played),
            reverse=True,
        )
    elif category == LeaderboardCategory.HATTRICK:
        records.sort(
            key=lambda r: (r.hattricks, r.goals, r.matches_played),
            reverse=True,
        )
    else:
        # Fallback to goals
        records.sort(key=lambda r: (r.goals, r.assists), reverse=True)

    return records


def get_all_teammates(
    aggregates: Dict[str, PlayerAggregateRecord],
) -> List[PlayerAggregateRecord]:
    """Returns all registered players sorted alphabetically by name."""
    return sorted(aggregates.values(), key=lambda r: r.player.lower())


def parse_match_datetime(date_str: str) -> Optional[datetime]:
    """Parses a stored match timestamp into a datetime object."""
    if not date_str:
        return None
    raw = date_str.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def get_calendar_week_bounds(
    reference_date: Optional[datetime] = None,
    weeks_ago: int = 0,
) -> Tuple[datetime, datetime]:
    """Returns (Monday 00:00:00, Sunday 23:59:59.999999) for the calendar week."""
    ref = (reference_date or datetime.now()) - timedelta(days=max(0, weeks_ago) * 7)
    monday = (ref - timedelta(days=ref.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    sunday = monday + timedelta(days=6, hours=23, minutes=59, seconds=59, microseconds=999999)
    return monday, sunday


def calculate_potw_rankings(
    matches: List[MatchRecord],
    reference_date: Optional[datetime] = None,
    weeks_ago: int = 0,
) -> Tuple[List[dict], datetime, datetime]:
    """Computes Player of the Week rankings using 0.4*Goals + 0.4*Assists + 0.2*Wins."""
    week_start, week_end = get_calendar_week_bounds(reference_date=reference_date, weeks_ago=weeks_ago)
    weekly_stats: Dict[str, dict] = {}

    for match in matches:
        match_dt = parse_match_datetime(match.date)
        if match_dt is None or not (week_start <= match_dt <= week_end):
            continue

        for stat in match.player_stats:
            canonical = canonicalize_player_name(stat.player)
            key = canonical.strip().lower()
            if not key:
                continue

            if key not in weekly_stats:
                weekly_stats[key] = {
                    "player": canonical,
                    "matches_played": 0,
                    "goals": 0,
                    "assists": 0,
                    "wins": 0,
                    "losses": 0,
                }

            entry = weekly_stats[key]
            entry["matches_played"] += 1
            entry["goals"] += stat.goals
            entry["assists"] += stat.assists
            if stat.result in (MatchResult.HOME_WIN, MatchResult.WIN):
                entry["wins"] += 1
            elif stat.result in (MatchResult.AWAY_WIN, MatchResult.LOSS):
                entry["losses"] += 1

    ranked: List[dict] = []
    for entry in weekly_stats.values():
        score = round(
            0.4 * entry["goals"] + 0.4 * entry["assists"] + 0.2 * entry["wins"],
            2,
        )
        entry["score"] = score
        ranked.append(entry)

    ranked.sort(
        key=lambda r: (
            r["score"],
            r["goals"],
            r["assists"],
            r["wins"],
            -r["losses"],
            r["matches_played"],
        ),
        reverse=True,
    )
    return ranked, week_start, week_end


def calculate_best_teams(matches: List[MatchRecord]) -> List[dict]:
    """Calculates co-op teammate duo records ranked by most wins together.

    Order-independent and case-insensitive: e.g., 'Skan & Suri' and 'Suri & Skan'
    always resolve to the same teammate pair.
    """
    team_data: Dict[Tuple[str, str], dict] = {}

    def _record_side(side_stats: List[Tuple[str, str, PlayerStat]], is_win: bool) -> None:
        if len(side_stats) < 2:
            return
        for (k1, p1, s1), (k2, p2, s2) in combinations(side_stats, 2):
            if k1 <= k2:
                pair_key = (k1, k2)
                players = (p1, p2)
            else:
                pair_key = (k2, k1)
                players = (p2, p1)

            if pair_key not in team_data:
                team_data[pair_key] = {
                    "players": players,
                    "team_name": f"{players[0]} & {players[1]}",
                    "matches_played": 0,
                    "wins": 0,
                    "losses": 0,
                    "goals": 0,
                    "assists": 0,
                }
            entry = team_data[pair_key]
            entry["matches_played"] += 1
            if is_win:
                entry["wins"] += 1
            else:
                entry["losses"] += 1
            entry["goals"] += s1.goals + s2.goals
            entry["assists"] += s1.assists + s2.assists

    for match in matches:
        winners_map: Dict[str, Tuple[str, PlayerStat]] = {}
        losers_map: Dict[str, Tuple[str, PlayerStat]] = {}
        for stat in match.player_stats:
            canonical = canonicalize_player_name(stat.player)
            key = canonical.strip().lower()
            if not key:
                continue
            if stat.result in (MatchResult.WIN, MatchResult.HOME_WIN):
                winners_map[key] = (canonical, stat)
            elif stat.result in (MatchResult.LOSS, MatchResult.AWAY_WIN):
                losers_map[key] = (canonical, stat)

        _record_side(
            [(k, v[0], v[1]) for k, v in sorted(winners_map.items(), key=lambda it: it[0])],
            is_win=True,
        )
        _record_side(
            [(k, v[0], v[1]) for k, v in sorted(losers_map.items(), key=lambda it: it[0])],
            is_win=False,
        )

    teams: List[dict] = []
    for entry in team_data.values():
        mp = entry["matches_played"]
        entry["win_rate"] = round((entry["wins"] / mp) * 100.0, 1) if mp > 0 else 0.0
        teams.append(entry)

    teams.sort(
        key=lambda t: (
            t["wins"],
            t["win_rate"],
            t["matches_played"],
            t["goals"],
            t["assists"],
        ),
        reverse=True,
    )
    return teams
