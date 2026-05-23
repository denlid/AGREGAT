from engine.strategy.context import Strategy, Param
import numpy as np

class AIStrategy(Strategy):
    """
    Трендова стратегія на основі перетину ковзних середніх (EMA50/EMA200)
    з фільтром тренду (ціна вище/нижче 200-періодної EMA для лонг/шорт).
    Використовує ATR для розрахунку стоп-лосу та тейк-профіту.
    """
    name = "Trend EMA50/200 + ATR"

    PARAMS = [
        Param("fast_period", "Швидка EMA", "int", 50, min=10, max=200, step=5),
        Param("slow_period", "Повільна EMA", "int", 200, min=50, max=500, step=10),
        Param("atr_period", "Період ATR", "int", 14, min=5, max=50, step=1),
        Param("sl_atr_mult", "Множник SL (ATR)", "float", 1.5, min=0.5, max=5.0, step=0.1),
        Param("tp_atr_mult", "Множник TP (ATR)", "float", 3.0, min=1.0, max=10.0, step=0.5),
        Param("risk_pct", "Ризик на угоду (%)", "float", 1.0, min=0.1, max=5.0, step=0.1),
        Param("only_trend_filter", "Фільтр тренду (ціна відносно повільної EMA)", "bool", True),
    ]

    def __init__(self):
        super().__init__()
        # Для зберігання попередніх значень EMA та ATR (ініціалізуються при першому виклику)
        self._prev_fast_ema = None
        self._prev_slow_ema = None
        self._prev_atr = None

    def on_candle(self, ctx):
        # Параметри
        fast_period = int(self.params["fast_period"])
        slow_period = int(self.params["slow_period"])
        atr_period = int(self.params["atr_period"])
        sl_mult = float(self.params["sl_atr_mult"])
        tp_mult = float(self.params["tp_atr_mult"])
        risk_pct = float(self.params["risk_pct"])
        use_trend_filter = bool(self.params["only_trend_filter"])

        # Поточний бар
        close = ctx.candle["close"]
        high = ctx.candle["high"]
        low = ctx.candle["low"]

        # Отримуємо масиви даних (до поточного бару включно)
        closes = ctx.data["close"]      # (n,) numpy array
        highs = ctx.data["high"]
        lows = ctx.data["low"]

        # Індекс поточного бару
        idx = len(closes) - 1

        # Потрібно мінімум slow_period + 1 барів для розрахунку EMA і ATR
        if idx < max(fast_period, slow_period, atr_period + 1):
            return

        # --- Розрахунок EMA (експоненційна ковзна середня) ---
        def ema(values, period):
            alpha = 2.0 / (period + 1)
            result = np.full(len(values), np.nan)
            # Починаємо з SMA для першого значення
            result[period - 1] = np.mean(values[:period])
            for i in range(period, len(values)):
                result[i] = alpha * values[i] + (1 - alpha) * result[i - 1]
            return result

        fast_ema_all = ema(closes, fast_period)
        slow_ema_all = ema(closes, slow_period)

        fast_ema = fast_ema_all[idx]
        slow_ema = slow_ema_all[idx]
        prev_fast_ema = fast_ema_all[idx - 1]
        prev_slow_ema = slow_ema_all[idx - 1]

        # --- Розрахунок ATR (середній істинний діапазон) ---
        # Істинний діапазон: max(high-low, high-prev_close, prev_close-low)
        tr = np.zeros(idx + 1)
        for i in range(1, idx + 1):
            h = highs[i]
            l = lows[i]
            pc = closes[i - 1]
            tr[i] = max(h - l, abs(h - pc), abs(pc - l))
        # ATR як EMA TR (використовуємо той самий принцип)
        if idx >= atr_period:
            atr = np.mean(tr[1:atr_period + 1])  # перше значення – SMA
            for i in range(atr_period + 1, idx + 1):
                atr = (tr[i] + (atr_period - 1) * atr) / atr_period
        else:
            return

        # --- Умови входу ---
        # Перетин вгору: fast перетинає slow знизу вгору
        cross_up = prev_fast_ema <= prev_slow_ema and fast_ema > slow_ema
        # Перетин вниз: fast перетинає slow зверху вниз
        cross_down = prev_fast_ema >= prev_slow_ema and fast_ema < slow_ema

        if use_trend_filter:
            # Для лонгу ціна повинна бути вище slow_ema, для шорту – нижче
            trend_up = close > slow_ema
            trend_down = close < slow_ema
        else:
            trend_up = True
            trend_down = True

        # Стоп-лосс і тейк-профіт на основі ATR
        sl_points = atr * sl_mult
        tp_points = atr * tp_mult

        # --- Логіка входу ---
        # Якщо немає позиції
        if not ctx.position:
            if cross_up and trend_up:
                # Розмір лоту з урахуванням ризику
                volume = ctx.calculate_lot(sl_points=sl_points, risk_pct=risk_pct)
                if volume > 0:
                    ctx.buy_at_open(
                        volume=volume,
                        sl=close - sl_points,
                        tp=close + tp_points,
                        reason="EMA50/200 перетин вгору, тренд вгору",
                        tag="LONG"
                    )
                    ctx.log(f"Відкрито LONG: ціна {close:.2f}, SL {close - sl_points:.2f}, TP {close + tp_points:.2f}, лот {volume:.4f}")
            elif cross_down and trend_down:
                volume = ctx.calculate_lot(sl_points=sl_points, risk_pct=risk_pct)
                if volume > 0:
                    ctx.sell_at_open(
                        volume=volume,
                        sl=close + sl_points,
                        tp=close - tp_points,
                        reason="EMA50/200 перетин вниз, тренд вниз",
                        tag="SHORT"
                    )
                    ctx.log(f"Відкрито SHORT: ціна {close:.2f}, SL {close + sl_points:.2f}, TP {close - tp_points:.2f}, лот {volume:.4f}")
        else:
            pass