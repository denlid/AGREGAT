from engine.api import Strategy
import numpy as np

strategy = Strategy(
    name="New Strategy",
    risk=1.0
)

@strategy.on_candle()
def _(c):
    pass
