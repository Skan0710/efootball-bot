"""Domain models and data structures for eFootball Co-op Statistics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import re
from typing import Dict, List, Optional


PLAYER_CANONICAL_MAP: Dict[str, str] = {
    # Lilly (lillym, lilly, maple)
    "lilly": "Lilly",
    "lillym": "Lilly",
    "maple": "Lilly",
    # Annie (AnnieSynx, annie)
    "annie": "Annie",
    "anniesynx": "Annie",
    # Naka (Son, SoNaka, naka)
    "son": "Naka",
    "sonaka": "Naka",
    "naka": "Naka",
    # Suri (SuriBG, Suri, SuriBG6)
    "suri": "Suri",
    "suribg": "Suri",
    "suribg6": "Suri",
    # Skan (SkanxPrznt07, SkanRocky, skan)
    "skan": "Skan",
    "skanrocky": "Skan",
    "skanxprznt07": "Skan",
}


def canonicalize_player_name(name: str) -> str:
    """Maps player name variants and OCR artifacts to canonical registered player names.

    Ensures that aliases like 'lillym', 'maple', 'AnnieSynx', 'SoNaka', 'Son', 'SuriBG',
    'SkanRocky', etc. all resolve to single canonical identities.
    """
    if not name:
        return ""

    raw = name.strip()
    clean = re.sub(r"[^a-zA-Z0-9]", "", raw).lower()

    if clean in PLAYER_CANONICAL_MAP:
        return PLAYER_CANONICAL_MAP[clean]

    # Prefix / stem checks for OCR variations and noise
    if clean.startswith("skan"):
        return "Skan"
    if clean.startswith("suribg") or clean.startswith("suri"):
        return "Suri"
    if clean.startswith("lilly") or clean.startswith("maple"):
        return "Lilly"
    if clean.startswith("annie"):
        return "Annie"
    if clean == "son" or clean.startswith("sonaka") or clean.startswith("naka"):
        return "Naka"

    return raw


class MatchResult(str, Enum):
    """Result of an eFootball match."""

    HOME_WIN = "Home Win"
    AWAY_WIN = "Away Win"
    DRAW = "Draw"
    WIN = "Win"
    LOSS = "Loss"

    @classmethod
    def from_str(cls, value: str) -> MatchResult:
        normalized = value.strip().lower()
        if normalized == "home win":
            return cls.HOME_WIN
        elif normalized == "away win":
            return cls.AWAY_WIN
        elif normalized == "win":
            return cls.WIN
        elif normalized == "loss":
            return cls.LOSS
        elif normalized == "draw":
            return cls.DRAW

        for member in cls:
            if member.value.lower() == normalized:
                return member
        raise ValueError(f"Invalid match result '{value}'. Must be Home Win, Away Win, or Draw.")


class LeaderboardCategory(str, Enum):
    """Categories available for ranking players on the leaderboard."""

    GOALS = "Goals"
    ASSISTS = "Assists"
    GA = "G/A"
    WINS = "Wins"
    LOSSES = "Losses"
    DRAWS = "Draws"
    WIN_RATE = "Win rate"
    MATCHES_PLAYED = "Matches played"
    GPG = "GPG"
    APG = "APG"
    GAPG = "GAPG"


@dataclass
class PlayerStat:
    """Individual player performance record within a single match."""

    player: str
    goals: int
    assists: int
    result: MatchResult

    def __post_init__(self) -> None:
        self.player = canonicalize_player_name(self.player)
        if not self.player:
            raise ValueError("Player name cannot be empty.")
        if self.goals < 0:
            raise ValueError(f"Goals cannot be negative, got {self.goals}.")
        if self.assists < 0:
            raise ValueError(f"Assists cannot be negative, got {self.assists}.")
        if not isinstance(self.result, MatchResult):
            self.result = MatchResult.from_str(str(self.result))


@dataclass
class MatchRecord:
    """Complete match record including participating players and metadata."""

    match_id: str
    date: str
    result: MatchResult
    submitted_by: str
    screenshot_ref: str = ""
    notes: str = ""
    player_stats: List[PlayerStat] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.match_id = self.match_id.strip()
        if not self.match_id:
            raise ValueError("Match ID cannot be empty.")
        if not isinstance(self.result, MatchResult):
            self.result = MatchResult.from_str(str(self.result))
        self.submitted_by = self.submitted_by.strip()
        if not self.submitted_by:
            raise ValueError("SubmittedBy user cannot be empty.")


@dataclass
class PlayerAggregateRecord:
    """Calculated historical career statistics for an individual player."""

    player: str
    matches_played: int = 0
    goals: int = 0
    assists: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    win_rate: float = 0.0
    loss_rate: float = 0.0
    draw_rate: float = 0.0
    ga: int = 0
    gpg: float = 0.0
    apg: float = 0.0
    gapg: float = 0.0

    @classmethod
    def compute(
        cls,
        player: str,
        matches_played: int,
        goals: int,
        assists: int,
        wins: int,
        losses: int,
        draws: int,
    ) -> PlayerAggregateRecord:
        """Computes win, loss, and draw rates safely avoiding division by zero."""
        ga = goals + assists
        if matches_played > 0:
            w_rate = round((wins / matches_played) * 100.0, 1)
            l_rate = round((losses / matches_played) * 100.0, 1)
            d_rate = round((draws / matches_played) * 100.0, 1)
            gpg = round(goals / matches_played, 2)
            apg = round(assists / matches_played, 2)
            gapg = round(ga / matches_played, 2)
        else:
            w_rate = 0.0
            l_rate = 0.0
            d_rate = 0.0
            gpg = 0.0
            apg = 0.0
            gapg = 0.0

        return cls(
            player=player,
            matches_played=matches_played,
            goals=goals,
            assists=assists,
            wins=wins,
            losses=losses,
            draws=draws,
            win_rate=w_rate,
            loss_rate=l_rate,
            draw_rate=d_rate,
            ga=ga,
            gpg=gpg,
            apg=apg,
            gapg=gapg,
        )


@dataclass
class OCRPlayerCandidate:
    """Intermediate candidate extracted from screenshot OCR before user confirmation."""

    player: str
    goals: int
    assists: int
    confidence_note: str = ""

    def __post_init__(self) -> None:
        self.player = canonicalize_player_name(self.player)
