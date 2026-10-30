"""Statistical aggregation and leaderboard computation for eFootball co-op."""

from __future__ import annotations

from typing import Dict, List, Optional

from models import LeaderboardCategory, MatchResult, PlayerAggregateRecord, PlayerStat, canonicalize_player_name


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
            }
        else:
            # Keep latest non-empty casing
            player_data[normalized]["display_name"] = stat.player.strip()

        data = player_data[normalized]
        data["matches"] += 1
        data["goals"] += stat.goals
        data["assists"] += stat.assists

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
    else:
        # Fallback to goals
        records.sort(key=lambda r: (r.goals, r.assists), reverse=True)

    return records


def get_all_teammates(
    aggregates: Dict[str, PlayerAggregateRecord],
) -> List[PlayerAggregateRecord]:
    """Returns all registered players sorted alphabetically by name."""
    return sorted(aggregates.values(), key=lambda r: r.player.lower())
