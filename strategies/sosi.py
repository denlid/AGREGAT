from engine.strategy.context import Strategy, Param
from engine.core.indicators import sma
import numpy as np
from datetime import datetime
from typing import List, Dict, Optional, Tuple


class LinearFVGStrategy(Strategy):
    """
    15m-1m Linear Structure & FVG Alignment

    Detects 15m market structure shifts (MSS) and Fair Value Gaps,
    waits for price to test the FVG zone, and enters on 1m linear
    structure break in the direction of the 15m context.
    """

    name = "Linear Structure & FVG Alignment"

    PARAMS = [
        Param("risk_pct", "Risk per Trade %", "float", 1.0, min=0.1, max=5.0, step=0.1),
        Param("rr", "Risk:Reward", "float", 2.0, min=0.5, max=5.0, step=0.5),
        Param("swing_lb", "Swing Lookback (bars)", "int", 2, min=1, max=5, step=1),
        Param("min_1m_bars", "Min 1m bars before MSS", "int", 3, min=2, max=10, step=1),
        Param("sl_buffer", "SL buffer (points)", "float", 10.0, min=0.0, max=100.0, step=1.0),
    ]

    def __init__(self):
        super().__init__()

        # ── 15m state ──
        self._15m_closes: List[float] = []
        self._15m_highs: List[float] = []
        self._15m_lows: List[float] = []
        self._15m_times: List[datetime] = []

        self._swing_highs: List[float] = []   # most recent swing high prices
        self._swing_lows: List[float] = []    # most recent swing low prices
        self._trend: str = "neutral"          # "bullish", "bearish", "neutral"

        # Active 15m FVG zones
        # Each dict: {type, upper, lower, active, time, sl_level, mss_level}
        self._fvg_zones: List[Dict] = []

        # ── 1m test state ──
        self._testing_fvg_idx: Optional[int] = None  # index into self._fvg_zones
        self._test_1m_closes: List[float] = []
        self._test_start_time: Optional[datetime] = None
        self._entry_triggered: bool = False  # prevent re-entry on same setup

    # ────────────────────────────────────────────────────────────
    #  15m HTF hooks
    # ────────────────────────────────────────────────────────────

    def on_15m_close(self, ctx):
        view = ctx.get_tf("15m")
        if view is None or len(view) < 3:
            return

        last_15m = view.candle_at(-1)
        if last_15m is None:
            return

        # 1) Append the new completed 15m bar
        self._15m_closes.append(float(last_15m["close"]))
        self._15m_highs.append(float(last_15m["high"]))
        self._15m_lows.append(float(last_15m["low"]))
        self._15m_times.append(last_15m["time"])

        # Keep lists bounded
        max_bars = int(self.params["fvg_lookback"]) + 20 if "fvg_lookback" in self.params else 100
        for lst in [self._15m_closes, self._15m_highs, self._15m_lows, self._15m_times]:
            if len(lst) > max_bars:
                diff = len(lst) - max_bars
                lst[:diff] = []

        risk_pct = float(self.params["risk_pct"])
        rr = float(self.params["rr"])
        swing_lb = int(self.params["swing_lb"])

        # 2) Detect swing points (2-bar left/right confirmation)
        self._detect_15m_swings(swing_lb)

        # 3) Determine trend from swing sequence
        self._update_15m_trend()

        # 4) Check for structural break (MSS)
        mss_info = self._check_mss()

        # 5) After MSS, look for FVG
        if mss_info is not None:
            fvg = self._find_15m_fvg(mss_info)
            if fvg is not None:
                fvg["sl_level"] = mss_info["sl_level"]
                fvg["mss_level"] = mss_info["mss_level"]
                self._fvg_zones.append(fvg)
                ctx.log(
                    f"15m {mss_info['direction'].upper()} MSS + "
                    f"FVG [{fvg['lower']:.2f} .. {fvg['upper']:.2f}], "
                    f"SL level = {fvg['sl_level']:.2f}"
                )

        # 6) Invalidate filled FVGs
        self._invalidate_fvg_zones()

        # 7) Clean up old FVGs
        self._fvg_zones = [z for z in self._fvg_zones if z["active"]]

        # 8) Clear test state if the FVG we were testing got invalidated
        if self._testing_fvg_idx is not None:
            if self._testing_fvg_idx >= len(self._fvg_zones) or \
               not self._fvg_zones[self._testing_fvg_idx]["active"]:
                self._clear_test_state()

    # ────────────────────────────────────────────────────────────
    #  1m LTF hook (entry logic)
    # ────────────────────────────────────────────────────────────

    def on_candle(self, ctx):
        if ctx.position is not None:
            return

        c = ctx.candle
        close_1m = float(c["close"])
        risk_pct = float(self.params["risk_pct"])
        rr = float(self.params["rr"])
        min_bars = int(self.params["min_1m_bars"])

        # ── Find any active FVG being tested by current 1m close ──
        active_idx = self._find_testing_fvg(close_1m)

        if active_idx is None:
            # Price is not testing any FVG — clear test state
            self._clear_test_state()
            return

        # We are testing an FVG
        if self._testing_fvg_idx != active_idx:
            # New test started on a different FVG
            self._clear_test_state()
            self._testing_fvg_idx = active_idx
            self._test_start_time = c["time"]
            self._test_1m_closes = [close_1m]
            self._entry_triggered = False
            ctx.log(f"Testing 15m {self._fvg_zones[active_idx]['type']} FVG zone at {close_1m:.2f}")
            return

        # Continue tracking
        self._test_1m_closes.append(close_1m)
        if len(self._test_1m_closes) > 100:
            self._test_1m_closes.pop(0)

        if self._entry_triggered:
            return

        # Need minimum bars to form local structure
        if len(self._test_1m_closes) < min_bars:
            return

        fvg = self._fvg_zones[self._testing_fvg_idx]
        direction = fvg["type"]  # "bullish" -> long, "bearish" -> short

        # ── Detect 1m linear structure break ──
        mss_1m = self._detect_1m_mss(direction)

        if mss_1m is None:
            return

        # ── Entry Signal ──
        sl_level = fvg["sl_level"]
        if sl_level is None:
            ctx.log("FVG has no SL level — skipping entry")
            self._clear_test_state()
            return

        entry_price = close_1m  # we'll enter at next open, but use current close for calc

        if direction == "bullish":
            # SL below the last 15m structural low
            stop_price = sl_level - float(self.params["sl_buffer"]) * 0.01
            if entry_price <= stop_price:
                ctx.log("Entry price too close to SL — skipping")
                self._clear_test_state()
                return
            sl_points = (entry_price - stop_price) / 0.01  # convert to points
            tp_price = entry_price + sl_points * rr * 0.01
            tp_points = sl_points * rr

            vol = ctx.calculate_lot(sl_points, risk_pct=risk_pct)
            if vol is None or vol <= 0:
                ctx.log(f"Invalid lot size ({vol}) — skipping")
                self._clear_test_state()
                return

            ctx.buy_at_open(
                volume=vol,
                sl=stop_price,
                tp=tp_price,
                reason=f"15m {direction.upper()} FVG + 1m MSS, RR={rr}",
                tag="FVG_LONG",
            )
            ctx.log(
                f"🟢 LONG entry: price={entry_price:.2f}, "
                f"SL={stop_price:.2f}, TP={tp_price:.2f}, "
                f"vol={vol:.4f}, risk={risk_pct}%"
            )
            self._entry_triggered = True

        else:  # bearish
            stop_price = sl_level + float(self.params["sl_buffer"]) * 0.01
            if entry_price >= stop_price:
                ctx.log("Entry price too close to SL — skipping")
                self._clear_test_state()
                return
            sl_points = (stop_price - entry_price) / 0.01
            tp_price = entry_price - sl_points * rr * 0.01
            tp_points = sl_points * rr

            vol = ctx.calculate_lot(sl_points, risk_pct=risk_pct)
            if vol is None or vol <= 0:
                ctx.log(f"Invalid lot size ({vol}) — skipping")
                self._clear_test_state()
                return

            ctx.sell_at_open(
                volume=vol,
                sl=stop_price,
                tp=tp_price,
                reason=f"15m {direction.upper()} FVG + 1m MSS, RR={rr}",
                tag="FVG_SHORT",
            )
            ctx.log(
                f"🔴 SHORT entry: price={entry_price:.2f}, "
                f"SL={stop_price:.2f}, TP={tp_price:.2f}, "
                f"vol={vol:.4f}, risk={risk_pct}%"
            )
            self._entry_triggered = True

    # ────────────────────────────────────────────────────────────
    #  Helper: 15m swing detection
    # ────────────────────────────────────────────────────────────

    def _detect_15m_swings(self, lookback: int):
        """Detect swing highs/lows with left/right confirmation."""
        closes = self._15m_closes
        if len(closes) < 2 * lookback + 1:
            return

        # Check the bar at idx = -(lookback+1) (fully confirmed now)
        check_idx = len(closes) - lookback - 1
        if check_idx < lookback:
            return

        pivot_val = closes[check_idx]

        # Swing high
        if all(pivot_val > closes[check_idx - j] for j in range(1, lookback + 1)) and \
           all(pivot_val > closes[check_idx + j] for j in range(1, lookback + 1)):
            if not self._swing_highs or pivot_val != self._swing_highs[-1]:
                self._swing_highs.append(pivot_val)
                # Keep last 8
                if len(self._swing_highs) > 8:
                    self._swing_highs.pop(0)
                # check_idx  # store time too

        # Swing low
        if all(pivot_val < closes[check_idx - j] for j in range(1, lookback + 1)) and \
           all(pivot_val < closes[check_idx + j] for j in range(1, lookback + 1)):
            if not self._swing_lows or pivot_val != self._swing_lows[-1]:
                self._swing_lows.append(pivot_val)
                if len(self._swing_lows) > 8:
                    self._swing_lows.pop(0)

    # ────────────────────────────────────────────────────────────
    #  Helper: trend from swing sequence
    # ────────────────────────────────────────────────────────────

    def _update_15m_trend(self):
        if len(self._swing_highs) < 2 or len(self._swing_lows) < 2:
            self._trend = "neutral"
            return

        highs_up = self._swing_highs[-1] > self._swing_highs[-2]
        lows_up = self._swing_lows[-1] > self._swing_lows[-2]

        if highs_up and lows_up:
            self._trend = "bullish"
        elif not highs_up and not lows_up:
            self._trend = "bearish"
        else:
            self._trend = "neutral"

    # ────────────────────────────────────────────────────────────
    #  Helper: detect 15m MSS (structure break)
    # ────────────────────────────────────────────────────────────

    def _check_mss(self) -> Optional[Dict]:
        """
        Returns MSS info if a break was detected on THIS 15m bar close.
        """
        if len(self._15m_closes) < 2:
            return None

        current_close = self._15m_closes[-1]
        prev_close = self._15m_closes[-2]

        # Bullish MSS: close above the last swing high
        if self._swing_highs:
            last_sh = self._swing_highs[-1]
            if prev_close <= last_sh < current_close:
                # Swing low for SL (most recent confirmed low)
                sl_level = self._swing_lows[-1] if self._swing_lows else None
                return {
                    "direction": "bullish",
                    "broken_level": last_sh,
                    "sl_level": sl_level,
                }

        # Bearish MSS: close below the last swing low
        if self._swing_lows:
            last_sl = self._swing_lows[-1]
            if prev_close >= last_sl > current_close:
                sl_level = self._swing_highs[-1] if self._swing_highs else None
                return {
                    "direction": "bearish",
                    "broken_level": last_sl,
                    "sl_level": sl_level,
                }

        return None

    # ────────────────────────────────────────────────────────────
    #  Helper: 15m FVG detection (3-candle gap)
    # ────────────────────────────────────────────────────────────

    def _find_15m_fvg(self, mss_info: Dict) -> Optional[Dict]:
        """
        Look for an FVG formed by the last 3 15m candles.
        The MSS bar is the middle candle (index -1).
        We check candles at indices [-3, -2, -1].
        """
        if len(self._15m_highs) < 3 or len(self._15m_lows) < 3:
            return None

        h = self._15m_highs
        lo = self._15m_lows

        direction = mss_info["direction"]

        if direction == "bullish":
            # Bullish FVG: low of candle 3 > high of candle 1
            # candle 1 = idx -3, candle 2 = idx -2 (impulsive), candle 3 = idx -1
            lower_bound = h[-3]   # high of first candle
            upper_bound = lo[-1]  # low of third candle
            if upper_bound > lower_bound + 0.0001:  # positive gap
                return {
                    "type": "bullish",
                    "lower": lower_bound,
                    "upper": upper_bound,
                    "active": True,
                    "time": self._15m_times[-1] if self._15m_times else None,
                    "sl_level": None,
                    "mss_level": mss_info["broken_level"],
                }

        else:  # bearish
            # Bearish FVG: high of candle 3 < low of candle 1
            lower_bound = h[-1]   # high of third candle
            upper_bound = lo[-3]  # low of first candle
            if upper_bound > lower_bound + 0.0001:  # positive gap
                return {
                    "type": "bearish",
                    "lower": lower_bound,
                    "upper": upper_bound,
                    "active": True,
                    "time": self._15m_times[-1] if self._15m_times else None,
                    "sl_level": None,
                    "mss_level": mss_info["broken_level"],
                }

        return None

    # ────────────────────────────────────────────────────────────
    #  Helper: invalidate FVG zones completely filled by 15m body
    # ────────────────────────────────────────────────────────────

    def _invalidate_fvg_zones(self):
        if not self._15m_closes:
            return

        last_close = self._15m_closes[-1]

        for zone in self._fvg_zones:
            if not zone["active"]:
                continue

            if zone["type"] == "bullish":
                # Close below the FVG lower bound → zone fully filled
                if last_close < zone["lower"]:
                    zone["active"] = False
            else:  # bearish
                if last_close > zone["upper"]:
                    zone["active"] = False

    # ────────────────────────────────────────────────────────────
    #  Helper: check if 1m close is testing an active FVG
    # ────────────────────────────────────────────────────────────

    def _find_testing_fvg(self, close_1m: float) -> Optional[int]:
        """
        Returns index of the most recent active FVG that contains close_1m
        (or touches its boundary).
        """
        best_idx = None
        best_time = None

        for i, zone in enumerate(self._fvg_zones):
            if not zone["active"]:
                continue

            lower = zone["lower"]
            upper = zone["upper"]

            # Check if close is inside the zone or at boundary
            if lower <= close_1m <= upper:
                # Pick the most recent (largest time) active zone
                t = zone.get("time")
                if best_time is None or (t is not None and t > best_time):
                    best_time = t
                    best_idx = i

        return best_idx

    # ────────────────────────────────────────────────────────────
    #  Helper: detect 1m linear structure break (MSS)
    # ────────────────────────────────────────────────────────────

    def _detect_1m_mss(self, direction: str) -> bool:
        """
        Detect a 1m linear structure break in the given direction.
        Uses the closes accumulated since entering the test zone.

        For 'bullish': break above the highest close seen so far during the test.
        For 'bearish': break below the lowest close seen so far during the test.
        """
        if len(self._test_1m_closes) < 3:
            return False

        current = self._test_1m_closes[-1]
        prev = self._test_1m_closes[-2]

        if direction == "bullish":
            # Break above the highest close seen so far (excluding current)
            peak = max(self._test_1m_closes[:-1])
            if prev <= peak < current:
                return True

        else:  # bearish
            trough = min(self._test_1m_closes[:-1])
            if prev >= trough > current:
                return True

        return False

    # ────────────────────────────────────────────────────────────
    #  Helper: clear test state
    # ────────────────────────────────────────────────────────────

    def _clear_test_state(self):
        self._testing_fvg_idx = None
        self._test_1m_closes.clear()
        self._test_start_time = None
        self._entry_triggered = False