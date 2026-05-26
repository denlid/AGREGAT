from engine.api import Strategy, Param
import numpy as np

strategy = Strategy(
    name="Pivot Extremes with Triangles",
    lookback=Param("int", 2, label="Lookback (bars each side)", min=1, max=10),
    offset_pts=Param("float", 5.0, label="Offset for triangle (points)", min=0.0, max=100.0),
    draw_highs=Param("bool", True, label="Draw highs"),
    draw_lows=Param("bool", True, label="Draw lows"),
    risk=1.0
)

@strategy.on_candle()
def _(c):
    lb = strategy.lookback
    offset = strategy.offset_pts

    # Need at least 2*lb+1 bars to confirm a pivot
    if len(c.close_arr) < 2 * lb + 1:
        return

    # Index of the bar we are checking (last completed bar that has 'lb' bars on both sides)
    idx = -lb - 1  # e.g. lb=2 → idx=-3

    high_arr = c.high_arr
    low_arr = c.low_arr
    time_arr = c.time_arr
    high = high_arr[idx]
    low = low_arr[idx]
    t = time_arr[idx]

    # Ensure we don't go out of bounds
    if idx - lb < 0:
        return

    # Check if it's a pivot high
    if strategy.draw_highs:
        left_highs = high_arr[idx - lb : idx]       # exclude current
        right_highs = high_arr[idx + 1 : idx + lb + 1]
        if high > max(np.max(left_highs), np.max(right_highs)):
            strategy.draw_text(t, high + offset, "▲", color="lime", tag=f"ph_{t}")
            strategy.log(f"Pivot HIGH at {t} := {high}")

    # Check if it's a pivot low
    if strategy.draw_lows:
        left_lows = low_arr[idx - lb : idx]
        right_lows = low_arr[idx + 1 : idx + lb + 1]
        if low < min(np.min(left_lows), np.min(right_lows)):
            strategy.draw_text(t, low - offset, "▼", color="red", tag=f"pl_{t}")
            strategy.log(f"Pivot LOW at {t} := {low}")