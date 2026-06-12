import pandas as pd

from engine.api import Strategy, Param


strategy = Strategy(
    name="Linear Structure & FVG (15m/1m)",
    risk_pct=Param(name="risk_pct", label="Risk %", kind="float", default=1.0, min=0.1, max=10.0, step=0.1),
    rr_ratio=Param(name="rr_ratio", label="RR Ratio", kind="float", default=2.0, min=1.0, max=5.0, step=0.1),
    htf_pivot_lookback=Param(name="htf_pivot_lookback", label="HTF Pivot Lookback", kind="int", default=3, min=1, max=10),
    ltf_pivot_lookback=Param(name="ltf_pivot_lookback", label="LTF Pivot Lookback", kind="int", default=2, min=1, max=10),
)

# ---- In‑memory state -----
strategy._htf_highs = []        # list of (index, price) for HTF swing highs
strategy._htf_lows  = []        # list of (index, price) for HTF swing lows
strategy._fvg_list  = []        # list of active FVG dicts
strategy._htf_trend = None      # 'bullish', 'bearish', or None

MAX_PIVOT_HISTORY = 10   # Keep last N pivots to avoid infinite list


def _time_tag_part(value):
    return pd.Timestamp(value).value


def _find_last_swing_high(arr, min_dist):
    """Return (index, value) of the most recent confirmed swing high."""
    n = len(arr)
    for i in range(n - 2, 0, -1):
        if i < min_dist or i > n - min_dist:
            continue
        if arr[i] > arr[i - 1] and arr[i] > arr[i + 1]:
            return i, arr[i]
    return None, None


def _find_last_swing_low(arr, min_dist):
    """Return (index, value) of the most recent confirmed swing low."""
    n = len(arr)
    for i in range(n - 2, 0, -1):
        if i < min_dist or i > n - min_dist:
            continue
        if arr[i] < arr[i - 1] and arr[i] < arr[i + 1]:
            return i, arr[i]
    return None, None


def _update_htf_structure(c):
    """Analyse 15m data, update swing points & FVG list."""
    closes = c.close_htf_arr

    # 1. Swing highs & lows (only if enough bars)
    if len(closes) < strategy.htf_pivot_lookback + 2:
        return

    idx_h, price_h = _find_last_swing_high(closes, strategy.htf_pivot_lookback)
    idx_l, price_l = _find_last_swing_low(closes, strategy.htf_pivot_lookback)

    # Record highs & lows (avoid duplicates, keep bounded list)
    if idx_h is not None:
        if not strategy._htf_highs or idx_h != strategy._htf_highs[-1][0]:
            strategy._htf_highs.append((idx_h, price_h))
            strategy.log(f"New HTF swing high: idx={idx_h}, price={price_h:.2f}")
            while len(strategy._htf_highs) > MAX_PIVOT_HISTORY:
                strategy._htf_highs.pop(0)

    if idx_l is not None:
        if not strategy._htf_lows or idx_l != strategy._htf_lows[-1][0]:
            strategy._htf_lows.append((idx_l, price_l))
            strategy.log(f"New HTF swing low: idx={idx_l}, price={price_l:.2f}")
            while len(strategy._htf_lows) > MAX_PIVOT_HISTORY:
                strategy._htf_lows.pop(0)

    # 2. Determine / update trend
    old_trend = strategy._htf_trend

    # If we have at least 2 highs and 2 lows, try to establish initial trend
    if len(strategy._htf_highs) >= 2 and len(strategy._htf_lows) >= 2:
        h1, h2 = strategy._htf_highs[-2][1], strategy._htf_highs[-1][1]
        l1, l2 = strategy._htf_lows[-2][1], strategy._htf_lows[-1][1]

        if strategy._htf_trend is None:
            # Initial structure recognition
            if h2 > h1 and l2 > l1:
                strategy._htf_trend = 'bullish'
                strategy.log("Initial HTF trend: BULLISH (HH + HL)")
            elif h2 < h1 and l2 < l1:
                strategy._htf_trend = 'bearish'
                strategy.log("Initial HTF trend: BEARISH (LH + LL)")
        else:
            # Check for MSS (break of structure)
            if strategy._htf_trend == 'bullish':
                # Break: close below last swing low (HL)
                if c.close_htf < strategy._htf_lows[-1][1]:
                    strategy._htf_trend = 'bearish'
                    strategy.log(f"HTF trend changed: BULLISH → BEARISH (MSS, close {c.close_htf:.2f} < low {strategy._htf_lows[-1][1]:.2f})")
                    # Видаляємо всі старі FVG з графіка і очищаємо список
                    for fvg in strategy._fvg_list:
                        if 'drawing_id' in fvg and fvg['drawing_id']:
                            strategy.remove_drawing(fvg['drawing_id'])
                    strategy._fvg_list.clear()
            elif strategy._htf_trend == 'bearish':
                # Break: close above last swing high (LH)
                if c.close_htf > strategy._htf_highs[-1][1]:
                    strategy._htf_trend = 'bullish'
                    strategy.log(f"HTF trend changed: BEARISH → BULLISH (MSS, close {c.close_htf:.2f} > high {strategy._htf_highs[-1][1]:.2f})")
                    for fvg in strategy._fvg_list:
                        if 'drawing_id' in fvg and fvg['drawing_id']:
                            strategy.remove_drawing(fvg['drawing_id'])
                    strategy._fvg_list.clear()

    if strategy._htf_trend != old_trend and strategy._htf_trend is None:
        strategy.log("HTF trend reset to: None")
    elif strategy._htf_trend != old_trend:
        strategy.log(f"HTF trend now: {strategy._htf_trend}")

    # 3. Invalidate existing FVGs (skip ones created later in this call)
    close_15m = c.close_htf
    for fvg in strategy._fvg_list[:]:
        invalidated = False
        if fvg['dir'] == 'bull' and close_15m > fvg['top']:
            invalidated = True
            strategy.log(f"Bullish FVG [{fvg['bottom']:.2f}-{fvg['top']:.2f}] invalidated (body above)")
        elif fvg['dir'] == 'bear' and close_15m < fvg['bottom']:
            invalidated = True
            strategy.log(f"Bearish FVG [{fvg['bottom']:.2f}-{fvg['top']:.2f}] invalidated (body below)")
        if invalidated:
            # Меняем цвет на серый, но прямоугольник не удаляем
            if fvg.get('drawing_id'):
                strategy.update_drawing(fvg['drawing_id'], {'color': 'gray'})
            strategy._fvg_list.remove(fvg)

    # 4. FVG detection – only when trend is established AND at least 3 candles
    if strategy._htf_trend is None:
        return

    if len(c.close_htf_arr) < 3:   # need 3 candles for genuine FVG
        return

    curr_high = c.high_htf                         # current 15m high (index -1)
    curr_low  = c.low_htf                          # current 15m low
    prev_prev_high = c.high_htf_arr[-3]            # high of the candle two bars back
    prev_prev_low  = c.low_htf_arr[-3]             # low of the candle two bars back

    # Helper to draw FVG rectangle (between candle -2 and current)
    def draw_fvg_rect(fvg):
        t_start = c.time_htf_arr[-3]   # time of intermediate candle
        t_end = c.time_htf             # current candle time
        col = 'green' if fvg['dir'] == 'bull' else 'red'
        tag = f"fvg_{_time_tag_part(t_start)}_{_time_tag_part(t_end)}"
        # Remove stale drawings for ALL inactive FVGs first
        for old_fvg in strategy._fvg_list[:]:
            if old_fvg is not fvg and old_fvg.get('drawing_id'):
                strategy.remove_drawing(old_fvg['drawing_id'])
                old_fvg['drawing_id'] = None
        fvg['drawing_id'] = tag
        strategy.draw_rect(t_start, fvg['bottom'], t_end, fvg['top'],
                            color=col, fill=True, opacity=0.3, tag=tag)

    # Bullish FVG: low of current candle > high of candle two before
    if curr_low > prev_prev_high:
        fvg = {
            'top': curr_low,
            'bottom': prev_prev_high,
            'dir': 'bull',
            'tested': False,
            'used': False,
            'drawing_id': None
        }
        draw_fvg_rect(fvg)
        strategy._fvg_list.append(fvg)
        strategy.log(f"Bullish FVG (15m): [prev_prev_high={prev_prev_high:.2f} – curr_low={curr_low:.2f}]")

    # Bearish FVG: high of current candle < low of candle two before
    if curr_high < prev_prev_low:
        fvg = {
            'top': prev_prev_low,
            'bottom': curr_high,
            'dir': 'bear',
            'tested': False,
            'used': False,
            'drawing_id': None
        }
        draw_fvg_rect(fvg)
        strategy._fvg_list.append(fvg)
        strategy.log(f"Bearish FVG (15m): [curr_high={curr_high:.2f} – prev_prev_low={prev_prev_low:.2f}]")


@strategy.on_15m_close()
def on_15m_close(c):
    _update_htf_structure(c)


@strategy.on_candle()
def on_1m_candle(c):
    # Abort if no clear HTF trend or no position
    if strategy._htf_trend is None or c.position is not None:
        return

    for fvg in strategy._fvg_list[:]:
        if fvg['used']:
            continue
        if (fvg['dir'] == 'bull' and strategy._htf_trend != 'bullish') or \
           (fvg['dir'] == 'bear' and strategy._htf_trend != 'bearish'):
            continue

        # TEST: 1m close inside (or touching) the FVG zone
        if not fvg['tested']:
            if fvg['bottom'] <= c.close <= fvg['top']:
                fvg['tested'] = True
                strategy.log(f"FVG [{fvg['bottom']:.2f}-{fvg['top']:.2f}] tested at {c.close:.2f}")
                # Change rectangle colour to yellow
                if fvg.get('drawing_id'):
                    strategy.update_drawing(fvg['drawing_id'], {'color': 'yellow'})

        # If already tested, look for LTF MSS (1m structure break)
        if fvg['tested']:
            ltf_closes = c.close_arr
            if fvg['dir'] == 'bull':
                _, swing_val = _find_last_swing_high(ltf_closes, strategy.ltf_pivot_lookback)
                if swing_val is not None and c.close > swing_val:
                    if len(strategy._htf_lows) < 1:
                        continue
                    last_hl = strategy._htf_lows[-1][1]
                    # Small offset to place SL below the structural low
                    sl = last_hl - 10 * (c.close * 0.0001)
                    risk_distance = c.close - sl
                    tp = c.close + risk_distance * strategy.rr_ratio
                    strategy.long(sl=sl, tp=tp, risk=strategy.risk_pct, tag="LONG_15M_FVG")
                    strategy.log(f"ENTER LONG | Entry: {c.close:.2f}, SL: {sl:.2f}, TP: {tp:.2f}")
                    fvg['used'] = True
                    # Optionally remove drawing after entry
                    if fvg.get('drawing_id'):
                        strategy.remove_drawing(fvg['drawing_id'])
                    break   # one entry per candle

            elif fvg['dir'] == 'bear':
                _, swing_val = _find_last_swing_low(ltf_closes, strategy.ltf_pivot_lookback)
                if swing_val is not None and c.close < swing_val:
                    if len(strategy._htf_highs) < 1:
                        continue
                    last_lh = strategy._htf_highs[-1][1]
                    sl = last_lh + 10 * (c.close * 0.0001)
                    risk_distance = sl - c.close
                    tp = c.close - risk_distance * strategy.rr_ratio
                    strategy.short(sl=sl, tp=tp, risk=strategy.risk_pct, tag="SHORT_15M_FVG")
                    strategy.log(f"ENTER SHORT | Entry: {c.close:.2f}, SL: {sl:.2f}, TP: {tp:.2f}")
                    fvg['used'] = True
                    if fvg.get('drawing_id'):
                        strategy.remove_drawing(fvg['drawing_id'])
                    break
