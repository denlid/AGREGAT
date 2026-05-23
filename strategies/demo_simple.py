from engine.api import Strategy
import numpy as np

strategy = Strategy(
    name="Pivot экстремумы (тело свечи)",
    risk=1.0
)

LEFT = 2
RIGHT = 2

last_pivot_high_idx = -1
last_pivot_low_idx = -1


def get_body_high(open_arr, close_arr):
    return np.maximum(open_arr, close_arr)


def get_body_low(open_arr, close_arr):
    return np.minimum(open_arr, close_arr)


@strategy.on_candle()
def _(c):
    global last_pivot_high_idx, last_pivot_low_idx

    body_high = get_body_high(c.open_arr, c.close_arr)
    body_low = get_body_low(c.open_arr, c.close_arr)

    if len(body_high) < LEFT + RIGHT + 1:
        return

    i = len(body_high) - RIGHT - 1

    if i <= max(last_pivot_high_idx, last_pivot_low_idx):
        return

    # --- PIVOT HIGH (строго > по обе стороны) ---
    is_high = True
    for j in range(i - LEFT, i + RIGHT + 1):
        if j == i:
            continue
        if body_high[j] >= body_high[i]:   # <-- исправлено >=
            is_high = False
            break

    if is_high:
        strategy.draw_text(
            c.time_arr[i],
            body_high[i] + 0.5,
            "▼",
            color="lime",
            tag=f"pivot_high_{i}"
        )
        strategy.log(f"Pivot HIGH: {body_high[i]:.2f} at {c.time_arr[i]}")
        last_pivot_high_idx = i

    # --- PIVOT LOW (строго < по обе стороны) ---
    is_low = True
    for j in range(i - LEFT, i + RIGHT + 1):
        if j == i:
            continue
        if body_low[j] <= body_low[i]:     # <-- исправлено <=
            is_low = False
            break

    if is_low:
        strategy.draw_text(
            c.time_arr[i],
            body_low[i],
            "▲",
            color="red",
            tag=f"pivot_low_{i}"
        )
        strategy.log(f"Pivot LOW: {body_low[i]:.2f} at {c.time_arr[i]}")
        last_pivot_low_idx = i