"""Phase 2 EPIC 2.5 — Milestone tracker (subtle gamification).

The tracker keeps a small state dict and reports new achievements when
the host calls :meth:`evaluate` with the latest trades / equity. It is
deliberately Qt-free; the host wires the returned :class:`Milestone`
list into ``ToastManager`` itself.

Each milestone has a stable ``key`` so it is reported at most once per
session unless the tracker is rebuilt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional, Sequence, Set

import numpy as np


@dataclass(frozen=True)
class Milestone:
    """A single achievement-style notification."""

    key: str
    icon: str  # short emoji or single glyph
    headline: str
    detail: str
    level: str = "success"  # success | info | warning


_TRADE_COUNT_TIERS: Sequence[int] = (100, 250, 500, 1000, 2500, 5000, 10000)


@dataclass
class MilestoneTracker:
    """Stateful tracker — call :meth:`evaluate` after each refresh."""

    seen: Set[str] = field(default_factory=set)
    peak_equity: float = float("-inf")
    longest_win_streak: int = 0
    best_day_pnl: float = 0.0
    was_in_drawdown: bool = False

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear state (e.g. when a new journal/strategy is loaded)."""
        self.seen.clear()
        self.peak_equity = float("-inf")
        self.longest_win_streak = 0
        self.best_day_pnl = 0.0
        self.was_in_drawdown = False

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def evaluate(
        self,
        trades: Iterable[Any],
        *,
        initial_equity: float = 0.0,
    ) -> List[Milestone]:
        """Return any milestones reached since the previous call."""
        trades = list(trades)
        if not trades:
            return []

        pnls = np.asarray(
            [float(getattr(t, "pnl", 0.0) or 0.0) for t in trades],
            dtype=np.float64,
        )
        equity = float(initial_equity) + float(np.cumsum(pnls)[-1])
        if equity > self.peak_equity:
            prev_peak = self.peak_equity
        else:
            prev_peak = None

        milestones: List[Milestone] = []

        # Trade count milestones (one per tier).
        n = len(trades)
        for tier in _TRADE_COUNT_TIERS:
            key = f"trade_count_{tier}"
            if n >= tier and key not in self.seen:
                self.seen.add(key)
                milestones.append(
                    Milestone(
                        key=key,
                        icon="🎯",
                        headline=f"{tier:,} trades",
                        detail="Sample size matters — your stats are getting meaningful.",
                    )
                )

        # New ATH equity.
        if prev_peak is not None and equity > 0:
            key = f"ath_equity_{int(equity)}"
            # Coalesce — only fire once per session for the highest ATH
            # we have observed. The key uses the equity value so a
            # subsequent higher ATH still fires.
            if key not in self.seen and not any(
                k.startswith("ath_equity_") for k in self.seen
            ) or (
                prev_peak != float("-inf")
                and equity > prev_peak * 1.05  # ≥5% above prior peak
            ):
                self.seen = {k for k in self.seen if not k.startswith("ath_equity_")}
                self.seen.add(key)
                milestones.append(
                    Milestone(
                        key=key,
                        icon="🏔",
                        headline="New equity high.",
                        detail=f"Account peaked at ${equity:,.0f}.",
                    )
                )

        if equity > self.peak_equity:
            self.peak_equity = equity

        # Longest win streak.
        cur_w = 0
        max_w = 0
        for v in pnls:
            if v > 0:
                cur_w += 1
                if cur_w > max_w:
                    max_w = cur_w
            else:
                cur_w = 0
        if max_w > self.longest_win_streak and max_w >= 5:
            self.longest_win_streak = max_w
            key = f"longest_win_streak_{max_w}"
            if key not in self.seen:
                self.seen.add(key)
                milestones.append(
                    Milestone(
                        key=key,
                        icon="🔥",
                        headline=f"New longest win streak: {max_w}.",
                        detail="Stay disciplined — streaks invite overconfidence.",
                        level="info",
                    )
                )

        # Best single-day PnL.
        by_day = self._group_by_day(trades, pnls)
        if by_day:
            best_day_pnl = max(by_day.values())
            if best_day_pnl > self.best_day_pnl and best_day_pnl > 0:
                self.best_day_pnl = best_day_pnl
                key = f"best_day_{int(best_day_pnl)}"
                if key not in self.seen:
                    self.seen.add(key)
                    milestones.append(
                        Milestone(
                            key=key,
                            icon="📈",
                            headline=f"Best single day: +${best_day_pnl:,.0f}.",
                            detail="Your strongest 24 h yet.",
                        )
                    )

        # Recovery from drawdown.
        if self.was_in_drawdown and equity >= self.peak_equity:
            key = "recovered_from_dd"
            # Always allow re-firing — clear prior one first.
            self.seen.discard(key)
            self.seen.add(key)
            milestones.append(
                Milestone(
                    key=key,
                    icon="🚀",
                    headline="Recovered from drawdown.",
                    detail="Equity back to its prior peak — execution paid off.",
                )
            )
            self.was_in_drawdown = False
        elif equity < self.peak_equity * 0.95:
            # Drop > 5% below prior peak → mark drawdown so the next
            # full recovery emits a milestone.
            self.was_in_drawdown = True

        return milestones

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _group_by_day(trades: Sequence[Any], pnls: np.ndarray) -> dict:
        by_day: dict[str, float] = {}
        for i, t in enumerate(trades):
            np_dt = getattr(t, "exit_time_np", None) or getattr(t, "entry_time_np", None)
            label: Optional[str] = None
            if np_dt is not None:
                try:
                    label = str(np.datetime_as_string(np_dt, unit="D"))
                except Exception:
                    label = None
            if label is None:
                s = getattr(t, "exit_time", None) or getattr(t, "entry_time", None)
                if isinstance(s, str) and len(s) >= 10:
                    label = s[:10]
            if label:
                by_day[label] = by_day.get(label, 0.0) + float(pnls[i])
        return by_day


__all__ = ["Milestone", "MilestoneTracker"]
