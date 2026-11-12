"""Statistical aggregation and leaderboard computation for eFootball co-op."""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta
from itertools import combinations
import re
from typing import Dict, List, Optional, Tuple

from models import (
    LeaderboardCategory,
    MatchRecord,
    MatchResult,
    PlayerAggregateRecord,
    PlayerStat,
    canonicalize_player_name,
)


def get_match_player_conceded_goals(match: MatchRecord, stat: PlayerStat) -> int:
    """Returns how many goals the given player's side conceded in a match."""
    hs, a_s = extract_match_scores(match)
    p_won = stat.result in (MatchResult.WIN, MatchResult.HOME_WIN)
    p_lost = stat.result in (MatchResult.LOSS, MatchResult.AWAY_WIN)

    if match.result in (MatchResult.HOME_WIN, MatchResult.WIN):
        if p_won:
            return a_s
        if p_lost:
            return hs
    elif match.result == MatchResult.AWAY_WIN:
        if p_won:
            return hs
        if p_lost:
            return a_s
    elif match.result == MatchResult.DRAW:
        return a_s if p_won or stat.result == MatchResult.DRAW else hs

    return min(hs, a_s) if p_won else max(hs, a_s)


def calculate_player_conceded_and_clean_sheets(matches: List[MatchRecord]) -> Dict[str, dict]:
    """Calculates clean sheets and goals conceded per canonical lowercase player key."""
    stats_map: Dict[str, dict] = {}
    for match in matches:
        hs, a_s = extract_match_scores(match)
        for stat in match.player_stats:
            canonical = canonicalize_player_name(stat.player)
            key = canonical.strip().lower()
            if not key:
                continue
            entry = stats_map.setdefault(
                key,
                {"player": canonical, "clean_sheets": 0, "goals_conceded": 0, "matches": 0},
            )
            conceded = get_match_player_conceded_goals(match, stat)
            entry["goals_conceded"] += conceded
            entry["matches"] += 1
            is_win = stat.result in (MatchResult.WIN, MatchResult.HOME_WIN)
            is_zero_draw = match.result == MatchResult.DRAW and hs == 0 and a_s == 0
            if conceded == 0 and (is_win or is_zero_draw):
                entry["clean_sheets"] += 1
    return stats_map


def calculate_player_aggregates(
    player_stats: List[PlayerStat],
    matches: Optional[List[MatchRecord]] = None,
) -> Dict[str, PlayerAggregateRecord]:
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

    cs_map = calculate_player_conceded_and_clean_sheets(matches) if matches else {}
    records: Dict[str, PlayerAggregateRecord] = {}
    for norm_name, d in player_data.items():
        cs_count = cs_map.get(norm_name, {}).get("clean_sheets", 0)
        records[norm_name] = PlayerAggregateRecord.compute(
            player=d["display_name"],
            matches_played=d["matches"],
            goals=d["goals"],
            assists=d["assists"],
            wins=d["wins"],
            losses=d["losses"],
            draws=d["draws"],
            hattricks=d["hattricks"],
            clean_sheets=cs_count,
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
    elif category == LeaderboardCategory.CLEAN_SHEETS:
        records.sort(
            key=lambda r: (r.clean_sheets, r.wins, r.win_rate, r.matches_played),
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
        "history": appearances[-10:],
        "form_list": [a["outcome"] for a in recent],
        "current_streak_type": current_streak_type,
        "current_streak_count": current_streak_count,
        "longest_win_streak": longest_win_streak,
        "longest_unbeaten_streak": longest_unbeaten_streak,
    }


def calculate_head_to_head(
    player1: str,
    player2: str,
    matches: List[MatchRecord],
) -> dict:
    """Computes playing-together chemistry and playing-against rivalry stats for two players."""
    p1 = canonicalize_player_name(player1)
    p2 = canonicalize_player_name(player2)
    k1 = p1.strip().lower()
    k2 = p2.strip().lower()
    if not k1 or not k2:
        raise ValueError("Both player names must be non-empty.")
    if k1 == k2:
        raise ValueError(f"Please choose two different players (both resolved to '{p1}').")

    together = {"matches": 0, "wins": 0, "losses": 0, "p1_goals": 0, "p1_assists": 0, "p2_goals": 0, "p2_assists": 0}
    against = {"matches": 0, "p1_wins": 0, "p2_wins": 0, "p1_goals": 0, "p1_assists": 0, "p2_goals": 0, "p2_assists": 0}

    for match in matches:
        stats_by_key: Dict[str, PlayerStat] = {}
        for stat in match.player_stats:
            ck = canonicalize_player_name(stat.player).strip().lower()
            if ck:
                stats_by_key[ck] = stat

        if k1 not in stats_by_key or k2 not in stats_by_key:
            continue

        s1 = stats_by_key[k1]
        s2 = stats_by_key[k2]
        s1_win = s1.result in (MatchResult.WIN, MatchResult.HOME_WIN)
        s1_loss = s1.result in (MatchResult.LOSS, MatchResult.AWAY_WIN)
        s2_win = s2.result in (MatchResult.WIN, MatchResult.HOME_WIN)
        s2_loss = s2.result in (MatchResult.LOSS, MatchResult.AWAY_WIN)

        if (s1_win and s2_win) or (s1_loss and s2_loss):
            together["matches"] += 1
            if s1_win:
                together["wins"] += 1
            else:
                together["losses"] += 1
            together["p1_goals"] += s1.goals
            together["p1_assists"] += s1.assists
            together["p2_goals"] += s2.goals
            together["p2_assists"] += s2.assists
        elif (s1_win and s2_loss) or (s1_loss and s2_win):
            against["matches"] += 1
            if s1_win:
                against["p1_wins"] += 1
            else:
                against["p2_wins"] += 1
            against["p1_goals"] += s1.goals
            against["p1_assists"] += s1.assists
            against["p2_goals"] += s2.goals
            against["p2_assists"] += s2.assists

    t_mp = together["matches"]
    together["win_rate"] = round((together["wins"] / t_mp) * 100.0, 1) if t_mp > 0 else 0.0
    a_mp = against["matches"]
    against["p1_win_rate"] = round((against["p1_wins"] / a_mp) * 100.0, 1) if a_mp > 0 else 0.0
    against["p2_win_rate"] = round((against["p2_wins"] / a_mp) * 100.0, 1) if a_mp > 0 else 0.0

    return {
        "player1": p1,
        "player2": p2,
        "together": together,
        "against": against,
    }


def extract_match_scores(match: MatchRecord) -> Tuple[int, int]:
    """Extracts (home_score, away_score) from notes or player goal totals."""
    if match.notes:
        score_match = re.match(
            r"^\s*(?:Score:\s*)?(?P<left>.+?)\s+(?P<hs>\d+)\s*-\s*(?P<as>\d+)\s+(?P<right>.+?)(?:\s*\([^)]*\))?\s*$",
            match.notes.strip(),
            flags=re.IGNORECASE,
        )
        if score_match:
            return int(score_match.group("hs")), int(score_match.group("as"))

    win_goals = sum(
        p.goals for p in match.player_stats if p.result in (MatchResult.WIN, MatchResult.HOME_WIN)
    )
    loss_goals = sum(
        p.goals for p in match.player_stats if p.result in (MatchResult.LOSS, MatchResult.AWAY_WIN)
    )
    if match.result in (MatchResult.AWAY_WIN, MatchResult.LOSS):
        return loss_goals, win_goals
    return win_goals, loss_goals


def calculate_hall_of_fame(matches: List[MatchRecord]) -> dict:
    """Computes all-time club records, single-match highs, margins, and streak leaders."""
    if not matches:
        return {}

    biggest_margin = -1
    biggest_margin_matches: List[Tuple[MatchRecord, int, int]] = []
    highest_total = -1
    highest_total_matches: List[Tuple[MatchRecord, int, int]] = []
    clean_sheets: List[Tuple[MatchRecord, int, int]] = []

    max_goals_val = -1
    max_goals_holders: List[str] = []
    max_assists_val = -1
    max_assists_holders: List[str] = []
    max_ga_val = -1
    max_ga_holders: List[str] = []

    all_stats: List[PlayerStat] = []
    unique_players: Dict[str, str] = {}

    for match in matches:
        hs, a_s = extract_match_scores(match)
        margin = abs(hs - a_s)
        total = hs + a_s

        if margin > biggest_margin:
            biggest_margin = margin
            biggest_margin_matches = [(match, hs, a_s)]
        elif margin == biggest_margin:
            biggest_margin_matches.append((match, hs, a_s))

        if total > highest_total:
            highest_total = total
            highest_total_matches = [(match, hs, a_s)]
        elif total == highest_total:
            highest_total_matches.append((match, hs, a_s))

        if (hs > 0 and a_s == 0) or (a_s > 0 and hs == 0):
            clean_sheets.append((match, hs, a_s))

        for stat in match.player_stats:
            all_stats.append(stat)
            c_name = canonicalize_player_name(stat.player)
            if c_name:
                unique_players[c_name.lower()] = c_name

            if stat.goals > max_goals_val:
                max_goals_val = stat.goals
                max_goals_holders = [c_name]
            elif stat.goals == max_goals_val and c_name not in max_goals_holders:
                max_goals_holders.append(c_name)

            if stat.assists > max_assists_val:
                max_assists_val = stat.assists
                max_assists_holders = [c_name]
            elif stat.assists == max_assists_val and c_name not in max_assists_holders:
                max_assists_holders.append(c_name)

            ga = stat.goals + stat.assists
            if ga > max_ga_val:
                max_ga_val = ga
                max_ga_holders = [c_name]
            elif ga == max_ga_val and c_name not in max_ga_holders:
                max_ga_holders.append(c_name)

    best_streak_val = 0
    best_streak_holders: List[str] = []
    for p_name in sorted(unique_players.values(), key=str.lower):
        f_data = calculate_player_form(p_name, matches)
        st = f_data["longest_win_streak"]
        if st > best_streak_val:
            best_streak_val = st
            best_streak_holders = [p_name]
        elif st == best_streak_val and st > 0:
            best_streak_holders.append(p_name)

    aggs = calculate_player_aggregates(all_stats)
    hattrick_leader = get_leaderboard(aggs, LeaderboardCategory.HATTRICK)
    goal_leader = get_leaderboard(aggs, LeaderboardCategory.GOALS)
    assist_leader = get_leaderboard(aggs, LeaderboardCategory.ASSISTS)

    return {
        "total_matches": len(matches),
        "biggest_margin": biggest_margin,
        "biggest_margin_matches": biggest_margin_matches,
        "highest_total": highest_total,
        "highest_total_matches": highest_total_matches,
        "clean_sheets": clean_sheets,
        "max_single_goals": max_goals_val,
        "max_single_goals_players": max_goals_holders,
        "max_single_assists": max_assists_val,
        "max_single_assists_players": max_assists_holders,
        "max_single_ga": max_ga_val,
        "max_single_ga_players": max_ga_holders,
        "best_win_streak": best_streak_val,
        "best_win_streak_players": best_streak_holders,
        "hattrick_king": hattrick_leader[0] if hattrick_leader else None,
        "top_scorer": goal_leader[0] if goal_leader else None,
        "top_assister": assist_leader[0] if assist_leader else None,
    }


def calculate_match_motm_and_milestones(
    saved_match: MatchRecord,
    all_matches: List[MatchRecord],
) -> dict:
    """Computes Man of the Match and celebratory milestones triggered by a newly saved match."""
    if not saved_match.player_stats:
        return {"motm": None, "milestones": []}

    # 1. Pick Man of the Match
    scored_players: List[dict] = []
    for stat in saved_match.player_stats:
        c_name = canonicalize_player_name(stat.player)
        is_win = stat.result in (MatchResult.WIN, MatchResult.HOME_WIN)
        impact = round(stat.goals * 1.0 + stat.assists * 0.8 + (0.6 if is_win else 0.0), 2)
        scored_players.append(
            {
                "player": c_name,
                "goals": stat.goals,
                "assists": stat.assists,
                "is_win": is_win,
                "score": impact,
            }
        )

    scored_players.sort(
        key=lambda x: (x["score"], x["goals"] + x["assists"], x["goals"], 1 if x["is_win"] else 0),
        reverse=True,
    )
    motm = scored_players[0]

    # 2. Compute match and career milestones
    milestones: List[str] = []
    cs_winners: List[str] = []

    for stat in saved_match.player_stats:
        c_name = canonicalize_player_name(stat.player)
        is_win = stat.result in (MatchResult.WIN, MatchResult.HOME_WIN)
        if stat.goals >= 3:
            milestones.append(f"🎩 **Hattrick!** {c_name} scored **{stat.goals} goals**")
        if stat.assists >= 3:
            milestones.append(f"🎯 **Playmaker!** {c_name} registered **{stat.assists} assists**")
        if is_win and get_match_player_conceded_goals(saved_match, stat) == 0:
            cs_winners.append(c_name)

    if cs_winners:
        milestones.append(f"🧤 **Clean Sheet!** {', '.join(cs_winners)} kept a shutout")

    # Compare before vs after for career milestones, streaks & records
    prior_matches = [m for m in all_matches if m.match_id != saved_match.match_id]
    prior_stats = [ps for m in prior_matches for ps in m.player_stats]
    all_stats = [ps for m in all_matches for ps in m.player_stats]
    prior_aggs = calculate_player_aggregates(prior_stats, prior_matches) if prior_stats else {}
    curr_aggs = calculate_player_aggregates(all_stats, all_matches) if all_stats else {}

    prev_max_g = max((ps.goals for ps in prior_stats), default=0)
    prev_max_a = max((ps.assists for ps in prior_stats), default=0)
    prev_max_ga = max((ps.goals + ps.assists for ps in prior_stats), default=0)

    thresholds_primary = (10, 20, 25, 50, 75, 100)
    thresholds_mp = (10, 25, 50, 100)

    seen_players: set[str] = set()
    for stat in saved_match.player_stats:
        c_name = canonicalize_player_name(stat.player)
        key = c_name.strip().lower()
        if not key or key in seen_players:
            continue
        seen_players.add(key)

        if prior_stats:
            if stat.goals > prev_max_g and stat.goals > 0:
                milestones.append(f"👑 **New Record!** {c_name} set a single-match record of **{stat.goals} goals**!")
            if stat.assists > prev_max_a and stat.assists > 0:
                milestones.append(f"👑 **New Record!** {c_name} set a single-match record of **{stat.assists} assists**!")
            if (stat.goals + stat.assists) > prev_max_ga and (stat.goals + stat.assists) > 0:
                milestones.append(
                    f"🌟 **New Record!** {c_name} set a single-match G/A high of **{stat.goals + stat.assists}**!"
                )

        f_data = calculate_player_form(c_name, all_matches, window=5)
        if f_data["current_streak_type"] == "W" and f_data["current_streak_count"] >= 3:
            milestones.append(
                f"🔥 **Hot Streak!** {c_name} is on a **{f_data['current_streak_count']}-match win streak**"
            )

        curr_rec = curr_aggs.get(key)
        prev_rec = prior_aggs.get(key)
        if curr_rec:
            prev_g = prev_rec.goals if prev_rec else 0
            prev_a = prev_rec.assists if prev_rec else 0
            prev_w = prev_rec.wins if prev_rec else 0
            prev_mp = prev_rec.matches_played if prev_rec else 0

            for th in thresholds_primary:
                if prev_g < th <= curr_rec.goals:
                    milestones.append(f"⚽ **Milestone!** {c_name} reached **{th} career goals**")
                if prev_a < th <= curr_rec.assists:
                    milestones.append(f"🅰️ **Milestone!** {c_name} reached **{th} career assists**")
                if prev_w < th <= curr_rec.wins:
                    milestones.append(f"🏆 **Milestone!** {c_name} reached **{th} career wins**")
            for th in thresholds_mp:
                if prev_mp < th <= curr_rec.matches_played:
                    milestones.append(f"📅 **Milestone!** {c_name} played their **{th}th match**")

    return {
        "motm": motm,
        "milestones": milestones,
    }


def calculate_player_card_stats(
    player_name: str,
    leaderboard_records: List[dict],
    matches: List[MatchRecord],
) -> Optional[dict]:
    """Computes FC 27-style card OVR and 6 face attributes (PAC/SHO/PAS/DRI/DEF/PHY)."""
    canonical = canonicalize_player_name(player_name)
    key = canonical.strip().lower()
    record = next(
        (
            r
            for r in leaderboard_records
            if canonicalize_player_name(str(r.get("player", ""))).lower() == key
            or str(r.get("player", "")).strip().lower() == player_name.strip().lower()
        ),
        None,
    )
    if not record:
        return None

    mp = int(record.get("matches_played", 0))
    goals = int(record.get("goals", 0))
    assists = int(record.get("assists", 0))
    gpg = float(record.get("gpg", 0.0))
    apg = float(record.get("apg", 0.0))
    gapg = float(record.get("gapg", 0.0))
    win_rate = float(record.get("win_rate", 0.0))
    clean_sheets = int(record.get("clean_sheets", 0))

    raw_rating = compute_player_rating(record)
    # Bayesian / Confidence-Weighted OVR:
    # Blend raw player performance with squad baseline (72.0) using damping factor K = 3.5 matches.
    # Debutants with 1 match can no longer jump directly to 96+ OVR without sustained consistency.
    squad_baseline = 72.0
    damping_k = 3.5
    raw_ovr = 60.0 + 0.38 * raw_rating
    weighted_ovr = ((mp / (mp + damping_k)) * raw_ovr) + ((damping_k / (mp + damping_k)) * squad_baseline)
    rating = int(max(60, min(99, round(weighted_ovr)))) if mp > 0 else 60

    pace = int(max(71, min(99, round(71.0 + (rating - 60) * (28.0 / 39.0)))))

    # Realistic Shooting: Driven by goals, GPG, and goal conversion ratio
    goal_ratio = (goals / (goals + assists)) if (goals + assists) > 0 else 0.5
    shooting = int(max(60, min(99, round(58.0 + min(goals * 1.0, 20.0) + gpg * 6.5 + goal_ratio * 7.0))))

    # Realistic Passing: Driven by assists, APG, and assist-to-total ratio (pure playmakers get high passing)
    assist_ratio = (assists / (goals + assists)) if (goals + assists) > 0 else 0.5
    passing = int(max(60, min(99, round(58.0 + min(assists * 1.1, 20.0) + apg * 7.5 + assist_ratio * 8.0))))

    dribbling = int(max(60, min(99, round(60.0 + gapg * 11.5))))

    cs_map = calculate_player_conceded_and_clean_sheets(matches)
    c_entry = cs_map.get(canonical.lower(), {})
    goals_conceded = int(c_entry.get("goals_conceded", 0))
    c_matches = int(c_entry.get("matches", 0)) or max(mp, 1)
    gc_per_game = goals_conceded / c_matches
    defending = int(max(60, min(99, round(92.0 - gc_per_game * 8.0 + clean_sheets * 2.0))))

    physical = int(max(60, min(99, round((rating + pace + dribbling) / 3.0))))

    # Realistic Football Position Classifier (All non-GK outfield roles):
    # ST, CF, LW, RW, CAM, CM, LM, RM, CDM, LB, RB, CB
    # 1. Clear Defenders (Defending is the primary standout trait)
    if defending >= 82 and defending >= shooting + 12:
        if pace >= 82 and passing >= 75:
            position = "LB" if "l" in canonical.lower() else "RB"
        elif defending >= 88:
            position = "CB"
        else:
            position = "CDM"
    elif defending >= 78 and defending > shooting and defending > passing:
        if pace >= 80 and passing >= 75:
            position = "RB" if canonical.lower() in ("azlan", "skan") else "LB"
        else:
            position = "CDM"
    # 2. Elite Goalscorers (Shooting is distinctly dominant)
    elif shooting >= 85 and shooting >= passing + 4:
        position = "ST"
    # 3. Creative Playmakers (Passing is distinctly dominant)
    elif passing >= 80 and passing >= shooting + 6:
        position = "CAM"
    # 4. Box-to-Box / Midfield Anchors
    elif abs(passing - defending) <= 6 and abs(shooting - defending) <= 6:
        position = "CM"
    # 5. Dual Threats / Wide Attackers
    elif shooting >= 78 and passing >= 78:
        position = "CF"
    elif pace >= 82 and passing >= 76 and shooting < 78:
        position = "LM" if "l" in canonical.lower() else "RM"
    elif pace >= 84 and dribbling >= 82 and (shooting >= 78 or passing >= 78):
        position = "LW" if "l" in canonical.lower() else "RW"
    elif shooting >= passing:
        position = "ST"
    else:
        position = "CAM"

    return {
        "player": record["player"],
        "position": position,
        "rating": rating,
        "pace": pace,
        "shooting": shooting,
        "passing": passing,
        "dribbling": dribbling,
        "defending": defending,
        "physical": physical,
        "matches_played": mp,
        "goals": goals,
        "assists": assists,
        "gapg": gapg,
        "win_rate": win_rate,
        "goals_conceded": goals_conceded,
        "clean_sheets": clean_sheets,
    }


def calculate_player_chemistry(player_name: str, matches: List[MatchRecord]) -> dict:
    """Computes teammate synergy and opponent rivalry stats for a single player."""
    canonical = canonicalize_player_name(player_name)
    target_key = canonical.strip().lower()
    if not target_key:
        raise ValueError("Player name cannot be empty.")

    partners: Dict[str, dict] = {}
    opponents: Dict[str, dict] = {}
    total_matches = 0

    for match in matches:
        stats_by_key: Dict[str, Tuple[str, PlayerStat]] = {}
        for stat in match.player_stats:
            c_name = canonicalize_player_name(stat.player)
            k = c_name.strip().lower()
            if k:
                stats_by_key[k] = (c_name, stat)

        if target_key not in stats_by_key:
            continue

        total_matches += 1
        _, me = stats_by_key[target_key]
        me_win = me.result in (MatchResult.WIN, MatchResult.HOME_WIN)
        me_loss = me.result in (MatchResult.LOSS, MatchResult.AWAY_WIN)

        for other_key, (other_name, other_stat) in stats_by_key.items():
            if other_key == target_key:
                continue
            o_win = other_stat.result in (MatchResult.WIN, MatchResult.HOME_WIN)
            o_loss = other_stat.result in (MatchResult.LOSS, MatchResult.AWAY_WIN)

            if (me_win and o_win) or (me_loss and o_loss):
                p_entry = partners.setdefault(
                    other_key,
                    {"player": other_name, "matches": 0, "wins": 0, "losses": 0},
                )
                p_entry["matches"] += 1
                if me_win:
                    p_entry["wins"] += 1
                else:
                    p_entry["losses"] += 1
            elif (me_win and o_loss) or (me_loss and o_win):
                o_entry = opponents.setdefault(
                    other_key,
                    {"player": other_name, "matches": 0, "my_wins": 0, "opp_wins": 0},
                )
                o_entry["matches"] += 1
                if me_win:
                    o_entry["my_wins"] += 1
                else:
                    o_entry["opp_wins"] += 1

    partner_list = []
    for entry in partners.values():
        mp = entry["matches"]
        entry["win_rate"] = round((entry["wins"] / mp) * 100.0, 1) if mp > 0 else 0.0
        partner_list.append(entry)

    opponent_list = []
    for entry in opponents.values():
        mp = entry["matches"]
        entry["my_win_rate"] = round((entry["my_wins"] / mp) * 100.0, 1) if mp > 0 else 0.0
        entry["opp_win_rate"] = round((entry["opp_wins"] / mp) * 100.0, 1) if mp > 0 else 0.0
        opponent_list.append(entry)

    min2_partners = [p for p in partner_list if p["matches"] >= 2]
    partner_pool = min2_partners or partner_list

    best_partner = max(
        partner_pool,
        key=lambda p: (p["win_rate"], p["wins"], p["matches"]),
        default=None,
    )
    cursed_duo = min(
        partner_pool,
        key=lambda p: (p["win_rate"], -p["losses"], -p["matches"]),
        default=None,
    )
    if cursed_duo and best_partner and cursed_duo["player"] == best_partner["player"] and len(partner_pool) == 1 and best_partner["losses"] == 0:
        cursed_duo = None
    most_frequent_partner = max(
        partner_list,
        key=lambda p: (p["matches"], p["wins"]),
        default=None,
    )

    min2_opps = [o for o in opponent_list if o["matches"] >= 2]
    opp_pool = min2_opps or opponent_list

    fav_pool = [o for o in opp_pool if o["my_wins"] > 0]
    favorite_victim = max(
        fav_pool,
        key=lambda o: (o["my_wins"], o["my_win_rate"], o["matches"]),
        default=None,
    )
    krypt_pool = [o for o in opp_pool if o["opp_wins"] > 0]
    kryptonite = max(
        krypt_pool,
        key=lambda o: (o["opp_wins"], o["opp_win_rate"], o["matches"]),
        default=None,
    )

    return {
        "player": canonical,
        "total_matches": total_matches,
        "best_partner": best_partner,
        "cursed_duo": cursed_duo,
        "most_frequent_partner": most_frequent_partner,
        "favorite_victim": favorite_victim,
        "kryptonite": kryptonite,
        "partners": sorted(partner_list, key=lambda p: (p["matches"], p["win_rate"]), reverse=True),
        "opponents": sorted(opponent_list, key=lambda o: (o["matches"], o["my_win_rate"]), reverse=True),
    }


def calculate_session_recap(
    matches: List[MatchRecord],
    target_date: Optional[str] = None,
) -> dict:
    """Calculates summary statistics and top performers for a specific day or latest session."""
    if not matches:
        return {"total_matches": 0, "date": target_date or "", "matches": []}

    if target_date:
        clean_target = target_date.strip()
        session_matches = [
            m for m in matches
            if m.date and m.date.strip().startswith(clean_target)
        ]
        chosen_date = clean_target
    else:
        # Auto-pick the date of the most recent match
        latest_match = matches[-1]
        dt = parse_match_datetime(latest_match.date)
        chosen_date = dt.strftime("%Y-%m-%d") if dt else (latest_match.date[:10] if len(latest_match.date) >= 10 else "Latest")
        session_matches = [
            m for m in matches
            if m.date and m.date.strip().startswith(chosen_date)
        ]

    if not session_matches:
        return {"total_matches": 0, "date": chosen_date, "matches": []}

    total_goals = 0
    clean_sheets_count = 0
    player_stats_map: Dict[str, dict] = {}
    biggest_win_margin = -1
    biggest_win_match: Optional[MatchRecord] = None

    for m in session_matches:
        hs, a_s = extract_match_scores(m)
        margin = abs(hs - a_s)
        if margin > biggest_win_margin:
            biggest_win_margin = margin
            biggest_win_match = m

        if (hs > 0 and a_s == 0) or (a_s > 0 and hs == 0):
            clean_sheets_count += 1

        for stat in m.player_stats:
            c_name = canonicalize_player_name(stat.player)
            k = c_name.strip().lower()
            if not k:
                continue
            p_entry = player_stats_map.setdefault(
                k,
                {"player": c_name, "goals": 0, "assists": 0, "wins": 0, "losses": 0, "matches": 0},
            )
            p_entry["goals"] += stat.goals
            p_entry["assists"] += stat.assists
            p_entry["matches"] += 1
            total_goals += stat.goals
            if stat.result in (MatchResult.WIN, MatchResult.HOME_WIN):
                p_entry["wins"] += 1
            elif stat.result in (MatchResult.LOSS, MatchResult.AWAY_WIN):
                p_entry["losses"] += 1

    player_list = list(player_stats_map.values())
    for p in player_list:
        p["ga"] = p["goals"] + p["assists"]
        p["score"] = round(0.4 * p["goals"] + 0.4 * p["assists"] + 0.2 * p["wins"], 2)

    player_list.sort(key=lambda p: (p["score"], p["ga"], p["goals"], p["wins"]), reverse=True)
    mvp = player_list[0] if player_list else None
    top_scorer = max(player_list, key=lambda p: (p["goals"], p["assists"]), default=None)
    top_playmaker = max(player_list, key=lambda p: (p["assists"], p["goals"]), default=None)

    return {
        "date": chosen_date,
        "total_matches": len(session_matches),
        "total_goals": total_goals,
        "clean_sheets": clean_sheets_count,
        "biggest_win_match": biggest_win_match,
        "biggest_win_margin": biggest_win_margin,
        "mvp": mvp,
        "top_scorer": top_scorer,
        "top_playmaker": top_playmaker,
        "players": player_list,
        "matches": session_matches,
    }


def calculate_player_trophies(
    player_name: str,
    matches: List[MatchRecord],
    all_aggregates: Dict[str, PlayerAggregateRecord],
) -> dict:
    """Computes unlockable badges and milestones for a player from career and match history."""
    canonical = canonicalize_player_name(player_name)
    k = canonical.strip().lower()
    rec = all_aggregates.get(k) or PlayerAggregateRecord.compute(canonical, 0, 0, 0, 0, 0, 0)

    # Calculate single match highs and streaks
    single_max_goals = 0
    single_max_assists = 0
    single_max_ga = 0
    clutch_wins = 0
    clean_sheet_wins = 0
    duo_partners: Dict[str, dict] = {}

    f_data = calculate_player_form(canonical, matches, window=5)
    best_streak = f_data["longest_win_streak"]

    for m in matches:
        player_stat: Optional[PlayerStat] = None
        teammates: List[str] = []
        for s in m.player_stats:
            c_s = canonicalize_player_name(s.player)
            if c_s.strip().lower() == k:
                player_stat = s
            else:
                teammates.append(c_s)

        if not player_stat:
            continue

        single_max_goals = max(single_max_goals, player_stat.goals)
        single_max_assists = max(single_max_assists, player_stat.assists)
        single_max_ga = max(single_max_ga, player_stat.goals + player_stat.assists)

        is_win = player_stat.result in (MatchResult.WIN, MatchResult.HOME_WIN)
        hs, a_s = extract_match_scores(m)
        margin = abs(hs - a_s)
        if is_win and margin == 1 and (player_stat.goals > 0 or player_stat.assists > 0):
            clutch_wins += 1

        conceded = get_match_player_conceded_goals(m, player_stat)
        if is_win and conceded == 0:
            clean_sheet_wins += 1

        for tm in teammates:
            entry = duo_partners.setdefault(tm, {"wins": 0, "matches": 0})
            entry["matches"] += 1
            if is_win:
                entry["wins"] += 1

    duo_master = any(d["wins"] >= 5 and (d["wins"] / d["matches"]) >= 0.75 for d in duo_partners.values() if d["matches"] >= 5)

    badge_definitions = [
        ("🎩", "Hattrick Hero", "Score 3+ goals in a single match", rec.hattricks >= 1 or single_max_goals >= 3),
        ("⚡", "Super Hattrick", "Score 4+ goals in a single match", single_max_goals >= 4),
        ("🎯", "Master Playmaker", "Provide 3+ assists in a single match", single_max_assists >= 3),
        ("🌟", "One-Man Army", "Achieve 4+ G/A in a single match", single_max_ga >= 4),
        ("🧤", "The Iron Wall", "Keep at least 2 clean sheet victories", clean_sheet_wins >= 2),
        ("🔥", "Unstoppable", "Achieve a 4+ match win streak", best_streak >= 4),
        ("👑", "Century Contender", "Reach 25+ career G/A", rec.ga >= 25),
        ("🎯", "Centurion Club", "Reach 50+ career G/A", rec.ga >= 50),
        ("⏱️", "Clutch Gene", "Deliver in 3+ close wins (1-goal margin)", clutch_wins >= 3),
        ("🤝", "Dynamic Duo", "Win 5+ matches with 75%+ win rate with a partner", duo_master),
        ("📅", "Veteran", "Play 10+ career matches", rec.matches_played >= 10),
    ]

    unlocked = [b for b in badge_definitions if b[3]]
    locked = [b for b in badge_definitions if not b[3]]

    return {
        "player": canonical,
        "total_unlocked": len(unlocked),
        "total_badges": len(badge_definitions),
        "unlocked": unlocked,
        "locked": locked,
    }


def calculate_player_tierlist(
    leaderboard_records: List[dict],
    matches: List[MatchRecord],
) -> Dict[str, List[dict]]:
    """Sorts players into S, A, B, and C tiers based on calibrated card ratings and form."""
    tiers: Dict[str, List[dict]] = {"S": [], "A": [], "B": [], "C": []}
    for r in leaderboard_records:
        card = calculate_player_card_stats(r["player"], leaderboard_records, matches)
        if not card:
            continue
        ovr = card["rating"]
        if ovr >= 88:
            t = "S"
        elif ovr >= 78:
            t = "A"
        elif ovr >= 70:
            t = "B"
        else:
            t = "C"
        tiers[t].append({
            "player": card["player"],
            "ovr": ovr,
            "position": card["position"],
            "matches_played": card["matches_played"],
            "ga": card["goals"] + card["assists"],
            "win_rate": card["win_rate"],
        })

    for t_list in tiers.values():
        t_list.sort(key=lambda x: (x["ovr"], x["win_rate"], x["ga"]), reverse=True)

    return tiers
