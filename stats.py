"""Statistical aggregation and leaderboard computation for eFootball co-op."""

from __future__ import annotations

import calendar
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


def get_calendar_month_bounds(
    reference_date: Optional[datetime] = None,
    months_ago: int = 0,
) -> Tuple[datetime, datetime]:
    """Returns (1st day 00:00:00, last day 23:59:59.999999) for the calendar month."""
    ref = reference_date or datetime.now()
    total_months = ref.year * 12 + (ref.month - 1) - max(0, months_ago)
    target_year, target_month_zero = divmod(total_months, 12)
    target_month = target_month_zero + 1
    last_day = calendar.monthrange(target_year, target_month)[1]
    month_start = datetime(target_year, target_month, 1, 0, 0, 0)
    month_end = datetime(target_year, target_month, last_day, 23, 59, 59, 999999)
    return month_start, month_end


def _calculate_weighted_rankings(
    matches: List[MatchRecord],
    start_dt: datetime,
    end_dt: datetime,
) -> List[dict]:
    """Computes weighted player rankings within [start_dt, end_dt] using 0.4G + 0.4A + 0.2W."""
    period_stats: Dict[str, dict] = {}

    for match in matches:
        match_dt = parse_match_datetime(match.date)
        if match_dt is None or not (start_dt <= match_dt <= end_dt):
            continue

        for stat in match.player_stats:
            canonical = canonicalize_player_name(stat.player)
            key = canonical.strip().lower()
            if not key:
                continue

            if key not in period_stats:
                period_stats[key] = {
                    "player": canonical,
                    "matches_played": 0,
                    "goals": 0,
                    "assists": 0,
                    "wins": 0,
                    "losses": 0,
                }

            entry = period_stats[key]
            entry["matches_played"] += 1
            entry["goals"] += stat.goals
            entry["assists"] += stat.assists
            if stat.result in (MatchResult.HOME_WIN, MatchResult.WIN):
                entry["wins"] += 1
            elif stat.result in (MatchResult.AWAY_WIN, MatchResult.LOSS):
                entry["losses"] += 1

    ranked: List[dict] = []
    for entry in period_stats.values():
        entry["score"] = round(
            0.4 * entry["goals"] + 0.4 * entry["assists"] + 0.2 * entry["wins"],
            2,
        )
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
    return ranked


def calculate_potw_rankings(
    matches: List[MatchRecord],
    reference_date: Optional[datetime] = None,
    weeks_ago: int = 0,
) -> Tuple[List[dict], datetime, datetime]:
    """Computes Player of the Week rankings using 0.4*Goals + 0.4*Assists + 0.2*Wins."""
    week_start, week_end = get_calendar_week_bounds(reference_date=reference_date, weeks_ago=weeks_ago)
    return _calculate_weighted_rankings(matches, week_start, week_end), week_start, week_end


def calculate_potm_rankings(
    matches: List[MatchRecord],
    reference_date: Optional[datetime] = None,
    months_ago: int = 0,
) -> Tuple[List[dict], datetime, datetime]:
    """Computes Player of the Month rankings using 0.4*Goals + 0.4*Assists + 0.2*Wins."""
    month_start, month_end = get_calendar_month_bounds(reference_date=reference_date, months_ago=months_ago)
    return _calculate_weighted_rankings(matches, month_start, month_end), month_start, month_end


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


def compute_player_rating(record: dict | PlayerAggregateRecord) -> float:
    """Computes a balanced historical skill rating from Win%, GAPG, and GPG."""
    if isinstance(record, dict):
        mp = int(record.get("matches_played", 0))
        wr = float(record.get("win_rate", 0.0))
        gapg = float(record.get("gapg", 0.0))
        gpg = float(record.get("gpg", 0.0))
    else:
        mp = record.matches_played
        wr = record.win_rate
        gapg = record.gapg
        gpg = record.gpg

    if mp <= 0:
        return 0.0
    return round(0.4 * wr + 15.0 * gapg + 5.0 * gpg, 2)


def generate_fair_teams(
    player_names: List[str],
    leaderboard_records: List[dict],
) -> dict:
    """Splits 4, 5, or 6 players into the fairest possible Team A vs Team B matchup."""
    canonical_players: List[str] = []
    seen_keys: set[str] = set()
    for raw in player_names:
        c = canonicalize_player_name(raw)
        if not c:
            continue
        k = c.lower()
        if k in seen_keys:
            raise ValueError(f"Duplicate player '{c}' detected. Please enter unique player names.")
        seen_keys.add(k)
        canonical_players.append(c)

    count = len(canonical_players)
    if count not in (4, 5, 6):
        raise ValueError(f"Expected 4, 5, or 6 unique players, got {count}.")

    ratings_by_key: Dict[str, float] = {}
    ranked_ratings: List[float] = []
    for rec in leaderboard_records:
        r_val = compute_player_rating(rec)
        key = canonicalize_player_name(str(rec.get("player", ""))).lower()
        if key:
            ratings_by_key[key] = r_val
        if int(rec.get("matches_played", 0)) > 0:
            ranked_ratings.append(r_val)

    default_rating = round(sum(ranked_ratings) / len(ranked_ratings), 2) if ranked_ratings else 40.0

    player_entries = [
        {
            "player": name,
            "rating": ratings_by_key.get(name.lower(), 0.0) or default_rating,
            "is_unranked": ratings_by_key.get(name.lower(), 0.0) == 0.0,
        }
        for name in canonical_players
    ]

    team_a_size = 2 if count == 4 else 3
    best_split = None
    best_key = None

    indices = list(range(count))
    for combo in combinations(indices, team_a_size):
        # Avoid symmetric mirror partitions when teams are equal size
        if count in (4, 6) and 0 not in combo:
            continue
        combo_set = set(combo)
        team_a = [player_entries[i] for i in combo]
        team_b = [player_entries[i] for i in indices if i not in combo_set]
        sum_a = round(sum(p["rating"] for p in team_a), 2)
        sum_b = round(sum(p["rating"] for p in team_b), 2)
        diff = round(abs(sum_a - sum_b), 2)
        avg_diff = round(abs((sum_a / len(team_a)) - (sum_b / len(team_b))), 2)
        score_key = (diff, avg_diff)
        if best_key is None or score_key < best_key:
            best_key = score_key
            best_split = {
                "team_a": sorted(team_a, key=lambda x: x["rating"], reverse=True),
                "team_b": sorted(team_b, key=lambda x: x["rating"], reverse=True),
                "team_a_rating": sum_a,
                "team_b_rating": sum_b,
                "rating_diff": diff,
            }

    assert best_split is not None
    return best_split


def calculate_player_form(
    player_name: str,
    matches: List[MatchRecord],
    window: int = 5,
) -> dict:
    """Computes a player's recent form (last N matches) and win/loss streaks."""
    canonical = canonicalize_player_name(player_name)
    target_key = canonical.strip().lower()

    appearances: List[dict] = []
    for match in matches:
        for stat in match.player_stats:
            if canonicalize_player_name(stat.player).strip().lower() == target_key:
                if stat.result in (MatchResult.WIN, MatchResult.HOME_WIN):
                    outcome = "W"
                elif stat.result in (MatchResult.LOSS, MatchResult.AWAY_WIN):
                    outcome = "L"
                else:
                    outcome = "D"
                appearances.append(
                    {
                        "match": match,
                        "outcome": outcome,
                        "goals": stat.goals,
                        "assists": stat.assists,
                    }
                )
                break

    longest_win_streak = 0
    longest_unbeaten_streak = 0
    running_win = 0
    running_unbeaten = 0
    for app in appearances:
        if app["outcome"] == "W":
            running_win += 1
            running_unbeaten += 1
            longest_win_streak = max(longest_win_streak, running_win)
            longest_unbeaten_streak = max(longest_unbeaten_streak, running_unbeaten)
        elif app["outcome"] == "D":
            running_win = 0
            running_unbeaten += 1
            longest_unbeaten_streak = max(longest_unbeaten_streak, running_unbeaten)
        else:
            running_win = 0
            running_unbeaten = 0

    current_streak_type = ""
    current_streak_count = 0
    if appearances:
        current_streak_type = appearances[-1]["outcome"]
        for app in reversed(appearances):
            if app["outcome"] == current_streak_type:
                current_streak_count += 1
            else:
                break

    recent = list(reversed(appearances[-max(1, window):]))
    return {
        "player": canonical,
        "total_matches": len(appearances),
        "recent": recent,
        "form_list": [a["outcome"] for a in recent],
        "current_streak_type": current_streak_type,
        "current_streak_count": current_streak_count,
        "longest_win_streak": longest_win_streak,
        "longest_unbeaten_streak": longest_unbeaten_streak,
    }
