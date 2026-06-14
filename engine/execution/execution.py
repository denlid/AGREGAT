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

import math
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional

from engine.models.models import AssetConfig

ProfileName = Literal["Optimistic", "Realistic", "Custom"]
# Phase 1.3 — slippage scaling shape. Default ``"fixed"`` reproduces
# the legacy behaviour. ``"linear"`` adds ``k * volume`` proportional
# to the order size; ``"sqrt"`` follows ``sqrt(volume)`` which is the
# classical square-root market-impact rule used by institutional
# transaction-cost models (e.g. Almgren–Chriss).
SlippageModel = Literal["fixed", "linear", "sqrt"]
# Numeric enum of :data:`SlippageModel` used by the shared slippage helpers.
SLIPPAGE_MODEL_FIXED = 0
SLIPPAGE_MODEL_LINEAR = 1
SLIPPAGE_MODEL_SQRT = 2


def slippage_model_to_int(model: SlippageModel) -> int:
    """Convert a :data:`SlippageModel` to the Numba-friendly enum int."""
    if model == "fixed":
        return SLIPPAGE_MODEL_FIXED
    if model == "linear":
        return SLIPPAGE_MODEL_LINEAR
    if model == "sqrt":
        return SLIPPAGE_MODEL_SQRT
    raise ValueError(f"Unknown slippage_model: {model!r}")


def compute_slippage_multiplier(
    model_int: int,
    volume: float,
    k: float,
) -> float:
    """Return the volume-driven multiplier applied on top of base slippage.

    ``base_slip * multiplier`` is the effective slippage in points for
    a fill of size ``volume``. Negative ``k`` is clamped to zero — a
    *favourable* slippage proportional to size has no market analogue.
    """
    if model_int == SLIPPAGE_MODEL_FIXED or k <= 0.0 or volume <= 0.0:
        return 1.0
    if model_int == SLIPPAGE_MODEL_LINEAR:
        return 1.0 + k * volume
    if model_int == SLIPPAGE_MODEL_SQRT:
        return 1.0 + k * math.sqrt(volume)
    return 1.0


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
    # Phase 1.3 — volume-aware slippage. ``model="fixed"`` keeps legacy
    # behaviour bit-exact; ``"linear"`` / ``"sqrt"`` scale the base
    # slippage by an ``f(volume)`` term controlled by ``k``.
    slippage_model: SlippageModel = "fixed"
    slippage_k: float = 0.0

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

    def slippage_price(
        self,
        asset: AssetConfig,
        volume: Optional[float] = None,
    ) -> float:
        """Effective slippage in *price units* (slippage_points × tick_size).

        Used by the SL/TP-trigger paths to widen the fill in the
        unfavourable direction (TZ Phase 5 / C1+A4) — a stop is filled at
        the next available price, not at the resting level. Long stops
        slip to a *lower* price (deeper loss); short stops slip to a
        *higher* price (also deeper loss).

        Volume-aware (Phase 1.3): when ``volume`` is supplied and the
        :pyattr:`slippage_model` is ``"linear"`` or ``"sqrt"``, the base
        slippage is multiplied by :func:`compute_slippage_multiplier`.
        Passing ``volume=None`` reproduces the legacy fixed-slippage
        behaviour bit-exact.
        """
        base = float(self._slippage(asset)) * float(asset.tick_size)
        if volume is None or volume <= 0.0:
            return base
        mult = compute_slippage_multiplier(
            slippage_model_to_int(self.slippage_model),
            float(volume),
            float(self.slippage_k),
        )
        return base * mult

    def spread_price(
        self,
        asset: AssetConfig,
        spread_points_override: Optional[float] = None,
    ) -> float:
        """Effective spread expressed in *price units* (Ask − Bid).

        Charts in AGREGAT are quoted on the Bid side, so SL/TP triggers for
        short positions must convert the Bid-quoted High/Low into the Ask side
        before comparing against the user-anchored stop levels.

        The result is cached against every input that affects it
        (`id(asset)`, the asset's spread / tick fields, the profile, and any
        override on this model) so the SL/TP-trigger hot loop doesn't repeat
        the lookup per bar. Mutating any of those keys automatically forces a
        recomputation. (TZ Phase 2 / B1.)

        Phase 1.4 — when ``spread_points_override`` is provided (typically
        a per-bar spread from the data feed), it short-circuits the
        cache and returns ``override * tick_size`` directly. The cached
        bar-agnostic value is still maintained for code paths that don't
        pass an override.
        """
        if spread_points_override is not None and spread_points_override >= 0.0:
            # Profile=Optimistic explicitly disables all costs, so a
            # per-bar spread should not bring them back on.
            if self.profile == "Optimistic":
                return 0.0
            return float(spread_points_override) * float(asset.tick_size)
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
        volume: Optional[float] = None,
        bar_spread_points: Optional[float] = None,
    ) -> float:
        """Return the *fill* price for an entry at `direction` given `quoted` (Bid).

        Long entries pay the Ask, so the full spread is added on top of Bid.
        Short entries hit the Bid, which is exactly what the chart shows, so no
        spread is applied; only slippage is added/subtracted in the
        unfavourable direction.

        Volume-aware slippage (Phase 1.3): pass the order ``volume`` to
        scale the slippage by the configured ``slippage_model``. ``None``
        keeps the legacy flat behaviour.

        Phase 1.4 — supply ``bar_spread_points`` to use the data-feed's
        per-bar spread (pulled from the ``spread`` column when present).
        Falls back to the configured fixed spread when ``None``.
        """
        if (
            bar_spread_points is not None
            and bar_spread_points >= 0.0
            and self.profile != "Optimistic"
        ):
            spread = float(bar_spread_points)
        else:
            spread = self._spread(asset)
        base_slip_pts = self._slippage(asset)
        if volume is not None and volume > 0.0:
            mult = compute_slippage_multiplier(
                slippage_model_to_int(self.slippage_model),
                float(volume),
                float(self.slippage_k),
            )
            slip_pts = base_slip_pts * mult
        else:
            slip_pts = base_slip_pts
        if direction == "long":
            return quoted + (spread + slip_pts) * asset.tick_size
        return quoted - slip_pts * asset.tick_size

    def adjust_close_price(
        self,
        quoted: float,
        direction: str,
        asset: AssetConfig,
        volume: Optional[float] = None,
        bar_spread_points: Optional[float] = None,
    ) -> float:
        """Return the *fill* price for a non-stop exit closing a `direction` position.

        Long exits sell at the Bid (= chart), so no spread is applied; only
        slippage. Short exits buy at the Ask (Bid + spread), so the full
        spread plus slippage is applied.

        SL / TP / LOCK / BE / GAP_SL closes do **not** flow through this
        function — the engine fills those via :py:meth:`slippage_price`
        applied directly inside :py:meth:`BacktestCore._check_sl_tp` (the
        anchor itself is user-supplied; only slippage widens the fill).

        Volume-aware slippage (Phase 1.3) — pass ``volume`` to scale by
        the configured ``slippage_model``.
        Phase 1.4 — pass ``bar_spread_points`` to override the fixed
        spread on this bar.
        """
        if (
            bar_spread_points is not None
            and bar_spread_points >= 0.0
            and self.profile != "Optimistic"
        ):
            spread = float(bar_spread_points)
        else:
            spread = self._spread(asset)
        base_slip_pts = self._slippage(asset)
        if volume is not None and volume > 0.0:
            mult = compute_slippage_multiplier(
                slippage_model_to_int(self.slippage_model),
                float(volume),
                float(self.slippage_k),
            )
            slip_pts = base_slip_pts * mult
        else:
            slip_pts = base_slip_pts
        if direction == "long":
            return quoted - slip_pts * asset.tick_size
        return quoted + (spread + slip_pts) * asset.tick_size

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
        # Phase 1.3 — slippage scales by the configured volume law so the
        # cost report reflects what was actually deducted at fill.
        mult = compute_slippage_multiplier(
            slippage_model_to_int(self.slippage_model),
            float(volume),
            float(self.slippage_k),
        )
        slip_pts = self._slippage(asset) * 2.0 * mult  # both legs slipped
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
            "slippage_model": self.slippage_model,
            "slippage_k": self.slippage_k,
        }

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, float | str | None],
        *,
        strict: bool = False,
    ) -> "ExecutionModel":
        """Rehydrate an :class:`ExecutionModel` from its persisted dict.

        Phase 1.9 / §7 fix #6 — when ``strict=True`` the loader will
        raise :class:`ValueError` on any unknown profile, unknown slippage
        model, or non-numeric override. The default lenient behaviour is
        unchanged: unknown values silently fall back to safe defaults
        with a warning so legacy ``.agbk`` archives keep loading.
        """
        import logging

        logger = logging.getLogger("StrategyTester.execution")

        profile = data.get("profile", "Optimistic")
        if profile not in ("Optimistic", "Realistic", "Custom"):
            msg = (
                f"ExecutionModel.from_dict: unknown profile {profile!r}; "
                "valid: Optimistic / Realistic / Custom."
            )
            if strict:
                raise ValueError(msg)
            logger.warning(msg + " Falling back to 'Optimistic'.")
            profile = "Optimistic"

        slippage_model = data.get("slippage_model", "fixed")
        if slippage_model not in ("fixed", "linear", "sqrt"):
            msg = (
                f"ExecutionModel.from_dict: unknown slippage_model "
                f"{slippage_model!r}; valid: fixed / linear / sqrt."
            )
            if strict:
                raise ValueError(msg)
            logger.warning(msg + " Falling back to 'fixed'.")
            slippage_model = "fixed"

        def _coerce_float(value: Any, name: str) -> Optional[float]:
            if value is None:
                return None
            try:
                return float(value)
            except (TypeError, ValueError) as exc:
                if strict:
                    raise ValueError(
                        f"ExecutionModel.from_dict: {name} must be numeric, got {value!r}"
                    ) from exc
                logger.warning(
                    "ExecutionModel.from_dict: ignoring non-numeric %s=%r", name, value
                )
                return None

        return cls(
            profile=profile,  # type: ignore[arg-type]
            spread_points=_coerce_float(data.get("spread_points"), "spread_points"),
            commission_per_lot=_coerce_float(
                data.get("commission_per_lot"), "commission_per_lot"
            ),
            slippage_points=_coerce_float(
                data.get("slippage_points"), "slippage_points"
            ),
            slippage_model=slippage_model,  # type: ignore[arg-type]
            slippage_k=_coerce_float(data.get("slippage_k"), "slippage_k") or 0.0,
        )


__all__ = [
    "ExecutionModel",
    "ExecutionCosts",
    "ProfileName",
    "SlippageModel",
    "SLIPPAGE_MODEL_FIXED",
    "SLIPPAGE_MODEL_LINEAR",
    "SLIPPAGE_MODEL_SQRT",
    "slippage_model_to_int",
    "compute_slippage_multiplier",
]
