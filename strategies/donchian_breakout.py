from engine.strategy.context import Strategy, Param
import random

class RandomStrategy(Strategy):
    name = "Pure Random"

    PARAMS = [
        Param("entry_prob",   "Ймовірність входу", "float", 0.05, min=0.001, max=0.5, step=0.005),
        Param("min_sl_pts",   "Мін SL (пунктів)",  "float", 20,   min=5,    max=200, step=5),
        Param("max_sl_pts",   "Макс SL (пунктів)",  "float", 100,  min=10,   max=500, step=5),
        Param("min_rr",       "Мін RR (TP/SL)",     "float", 1.0,  min=1.0,  max=5.0, step=0.1),
        Param("max_rr",       "Макс RR (TP/SL)",    "float", 5.0,  min=1.0,  max=10.0, step=0.1),
        Param("min_risk_pct", "Мін ризик %",         "float", 0.5,  min=0.1,  max=3.0,  step=0.1),
        Param("max_risk_pct", "Макс ризик %",        "float", 2.0,  min=0.5,  max=5.0,  step=0.1),
    ]

    def on_candle(self, ctx):
        # Випадковий вхід з заданою ймовірністю
        prob = self.params["entry_prob"]
        if random.random() > prob:
            return

        # Випадковий напрямок
        direction = random.choice(["long", "short"])

        # Випадковий SL (пунктів)
        min_sl = float(self.params["min_sl_pts"])
        max_sl = float(self.params["max_sl_pts"])
        sl_points = random.uniform(min_sl, max_sl)

        # Випадкове RR (TP = SL * RR)
        min_rr = float(self.params["min_rr"])
        max_rr = float(self.params["max_rr"])
        rr = random.uniform(min_rr, max_rr)
        tp_points = sl_points * rr

        # Випадковий відсоток ризику
        min_risk = float(self.params["min_risk_pct"])
        max_risk = float(self.params["max_risk_pct"])
        risk_pct = random.uniform(min_risk, max_risk)

        # Розрахунок об'єму (строгий режим)
        volume = ctx.calculate_lot(sl_points, risk_pct=risk_pct, strict=True)

        # Якщо розрахунок невалідний — пропускаємо угоду
        if volume is None or volume <= 0:
            ctx.log(f"Skipping entry – invalid lot (vol={volume}, sl={sl_points:.1f}, risk={risk_pct:.1f}%)")
            return

        # Відкриття позиції
        if direction == "long":
            ctx.buy_at_open(volume=volume, sl=sl_points, tp=tp_points,
                            reason=f"Random long | RR={rr:.1f} risk={risk_pct:.1f}%")
        else:
            ctx.sell_at_open(volume=volume, sl=sl_points, tp=tp_points,
                             reason=f"Random short | RR={rr:.1f} risk={risk_pct:.1f}%")

        ctx.log(f"Entered {direction} | SL={sl_points:.1f} TP={tp_points:.1f} risk={risk_pct:.1f}% lot={volume:.4f}")