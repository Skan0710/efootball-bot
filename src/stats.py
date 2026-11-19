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
    parse_players_from_team_param,
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


def extract_match_team_leaders(match: MatchRecord) -> Tuple[Optional[str], Optional[str]]:
    """Extracts (home_leader, away_leader) from a match record.

    The leader is the first player name of each team entered during a match.
    Parses team names from match notes (e.g., 'Score: Skan & Jonny & Suri 4 - 2 Shallowe & Naka & Azlan').
    """
    if not match.notes:
        return None, None

    sm = re.match(
        r"^\s*(?:Score:\s*)?(?P<left>.+?)\s+(?P<hs>\d+)\s*-\s*(?P<as>\d+)\s+(?P<right>.+?)(?:\s*\([^)]*\))?\s*$",
        match.notes.strip(),
        flags=re.IGNORECASE,
    )
    if not sm:
        return None, None

    left_str = sm.group("left").strip()
    right_str = sm.group("right").strip()

    home_players = parse_players_from_team_param(left_str)
    away_players = parse_players_from_team_param(right_str)

    home_leader = canonicalize_player_name(home_players[0]) if home_players else None
    away_leader = canonicalize_player_name(away_players[0]) if away_players else None

    # Validate against actual player names in the match if player_stats is present
    if match.player_stats:
        known_players = {canonicalize_player_name(p.player).lower() for p in match.player_stats}
        if home_leader and home_leader.lower() not in known_players:
            home_leader = None
        if away_leader and away_leader.lower() not in known_players:
            away_leader = None

    return home_leader, away_leader


def calculate_team_leader_aggregates(matches: List[MatchRecord]) -> List[dict]:
    """Calculates team leader stats across all matches.

    Ranked primarily by Wins as Leader (descending), then Win Rate (descending),
    then Matches Led (descending).
    """
    leader_records: Dict[str, dict] = {}

    for match in matches:
        home_leader, away_leader = extract_match_team_leaders(match)

        is_home_win = match.result in (MatchResult.HOME_WIN, MatchResult.WIN)
        is_away_win = match.result in (MatchResult.AWAY_WIN, MatchResult.LOSS)
        is_draw = match.result == MatchResult.DRAW

        if home_leader:
            rec = leader_records.setdefault(
                home_leader,
                {"player": home_leader, "matches_led": 0, "wins": 0, "losses": 0, "draws": 0}
            )
            rec["matches_led"] += 1
            if is_home_win:
                rec["wins"] += 1
            elif is_away_win:
                rec["losses"] += 1
            elif is_draw:
                rec["draws"] += 1

        if away_leader:
            rec = leader_records.setdefault(
                away_leader,
                {"player": away_leader, "matches_led": 0, "wins": 0, "losses": 0, "draws": 0}
            )
            rec["matches_led"] += 1
            if is_away_win:
                rec["wins"] += 1
            elif is_home_win:
                rec["losses"] += 1
            elif is_draw:
                rec["draws"] += 1

    results: List[dict] = []
    for player, rec in leader_records.items():
        m_cnt = rec["matches_led"]
        w = rec["wins"]
        l = rec["losses"]
        d = rec["draws"]
        wr = round((w / m_cnt) * 100, 1) if m_cnt > 0 else 0.0
        results.append({
            "player": player,
            "matches_led": m_cnt,
            "wins": w,
            "losses": l,
            "draws": d,
            "win_rate": wr,
        })

    results.sort(
        key=lambda r: (r["wins"], r["win_rate"], r["matches_led"]),
        reverse=True,
    )
    return results


def calculate_captain_head_to_head(
    captain1: str,
    captain2: str,
    matches: List[MatchRecord],
) -> dict:
    """Computes head-to-head stats specifically when two players captained opposing teams."""
    c1 = canonicalize_player_name(captain1)
    c2 = canonicalize_player_name(captain2)
    k1 = c1.strip().lower()
    k2 = c2.strip().lower()
    if not k1 or not k2:
        raise ValueError("Both captain names must be non-empty.")
    if k1 == k2:
        raise ValueError(f"Please select two different captains (both resolved to '{c1}').")

    matches_against = 0
    c1_wins = 0
    c2_wins = 0
    draws = 0
    c1_goals = 0
    c2_goals = 0
    clashes: List[dict] = []

    for match in matches:
        home_leader, away_leader = extract_match_team_leaders(match)
        if not home_leader or not away_leader:
            continue

        hl_key = home_leader.strip().lower()
        al_key = away_leader.strip().lower()

        # Check if this match was captain1 vs captain2
        if {hl_key, al_key} != {k1, k2}:
            continue

        matches_against += 1
        scores = extract_match_scores(match)
        home_score = scores[0] if scores else None
        away_score = scores[1] if scores else None

        is_home_win = match.result in (MatchResult.HOME_WIN, MatchResult.WIN)
        is_away_win = match.result in (MatchResult.AWAY_WIN, MatchResult.LOSS)
        is_draw = match.result == MatchResult.DRAW

        if hl_key == k1:
            # c1 is home leader, c2 is away leader
            if is_home_win:
                c1_wins += 1
                winner = c1
            elif is_away_win:
                c2_wins += 1
                winner = c2
            else:
                draws += 1
                winner = "Draw"

            if home_score is not None and away_score is not None:
                c1_goals += home_score
                c2_goals += away_score
        else:
            # c2 is home leader, c1 is away leader
            if is_home_win:
                c2_wins += 1
                winner = c2
            elif is_away_win:
                c1_wins += 1
                winner = c1
            else:
                draws += 1
                winner = "Draw"

            if home_score is not None and away_score is not None:
                c2_goals += home_score
                c1_goals += away_score

        clashes.append({
            "match_id": match.match_id,
            "date": match.date,
            "home_leader": home_leader,
            "away_leader": away_leader,
            "home_score": home_score,
            "away_score": away_score,
            "result": match.result,
            "winner": winner,
            "match": match,
        })

    c1_wr = round((c1_wins / matches_against) * 100, 1) if matches_against > 0 else 0.0
    c2_wr = round((c2_wins / matches_against) * 100, 1) if matches_against > 0 else 0.0

    return {
        "captain1": c1,
        "captain2": c2,
        "matches": matches_against,
        "c1_wins": c1_wins,
        "c2_wins": c2_wins,
        "draws": draws,
        "c1_goals": c1_goals,
        "c2_goals": c2_goals,
        "c1_win_rate": c1_wr,
        "c2_win_rate": c2_wr,
        "clashes": clashes,
    }


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


def compute_player_card_ovr(
    record: dict | PlayerAggregateRecord,
    squad_baseline: float = 72.0,
    damping_k: float = 3.5,
) -> int:
    """Computes the official confidence-weighted FC 27 Card OVR rating (60–99)."""
    p_name = str(record.get("player", "") if isinstance(record, dict) else getattr(record, "player", "")).lower()
    if p_name == "harold" or "harold" in p_name:
        return 99

    if isinstance(record, dict):
        mp = int(record.get("matches_played", 0))
    else:
        mp = int(record.matches_played)

    if mp <= 0:
        return 60

    raw_rating = compute_player_rating(record)
    raw_ovr = 60.0 + 0.38 * raw_rating
    weighted_ovr = ((mp / (mp + damping_k)) * raw_ovr) + ((damping_k / (mp + damping_k)) * squad_baseline)
    return int(max(60, min(99, round(weighted_ovr))))


def determine_team_leader(team_players: List[dict]) -> dict:
    """Determines the team captain based on:
    1. Highest standing on Team Leaders leaderboard (wins, win_rate, matches_led).
    2. Tie-breaker: Higher OVR rating.
    3. If OVR is also tied: Leader is chosen at random (not finalized on one).
    """
    if not team_players:
        return {"leader": None, "is_tied": False, "tied_players": [], "leader_ovr": 0}

    def leader_stats_key(p: dict) -> Tuple[int, float, int]:
        return (int(p.get("leader_wins", 0)), float(p.get("leader_wr", 0.0)), int(p.get("leader_matches", 0)))

    max_stats = max(leader_stats_key(p) for p in team_players)
    candidates_by_stats = [p for p in team_players if leader_stats_key(p) == max_stats]

    if len(candidates_by_stats) == 1:
        chosen = candidates_by_stats[0]
        return {
            "leader": chosen["player"],
            "is_tied": False,
            "tied_players": [],
            "leader_ovr": int(round(chosen["rating"])),
        }

    # Tie on leader stats -> Tie-breaker: Higher OVR
    max_ovr = max(int(round(p["rating"])) for p in candidates_by_stats)
    candidates_by_ovr = [p for p in candidates_by_stats if int(round(p["rating"])) == max_ovr]

    if len(candidates_by_ovr) == 1:
        chosen = candidates_by_ovr[0]
        return {
            "leader": chosen["player"],
            "is_tied": False,
            "tied_players": [],
            "leader_ovr": max_ovr,
        }

    # Tie in OVR tie-breaker as well
    return {
        "leader": None,
        "is_tied": True,
        "tied_players": [p["player"] for p in candidates_by_ovr],
        "leader_ovr": max_ovr,
    }


def generate_fair_teams(
    player_names: List[str],
    leaderboard_records: List[dict],
    matches: Optional[List[MatchRecord]] = None,
    leader_records: Optional[List[dict]] = None,
) -> dict:
    """Splits 4, 5, or 6 players into the fairest possible Team A vs Team B matchup,
    ensuring that the top 2 team leaders of the leaderboard stay on opposite teams.
    """
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
        r_val = float(compute_player_card_ovr(rec))
        key = canonicalize_player_name(str(rec.get("player", ""))).lower()
        if key:
            ratings_by_key[key] = r_val
        if int(rec.get("matches_played", 0)) > 0:
            ranked_ratings.append(r_val)

    default_rating = round(sum(ranked_ratings) / len(ranked_ratings), 1) if ranked_ratings else 70.0

    # Resolve leader standings from leader_records or matches
    leader_stats_by_player: Dict[str, dict] = {}
    if leader_records is None and matches is not None:
        leader_records = calculate_team_leader_aggregates(matches)

    if leader_records:
        for lr in leader_records:
            k = canonicalize_player_name(str(lr.get("player", ""))).lower()
            if k:
                leader_stats_by_player[k] = lr

    player_entries = []
    for name in canonical_players:
        p_key = name.lower()
        ldr = leader_stats_by_player.get(p_key, {})
        r_val = ratings_by_key.get(p_key, 0.0) or default_rating
        player_entries.append({
            "player": name,
            "rating": r_val,
            "is_unranked": ratings_by_key.get(p_key, 0.0) == 0.0,
            "leader_wins": int(ldr.get("wins", 0)),
            "leader_wr": float(ldr.get("win_rate", 0.0)),
            "leader_matches": int(ldr.get("matches_led", 0)),
        })

    # Rank players to identify the top 2 best leaders in the pool:
    # 1. Leaderboard standing (wins desc, win_rate desc, matches_led desc)
    # 2. Tie-breaker: higher OVR rating
    sorted_leaders = sorted(
        player_entries,
        key=lambda x: (
            x["leader_wins"],
            x["leader_wr"],
            x["leader_matches"],
            x["rating"],
        ),
        reverse=True,
    )
    top_leader_1 = sorted_leaders[0]
    top_leader_2 = sorted_leaders[1]

    idx_1 = player_entries.index(top_leader_1)
    idx_2 = player_entries.index(top_leader_2)

    team_a_size = 2 if count == 4 else 3
    best_split = None
    best_key = None
    fallback_split = None
    fallback_key = None

    indices = list(range(count))
    for combo in combinations(indices, team_a_size):
        combo_set = set(combo)
        in_a_1 = idx_1 in combo_set
        in_a_2 = idx_2 in combo_set

        team_a = [player_entries[i] for i in combo]
        team_b = [player_entries[i] for i in indices if i not in combo_set]
        sum_a = round(sum(p["rating"] for p in team_a), 2)
        sum_b = round(sum(p["rating"] for p in team_b), 2)
        diff = round(abs(sum_a - sum_b), 2)
        avg_diff = round(abs((sum_a / len(team_a)) - (sum_b / len(team_b))), 2)
        score_key = (diff, avg_diff)

        # Record fallback without leader restriction
        if fallback_key is None or score_key < fallback_key:
            fallback_key = score_key
            fallback_split = (team_a, team_b, sum_a, sum_b, diff)

        # Enforce requirement: Top 2 leaders must stay on OPPOSITE teams
        if in_a_1 == in_a_2:
            continue

        # For equal size teams (4 or 6), break symmetry by fixing leader 1 in Team A
        if count in (4, 6) and not in_a_1:
            continue

        if best_key is None or score_key < best_key:
            best_key = score_key
            best_split = (team_a, team_b, sum_a, sum_b, diff)

    chosen = best_split or fallback_split
    assert chosen is not None
    team_a_chosen, team_b_chosen, sum_a, sum_b, diff = chosen

    ldr_a = determine_team_leader(team_a_chosen)
    ldr_b = determine_team_leader(team_b_chosen)

    def _order_team(players: List[dict], leader_info: dict) -> List[dict]:
        leader_name = leader_info.get("leader")
        if leader_name and not leader_info.get("is_tied"):
            leader_p = [p for p in players if p["player"].lower() == leader_name.lower()]
            other_p = [p for p in players if p["player"].lower() != leader_name.lower()]
            other_p.sort(key=lambda x: x["rating"], reverse=True)
            return leader_p + other_p
        return sorted(players, key=lambda x: x["rating"], reverse=True)

    return {
        "team_a": _order_team(team_a_chosen, ldr_a),
        "team_b": _order_team(team_b_chosen, ldr_b),
        "team_a_rating": sum_a,
        "team_b_rating": sum_b,
        "rating_diff": diff,
        "team_a_leader": ldr_a,
        "team_b_leader": ldr_b,
    }


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


LEGAL_OUTFIELD_POSITIONS = {"LB", "CB", "RB", "CDM", "CM", "CAM", "LW", "ST", "RW"}
LEGAL_POSITIONS = {"GK"} | LEGAL_OUTFIELD_POSITIONS


def _assign_individual_position(
    canonical: str,
    pace: int,
    shooting: int,
    passing: int,
    dribbling: int,
    defending: int,
    physical: Optional[int] = None,
) -> str:
    """Assigns realistic tactical position for a single player in isolation."""
    key = canonical.strip().lower()
    if key == "harold":
        return "GK"

    phy = physical if physical is not None else int(round((pace + dribbling + 75) / 3.0))

    # Gullit Gang rule: All 6 face attributes >= 80 -> CM by default
    stats_list = [pace, shooting, passing, dribbling, defending, phy]
    if all(s >= 80 for s in stats_list):
        return "CM"

    is_midfield = passing >= shooting and passing >= defending
    is_defence = defending >= shooting and defending >= passing
    is_attack = not is_midfield and not is_defence

    # 3 stats combination (pace, dribbling, and def/sho >= 75 each)
    if pace >= 75 and dribbling >= 75:
        if defending >= 75 and is_defence:
            return "LB"
        if shooting >= 75 and is_attack:
            return "LW"

    # 4 stats combination (pace, dribbling, passing, and shooting/defending)
    is_4stat = (pace >= 75 and dribbling >= 75 and passing >= 75 and (shooting >= 75 or defending >= 75)) or (
        is_midfield and pace >= 75 and dribbling >= 75 and (shooting >= 75 or defending >= 75)
    )
    if is_4stat:
        return "CAM" if shooting >= defending else "CDM"

    # 2 stats combination / Main region decision
    if is_defence:
        return "CB"
    if is_midfield:
        return "CM"
    return "ST"


def assign_roster_positions(
    leaderboard_records: List[dict],
    matches: List[MatchRecord],
) -> Dict[str, str]:
    """Determines realistic tactical positions across the entire roster.

    Enforces strict tactical constraints:
    - Special tribute players (Harold) -> 'GK'
    - Gullit Gang rule: If all 6 face attributes >= 80 -> 'CM' by default (e.g. Suri).
    - Combination of 3 stats (Pace >= 75, Dribbling >= 75, and Def/Sho >= 75):
      - Higher defending -> LB/RB (at most 1 LB and 1 RB across the roster)
      - Higher shooting -> LW/RW (at most 1 LW and 1 RW across the roster)
    - Single CAM Rule: At most 1 CAM allowed in the squad; the other candidate becomes CM.
    - Combination of 4 stats (Pace, Dribbling, Passing, and Shooting/Defending):
      - Higher shooting -> CAM (or CM if a CAM already exists)
      - Higher defending -> CDM
    - Combination of 2 stats / Primary region:
      - Defending highest -> CB
      - Passing highest -> CM
      - Shooting highest -> ST
    - Legal positions only: LB, CB, RB, CDM, CM, CAM, LW, ST, RW, GK.
      'CF', 'LM', 'RM' are converted to ST, LW, RW respectively.
    """
    if not leaderboard_records:
        return {}

    cs_map = calculate_player_conceded_and_clean_sheets(matches)

    raw_players = []
    for r in leaderboard_records:
        pname = str(r.get("player", ""))
        canonical = canonicalize_player_name(pname)
        key = canonical.strip().lower()
        if not key:
            continue

        if key == "harold":
            raw_players.append({
                "key": key,
                "canonical": canonical,
                "is_gk": True,
                "pace": 99,
                "shooting": 99,
                "passing": 99,
                "dribbling": 99,
                "defending": 99,
                "physical": 99,
                "rating": 99,
            })
            continue

        mp = int(r.get("matches_played", 0))
        goals = int(r.get("goals", 0))
        assists = int(r.get("assists", 0))
        gpg = float(r.get("gpg", 0.0))
        apg = float(r.get("apg", 0.0))
        gapg = float(r.get("gapg", 0.0))
        win_rate = float(r.get("win_rate", 0.0))
        wins = int(r.get("wins", 0))
        if wins == 0 and win_rate > 0 and mp > 0:
            wins = int(round((win_rate / 100.0) * mp))
        clean_sheets = int(r.get("clean_sheets", 0))

        rating = compute_player_card_ovr(r)
        pace = int(max(71, min(99, round(71.0 + (rating - 60) * (28.0 / 39.0)))))
        goal_ratio = (goals / (goals + assists)) if (goals + assists) > 0 else 0.5
        shooting = int(max(60, min(99, round(58.0 + min(goals * 1.0, 20.0) + gpg * 6.5 + goal_ratio * 7.0))))
        assist_ratio = (assists / (goals + assists)) if (goals + assists) > 0 else 0.5
        passing = int(max(60, min(99, round(58.0 + min(assists * 1.1, 20.0) + apg * 7.5 + assist_ratio * 8.0))))
        dribbling = int(max(60, min(99, round(60.0 + gapg * 11.5))))

        c_entry = cs_map.get(key, {})
        goals_conceded = int(c_entry.get("goals_conceded", 0))
        clean_sheets = max(clean_sheets, int(c_entry.get("clean_sheets", 0)))
        c_matches = int(c_entry.get("matches", 0)) or max(mp, 1)
        gc_per_game = goals_conceded / c_matches
        gc_factor = max(0.0, min(1.0, 1.0 - (gc_per_game / 4.0)))
        win_factor = max(0.0, min(1.0, (wins / max(mp, 1)) if mp > 0 else (win_rate / 100.0)))
        cs_factor = max(0.0, min(1.0, clean_sheets / max(mp, 1)))
        raw_def_efficiency = (0.50 * gc_factor) + (0.30 * win_factor) + (0.20 * cs_factor)
        def_damping_k = 3.0
        baseline_def = 0.45
        damped_def_efficiency = ((mp / (mp + def_damping_k)) * raw_def_efficiency) + ((def_damping_k / (mp + def_damping_k)) * baseline_def)
        defending = int(max(60, min(99, round(60.0 + 39.0 * damped_def_efficiency))))
        physical = int(max(60, min(99, round((rating + pace + dribbling) / 3.0))))

        raw_players.append({
            "key": key,
            "canonical": canonical,
            "is_gk": False,
            "pace": pace,
            "shooting": shooting,
            "passing": passing,
            "dribbling": dribbling,
            "defending": defending,
            "physical": physical,
            "rating": rating,
        })

    positions: Dict[str, str] = {}
    assigned: set[str] = set()

    # 1. Special tribute GK
    for p in raw_players:
        if p["is_gk"]:
            positions[p["key"]] = "GK"
            assigned.add(p["key"])

    # 2. Gullit Gang: All 6 face attributes >= 80 -> CM by default
    for p in raw_players:
        if p["key"] in assigned:
            continue
        stats_list = [p["pace"], p["shooting"], p["passing"], p["dribbling"], p["defending"], p["physical"]]
        if all(s >= 80 for s in stats_list):
            positions[p["key"]] = "CM"
            assigned.add(p["key"])

    # 3. Fullbacks (LB / RB) - Exactly max 1 LB and 1 RB across the roster
    fb_candidates = [
        p for p in raw_players
        if p["key"] not in assigned
        and p["pace"] >= 75 and p["dribbling"] >= 75 and p["defending"] >= 75
        and p["defending"] >= p["shooting"]
    ]
    fb_candidates.sort(key=lambda p: (p["defending"], p["pace"] + p["dribbling"]), reverse=True)
    if len(fb_candidates) >= 1:
        positions[fb_candidates[0]["key"]] = "LB"
        assigned.add(fb_candidates[0]["key"])
    if len(fb_candidates) >= 2:
        positions[fb_candidates[1]["key"]] = "RB"
        assigned.add(fb_candidates[1]["key"])

    # 4. Wingers (LW / RW) - Exactly max 1 LW and 1 RW across the roster
    w_candidates = [
        p for p in raw_players
        if p["key"] not in assigned
        and p["pace"] >= 75 and p["dribbling"] >= 75 and p["shooting"] >= 75
        and p["shooting"] >= p["defending"]
    ]
    w_candidates.sort(key=lambda p: (p["shooting"], p["pace"] + p["dribbling"]), reverse=True)
    if len(w_candidates) >= 1:
        positions[w_candidates[0]["key"]] = "LW"
        assigned.add(w_candidates[0]["key"])
    if len(w_candidates) >= 2:
        positions[w_candidates[1]["key"]] = "RW"
        assigned.add(w_candidates[1]["key"])

    # 5. Remaining outfield players: Single CAM rule & 2 stats (CB/CM/ST)
    cam_assigned = False
    unassigned = [p for p in raw_players if p["key"] not in assigned]
    unassigned.sort(key=lambda p: (p["rating"], p["passing"] + p["shooting"]), reverse=True)

    for p in unassigned:
        k = p["key"]
        pac, dri, pas = p["pace"], p["dribbling"], p["passing"]
        sho, df = p["shooting"], p["defending"]

        is_midfield = pas >= sho and pas >= df
        is_defence = df >= sho and df >= pas

        # Combination of 4 stats
        is_4stat = (pac >= 75 and dri >= 75 and pas >= 75 and (sho >= 75 or df >= 75)) or (
            is_midfield and pac >= 75 and dri >= 75 and (sho >= 75 or df >= 75)
        )
        if is_4stat:
            if sho >= df:
                if not cam_assigned:
                    positions[k] = "CAM"
                    cam_assigned = True
                else:
                    # Only 1 CAM allowed! The other person has to be CM
                    positions[k] = "CM"
            else:
                positions[k] = "CDM"
            assigned.add(k)
            continue

        # Combination of 2 stats / Region decision
        if is_defence:
            positions[k] = "CB"
        elif is_midfield:
            positions[k] = "CM"
        else:
            positions[k] = "ST"
        assigned.add(k)

    # Conversion of forbidden roles
    conversion = {"CF": "ST", "LM": "LW", "RM": "RW"}
    for k, pos in list(positions.items()):
        if pos in conversion:
            positions[k] = conversion[pos]

    return positions


def calculate_player_card_stats(
    player_name: str,
    leaderboard_records: List[dict],
    matches: List[MatchRecord],
) -> Optional[dict]:
    """Computes FC 27-style card OVR and 6 face attributes (PAC/SHO/PAS/DRI/DEF/PHY)."""
    canonical = canonicalize_player_name(player_name)
    key = canonical.strip().lower()

    # Special Harold 99 GK Tribute Card (Fixed 99 OVR, all GK stats 99, never changes)
    if key == "harold":
        return {
            "player": "Harold",
            "position": "GK",
            "rating": 99,
            "pace": 99,
            "shooting": 99,
            "passing": 99,
            "dribbling": 99,
            "defending": 99,
            "physical": 99,
            "div": 99,
            "han": 99,
            "kic": 99,
            "ref": 99,
            "spe": 99,
            "pos": 99,
            "diving": 99,
            "handling": 99,
            "kicking": 99,
            "reflexes": 99,
            "speed": 99,
            "positioning": 99,
            "matches_played": 99,
            "goals": 0,
            "assists": 0,
            "gapg": 0.0,
            "win_rate": 100.0,
            "goals_conceded": 0,
            "clean_sheets": 99,
            "is_tribute": True,
            "tier_label": "TRIBUTE ICON",
        }

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
    wins = int(record.get("wins", 0))
    if wins == 0 and win_rate > 0 and mp > 0:
        wins = int(round((win_rate / 100.0) * mp))
    clean_sheets = int(record.get("clean_sheets", 0))

    rating = compute_player_card_ovr(record)

    pace = int(max(71, min(99, round(71.0 + (rating - 60) * (28.0 / 39.0)))))

    # Realistic Shooting: Driven by goals, GPG, and goal conversion ratio
    goal_ratio = (goals / (goals + assists)) if (goals + assists) > 0 else 0.5
    shooting = int(max(60, min(99, round(58.0 + min(goals * 1.0, 20.0) + gpg * 6.5 + goal_ratio * 7.0))))

    # Realistic Passing: Driven by assists, APG, and assist-to-total ratio (pure playmakers get high passing)
    assist_ratio = (assists / (goals + assists)) if (goals + assists) > 0 else 0.5
    passing = int(max(60, min(99, round(58.0 + min(assists * 1.1, 20.0) + apg * 7.5 + assist_ratio * 8.0))))

    dribbling = int(max(60, min(99, round(60.0 + gapg * 11.5))))

    # Realistic Defending: Composite decimal-weighted defensive efficiency
    # 50% Goals Conceded containment + 30% Winning contribution + 20% Clean Sheet rate
    cs_map = calculate_player_conceded_and_clean_sheets(matches)
    c_entry = cs_map.get(canonical.lower(), {})
    goals_conceded = int(c_entry.get("goals_conceded", 0))
    clean_sheets = max(clean_sheets, int(c_entry.get("clean_sheets", 0)))
    c_matches = int(c_entry.get("matches", 0)) or max(mp, 1)
    gc_per_game = goals_conceded / c_matches

    gc_factor = max(0.0, min(1.0, 1.0 - (gc_per_game / 4.0)))
    win_factor = max(0.0, min(1.0, (wins / max(mp, 1)) if mp > 0 else (win_rate / 100.0)))
    cs_factor = max(0.0, min(1.0, clean_sheets / max(mp, 1)))

    raw_def_efficiency = (0.50 * gc_factor) + (0.30 * win_factor) + (0.20 * cs_factor)
    def_damping_k = 3.0
    baseline_def = 0.45
    damped_def_efficiency = ((mp / (mp + def_damping_k)) * raw_def_efficiency) + ((def_damping_k / (mp + def_damping_k)) * baseline_def)
    defending = int(max(60, min(99, round(60.0 + 39.0 * damped_def_efficiency))))

    physical = int(max(60, min(99, round((rating + pace + dribbling) / 3.0))))

    # Position Assignment: Use roster-wide tactical positioning or individual fallback
    positions_map = assign_roster_positions(leaderboard_records, matches) if leaderboard_records else {}
    if key in positions_map:
        position = positions_map[key]
    else:
        position = _assign_individual_position(
            canonical=canonical,
            pace=pace,
            shooting=shooting,
            passing=passing,
            dribbling=dribbling,
            defending=defending,
            physical=physical,
        )

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


def calculate_player_rivalry(
    player1_name: str,
    player2_name: str,
    matches: List[MatchRecord],
) -> dict:
    """Computes head-to-head match history, goal margins, and bragging rights between two rivals."""
    c1 = canonicalize_player_name(player1_name)
    c2 = canonicalize_player_name(player2_name)
    k1 = c1.strip().lower()
    k2 = c2.strip().lower()

    if not k1 or not k2:
        raise ValueError("Both player names must be non-empty.")
    if k1 == k2:
        raise ValueError(f"Cannot calculate rivalry against the same player ({c1}).")

    clashes: List[dict] = []
    p1_wins = 0
    p2_wins = 0
    draws = 0
    p1_goals = 0
    p2_goals = 0
    p1_assists = 0
    p2_assists = 0

    p1_biggest_margin = -1
    p1_biggest_win: Optional[str] = None
    p2_biggest_margin = -1
    p2_biggest_win: Optional[str] = None

    for m in matches:
        p_map = {canonicalize_player_name(ps.player).lower(): ps for ps in m.player_stats}
        if k1 not in p_map or k2 not in p_map:
            continue

        s1 = p_map[k1]
        s2 = p_map[k2]

        s1_won = s1.result in (MatchResult.WIN, MatchResult.HOME_WIN)
        s1_lost = s1.result in (MatchResult.LOSS, MatchResult.AWAY_WIN)
        s2_won = s2.result in (MatchResult.WIN, MatchResult.HOME_WIN)
        s2_lost = s2.result in (MatchResult.LOSS, MatchResult.AWAY_WIN)

        is_opposing = False
        if (s1_won and s2_lost) or (s1_lost and s2_won):
            is_opposing = True
        elif s1.result == MatchResult.DRAW and s2.result == MatchResult.DRAW:
            p_list = [canonicalize_player_name(ps.player).lower() for ps in m.player_stats]
            idx1 = p_list.index(k1)
            idx2 = p_list.index(k2)
            half = len(p_list) // 2
            if (idx1 < half and idx2 >= half) or (idx1 >= half and idx2 < half):
                is_opposing = True

        if not is_opposing:
            continue

        p1_g = s1.goals
        p2_g = s2.goals
        p1_a = s1.assists
        p2_a = s2.assists

        p1_goals += p1_g
        p2_goals += p2_g
        p1_assists += p1_a
        p2_assists += p2_a

        h_score, a_score = extract_match_scores(m)
        margin = abs(h_score - a_score)
        match_scoreline = f"{h_score}-{a_score}"

        if s1_won:
            p1_wins += 1
            winner = c1
            if margin > p1_biggest_margin:
                p1_biggest_margin = margin
                p1_biggest_win = f"{match_scoreline} (+{margin} goals)"
        elif s2_won:
            p2_wins += 1
            winner = c2
            if margin > p2_biggest_margin:
                p2_biggest_margin = margin
                p2_biggest_win = f"{match_scoreline} (+{margin} goals)"
        else:
            draws += 1
            winner = "Draw"

        clashes.append({
            "match_id": m.match_id,
            "date": m.date,
            "winner": winner,
            "scoreline": match_scoreline,
            "p1_goals": p1_g,
            "p2_goals": p2_g,
            "p1_assists": p1_a,
            "p2_assists": p2_a,
        })

    total_clashes = len(clashes)
    p1_wr = round((p1_wins / total_clashes) * 100.0, 1) if total_clashes > 0 else 0.0
    p2_wr = round((p2_wins / total_clashes) * 100.0, 1) if total_clashes > 0 else 0.0

    if p1_wins > p2_wins:
        diff = p1_wins - p2_wins
        bragging_rights = f"👑 **{c1}** holds bragging rights (+{diff} win{'s' if diff > 1 else ''})"
        leader = c1
    elif p2_wins > p1_wins:
        diff = p2_wins - p1_wins
        bragging_rights = f"👑 **{c2}** holds bragging rights (+{diff} win{'s' if diff > 1 else ''})"
        leader = c2
    else:
        bragging_rights = f"🤝 **Dead Heat** — Tied at {p1_wins} win{'s' if p1_wins != 1 else ''} each!"
        leader = "Tied"

    current_streak = "None"
    if clashes:
        last_winner = clashes[-1]["winner"]
        if last_winner in (c1, c2):
            streak_count = 0
            for c in reversed(clashes):
                if c["winner"] == last_winner:
                    streak_count += 1
                else:
                    break
            current_streak = f"{last_winner} ({streak_count}W)"

    return {
        "player1": c1,
        "player2": c2,
        "total_clashes": total_clashes,
        "p1_wins": p1_wins,
        "p2_wins": p2_wins,
        "draws": draws,
        "p1_win_rate": p1_wr,
        "p2_win_rate": p2_wr,
        "p1_goals": p1_goals,
        "p2_goals": p2_goals,
        "p1_assists": p1_assists,
        "p2_assists": p2_assists,
        "leader": leader,
        "bragging_rights": bragging_rights,
        "current_streak": current_streak,
        "p1_biggest_win": p1_biggest_win,
        "p2_biggest_win": p2_biggest_win,
        "recent_clashes": list(reversed(clashes))[:5],
    }


def calculate_duo_card_stats(
    player1_name: str,
    player2_name: str,
    leaderboard_records: List[dict],
    matches: List[MatchRecord],
) -> dict:
    """Computes partnership synergy and individual attributes for an EA FC Dynamic Duo card."""
    c1 = canonicalize_player_name(player1_name)
    c2 = canonicalize_player_name(player2_name)
    k1 = c1.strip().lower()
    k2 = c2.strip().lower()

    if not k1 or not k2:
        raise ValueError("Both player names must be non-empty.")
    if k1 == k2:
        raise ValueError(f"Cannot form a duo card with the same player ({c1}).")

    card1 = calculate_player_card_stats(c1, leaderboard_records, matches)
    card2 = calculate_player_card_stats(c2, leaderboard_records, matches)

    if not card1:
        raise ValueError(f"Player '{c1}' not found in leaderboard.")
    if not card2:
        raise ValueError(f"Player '{c2}' not found in leaderboard.")

    matches_together = 0
    wins_together = 0
    losses_together = 0
    draws_together = 0
    combined_goals = 0
    combined_assists = 0
    clean_sheets_together = 0

    for m in matches:
        p_map = {canonicalize_player_name(ps.player).lower(): ps for ps in m.player_stats}
        if k1 not in p_map or k2 not in p_map:
            continue

        s1 = p_map[k1]
        s2 = p_map[k2]

        s1_won = s1.result in (MatchResult.WIN, MatchResult.HOME_WIN)
        s1_lost = s1.result in (MatchResult.LOSS, MatchResult.AWAY_WIN)
        s2_won = s2.result in (MatchResult.WIN, MatchResult.HOME_WIN)
        s2_lost = s2.result in (MatchResult.LOSS, MatchResult.AWAY_WIN)

        is_partner = False
        if (s1_won and s2_won) or (s1_lost and s2_lost):
            is_partner = True
        elif s1.result == MatchResult.DRAW and s2.result == MatchResult.DRAW:
            p_list = [canonicalize_player_name(ps.player).lower() for ps in m.player_stats]
            idx1 = p_list.index(k1)
            idx2 = p_list.index(k2)
            half = len(p_list) // 2
            if (idx1 < half and idx2 < half) or (idx1 >= half and idx2 >= half):
                is_partner = True

        if not is_partner:
            continue

        matches_together += 1
        if s1_won:
            wins_together += 1
            if get_match_player_conceded_goals(m, s1) == 0:
                clean_sheets_together += 1
        elif s1_lost:
            losses_together += 1
        else:
            draws_together += 1

        combined_goals += (s1.goals + s2.goals)
        combined_assists += (s1.assists + s2.assists)

    win_rate = round((wins_together / matches_together) * 100.0, 1) if matches_together > 0 else 0.0

    if win_rate >= 80.0 and matches_together >= 3:
        synergy_tier = "DIAMOND"
        synergy_color = (45, 212, 191)
        title = "The Lethal Duo"
    elif win_rate >= 65.0 and matches_together >= 2:
        synergy_tier = "GOLD"
        synergy_color = (250, 204, 21)
        title = "Dynamic Playmakers"
    elif win_rate >= 50.0:
        synergy_tier = "SILVER"
        synergy_color = (148, 163, 184)
        title = "Solid Partnership"
    else:
        synergy_tier = "BRONZE"
        synergy_color = (180, 83, 9)
        title = "Developing Chemistry"

    avg_ovr = int(round((card1["rating"] + card2["rating"]) / 2.0))

    return {
        "player1": c1,
        "player2": c2,
        "card1": card1,
        "card2": card2,
        "avg_ovr": avg_ovr,
        "matches_together": matches_together,
        "wins_together": wins_together,
        "losses_together": losses_together,
        "draws_together": draws_together,
        "win_rate": win_rate,
        "combined_goals": combined_goals,
        "combined_assists": combined_assists,
        "combined_ga": combined_goals + combined_assists,
        "clean_sheets_together": clean_sheets_together,
        "synergy_tier": synergy_tier,
        "synergy_color": synergy_color,
        "title": title,
    }


def order_defenders_lineup(defenders: List[dict]) -> List[dict]:
    """Positions defenders left-to-right: LB on the left end, CBs in the middle, RB on the right end."""
    lb = [p for p in defenders if p.get("position") == "LB"]
    cb = [p for p in defenders if p.get("position") == "CB"]
    rb = [p for p in defenders if p.get("position") == "RB"]
    others = [p for p in defenders if p.get("position") not in ("LB", "CB", "RB")]
    cb_sorted = sorted(cb + others, key=lambda p: (p.get("rating", 0), p.get("defending", 0)), reverse=True)
    return lb + cb_sorted + rb


def order_attackers_lineup(attackers: List[dict]) -> List[dict]:
    """Positions attackers left-to-right: LW on the left end, STs in the middle, RW on the right end."""
    lw = [p for p in attackers if p.get("position") == "LW"]
    st = [p for p in attackers if p.get("position") in ("ST", "CF")]
    rw = [p for p in attackers if p.get("position") == "RW"]
    others = [p for p in attackers if p.get("position") not in ("LW", "ST", "CF", "RW")]
    st_sorted = sorted(st + others, key=lambda p: (p.get("rating", 0), p.get("shooting", 0)), reverse=True)
    return lw + st_sorted + rw


def order_midfielders_lineup(midfielders: List[dict]) -> List[dict]:
    """Positions midfielders left-to-right: LCM on the left, CAM/CDM in the center, RCM on the right."""
    cam = [p for p in midfielders if p.get("position") == "CAM"]
    cdm = [p for p in midfielders if p.get("position") == "CDM"]
    cm = [p for p in midfielders if p.get("position") == "CM"]
    others = [p for p in midfielders if p.get("position") not in ("CAM", "CDM", "CM")]
    all_cm = cm + others
    # In 4-3-3: LCM, CAM, RCM
    if cam and len(all_cm) >= 2:
        return [all_cm[0]] + cam + all_cm[1:]
    elif cam:
        return cam + all_cm
    elif cdm and len(all_cm) >= 2:
        return [all_cm[0]] + cdm + all_cm[1:]
    return sorted(midfielders, key=lambda p: p.get("rating", 0), reverse=True)


def calculate_squad_lineup(
    leaderboard_records: List[dict],
    matches: List[MatchRecord],
) -> dict:
    """Classifies registered players into starting tactical lineup zones."""
    all_cards = []
    for r in leaderboard_records:
        name = str(r.get("player", ""))
        c = calculate_player_card_stats(name, leaderboard_records, matches)
        if c and c.get("matches_played", 0) > 0:
            all_cards.append(c)

    # Ensure Harold (99 GK Tribute) is included in starting lineup and TOTW
    has_harold = any(str(c.get("player", "")).strip().lower() == "harold" for c in all_cards)
    if not has_harold:
        harold_card = calculate_player_card_stats("Harold", leaderboard_records, matches)
        if harold_card:
            all_cards.append(harold_card)

    all_cards.sort(key=lambda x: (x["rating"], x["dribbling"] + x["shooting"]), reverse=True)

    goalkeepers = [c for c in all_cards if c["position"] == "GK"]
    attackers = [c for c in all_cards if c["position"] in ("ST", "LW", "RW")]
    midfielders = [c for c in all_cards if c["position"] in ("CAM", "CM", "CDM")]
    defenders = [c for c in all_cards if c["position"] in ("CB", "LB", "RB")]

    unassigned = [
        c for c in all_cards
        if c not in attackers and c not in midfielders and c not in defenders and c not in goalkeepers
    ]
    for u in unassigned:
        if u["shooting"] >= 80:
            attackers.append(u)
        elif u["defending"] >= 76:
            defenders.append(u)
        else:
            midfielders.append(u)

    # Perfectly align positions left-to-right (LB left, RB right, LW left, RW right)
    defenders = order_defenders_lineup(defenders)
    midfielders = order_midfielders_lineup(midfielders)
    attackers = order_attackers_lineup(attackers)

    avg_ovr = int(round(sum(c["rating"] for c in all_cards) / len(all_cards))) if all_cards else 70

    return {
        "squad_size": len(all_cards),
        "avg_ovr": avg_ovr,
        "goalkeepers": goalkeepers,
        "attackers": attackers,
        "midfielders": midfielders,
        "defenders": defenders,
        "all_players": all_cards,
    }
