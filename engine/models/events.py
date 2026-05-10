"""
Typed event payloads for engine signals and EventBus subscribers.

Replaces ad-hoc `dict` payloads so callers get IDE autocomplete and mypy
catches typos. Events are frozen dataclasses (cheap, hashable, immutable).

Compatibility: all events expose `to_dict()` for the legacy event bus
which is still keyed by string event-name and dict payload.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from engine.models.models import Position, Trade


@dataclass(frozen=True)
class CandleEvent:
    """Emitted after each processed candle."""

    index: int
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StepCompletedEvent:
    """Companion payload for `BacktestEngine.step_completed`."""

    index: int
    candle: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {"index": self.index, "candle": self.candle}


@dataclass(frozen=True)
class PendingFilledEvent:
    """A pending order was filled at the next candle's open."""

    position: Position

    def to_dict(self) -> Dict[str, Any]:
        return {"position": self.position}


@dataclass(frozen=True)
class PositionOpenedEvent:
    position: Position

    def to_dict(self) -> Dict[str, Any]:
        return {"position": self.position}


@dataclass(frozen=True)
class PositionClosedEvent:
    trade: Trade

    def to_dict(self) -> Dict[str, Any]:
        return {"trade": self.trade}


@dataclass(frozen=True)
class TradeCompletedEvent:
    """Mirror of PositionClosedEvent; surfaced via the `trade_completed` Qt signal."""

    trade: Trade

    def to_dict(self) -> Dict[str, Any]:
        return {"trade": self.trade}


@dataclass(frozen=True)
class StatsUpdatedEvent:
    total_trades: int
    wins: int
    losses: int
    longs: int
    shorts: int
    gross_profit: float
    gross_loss: float
    equity: float
    max_equity: float
    max_drawdown_abs: float
    max_drawdown_pct: float

    @property
    def win_rate(self) -> float:
        return (self.wins / self.total_trades * 100.0) if self.total_trades else 0.0

    @property
    def profit_factor(self) -> float:
        if self.gross_loss > 0:
            return self.gross_profit / self.gross_loss
        return float("inf") if self.gross_profit > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["win_rate"] = self.win_rate
        d["profit_factor"] = self.profit_factor
        return d


@dataclass(frozen=True)
class SimulationFinishedEvent:
    total_candles: int
    total_trades: int
    final_equity: float
    return_pct: float
    max_drawdown_pct: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DrawingChangedEvent:
    drawing_id: int
    action: str  # 'added' | 'updated' | 'removed'
    payload: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


__all__ = [
    "CandleEvent",
    "StepCompletedEvent",
    "PendingFilledEvent",
    "PositionOpenedEvent",
    "PositionClosedEvent",
    "TradeCompletedEvent",
    "StatsUpdatedEvent",
    "SimulationFinishedEvent",
    "DrawingChangedEvent",
]
