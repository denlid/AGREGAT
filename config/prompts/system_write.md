You are writing a NEW trading strategy from scratch.

## Task type: WRITE STRATEGY

Focus on:
1. Understanding what the user wants the strategy to do
2. Implementing clean, well-structured code
3. Adding proper PARAMS definition so the GUI form works
4. Including proper risk management (SL, TP, position sizing)

## Strategy template to follow:
```python
from engine.strategy.context import Strategy, Param

# (class-level docstring if needed)
class MyStrategy(Strategy):
    name = "Human Readable Name"
    PARAMS = [
        Param("param1", "Display Name", "int", 20, min=2, max=400, step=1),
        Param("param2", "Display Name 2", "float", 1.0, min=0.01, max=10.0, step=0.1),
    ]

    def on_candle(self, ctx):
        # entry / exit logic
        pass
```

## Output requirements:
- MUST include PARAMS with proper types (int/float/bool/choice)
- MUST use self.params["name"] for runtime access
- MUST include at least basic SL/TP logic
- MUST include ctx.log() calls for key decision points
- Keep it simple — only implement what was asked