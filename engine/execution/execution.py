"""
ExecutionModel — Apply realistic broker frictions to order fills and closes.

The model assumes the **MetaTrader 5 / AGREGAT Bid convention**: every OHLC
candle is plotted on the Bid side. Ask = Bid + spread. Fill prices follow
directly from this convention rather than from a synthetic Mid:

    long entry  (buy at Ask)        = Bid + spread + slippage
    long exit   (sell at Bid)       = Bid - slippage              # SL/TP fire exactly on chart
    short entry (sell at Bid)       = Bid - slippage              # entry exactly on chart
    short exit  (buy at Ask)        = Bid + spread + slippage     # SL/TP fire spread above chart
    pnl_after_costs                 = pnl_before - commission_per_lot * volume

``adjust_close_price`` is invoked only for non-stop closes (MANUAL / EOD /
WEEKEND). User-anchored SL/TP exit *prices* are not modified — the trader
set those levels deliberately and the engine fills at exactly that level.
For short SL/TP triggers the engine's ``_check_sl_tp`` already converts the
candle High/Low to the Ask side before comparison, so the SL/TP price is
the correct fill price (Bid + spread).

Three built-in profiles:
    Optimistic  — all costs zero (Phase 1 default; useful as upper bound).
    Realistic   — uses asset's spread / commission / slippage from AssetConfig.
    Custom      — user-supplied overrides via Settings.

Profiles are pure-data dicts so they round-trip through .agbk persistence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional

from engine.models.models import AssetConfig

ProfileName = Literal["Optimistic", "Realistic", "Custom"]


@dataclass(frozen=True)
class ExecutionCosts:
    """Per-fill cost breakdown — useful for analytics attribution."""

    spread_cost: float = 0.0  # dollars charged on the spread half
    slippage_cost: float = 0.0  # dollars charged on slippage
    commission_cost: float = 0.0  # dollars charged on commission_per_lot * volume

    @property
    def total(self) -> float:
        return self.spread_cost + self.slippage_cost + self.commission_cost


@dataclass
class ExecutionModel:
    """Transforms quoted prices into realistic fill prices.

    All fields are *overrides* applied on top of the asset config; leave them
    None to fall back to the per-asset values from `AssetConfig`. This lets
    the Settings UI dial in a slippage value once and have every asset use it.
    """

    profile: ProfileName = "Optimistic"
    spread_points: Optional[float] = None
    commission_per_lot: Optional[float] = None
    slippage_points: Optional[float] = None

    def __post_init__(self) -> None:
        # Single-slot cache for ``spread_price`` — the SL/TP-trigger hot loop
        # in ``BacktestCore._check_sl_tp`` calls ``spread_price`` once per
        # short bar. The lookup is cheap on its own, but on million-bar
        # datasets the per-call overhead adds up. Keying by every input that
        # affects the output keeps the cache automatically correct under any
        # field mutation. (TZ Phase 2 / B1.)
        self._spread_price_cache: Optional[tuple[Any, float]] = None

    # ----------------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------------
    def _spread(self, asset: AssetConfig) -> float:
        if self.profile == "Optimistic":
            return 0.0
        if self.spread_points is not None:
            return float(self.spread_points)
        return float(asset.spread_points or 0.0)

    def _slippage(self, asset: AssetConfig) -> float:
        if self.profile == "Optimistic":
            return 0.0
        if self.slippage_points is not None:
            return float(self.slippage_points)
        return float(asset.slippage_points or 0.0)

    def _commission(self, asset: AssetConfig) -> float:
        if self.profile == "Optimistic":
            return 0.0
        if self.commission_per_lot is not None:
            return float(self.commission_per_lot)
        return float(asset.commission_per_lot or 0.0)

    def slippage_price(self, asset: AssetConfig) -> float:
        """Effective slippage in *price units* (slippage_points × tick_size).

        Used by the SL/TP-trigger paths to widen the fill in the
        unfavourable direction (TZ Phase 5 / C1+A4) — a stop is filled at
        the next available price, not at the resting level. Long stops
        slip to a *lower* price (deeper loss); short stops slip to a
        *higher* price (also deeper loss).
        """
        return float(self._slippage(asset)) * float(asset.tick_size)

    def spread_price(self, asset: AssetConfig) -> float:
        """Effective spread expressed in *price units* (Ask − Bid).

        Charts in AGREGAT are quoted on the Bid side, so SL/TP triggers for
        short positions must convert the Bid-quoted High/Low into the Ask side
        before comparing against the user-anchored stop levels.

        The result is cached against every input that affects it
        (`id(asset)`, the asset's spread / tick fields, the profile, and any
        override on this model) so the SL/TP-trigger hot loop doesn't repeat
        the lookup per bar. Mutating any of those keys automatically forces a
        recomputation. (TZ Phase 2 / B1.)
        """
        cache_key = (
            id(asset),
            asset.spread_points,
            asset.tick_size,
            self.profile,
            self.spread_points,
        )
        cached = self._spread_price_cache
        if cached is not None and cached[0] == cache_key:
            return float(cached[1])
        value = float(self._spread(asset)) * float(asset.tick_size)
        self._spread_price_cache = (cache_key, value)
        return value

    def invalidate_caches(self) -> None:
        """Drop any memoised values keyed off mutable model state.

        Adapters call this after mutating ``profile`` / ``*_points`` overrides
        in-place (e.g. from a Settings dialog) when they cannot rebuild the
        whole ``ExecutionModel``. (TZ Phase 2 / B1.)
        """
        self._spread_price_cache = None

    # ----------------------------------------------------------------------
    # Fill-price transforms
    # ----------------------------------------------------------------------
    def adjust_fill_price(
        self,
        quoted: float,
        direction: str,
        asset: AssetConfig,
    ) -> float:
        """Return the *fill* price for an entry at `direction` given `quoted` (Bid).

        Long entries pay the Ask, so the full spread is added on top of Bid.
        Short entries hit the Bid, which is exactly what the chart shows, so no
        spread is applied; only slippage is added/subtracted in the
        unfavourable direction.
        """
        spread = self._spread(asset)
        slip = self._slippage(asset)
        if direction == "long":
            return quoted + (spread + slip) * asset.tick_size
        return quoted - slip * asset.tick_size

    def adjust_close_price(
        self,
        quoted: float,
        direction: str,
        asset: AssetConfig,
    ) -> float:
        """Return the *fill* price for a non-stop exit closing a `direction` position.

        Long exits sell at the Bid (= chart), so no spread is applied; only
        slippage. Short exits buy at the Ask (Bid + spread), so the full
        spread plus slippage is applied.

        SL / TP / LOCK / BE / GAP_SL closes do **not** flow through this
        function — the engine fills those via :py:meth:`slippage_price`
        applied directly inside :py:meth:`BacktestCore._check_sl_tp` (the
        anchor itself is user-supplied; only slippage widens the fill).
        """
        spread = self._spread(asset)
        slip = self._slippage(asset)
        if direction == "long":
            return quoted - slip * asset.tick_size
        return quoted + (spread + slip) * asset.tick_size

    # ----------------------------------------------------------------------
    # Cost computation (called on close to deduct from PnL)
    # ----------------------------------------------------------------------
    def costs_for_round_trip(
        self,
        volume: float,
        asset: AssetConfig,
    ) -> ExecutionCosts:
        """Per-trade dollar costs for a complete entry+exit cycle.

        spread_cost + slippage_cost are baked into adjusted prices already, so
        they are *informational* here. commission_cost MUST be deducted from PnL
        explicitly (broker charges it as a separate line item).
        """
        # Under the Bid model the full spread is paid on exactly one side of
        # the round-trip (long: entry; short: exit), so the round-trip spread
        # cost is the same as in the Mid model: one full spread.
        spread_pts = self._spread(asset)
        slip_pts = self._slippage(asset) * 2.0  # both legs slipped
        spread_dollars = spread_pts * volume * asset.point_value
        slip_dollars = slip_pts * volume * asset.point_value
        commission_dollars = self._commission(asset) * volume
        return ExecutionCosts(
            spread_cost=spread_dollars,
            slippage_cost=slip_dollars,
            commission_cost=commission_dollars,
        )

    # ----------------------------------------------------------------------
    # Persistence
    # ----------------------------------------------------------------------
    def to_dict(self) -> Dict[str, float | str | None]:
        return {
            "profile": self.profile,
            "spread_points": self.spread_points,
            "commission_per_lot": self.commission_per_lot,
            "slippage_points": self.slippage_points,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, float | str | None]) -> "ExecutionModel":
        profile = data.get("profile", "Optimistic")
        if profile not in ("Optimistic", "Realistic", "Custom"):
            profile = "Optimistic"
        return cls(
            profile=profile,  # type: ignore[arg-type]
            spread_points=data.get("spread_points"),  # type: ignore[arg-type]
            commission_per_lot=data.get("commission_per_lot"),  # type: ignore[arg-type]
            slippage_points=data.get("slippage_points"),  # type: ignore[arg-type]
        )


__all__ = ["ExecutionModel", "ExecutionCosts", "ProfileName"]
