"""Unit tests for statistics, calculations, and leaderboard generation."""

from datetime import datetime

import pytest

from models import LeaderboardCategory, MatchRecord, MatchResult, PlayerAggregateRecord, PlayerStat
from stats import (
    calculate_best_teams,
    calculate_hall_of_fame,
    calculate_head_to_head,
    calculate_match_motm_and_milestones,
    calculate_player_aggregates,
    calculate_player_card_stats,
    calculate_player_chemistry,
    calculate_player_form,
    calculate_potm_rankings,
    calculate_potw_rankings,
    generate_fair_teams,
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

    # Jonny
    assert canonicalize_player_name("Jonny") == "Jonny"
    assert canonicalize_player_name("jonny") == "Jonny"
    assert canonicalize_player_name("JonnyH") == "Jonny"
    assert canonicalize_player_name("jonnyh") == "Jonny"
    assert canonicalize_player_name("itsJonnyH") == "Jonny"
    assert canonicalize_player_name("itsjonnyh") == "Jonny"
    assert canonicalize_player_name("itsJonaldinhoH") == "Jonny"
    assert canonicalize_player_name("itsjonaldinhoh") == "Jonny"
    assert canonicalize_player_name("Jonaldinho") == "Jonny"

    # Ashy
    assert canonicalize_player_name("Ashy") == "Ashy"
    assert canonicalize_player_name("ashy") == "Ashy"
    assert canonicalize_player_name("MrAshy21") == "Ashy"
    assert canonicalize_player_name("mrashy21") == "Ashy"

    # Roshan
    assert canonicalize_player_name("16_roshan") == "Roshan"
    assert canonicalize_player_name("Roshan16") == "Roshan"
    assert canonicalize_player_name("roshan") == "Roshan"


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
        PlayerStat(player="JonnyH", goals=1, assists=0, result=MatchResult.WIN),
        PlayerStat(player="itsJonnyH", goals=0, assists=1, result=MatchResult.LOSS),
        PlayerStat(player="itsJonaldinhoH", goals=1, assists=1, result=MatchResult.WIN),
        PlayerStat(player="Ashy", goals=1, assists=0, result=MatchResult.WIN),
        PlayerStat(player="MrAshy21", goals=2, assists=1, result=MatchResult.LOSS),
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

    # All Jonny variants merged into "Jonny"
    jonny_rec = get_player_record("Jonny", aggregates)
    assert jonny_rec is not None
    assert jonny_rec.player == "Jonny"
    assert jonny_rec.matches_played == 3
    assert jonny_rec.goals == 2
    assert jonny_rec.assists == 2
    assert get_player_record("JonnyH", aggregates) == jonny_rec
    assert get_player_record("itsJonnyH", aggregates) == jonny_rec
    assert get_player_record("itsJonaldinhoH", aggregates) == jonny_rec

    # All Ashy variants merged into "Ashy"
    ashy_rec = get_player_record("Ashy", aggregates)
    assert ashy_rec is not None
    assert ashy_rec.player == "Ashy"
    assert ashy_rec.matches_played == 2
    assert ashy_rec.goals == 3
    assert ashy_rec.assists == 1
    assert get_player_record("MrAshy21", aggregates) == ashy_rec


def test_hattrick_counting_and_leaderboard_ranking() -> None:
    """Verifies hattricks (3+ goals in a single game) are counted and ranked properly."""
    raw_stats = [
        PlayerStat(player="Suri", goals=3, assists=1, result=MatchResult.WIN),
        PlayerStat(player="Suri", goals=4, assists=0, result=MatchResult.WIN),
        PlayerStat(player="Suri", goals=2, assists=2, result=MatchResult.LOSS),
        PlayerStat(player="Skan", goals=3, assists=0, result=MatchResult.WIN),
        PlayerStat(player="Annie", goals=2, assists=3, result=MatchResult.WIN),
    ]

    aggs = calculate_player_aggregates(raw_stats)
    assert aggs["suri"].hattricks == 2
    assert aggs["skan"].hattricks == 1
    assert aggs["annie"].hattricks == 0

    ranked = get_leaderboard(aggs, LeaderboardCategory.HATTRICK)
    assert [r.player for r in ranked] == ["Suri", "Skan", "Annie"]
    assert LeaderboardCategory.from_str("hattrick") == LeaderboardCategory.HATTRICK
    assert LeaderboardCategory.from_str("hat-trick") == LeaderboardCategory.HATTRICK


def test_potw_rankings_calendar_week_and_weights() -> None:
    """Verifies Mon-Sun calendar week filtering and 0.4G + 0.4A + 0.2W formula."""
    # Reference date: Wednesday Nov 11, 2026 -> Week is Mon Nov 9 to Sun Nov 15, 2026
    ref = datetime(2026, 11, 11, 12, 0, 0)

    matches = [
        # Sunday Nov 8 (last week - should be excluded for weeks_ago=0, included for weeks_ago=1)
        MatchRecord(
            match_id="M-OLD",
            date="2026-11-08 23:59:59",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Annie", goals=5, assists=5, result=MatchResult.WIN),
            ],
        ),
        # Monday Nov 9 00:00:00 (start of current calendar week - included)
        MatchRecord(
            match_id="M-MON",
            date="2026-11-09 00:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Suri", goals=2, assists=1, result=MatchResult.WIN),
                PlayerStat(player="Skan", goals=1, assists=2, result=MatchResult.LOSS),
            ],
        ),
        # Wednesday Nov 11 (current week - included)
        MatchRecord(
            match_id="M-WED",
            date="2026-11-11 10:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Suri", goals=3, assists=2, result=MatchResult.WIN),
                PlayerStat(player="Ashy", goals=2, assists=3, result=MatchResult.WIN),
            ],
        ),
    ]

    rankings, w_start, w_end = calculate_potw_rankings(matches, reference_date=ref, weeks_ago=0)
    assert w_start == datetime(2026, 11, 9, 0, 0, 0)
    assert w_end.date() == datetime(2026, 11, 15).date()

    # Suri: 5G, 3A, 2W -> 0.4*5 + 0.4*3 + 0.2*2 = 2.0 + 1.2 + 0.4 = 3.6
    # Ashy: 2G, 3A, 1W -> 0.4*2 + 0.4*3 + 0.2*1 = 0.8 + 1.2 + 0.2 = 2.2
    # Skan: 1G, 2A, 0W -> 0.4*1 + 0.4*2 + 0.2*0 = 0.4 + 0.8 + 0.0 = 1.2
    assert [r["player"] for r in rankings] == ["Suri", "Ashy", "Skan"]
    assert rankings[0]["score"] == 3.6
    assert rankings[1]["score"] == 2.2
    assert rankings[2]["score"] == 1.2

    # Previous week (weeks_ago=1) includes M-OLD (Annie: 5G, 5A, 1W -> 4.2)
    prev_rankings, _, _ = calculate_potw_rankings(matches, reference_date=ref, weeks_ago=1)
    assert len(prev_rankings) == 1
    assert prev_rankings[0]["player"] == "Annie"
    assert prev_rankings[0]["score"] == 4.2


def test_best_team_rankings() -> None:
    """Verifies teammate duo wins, win-rate tie-breaking, and alias merging."""
    matches = [
        MatchRecord(
            match_id="M-1",
            date="2026-11-10 12:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Suri", goals=2, assists=1, result=MatchResult.WIN),
                PlayerStat(player="MrAshy21", goals=1, assists=2, result=MatchResult.WIN),
                PlayerStat(player="Skan", goals=0, assists=0, result=MatchResult.LOSS),
                PlayerStat(player="Annie", goals=0, assists=0, result=MatchResult.LOSS),
            ],
        ),
        MatchRecord(
            match_id="M-2",
            date="2026-11-11 12:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Ashy", goals=2, assists=0, result=MatchResult.WIN),
                PlayerStat(player="Suri", goals=1, assists=2, result=MatchResult.WIN),
                PlayerStat(player="Skan", goals=1, assists=0, result=MatchResult.LOSS),
                PlayerStat(player="Annie", goals=0, assists=1, result=MatchResult.LOSS),
            ],
        ),
        MatchRecord(
            match_id="M-3",
            date="2026-11-11 15:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Skan", goals=2, assists=0, result=MatchResult.WIN),
                PlayerStat(player="Suri", goals=1, assists=1, result=MatchResult.WIN),
                PlayerStat(player="Ashy", goals=0, assists=1, result=MatchResult.LOSS),
                PlayerStat(player="Annie", goals=1, assists=0, result=MatchResult.LOSS),
            ],
        ),
    ]

    teams = calculate_best_teams(matches)
    assert teams[0]["team_name"] == "Ashy & Suri"
    assert teams[0]["wins"] == 2
    assert teams[0]["losses"] == 0
    assert teams[0]["win_rate"] == 100.0
    assert teams[0]["goals"] == 6

    skan_suri = next(t for t in teams if t["team_name"] == "Skan & Suri")
    assert skan_suri["wins"] == 1
    assert skan_suri["losses"] == 0

    annie_skan = next(t for t in teams if t["team_name"] == "Annie & Skan")
    assert annie_skan["wins"] == 0
    assert annie_skan["losses"] == 2


def test_best_team_order_and_case_independence() -> None:
    """Verifies Skan & Suri and Suri & Skan (and case/alias variants) count as the exact same team."""
    matches = [
        MatchRecord(
            match_id="M-A",
            date="2026-11-11 10:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Skan", goals=2, assists=1, result=MatchResult.WIN),
                PlayerStat(player="Suri", goals=1, assists=1, result=MatchResult.WIN),
            ],
        ),
        MatchRecord(
            match_id="M-B",
            date="2026-11-11 11:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Suri", goals=2, assists=0, result=MatchResult.WIN),
                PlayerStat(player="Skan", goals=1, assists=2, result=MatchResult.WIN),
            ],
        ),
        MatchRecord(
            match_id="M-C",
            date="2026-11-11 12:00:00",
            result=MatchResult.AWAY_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="GuestB", goals=1, assists=0, result=MatchResult.LOSS),
                PlayerStat(player="GuestA", goals=0, assists=1, result=MatchResult.LOSS),
            ],
        ),
        MatchRecord(
            match_id="M-D",
            date="2026-11-11 13:00:00",
            result=MatchResult.AWAY_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="guesta", goals=2, assists=0, result=MatchResult.LOSS),
                PlayerStat(player="guestb", goals=1, assists=1, result=MatchResult.LOSS),
            ],
        ),
    ]

    teams = calculate_best_teams(matches)
    assert len(teams) == 2

    skan_suri = teams[0]
    assert skan_suri["team_name"] == "Skan & Suri"
    assert skan_suri["matches_played"] == 2
    assert skan_suri["wins"] == 2
    assert skan_suri["goals"] == 6

    guests = teams[1]
    assert guests["team_name"] == "GuestA & GuestB"
    assert guests["matches_played"] == 2
    assert guests["losses"] == 2
    assert guests["goals"] == 4


def test_potm_rankings_calendar_month_and_rollover() -> None:
    """Verifies calendar month filtering, weighted scoring, and Jan->Dec year rollover."""
    ref_jan = datetime(2027, 1, 15, 10, 0, 0)
    matches = [
        MatchRecord(
            match_id="M-DEC",
            date="2026-12-20 18:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Skan", goals=4, assists=2, result=MatchResult.WIN),
            ],
        ),
        MatchRecord(
            match_id="M-JAN-1",
            date="2027-01-01 00:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Ashy", goals=3, assists=1, result=MatchResult.WIN),
                PlayerStat(player="Suri", goals=1, assists=2, result=MatchResult.LOSS),
            ],
        ),
        MatchRecord(
            match_id="M-JAN-2",
            date="2027-01-31 23:59:59",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Suri", goals=2, assists=3, result=MatchResult.WIN),
            ],
        ),
    ]

    jan_rankings, m_start, m_end = calculate_potm_rankings(
        matches, reference_date=ref_jan, months_ago=0
    )
    assert m_start == datetime(2027, 1, 1, 0, 0, 0)
    assert m_end.date() == datetime(2027, 1, 31).date()

    # Suri: 3G, 5A, 1W -> base 3.4, POTW pts 8 -> final score 3.4
    # Ashy: 3G, 1A, 1W -> base 1.8, POTW pts 5 -> 0.9*1.8 + 0.1*3.4*(5/8) = 1.83
    assert [r["player"] for r in jan_rankings] == ["Suri", "Ashy"]
    assert jan_rankings[0]["score"] == 3.4
    assert jan_rankings[1]["base_score"] == 1.8
    assert jan_rankings[1]["score"] == 1.83

    # months_ago=1 from Jan 2027 rolls back cleanly to Dec 2026
    dec_rankings, dec_start, dec_end = calculate_potm_rankings(
        matches, reference_date=ref_jan, months_ago=1
    )
    assert dec_start == datetime(2026, 12, 1, 0, 0, 0)
    assert dec_end.date() == datetime(2026, 12, 31).date()
    assert len(dec_rankings) == 1
    assert dec_rankings[0]["player"] == "Skan"
    assert dec_rankings[0]["score"] == 2.6


def test_generate_fair_teams_4_5_6() -> None:
    """Verifies optimal team balancing for 4 (2v2), 5 (3v2), and 6 (3v3) players."""
    records = [
        {"player": "Suri", "matches_played": 10, "win_rate": 80.0, "gapg": 3.0, "gpg": 2.0},   # rating 87.0
        {"player": "Ashy", "matches_played": 4, "win_rate": 50.0, "gapg": 2.0, "gpg": 1.0},    # rating 55.0
        {"player": "Skan", "matches_played": 10, "win_rate": 40.0, "gapg": 1.6, "gpg": 1.0},   # rating 45.0
        {"player": "Lilly", "matches_played": 5, "win_rate": 0.0, "gapg": 0.6, "gpg": 0.6},    # rating 12.0
        {"player": "Azlan", "matches_played": 8, "win_rate": 50.0, "gapg": 1.5, "gpg": 0.5},   # rating 45.0
        {"player": "Annie", "matches_played": 8, "win_rate": 50.0, "gapg": 1.2, "gpg": 0.6},   # rating 41.0
    ]

    # 4 players: FC 27 OVR ratings [88, 77, 76, 68] -> optimal is (88+68=156) vs (77+76=153), diff=3.0
    split_4 = generate_fair_teams(["Suri", "Ashy", "SkanRocky", "maple"], records)
    assert len(split_4["team_a"]) == 2
    assert len(split_4["team_b"]) == 2
    assert split_4["rating_diff"] == 3.0

    # 5 players: 3v2 split
    split_5 = generate_fair_teams(["Suri", "Ashy", "Skan", "Lilly", "Azlan"], records)
    assert len(split_5["team_a"]) == 3
    assert len(split_5["team_b"]) == 2
    assert split_5["rating_diff"] <= 60.0

    # 6 players: 3v3 split
    split_6 = generate_fair_teams(["Suri", "Ashy", "Skan", "Lilly", "Azlan", "Annie"], records)
    assert len(split_6["team_a"]) == 3
    assert len(split_6["team_b"]) == 3
    assert split_6["rating_diff"] <= 5.0

    # Duplicate name should raise ValueError
    with pytest.raises(ValueError, match="Duplicate"):
        generate_fair_teams(["Suri", "Suri", "Skan", "Ashy"], records)


def test_calculate_player_form_and_streaks() -> None:
    """Verifies last-N form ordering, current streak, and longest win streak calculation."""
    matches = [
        MatchRecord(
            match_id=f"M-{i}",
            date=f"2026-11-10 1{i}:00:00",
            result=MatchResult.HOME_WIN if res == MatchResult.WIN else MatchResult.AWAY_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Suri", goals=1, assists=1, result=res),
                PlayerStat(player="Skan", goals=0, assists=0, result=MatchResult.LOSS if res == MatchResult.WIN else MatchResult.WIN),
            ],
        )
        for i, res in enumerate([
            MatchResult.WIN,
            MatchResult.WIN,
            MatchResult.WIN,
            MatchResult.LOSS,
            MatchResult.WIN,
            MatchResult.WIN,
        ])
    ]

    form = calculate_player_form("SuriBG", matches, window=5)
    assert form["player"] == "Suri"
    assert form["total_matches"] == 6
    assert form["form_list"] == ["W", "W", "L", "W", "W"]  # Latest first
    assert form["current_streak_type"] == "W"
    assert form["current_streak_count"] == 2
    assert form["longest_win_streak"] == 3
    assert form["longest_unbeaten_streak"] == 3


def test_calculate_head_to_head() -> None:
    """Verifies duo chemistry (together) vs rivalry (against) stats across matches."""
    matches = [
        MatchRecord(
            match_id="M-H2H-1",
            date="2026-11-11 10:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Suri", goals=2, assists=1, result=MatchResult.WIN),
                PlayerStat(player="Skan", goals=1, assists=2, result=MatchResult.WIN),
            ],
        ),
        MatchRecord(
            match_id="M-H2H-2",
            date="2026-11-11 11:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Suri", goals=3, assists=0, result=MatchResult.WIN),
                PlayerStat(player="Skan", goals=1, assists=0, result=MatchResult.LOSS),
            ],
        ),
        MatchRecord(
            match_id="M-H2H-3",
            date="2026-11-11 12:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Skan", goals=2, assists=1, result=MatchResult.WIN),
                PlayerStat(player="Suri", goals=0, assists=1, result=MatchResult.LOSS),
            ],
        ),
    ]

    h2h = calculate_head_to_head("Suri", "SkanRocky", matches)
    assert h2h["player1"] == "Suri"
    assert h2h["player2"] == "Skan"
    assert h2h["together"]["matches"] == 1
    assert h2h["together"]["wins"] == 1
    assert h2h["together"]["win_rate"] == 100.0
    assert h2h["against"]["matches"] == 2
    assert h2h["against"]["p1_wins"] == 1
    assert h2h["against"]["p2_wins"] == 1
    assert h2h["against"]["p1_win_rate"] == 50.0


def test_calculate_hall_of_fame() -> None:
    """Verifies biggest margin, highest total score, single-game highs, clean sheets, and streaks."""
    matches = [
        MatchRecord(
            match_id="M-HOF-1",
            date="2026-11-10 10:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            notes="Score: Suri & Ashy 5 - 0 Skan & Annie (Home Win)",
            player_stats=[
                PlayerStat(player="Suri", goals=3, assists=2, result=MatchResult.WIN),
                PlayerStat(player="Ashy", goals=2, assists=3, result=MatchResult.WIN),
                PlayerStat(player="Skan", goals=0, assists=0, result=MatchResult.LOSS),
                PlayerStat(player="Annie", goals=0, assists=0, result=MatchResult.LOSS),
            ],
        ),
        MatchRecord(
            match_id="M-HOF-2",
            date="2026-11-11 10:00:00",
            result=MatchResult.AWAY_WIN,
            submitted_by="Skan",
            notes="Score: Skan & Lilly 3 - 5 Suri & Azlan (Away Win)",
            player_stats=[
                PlayerStat(player="Skan", goals=1, assists=1, result=MatchResult.LOSS),
                PlayerStat(player="Lilly", goals=2, assists=0, result=MatchResult.LOSS),
                PlayerStat(player="Suri", goals=4, assists=0, result=MatchResult.WIN),
                PlayerStat(player="Azlan", goals=1, assists=3, result=MatchResult.WIN),
            ],
        ),
    ]

    hof = calculate_hall_of_fame(matches)
    assert hof["total_matches"] == 2
    assert hof["biggest_margin"] == 5
    assert hof["biggest_margin_matches"][0][0].match_id == "M-HOF-1"
    assert hof["highest_total"] == 8
    assert hof["highest_total_matches"][0][0].match_id == "M-HOF-2"
    assert hof["max_single_goals"] == 4
    assert hof["max_single_goals_players"] == ["Suri"]
    assert hof["max_single_assists"] == 3
    assert set(hof["max_single_assists_players"]) == {"Ashy", "Azlan"}
    assert hof["max_single_ga"] == 5
    assert set(hof["max_single_ga_players"]) == {"Suri", "Ashy"}
    assert hof["best_win_streak"] == 2
    assert hof["best_win_streak_players"] == ["Suri"]
    assert len(hof["clean_sheets"]) == 1
    assert hof["hattrick_king"].player == "Suri"
    assert hof["hattrick_king"].hattricks == 2


def test_clean_sheets_and_motm_milestones() -> None:
    """Verifies Clean Sheets ranking and auto MOTM + Milestones detection."""
    m1 = MatchRecord(
        match_id="M-MM-1",
        date="2026-11-11 10:00:00",
        result=MatchResult.HOME_WIN,
        submitted_by="Skan",
        notes="Score: Home 3 - 0 Away (Home Win)",
        player_stats=[
            PlayerStat(player="Lilly", goals=3, assists=0, result=MatchResult.WIN),
            PlayerStat(player="Azlan", goals=0, assists=3, result=MatchResult.WIN),
            PlayerStat(player="Skan", goals=0, assists=0, result=MatchResult.LOSS),
            PlayerStat(player="Suri", goals=0, assists=0, result=MatchResult.LOSS),
        ],
    )
    m2 = MatchRecord(
        match_id="M-MM-2",
        date="2026-11-12 10:00:00",
        result=MatchResult.HOME_WIN,
        submitted_by="Skan",
        notes="Score: Home 3 - 2 Away (Home Win)",
        player_stats=[
            PlayerStat(player="Lilly", goals=3, assists=0, result=MatchResult.WIN),
            PlayerStat(player="Azlan", goals=0, assists=3, result=MatchResult.WIN),
            PlayerStat(player="Skan", goals=0, assists=1, result=MatchResult.LOSS),
            PlayerStat(player="Suri", goals=2, assists=0, result=MatchResult.LOSS),
        ],
    )
    all_matches = [m1, m2]
    all_stats = [ps for m in all_matches for ps in m.player_stats]
    aggs = calculate_player_aggregates(all_stats, matches=all_matches)
    assert aggs["lilly"].clean_sheets == 1
    assert aggs["azlan"].clean_sheets == 1
    assert aggs["skan"].clean_sheets == 0

    ranked_cs = get_leaderboard(aggs, LeaderboardCategory.CLEAN_SHEETS)
    assert ranked_cs[0].clean_sheets == 1

    highlights_1 = calculate_match_motm_and_milestones(m1, [m1])
    assert highlights_1["motm"]["player"] == "Lilly"
    joined_1 = "\n".join(highlights_1["milestones"])
    assert "Hattrick" in joined_1
    assert "Playmaker" in joined_1
    assert "Clean Sheet" in joined_1


def test_player_card_stats_and_image_render() -> None:
    """Verifies FC 27 card stat formulas and PNG card rendering."""
    from visuals import render_fc27_card

    matches = [
        MatchRecord(
            match_id="M-CARD-1",
            date="2026-11-11 10:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            notes="Score: Home 4 - 0 Away (Home Win)",
            player_stats=[
                PlayerStat(player="Suri", goals=3, assists=1, result=MatchResult.WIN),
                PlayerStat(player="Skan", goals=1, assists=2, result=MatchResult.WIN),
                PlayerStat(player="Lilly", goals=0, assists=0, result=MatchResult.LOSS),
            ],
        )
    ]
    lb = [
        {
            "player": "Suri",
            "matches_played": 1,
            "goals": 3,
            "assists": 1,
            "ga": 4,
            "wins": 1,
            "losses": 0,
            "win_rate": 100.0,
            "gpg": 3.0,
            "apg": 1.0,
            "gapg": 4.0,
            "clean_sheets": 1,
        },
        {
            "player": "Lilly",
            "matches_played": 1,
            "goals": 0,
            "assists": 0,
            "ga": 0,
            "wins": 0,
            "losses": 1,
            "win_rate": 0.0,
            "gpg": 0.0,
            "apg": 0.0,
            "gapg": 0.0,
            "clean_sheets": 0,
        },
    ]

    suri_card = calculate_player_card_stats("Suri", lb, matches)
    lilly_card = calculate_player_card_stats("Lilly", lb, matches)
    assert suri_card is not None and lilly_card is not None

    for card in (suri_card, lilly_card):
        for stat_key in ("rating", "shooting", "passing", "dribbling", "defending", "physical"):
            assert 60 <= card[stat_key] <= 99
        assert 71 <= card["pace"] <= 99

    assert suri_card["physical"] == int(round((suri_card["rating"] + suri_card["pace"] + suri_card["dribbling"]) / 3.0))
    assert suri_card["shooting"] > lilly_card["shooting"]
    assert suri_card["passing"] > lilly_card["passing"]
    assert suri_card["dribbling"] > lilly_card["dribbling"]
    assert suri_card["defending"] > lilly_card["defending"]

    png_buf = render_fc27_card(suri_card)
    data = png_buf.getvalue()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(data) > 5000

    # Also verify rendering with a player who has a PFP in the pfp/ directory
    lilly_buf = render_fc27_card(lilly_card)
    assert lilly_buf.getvalue()[:8] == b"\x89PNG\r\n\x1a\n"


def test_calculate_player_chemistry() -> None:
    """Verifies best partner, cursed duo, kryptonite, and favorite victim."""
    matches = [
        MatchRecord(
            match_id="M-CH-1",
            date="2026-11-10 12:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Suri", goals=2, assists=1, result=MatchResult.WIN),
                PlayerStat(player="Ashy", goals=1, assists=2, result=MatchResult.WIN),
                PlayerStat(player="Skan", goals=1, assists=0, result=MatchResult.LOSS),
                PlayerStat(player="Lilly", goals=0, assists=1, result=MatchResult.LOSS),
            ],
        ),
        MatchRecord(
            match_id="M-CH-2",
            date="2026-11-10 13:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Suri", goals=2, assists=0, result=MatchResult.WIN),
                PlayerStat(player="Ashy", goals=1, assists=1, result=MatchResult.WIN),
                PlayerStat(player="Skan", goals=0, assists=1, result=MatchResult.LOSS),
                PlayerStat(player="Lilly", goals=1, assists=0, result=MatchResult.LOSS),
            ],
        ),
        MatchRecord(
            match_id="M-CH-3",
            date="2026-11-11 12:00:00",
            result=MatchResult.AWAY_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Suri", goals=1, assists=0, result=MatchResult.LOSS),
                PlayerStat(player="Annie", goals=0, assists=1, result=MatchResult.LOSS),
                PlayerStat(player="Azlan", goals=2, assists=1, result=MatchResult.WIN),
                PlayerStat(player="Skan", goals=1, assists=2, result=MatchResult.WIN),
            ],
        ),
        MatchRecord(
            match_id="M-CH-4",
            date="2026-11-11 14:00:00",
            result=MatchResult.AWAY_WIN,
            submitted_by="Skan",
            player_stats=[
                PlayerStat(player="Suri", goals=0, assists=1, result=MatchResult.LOSS),
                PlayerStat(player="Annie", goals=1, assists=0, result=MatchResult.LOSS),
                PlayerStat(player="Azlan", goals=2, assists=0, result=MatchResult.WIN),
                PlayerStat(player="Ashy", goals=0, assists=2, result=MatchResult.WIN),
            ],
        ),
    ]

    chem = calculate_player_chemistry("Suri", matches)
    assert chem["player"] == "Suri"
    assert chem["total_matches"] == 4
    assert chem["best_partner"]["player"] == "Ashy"
    assert chem["best_partner"]["win_rate"] == 100.0
    assert chem["cursed_duo"]["player"] == "Annie"
    assert chem["cursed_duo"]["win_rate"] == 0.0
    assert chem["favorite_victim"]["player"] == "Lilly"
    assert chem["kryptonite"]["player"] == "Azlan"


def test_calculate_team_leader_aggregates() -> None:
    """Verifies that the first player of each team is treated as the leader and ranked by wins."""
    from stats import calculate_team_leader_aggregates, extract_match_team_leaders

    m1 = MatchRecord(
        match_id="M-TL-01",
        date="2026-11-19 10:00:00",
        result=MatchResult.HOME_WIN,
        submitted_by="Admin",
        notes="Score: Skan & Jonny & Suri 4 - 2 Shallowe & Naka & Azlan (Home Win)",
        player_stats=[
            PlayerStat(player="Skan", goals=2, assists=1, result=MatchResult.WIN),
            PlayerStat(player="Jonny", goals=1, assists=1, result=MatchResult.WIN),
            PlayerStat(player="Suri", goals=1, assists=0, result=MatchResult.WIN),
            PlayerStat(player="Shallowe", goals=1, assists=0, result=MatchResult.LOSS),
            PlayerStat(player="Naka", goals=1, assists=1, result=MatchResult.LOSS),
            PlayerStat(player="Azlan", goals=0, assists=0, result=MatchResult.LOSS),
        ],
    )
    m2 = MatchRecord(
        match_id="M-TL-02",
        date="2026-11-19 11:00:00",
        result=MatchResult.AWAY_WIN,
        submitted_by="Admin",
        notes="Score: Lilly & Azlan 1 - 3 Skan & Annie (Away Win)",
        player_stats=[
            PlayerStat(player="Lilly", goals=1, assists=0, result=MatchResult.LOSS),
            PlayerStat(player="Azlan", goals=0, assists=1, result=MatchResult.LOSS),
            PlayerStat(player="Skan", goals=2, assists=1, result=MatchResult.WIN),
            PlayerStat(player="Annie", goals=1, assists=1, result=MatchResult.WIN),
        ],
    )

    h1, a1 = extract_match_team_leaders(m1)
    assert h1 == "Skan"
    assert a1 == "Shallowe"

    h2, a2 = extract_match_team_leaders(m2)
    assert h2 == "Lilly"
    assert a2 == "Skan"

    leaders = calculate_team_leader_aggregates([m1, m2])
    assert len(leaders) == 3

    # Skan led 2 matches and won both
    skan_rec = next(l for l in leaders if l["player"] == "Skan")
    assert skan_rec["wins"] == 2
    assert skan_rec["matches_led"] == 2
    assert skan_rec["win_rate"] == 100.0

    # Shallowe led 1 and lost
    shallowe_rec = next(l for l in leaders if l["player"] == "Shallowe")
    assert shallowe_rec["wins"] == 0
    assert shallowe_rec["matches_led"] == 1
    assert shallowe_rec["losses"] == 1

    # Lilly led 1 and lost
    lilly_rec = next(l for l in leaders if l["player"] == "Lilly")
    assert lilly_rec["wins"] == 0
    assert lilly_rec["matches_led"] == 1
    assert lilly_rec["losses"] == 1

    # Skan ranked #1
    assert leaders[0]["player"] == "Skan"


def test_calculate_captain_head_to_head() -> None:
    """Verifies H2H calculations specifically for opposing team captains."""
    from stats import calculate_captain_head_to_head

    m1 = MatchRecord(
        match_id="M-CH2H-01",
        date="2026-11-19 10:00:00",
        result=MatchResult.HOME_WIN,
        submitted_by="Admin",
        notes="Score: Skan & Jonny 3 - 1 Suri & Shallowe (Home Win)",
        player_stats=[
            PlayerStat(player="Skan", goals=2, assists=1, result=MatchResult.WIN),
            PlayerStat(player="Jonny", goals=1, assists=1, result=MatchResult.WIN),
            PlayerStat(player="Suri", goals=1, assists=0, result=MatchResult.LOSS),
            PlayerStat(player="Shallowe", goals=0, assists=1, result=MatchResult.LOSS),
        ],
    )
    m2 = MatchRecord(
        match_id="M-CH2H-02",
        date="2026-11-19 11:00:00",
        result=MatchResult.HOME_WIN,
        submitted_by="Admin",
        notes="Score: Suri & Azlan 4 - 2 Skan & Annie (Home Win)",
        player_stats=[
            PlayerStat(player="Suri", goals=3, assists=1, result=MatchResult.WIN),
            PlayerStat(player="Azlan", goals=1, assists=1, result=MatchResult.WIN),
            PlayerStat(player="Skan", goals=1, assists=0, result=MatchResult.LOSS),
            PlayerStat(player="Annie", goals=1, assists=1, result=MatchResult.LOSS),
        ],
    )
    m3 = MatchRecord(
        match_id="M-CH2H-03",
        date="2026-11-19 12:00:00",
        result=MatchResult.DRAW,
        submitted_by="Admin",
        notes="Score: Skan & Lilly 2 - 2 Suri & Naka (Draw)",
        player_stats=[
            PlayerStat(player="Skan", goals=1, assists=0, result=MatchResult.DRAW),
            PlayerStat(player="Lilly", goals=1, assists=1, result=MatchResult.DRAW),
            PlayerStat(player="Suri", goals=1, assists=1, result=MatchResult.DRAW),
            PlayerStat(player="Naka", goals=1, assists=0, result=MatchResult.DRAW),
        ],
    )

    data = calculate_captain_head_to_head("Skan", "Suri", [m1, m2, m3])
    assert data["matches"] == 3
    assert data["c1_wins"] == 1  # Skan won M1
    assert data["c2_wins"] == 1  # Suri won M2
    assert data["draws"] == 1    # M3 draw
    assert data["c1_goals"] == 3 + 2 + 2  # 7
    assert data["c2_goals"] == 1 + 4 + 2  # 7
    assert round(data["c1_win_rate"], 1) == 33.3
    assert round(data["c2_win_rate"], 1) == 33.3
    assert len(data["clashes"]) == 3

    # Error handling
    try:
        calculate_captain_head_to_head("Skan", "skan", [m1])
        assert False, "Should raise ValueError for identical captains"
    except ValueError:
        pass


def test_determine_team_leader() -> None:
    """Verifies team leader determination hierarchy:
    1. Leaderboard wins/WR/matches.
    2. Higher OVR tie-breaker.
    3. Random tie-breaker if both leaderboard and OVR are tied.
    """
    from stats import determine_team_leader

    # Case 1: Clear winner on leader stats
    p1 = {"player": "Skan", "rating": 81.0, "leader_wins": 5, "leader_wr": 83.3, "leader_matches": 6}
    p2 = {"player": "Jonny", "rating": 85.0, "leader_wins": 1, "leader_wr": 50.0, "leader_matches": 2}
    res1 = determine_team_leader([p1, p2])
    assert res1["leader"] == "Skan"
    assert not res1["is_tied"]

    # Case 2: Tie on leader stats (both 0 wins) -> Higher OVR gets leader
    p3 = {"player": "Ashy", "rating": 82.0, "leader_wins": 0, "leader_wr": 0.0, "leader_matches": 0}
    p4 = {"player": "Maple", "rating": 70.0, "leader_wins": 0, "leader_wr": 0.0, "leader_matches": 0}
    res2 = determine_team_leader([p3, p4])
    assert res2["leader"] == "Ashy"
    assert not res2["is_tied"]

    # Case 3: Tie on leader stats AND tie on OVR -> Leader is at random
    p5 = {"player": "PlayerA", "rating": 75.0, "leader_wins": 0, "leader_wr": 0.0, "leader_matches": 0}
    p6 = {"player": "PlayerB", "rating": 75.0, "leader_wins": 0, "leader_wr": 0.0, "leader_matches": 0}
    res3 = determine_team_leader([p5, p6])
    assert res3["leader"] is None
    assert res3["is_tied"]
    assert "PlayerA" in res3["tied_players"] and "PlayerB" in res3["tied_players"]


def test_fair_teams_captain_balancing() -> None:
    """Verifies that top 2 leaders from the pool stay on opposite teams."""
    from stats import generate_fair_teams

    records = [
        {"player": "Skan", "matches_played": 10, "win_rate": 80.0, "gapg": 2.5, "gpg": 2.0},   # ~87 OVR
        {"player": "Suri", "matches_played": 10, "win_rate": 80.0, "gapg": 2.5, "gpg": 2.0},   # ~87 OVR
        {"player": "Jonny", "matches_played": 10, "win_rate": 50.0, "gapg": 1.5, "gpg": 1.0},  # ~78 OVR
        {"player": "Ashy", "matches_played": 10, "win_rate": 50.0, "gapg": 1.5, "gpg": 1.0},   # ~78 OVR
    ]
    # Skan and Suri are top leaders
    leader_records = [
        {"player": "Skan", "wins": 8, "matches_led": 9, "win_rate": 88.9},
        {"player": "Suri", "wins": 5, "matches_led": 6, "win_rate": 83.3},
        {"player": "Jonny", "wins": 0, "matches_led": 0, "win_rate": 0.0},
        {"player": "Ashy", "wins": 0, "matches_led": 0, "win_rate": 0.0},
    ]

    split = generate_fair_teams(["Skan", "Suri", "Jonny", "Ashy"], records, leader_records=leader_records)
    team_a_names = [p["player"] for p in split["team_a"]]
    team_b_names = [p["player"] for p in split["team_b"]]

    # Skan and Suri MUST NOT be on the same team
    assert ("Skan" in team_a_names and "Suri" in team_b_names) or ("Suri" in team_a_names and "Skan" in team_b_names)

    # Leader is placed at index 0
    assert split["team_a"][0]["player"] in ("Skan", "Suri")
    assert split["team_b"][0]["player"] in ("Skan", "Suri")


def test_filter_matches_from_leader_start() -> None:
    """Verifies that matches preceding the leader start match ID are excluded from leader tracking."""
    from stats import (
        DEFAULT_LEADER_START_MATCH_ID,
        calculate_team_leader_aggregates,
        filter_matches_from_leader_start,
    )

    m_old = MatchRecord(
        match_id="M-OLD-01",
        date="2026-11-18 10:00:00",
        result=MatchResult.HOME_WIN,
        submitted_by="Admin",
        notes="Score: Skan & Jonny 3 - 1 Suri & Ashy (Home Win)",
        player_stats=[
            PlayerStat(player="Skan", goals=2, assists=1, result=MatchResult.WIN),
            PlayerStat(player="Jonny", goals=1, assists=1, result=MatchResult.WIN),
            PlayerStat(player="Suri", goals=1, assists=0, result=MatchResult.LOSS),
            PlayerStat(player="Ashy", goals=0, assists=1, result=MatchResult.LOSS),
        ],
    )
    m_cutoff = MatchRecord(
        match_id="M-20261119-8723FE",
        date="2026-11-19 23:59:40",
        result=MatchResult.HOME_WIN,
        submitted_by="Admin",
        notes="Score: Home 3 - 2 Away (Home Win)",
        player_stats=[
            PlayerStat(player="Jonny", goals=1, assists=2, result=MatchResult.WIN),
            PlayerStat(player="Annie", goals=2, assists=0, result=MatchResult.WIN),
            PlayerStat(player="Azlan", goals=2, assists=0, result=MatchResult.LOSS),
            PlayerStat(player="Suri", goals=0, assists=0, result=MatchResult.LOSS),
        ],
    )
    m_new = MatchRecord(
        match_id="M-NEW-01",
        date="2026-11-20 01:00:00",
        result=MatchResult.HOME_WIN,
        submitted_by="Admin",
        notes="Score: Jonny & Annie 4 - 1 Azlan & Suri (Home Win)",
        player_stats=[
            PlayerStat(player="Jonny", goals=2, assists=2, result=MatchResult.WIN),
            PlayerStat(player="Annie", goals=2, assists=1, result=MatchResult.WIN),
            PlayerStat(player="Azlan", goals=1, assists=0, result=MatchResult.LOSS),
            PlayerStat(player="Suri", goals=0, assists=1, result=MatchResult.LOSS),
        ],
    )

    all_matches = [m_old, m_cutoff, m_new]
    filtered = filter_matches_from_leader_start(all_matches, start_match_id="M-20261119-8723FE")
    assert len(filtered) == 2
    assert filtered[0].match_id == "M-20261119-8723FE"
    assert filtered[1].match_id == "M-NEW-01"

    # calculate_team_leader_aggregates only counts from cutoff onwards: Skan's old win is excluded!
    leaders = calculate_team_leader_aggregates(all_matches, start_match_id="M-20261119-8723FE")
    leader_names = [l["player"] for l in leaders]
    assert "Skan" not in leader_names
    assert "Jonny" in leader_names
    jonny_rec = next(l for l in leaders if l["player"] == "Jonny")
    assert jonny_rec["wins"] == 1
    assert jonny_rec["matches_led"] == 1


def test_calculate_special_card_stats_totw_and_potm() -> None:
    """Verifies that TOTW gets 90+ OVR, POTM gets 95+ OVR, and the base card is untouched."""
    from stats import calculate_special_card_stats, calculate_player_card_stats

    records = [
        {
            "player": "Jonny",
            "matches_played": 10,
            "wins": 7,
            "draws": 1,
            "losses": 2,
            "goals": 12,
            "assists": 8,
            "gapg": 2.0,
            "win_rate": 70.0,
        }
    ]
    matches = [
        MatchRecord(
            match_id="M-1",
            date="2026-11-10 12:00:00",
            result=MatchResult.HOME_WIN,
            submitted_by="Admin",
            player_stats=[
                PlayerStat(player="Jonny", goals=2, assists=1, result=MatchResult.WIN),
            ],
        )
    ]

    base_card = calculate_player_card_stats("Jonny", records, matches)
    assert base_card is not None
    base_ovr = base_card["rating"]
    base_pac = base_card["pace"]

    # TOTW: guaranteed 90+ OVR
    totw_card = calculate_special_card_stats("Jonny", records, matches, card_type="TOTW")
    assert totw_card is not None
    assert totw_card["rating"] >= 90
    assert totw_card["rating"] <= 99
    assert totw_card["card_type"] == "TOTW"
    assert totw_card["tier_label"] == "TEAM OF THE WEEK"
    assert totw_card["pace"] >= base_pac

    # POTM: guaranteed 95+ OVR
    potm_card = calculate_special_card_stats("Jonny", records, matches, card_type="POTM")
    assert potm_card is not None
    assert potm_card["rating"] >= 95
    assert potm_card["rating"] <= 99
    assert potm_card["card_type"] == "POTM"
    assert potm_card["tier_label"] == "PLAYER OF THE MONTH"
    assert potm_card["rating"] >= totw_card["rating"]
    assert potm_card["pace"] >= totw_card["pace"]

    # Base card is completely untouched (no side-effects / mutations)
    assert base_card["rating"] == base_ovr
    assert base_card["pace"] == base_pac


def test_calculate_potm_rankings_with_potw_weight() -> None:
    """Verifies POTM ranking incorporates weekly POTW form (0.9 match stats + 0.1 POTW weightage)."""
    from stats import calculate_potm_rankings

    # Week 1: Skan dominates and wins POTW
    m_w1 = MatchRecord(
        match_id="M-W1",
        date="2026-11-03 15:00:00",
        result=MatchResult.HOME_WIN,
        submitted_by="Admin",
        player_stats=[
            PlayerStat(player="Skan", goals=5, assists=3, result=MatchResult.WIN),
            PlayerStat(player="Jonny", goals=1, assists=0, result=MatchResult.WIN),
        ],
    )
    # Week 2: Jonny plays well
    m_w2 = MatchRecord(
        match_id="M-W2",
        date="2026-11-10 15:00:00",
        result=MatchResult.HOME_WIN,
        submitted_by="Admin",
        player_stats=[
            PlayerStat(player="Jonny", goals=2, assists=2, result=MatchResult.WIN),
            PlayerStat(player="Skan", goals=0, assists=1, result=MatchResult.WIN),
        ],
    )

    matches = [m_w1, m_w2]
    rankings, m_start, m_end = calculate_potm_rankings(
        matches, reference_date=datetime(2026, 11, 15, 12, 0, 0), months_ago=0
    )

    assert len(rankings) == 2
    skan_rec = next(r for r in rankings if r["player"] == "Skan")
    jonny_rec = next(r for r in rankings if r["player"] == "Jonny")

    # Skan won week 1 POTW, so has potw_cards and potw_bonus
    assert skan_rec["potw_cards"] >= 1
    assert "potw_bonus" in skan_rec
    assert "base_score" in skan_rec
    assert skan_rec["score"] > 0


def test_compute_player_defending_stat_pure_hybrid() -> None:
    """Verifies that Pure Hybrid (1, 2) rewards true defensive output with high defending stats."""
    from stats import compute_player_defending_stat

    records = [
        # Skan: Elite defender (low conceded, high clean sheets)
        {"player": "Skan", "matches_played": 25, "wins": 18, "win_rate": 72.0, "clean_sheets": 5},
        # Suri: Top defensive anchor (lowest conceded, highest clean sheets)
        {"player": "Suri", "matches_played": 25, "wins": 18, "win_rate": 72.0, "clean_sheets": 6},
        # AttackerX: Concedes 3.5+ goals per game, 0 clean sheets
        {"player": "AttackerX", "matches_played": 20, "wins": 6, "win_rate": 30.0, "clean_sheets": 0},
    ]

    cs_map = {
        "skan": {"goals_conceded": 45, "clean_sheets": 5, "matches": 25},      # 1.80 GC/game
        "suri": {"goals_conceded": 40, "clean_sheets": 6, "matches": 25},      # 1.60 GC/game
        "attackerx": {"goals_conceded": 72, "clean_sheets": 0, "matches": 20}, # 3.60 GC/game
    }

    skan_def, _, _ = compute_player_defending_stat("Skan", records[0], [], all_records=records, cs_map=cs_map)
    suri_def, _, _ = compute_player_defending_stat("Suri", records[1], [], all_records=records, cs_map=cs_map)
    att_def, _, _ = compute_player_defending_stat("AttackerX", records[2], [], all_records=records, cs_map=cs_map)

    # Suri and Skan should both receive elite defending (88+)
    assert suri_def >= 88
    assert skan_def >= 86
    assert suri_def >= skan_def
    # AttackerX who concedes 3.6 goals per game should be significantly lower
    assert att_def <= 70
    assert suri_def - att_def >= 18













