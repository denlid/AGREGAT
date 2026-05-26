"""
High-frequency test strategy — generates 200-1000+ trades for analytics testing.
Uses small fixed SL/TP on every 1m candle, alternating direction.
"""
from engine.strategy.context import Strategy, Param
import random

random.seed(42)


class HFTest(Strategy):
    name = "HF Test"

    PARAMS = [
        Param("entry_prob", "Entry probability per bar", "float", 0.4, min=0.05, max=1.0, step=0.05),
        Param("sl_pts",     "Stop Loss (points)",         "float", 50,  min=10,  max=500, step=5),
        Param("tp_pts",     "Take Profit (points)",        "float", 100, min=10,  max=500, step=5),
        Param("risk_pct",   "Risk per trade %",            "float", 0.5, min=0.1, max=5.0, step=0.1),
    ]

    def __init__(self):
        super().__init__()
        self._trade_count = 0

    def on_candle(self, ctx):
        if ctx.position is not None:
            return

        sl_pts = self.params["sl_pts"]
        tp_pts = self.params["tp_pts"]

        volume = ctx.calculate_lot(sl_pts, risk_pct=self.params["risk_pct"])
        if volume is None or volume <= 0:
            return

        if random.random() > self.params["entry_prob"]:
            return

        direction = random.choice(["long", "short"])
        ref = float(ctx.data.close[-1])

        if direction == "long":
            sl = ref - sl_pts
            tp = ref + tp_pts
        else:
            sl = ref + sl_pts
            tp = ref - tp_pts

        self._trade_count += 1
        reason = f"HF#{self._trade_count} {direction} SL={sl_pts} TP={tp_pts}"
        if direction == "long":
            ctx.buy_at_open(volume=volume, sl=sl, tp=tp, reason=reason, tag="hf_test")
        else:
            ctx.sell_at_open(volume=volume, sl=sl, tp=tp, reason=reason, tag="hf_test")
