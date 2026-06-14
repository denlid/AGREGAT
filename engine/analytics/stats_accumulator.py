"""
StatsAccumulator — Incremental tally of trade statistics.

Pulled out of `BacktestEngine` to give us a single place to track
wins/losses/gross_profit/gross_loss/longs/shorts. The engine keeps
private mirror fields for backwards-compat with tests and external code,
but they're populated from this accumulator on every closed trade.

`DrawdownTracker` is the on-line counterpart for equity-based metrics:
peak equity, max DD in dollars and percent. The step-by-step engine feeds it
post-trade equity and the Stats / Analytics tabs read the same numbers back.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.models.models import Trade


@dataclass
class StatsAccumulator:
    wins: int = 0
    losses: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    longs: int = 0
    shorts: int = 0
    break_evens: int = 0

    def reset(self) -> None:
        self.wins = 0
        self.losses = 0
        self.gross_profit = 0.0
        self.gross_loss = 0.0
        self.longs = 0
        self.shorts = 0
        self.break_evens = 0

    def update(self, trade: Trade) -> None:
        """Add one closed trade to the running totals."""
        if trade.pnl > 0:
            self.wins += 1
            self.gross_profit += trade.pnl
        elif trade.pnl < 0:
            self.losses += 1
            self.gross_loss += abs(trade.pnl)
        else:
            self.break_evens += 1

        if trade.direction == "long":
            self.longs += 1
        else:
            self.shorts += 1

    @property
    def total_trades(self) -> int:
        return self.wins + self.losses + self.break_evens

    @property
    def decisive_trades(self) -> int:
        """Trades whose PnL is non-zero — used as the denominator in
        :attr:`win_rate` so break-even trades neither inflate nor deflate
        the percentage. (TZ Phase 1 / A9.)
        """
        return self.wins + self.losses

    @property
    def win_rate(self) -> float:
        """Win-rate excluding break-even trades from the denominator.

        Industry-standard convention (e.g. NinjaTrader, TradeStation):
        ``wins / (wins + losses) × 100``. Break-evens are reported
        separately via :attr:`break_evens`. Returns ``0.0`` when there
        are no decisive trades (only BE or empty).
        """
        denom = self.decisive_trades
        return (self.wins / denom * 100.0) if denom else 0.0

    @property
    def win_rate_inclusive(self) -> float:
        """Legacy win-rate that counts break-evens in the denominator.

        Kept for callers that explicitly want the inclusive variant.
        """
        return (self.wins / self.total_trades * 100.0) if self.total_trades else 0.0

    @property
    def profit_factor(self) -> float:
        if self.gross_loss > 0:
            return self.gross_profit / self.gross_loss
        return float("inf") if self.gross_profit > 0 else 0.0


@dataclass
class DrawdownTracker:
    """Incremental peak / drawdown tracker driven by post-trade equity.

    BacktestCore calls :meth:`update` with the running equity after every
    closed trade and reads the resulting :attr:`max_drawdown_abs` /
    :attr:`max_drawdown_pct` back. Centralising this here keeps drawdown math
    consistent across the app.

    Conventions:
      * ``max_drawdown_pct`` is expressed as a percent value in
        ``[0, 100]`` to match the engine's legacy mirror fields.
      * ``initial_equity`` seeds the peak so a strategy that starts with
        a losing trade still books a positive DD against the starting
        bankroll.
    """

    initial_equity: float = 0.0
    peak_equity: float = 0.0
    max_drawdown_abs: float = 0.0
    max_drawdown_pct: float = 0.0

    def reset(self, initial_equity: float) -> None:
        self.initial_equity = float(initial_equity)
        self.peak_equity = float(initial_equity)
        self.max_drawdown_abs = 0.0
        self.max_drawdown_pct = 0.0

    def update(self, equity: float) -> None:
        """Feed a new post-trade equity reading.

        Updates ``peak_equity`` on a new high; otherwise widens the DD
        envelope when the trough deepens.
        """
        eq = float(equity)
        if eq > self.peak_equity:
            self.peak_equity = eq
            return
        dd_abs = self.peak_equity - eq
        if dd_abs > self.max_drawdown_abs:
            self.max_drawdown_abs = dd_abs
        if self.peak_equity > 0:
            dd_pct = dd_abs / self.peak_equity * 100.0
            if dd_pct > self.max_drawdown_pct:
                self.max_drawdown_pct = dd_pct


__all__ = ["StatsAccumulator", "DrawdownTracker"]
