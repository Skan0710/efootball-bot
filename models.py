"""Domain models and data structures for eFootball Co-op Statistics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional


class MatchResult(str, Enum):
    """Result of an eFootball match from the perspective of the submitting co-op team."""

    WIN = "Win"
    LOSS = "Loss"
    DRAW = "Draw"

    @classmethod
    def from_str(cls, value: str) -> MatchResult:
        normalized = value.strip().capitalize()
        for member in cls:
            if member.value == normalized:
                return member
        raise ValueError(f"Invalid match result '{value}'. Must be Win, Loss, or Draw.")


class LeaderboardCategory(str, Enum):
    """Categories available for ranking players on the leaderboard."""

    GOALS = "Goals"
    ASSISTS = "Assists"
    WINS = "Wins"
    LOSSES = "Losses"
    DRAWS = "Draws"
    WIN_RATE = "Win rate"
    MATCHES_PLAYED = "Matches played"


@dataclass
class PlayerStat:
    """Individual player performance record within a single match."""

    player: str
    goals: int
    assists: int
    result: MatchResult

    def __post_init__(self) -> None:
        self.player = self.player.strip()
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
        if matches_played > 0:
            w_rate = round((wins / matches_played) * 100.0, 1)
            l_rate = round((losses / matches_played) * 100.0, 1)
            d_rate = round((draws / matches_played) * 100.0, 1)
        else:
            w_rate = 0.0
            l_rate = 0.0
            d_rate = 0.0

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
        )


@dataclass
class OCRPlayerCandidate:
    """Intermediate candidate extracted from screenshot OCR before user confirmation."""

    player: str
    goals: int
    assists: int
    confidence_note: str = ""
