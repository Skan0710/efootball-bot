"""Unit tests for statistics, calculations, and leaderboard generation."""

import pytest

from models import LeaderboardCategory, MatchResult, PlayerAggregateRecord, PlayerStat
from stats import (
    calculate_player_aggregates,
    get_all_teammates,
    get_leaderboard,
    get_player_record,
)


def test_player_aggregate_zero_matches() -> None:
    """Verifies that 0 matches played produces 0.0 rates without division by zero."""
    record = PlayerAggregateRecord.compute(
        player="NewPlayer",
        matches_played=0,
        goals=0,
        assists=0,
        wins=0,
        losses=0,
        draws=0,
    )
    assert record.matches_played == 0
    assert record.win_rate == 0.0
    assert record.loss_rate == 0.0
    assert record.draw_rate == 0.0


def test_player_aggregate_rate_calculations() -> None:
    """Verifies accurate calculation of win, loss, and draw percentages."""
    record = PlayerAggregateRecord.compute(
        player="SkanRocky",
        matches_played=10,
        goals=15,
        assists=8,
        wins=7,
        losses=2,
        draws=1,
    )
    assert record.win_rate == 70.0
    assert record.loss_rate == 20.0
    assert record.draw_rate == 10.0


def test_calculate_player_aggregates_multiple_matches() -> None:
    """Verifies summing stats across multiple matches and case-insensitivity."""
    raw_stats = [
        PlayerStat(player="SkanRocky", goals=2, assists=1, result=MatchResult.WIN),
        PlayerStat(player="skanrocky", goals=1, assists=0, result=MatchResult.LOSS),
        PlayerStat(player="SKANROCKY", goals=0, assists=2, result=MatchResult.DRAW),
        PlayerStat(player="lillym", goals=1, assists=3, result=MatchResult.WIN),
    ]

    aggregates = calculate_player_aggregates(raw_stats)
    assert len(aggregates) == 2
    assert "skan" in aggregates
    assert "lilly" in aggregates

    skan = aggregates["skan"]
    assert skan.matches_played == 3
    assert skan.goals == 3
    assert skan.assists == 3
    assert skan.wins == 1
    assert skan.losses == 1
    assert skan.draws == 1
    assert skan.win_rate == 33.3
    assert skan.loss_rate == 33.3
    assert skan.draw_rate == 33.3

    lilly = aggregates["lilly"]
    assert lilly.matches_played == 1
    assert lilly.goals == 1
    assert lilly.assists == 3
    assert lilly.wins == 1
    assert lilly.win_rate == 100.0


def test_get_player_record_case_insensitive() -> None:
    """Verifies finding player records regardless of letter casing."""
    raw_stats = [
        PlayerStat(player="CaptainTsubasa", goals=5, assists=2, result=MatchResult.WIN)
    ]
    aggregates = calculate_player_aggregates(raw_stats)

    assert get_player_record("captaintsubasa", aggregates) is not None
    assert get_player_record("CAPTAINTSUBASA", aggregates) is not None
    assert get_player_record("CaptainTsubasa", aggregates) is not None
    assert get_player_record("NonExistent", aggregates) is None


def test_leaderboard_all_categories() -> None:
    """Verifies correct ranking order across all supported leaderboard categories."""
    raw_stats = [
        PlayerStat(player="Striker", goals=10, assists=1, result=MatchResult.WIN),
        PlayerStat(player="Striker", goals=5, assists=0, result=MatchResult.LOSS),
        PlayerStat(player="Playmaker", goals=2, assists=12, result=MatchResult.WIN),
        PlayerStat(player="Playmaker", goals=1, assists=3, result=MatchResult.WIN),
        PlayerStat(player="Defender", goals=0, assists=1, result=MatchResult.DRAW),
    ]
    aggregates = calculate_player_aggregates(raw_stats)

    # Goals Leaderboard
    lb_goals = get_leaderboard(aggregates, LeaderboardCategory.GOALS)
    assert lb_goals[0].player == "Striker"
    assert lb_goals[0].goals == 15

    # Assists Leaderboard
    lb_assists = get_leaderboard(aggregates, LeaderboardCategory.ASSISTS)
    assert lb_assists[0].player == "Playmaker"
    assert lb_assists[0].assists == 15

    # Wins Leaderboard
    lb_wins = get_leaderboard(aggregates, LeaderboardCategory.WINS)
    assert lb_wins[0].player == "Playmaker"
    assert lb_wins[0].wins == 2

    # Losses Leaderboard
    lb_losses = get_leaderboard(aggregates, LeaderboardCategory.LOSSES)
    assert lb_losses[0].player == "Striker"
    assert lb_losses[0].losses == 1

    # Draws Leaderboard
    lb_draws = get_leaderboard(aggregates, LeaderboardCategory.DRAWS)
    assert lb_draws[0].player == "Defender"
    assert lb_draws[0].draws == 1

    # Win Rate Leaderboard
    lb_win_rate = get_leaderboard(aggregates, LeaderboardCategory.WIN_RATE)
    assert lb_win_rate[0].player == "Playmaker"
    assert lb_win_rate[0].win_rate == 100.0

    # Matches Played
    lb_matches = get_leaderboard(aggregates, LeaderboardCategory.MATCHES_PLAYED)
    assert lb_matches[0].matches_played == 2


def test_empty_aggregates() -> None:
    """Verifies handling empty dataset gracefully."""
    empty_dict = {}
    assert get_leaderboard(empty_dict, LeaderboardCategory.GOALS) == []
    assert get_all_teammates(empty_dict) == []
    assert get_player_record("Anyone", empty_dict) is None


def test_get_all_teammates_alphabetical() -> None:
    """Verifies teammate list is sorted alphabetically by name."""
    raw_stats = [
        PlayerStat(player="Zack", goals=1, assists=0, result=MatchResult.WIN),
        PlayerStat(player="Alice", goals=2, assists=1, result=MatchResult.WIN),
        PlayerStat(player="Bob", goals=0, assists=3, result=MatchResult.WIN),
    ]
    aggregates = calculate_player_aggregates(raw_stats)
    teammates = get_all_teammates(aggregates)
    names = [t.player for t in teammates]
    assert names == ["Alice", "Bob", "Zack"]


def test_player_name_canonicalization() -> None:
    """Verifies all specified player aliases map to their exact canonical identity."""
    from models import canonicalize_player_name

    # Lilly
    assert canonicalize_player_name("lillym") == "Lilly"
    assert canonicalize_player_name("lilly") == "Lilly"
    assert canonicalize_player_name("maple") == "Lilly"
    assert canonicalize_player_name("MAPLE") == "Lilly"

    # Annie
    assert canonicalize_player_name("AnnieSynx") == "Annie"
    assert canonicalize_player_name("annie") == "Annie"
    assert canonicalize_player_name("ANNIE") == "Annie"

    # Naka
    assert canonicalize_player_name("Son") == "Naka"
    assert canonicalize_player_name("SoNaka") == "Naka"
    assert canonicalize_player_name("sonaka") == "Naka"
    assert canonicalize_player_name("Naka") == "Naka"

    # Suri
    assert canonicalize_player_name("SuriBG") == "Suri"
    assert canonicalize_player_name("Suri") == "Suri"
    assert canonicalize_player_name("suribg6") == "Suri"

    # Skan
    assert canonicalize_player_name("SkanxPrznt07") == "Skan"
    assert canonicalize_player_name("SkanRocky") == "Skan"
    assert canonicalize_player_name("skan") == "Skan"

    # Azlan
    assert canonicalize_player_name("Azlan") == "Azlan"
    assert canonicalize_player_name("azlan") == "Azlan"
    assert canonicalize_player_name("Azlan19Siuu") == "Azlan"
    assert canonicalize_player_name("azlan19siuu") == "Azlan"


def test_multiple_aliases_merged_into_single_player_record() -> None:
    """Verifies stats for different aliases merge into one single canonical player record."""
    raw_stats = [
        PlayerStat(player="lillym", goals=1, assists=1, result=MatchResult.WIN),
        PlayerStat(player="maple", goals=2, assists=0, result=MatchResult.WIN),
        PlayerStat(player="Lilly", goals=0, assists=2, result=MatchResult.LOSS),
        PlayerStat(player="SkanxPrznt07", goals=3, assists=1, result=MatchResult.WIN),
        PlayerStat(player="SkanRocky", goals=2, assists=1, result=MatchResult.WIN),
        PlayerStat(player="SoNaka", goals=1, assists=0, result=MatchResult.WIN),
        PlayerStat(player="Son", goals=1, assists=2, result=MatchResult.LOSS),
        PlayerStat(player="Azlan", goals=1, assists=1, result=MatchResult.WIN),
        PlayerStat(player="Azlan19Siuu", goals=2, assists=0, result=MatchResult.WIN),
    ]

    aggregates = calculate_player_aggregates(raw_stats)

    # All Lilly variants merged into one "Lilly"
    lilly_rec = get_player_record("Lilly", aggregates)
    assert lilly_rec is not None
    assert lilly_rec.player == "Lilly"
    assert lilly_rec.matches_played == 3
    assert lilly_rec.goals == 3
    assert lilly_rec.assists == 3
    assert lilly_rec.wins == 2
    assert lilly_rec.losses == 1

    # Look up using alias
    assert get_player_record("maple", aggregates) == lilly_rec
    assert get_player_record("lillym", aggregates) == lilly_rec

    # All Skan variants merged into "Skan"
    skan_rec = get_player_record("Skan", aggregates)
    assert skan_rec is not None
    assert skan_rec.player == "Skan"
    assert skan_rec.matches_played == 2
    assert skan_rec.goals == 5
    assert skan_rec.assists == 2

    # All Naka variants merged into "Naka"
    naka_rec = get_player_record("Naka", aggregates)
    assert naka_rec is not None
    assert naka_rec.player == "Naka"
    assert naka_rec.matches_played == 2
    assert naka_rec.goals == 2
    assert naka_rec.assists == 2

    # All Azlan variants merged into "Azlan"
    azlan_rec = get_player_record("Azlan", aggregates)
    assert azlan_rec is not None
    assert azlan_rec.player == "Azlan"
    assert azlan_rec.matches_played == 2
    assert azlan_rec.goals == 3
    assert azlan_rec.assists == 1
    assert get_player_record("Azlan19Siuu", aggregates) == azlan_rec


