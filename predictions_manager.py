"""Persistent Matchday Prediction Manager for eFootball Discord Bot."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
from typing import Dict, List, Optional

from models import MatchResult

LOGGER = logging.getLogger("efootball_bot.predictions")


class PredictionsManager:
    """Manages matchday score and outcome predictions and server points leaderboard."""

    def __init__(self, storage_path: str | Path = "data/predictions.json") -> None:
        self.path = Path(storage_path)
        self._ensure_storage()

    def _ensure_storage(self) -> None:
        if not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._save_data({"pending": {}, "leaderboard": {}, "history": []})

    def _load_data(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {"pending": {}, "leaderboard": {}, "history": []}

    def _save_data(self, data: dict) -> None:
        temp_path = self.path.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        temp_path.replace(self.path)

    @staticmethod
    def normalize_outcome(outcome: str) -> str:
        clean = outcome.strip().lower()
        if "home" in clean or clean in ("1", "hw", "h"):
            return "Home Win"
        if "away" in clean or clean in ("2", "aw", "a"):
            return "Away Win"
        if "draw" in clean or clean in ("x", "d", "tie"):
            return "Draw"
        return "Home Win"

    @staticmethod
    def normalize_score(score: Optional[str]) -> Optional[str]:
        if not score:
            return None
        match = re.search(r"(\d+)\s*[-:, ]\s*(\d+)", score.strip())
        if match:
            return f"{match.group(1)}-{match.group(2)}"
        return None

    def set_prediction(
        self,
        user_id: int,
        user_name: str,
        outcome: str,
        score: Optional[str] = None,
    ) -> dict:
        """Stores or updates a player's pending matchday prediction."""
        norm_outcome = self.normalize_outcome(outcome)
        norm_score = self.normalize_score(score)

        data = self._load_data()
        pred_entry = {
            "user_id": user_id,
            "user_name": user_name,
            "outcome": norm_outcome,
            "score": norm_score,
        }
        data.setdefault("pending", {})[str(user_id)] = pred_entry
        self._save_data(data)
        return pred_entry

    def get_prediction(self, user_id: int) -> Optional[dict]:
        """Returns the pending prediction for a user if present."""
        data = self._load_data()
        return data.get("pending", {}).get(str(user_id))

    def get_all_pending(self) -> List[dict]:
        """Returns all pending predictions for the upcoming match."""
        data = self._load_data()
        return list(data.get("pending", {}).values())

    def get_leaderboard(self) -> List[dict]:
        """Returns user prediction standings sorted by total points."""
        data = self._load_data()
        lb = list(data.get("leaderboard", {}).values())
        lb.sort(
            key=lambda x: (
                x.get("points", 0),
                x.get("exact_scores", 0),
                x.get("correct_outcomes", 0),
                -x.get("total_predictions", 0),
            ),
            reverse=True,
        )
        return lb

    def resolve_predictions(
        self,
        match_id: str,
        actual_result: MatchResult | str,
        home_score: int,
        away_score: int,
    ) -> List[dict]:
        """Evaluates pending predictions against the finalized match result.

        Scoring:
        - Correct Outcome (Home Win / Away Win / Draw): +1 point
        - Exact Scoreline: +3 bonus points (+4 points total)
        """
        data = self._load_data()
        pending = data.get("pending", {})
        if not pending:
            return []

        res_str = actual_result.value if isinstance(actual_result, MatchResult) else str(actual_result)
        actual_outcome = self.normalize_outcome(res_str)
        actual_score = f"{home_score}-{away_score}"

        awarded: List[dict] = []
        lb = data.setdefault("leaderboard", {})

        for u_id, pred in pending.items():
            u_name = pred.get("user_name", "Player")
            pred_out = pred.get("outcome")
            pred_sc = pred.get("score")

            is_correct_outcome = (pred_out == actual_outcome)
            is_exact_score = (pred_sc == actual_score)

            pts = 0
            if is_correct_outcome:
                pts += 1
            if is_exact_score:
                pts += 3

            user_stat = lb.setdefault(
                str(u_id),
                {
                    "user_id": int(u_id),
                    "user_name": u_name,
                    "points": 0,
                    "correct_outcomes": 0,
                    "exact_scores": 0,
                    "total_predictions": 0,
                },
            )
            user_stat["user_name"] = u_name
            user_stat["total_predictions"] += 1
            user_stat["points"] += pts
            if is_correct_outcome:
                user_stat["correct_outcomes"] += 1
            if is_exact_score:
                user_stat["exact_scores"] += 1

            if pts > 0:
                awarded.append({
                    "user_id": int(u_id),
                    "user_name": u_name,
                    "points": pts,
                    "correct_outcome": is_correct_outcome,
                    "exact_score": is_exact_score,
                    "predicted_outcome": pred_out,
                    "predicted_score": pred_sc,
                })

        # Save history & clear pending
        data.setdefault("history", []).append({
            "match_id": match_id,
            "actual_outcome": actual_outcome,
            "actual_score": actual_score,
            "awarded": awarded,
        })
        data["pending"] = {}
        self._save_data(data)

        awarded.sort(key=lambda x: x["points"], reverse=True)
        return awarded
