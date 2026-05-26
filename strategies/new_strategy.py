# Проста стратегія на основі перетину ковзних середніх (EMA)

from engine.api import Strategy, Param, crossover, crossunder
from engine.core.indicators import ema

strategy = Strategy(
    name="Перетин EMA",
    fast_period=Param(name="fast_period", label="Швидка EMA", kind="int", default=12, min=2, max=100),
    slow_period=Param(name="slow_period", label="Повільна EMA", kind="int", default=26, min=5, max=200),
    risk_pct=Param(name="risk_pct", label="Ризик, %", kind="float", default=1.0, min=0.1, max=5.0),
    sl_points=Param(name="sl_points", label="Стоп-лосс (пункти)", kind="float", default=50.0, min=10.0, max=500.0),
    tp_ratio=Param(name="tp_ratio", label="Співвідношення TP/SL", kind="float", default=2.0, min=1.0, max=5.0)
)

@strategy.on_candle()
def _(c):
    fast = ema(c.close_arr, strategy.fast_period)
    slow = ema(c.close_arr, strategy.slow_period)

    if crossover(fast, slow):
        sl_price = c.close - strategy.sl_points
        tp_price = c.close + strategy.sl_points * strategy.tp_ratio
        strategy.long(sl=sl_price, tp=tp_price, risk=strategy.risk_pct)
        strategy.log(f"Вхід LONG: close={c.close:.1f}, sl={sl_price:.1f}, tp={tp_price:.1f}")

    elif crossunder(fast, slow):
        strategy.close()
        strategy.log("Вихід: перетин під повільну EMA")