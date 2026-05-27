import numpy as np
from engine.api import Strategy, Param

strategy = Strategy(
    name="Linear CISD 15m-1m",
    risk_pct=Param(name="risk_pct", label="Risk %", kind="float", default=1.0, min=0.1, max=5.0, step=0.1),
    rr=Param(name="rr", label="R:R", kind="float", default=2.0, min=1.0, max=5.0, step=0.5),
    htf_lookback=Param(name="htf_lookback", label="HTF Structure Lookback", kind="int", default=10, min=3, max=50, step=1),
    ltf_lookback=Param(name="ltf_lookback", label="LTF Structure Lookback", kind="int", default=5, min=2, max=20, step=1),
)

# --- State (HTF context) ---
strategy.htf_structure = None      # "long" | "short"
strategy.htf_fvg_top = None
strategy.htf_fvg_bottom = None
strategy.htf_sl_level = None       # structural invalidation level
strategy.pending_direction = None  # direction waiting for 1m confirmation


@strategy.on_15m_close()
def _(c):
    if len(c.close_htf_arr) < strategy.htf_lookback + 10:
        return

    closes = c.close_htf_arr
    recent = closes[-strategy.htf_lookback - 1:-1]
    struct_high = float(np.max(recent))
    struct_low = float(np.min(recent))
    curr_close = c.close_htf

    # === 15m Linear BOS / MSS (body close only) ===
    if curr_close > struct_high and strategy.htf_structure != "long":
        strategy.htf_structure = "long"
        strategy.htf_sl_level = struct_low
        strategy.pending_direction = None

        # Search bullish FVG: low[i] > high[i-2]
        for i in range(-5, -1):
            if c.low_htf_arr[i] > c.high_htf_arr[i - 2]:
                strategy.htf_fvg_bottom = float(c.high_htf_arr[i - 2])
                strategy.htf_fvg_top = float(c.low_htf_arr[i])
                strategy.pending_direction = "long"
                strategy.log(f"15m BOS Long | FVG {strategy.htf_fvg_bottom:.2f}-{strategy.htf_fvg_top:.2f} | SL {strategy.htf_sl_level:.2f}")
                break
        else:
            strategy.htf_fvg_top = None
            strategy.htf_fvg_bottom = None

    elif curr_close < struct_low and strategy.htf_structure != "short":
        strategy.htf_structure = "short"
        strategy.htf_sl_level = struct_high
        strategy.pending_direction = None

        # Search bearish FVG: high[i] < low[i-2]
        for i in range(-5, -1):
            if c.high_htf_arr[i] < c.low_htf_arr[i - 2]:
                strategy.htf_fvg_top = float(c.low_htf_arr[i - 2])
                strategy.htf_fvg_bottom = float(c.high_htf_arr[i])
                strategy.pending_direction = "short"
                strategy.log(f"15m BOS Short | FVG {strategy.htf_fvg_bottom:.2f}-{strategy.htf_fvg_top:.2f} | SL {strategy.htf_sl_level:.2f}")
                break
        else:
            strategy.htf_fvg_top = None
            strategy.htf_fvg_bottom = None

    # === FVG invalidation: 15m body closes beyond the zone ===
    if strategy.htf_fvg_top is not None:
        if strategy.htf_structure == "long" and curr_close < strategy.htf_fvg_bottom:
            strategy.log("15m FVG invalidated (long)")
            strategy.htf_fvg_top = None
            strategy.htf_fvg_bottom = None
            strategy.pending_direction = None
        elif strategy.htf_structure == "short" and curr_close > strategy.htf_fvg_top:
            strategy.log("15m FVG invalidated (short)")
            strategy.htf_fvg_top = None
            strategy.htf_fvg_bottom = None
            strategy.pending_direction = None


@strategy.on_candle()
def _(c):
    if strategy.pending_direction is None or strategy.htf_fvg_top is None:
        return
    if c.position is not None:
        return

    # === Zone test: 1m body inside FVG or candle touched the zone ===
    in_zone = (strategy.htf_fvg_bottom <= c.close <= strategy.htf_fvg_top)
    touched = (c.high >= strategy.htf_fvg_bottom and c.low <= strategy.htf_fvg_top)
    if not in_zone and not touched:
        return

    if len(c.close_arr) < strategy.ltf_lookback + 2:
        return

    recent_1m = c.close_arr[-strategy.ltf_lookback - 1:-1]
    local_high = float(np.max(recent_1m))
    local_low = float(np.min(recent_1m))

    # === Long entry: 1m linear break up ===
    if strategy.pending_direction == "long":
        if c.close > local_high:
            entry = c.close
            sl = strategy.htf_sl_level
            risk = entry - sl
            if risk <= 0:
                return
            tp = entry + risk * strategy.rr
            strategy.long(sl=sl, tp=tp, risk=strategy.risk_pct)
            strategy.log(f"LONG entry {entry:.2f} | SL {sl:.2f} | TP {tp:.2f}")
            strategy.pending_direction = None

    # === Short entry: 1m linear break down ===
    elif strategy.pending_direction == "short":
        if c.close < local_low:
            entry = c.close
            sl = strategy.htf_sl_level
            risk = sl - entry
            if risk <= 0:
                return
            tp = entry - risk * strategy.rr
            strategy.short(sl=sl, tp=tp, risk=strategy.risk_pct)
            strategy.log(f"SHORT entry {entry:.2f} | SL {sl:.2f} | TP {tp:.2f}")
            strategy.pending_direction = None