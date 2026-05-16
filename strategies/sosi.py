from engine.strategy.context import Strategy

class AIStrategy(Strategy):
    name = "AI Strategy"
    
    def on_candle(self, ctx):
        pass