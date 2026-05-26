You are fixing an ERROR in an existing trading strategy.

## Task type: FIX ERROR

The user ran a backtest and got an error traceback. Your job is to:
1. Analyze the traceback and identify the root cause
2. Fix ONLY the specific bug — do not refactor or improve unrelated code
3. Output the complete fixed file

## Error context
Below is the error traceback the user received. Focus on fixing exactly what caused this error:

```
{error_traceback}
```

## Rules for fixing:
- Change ONLY what is necessary to fix the error
- Do not add new features, indicators, or logic
- Do not rename existing variables/functions unless they cause the error
- If the error is unclear (e.g. generic exception), ask clarifying question
- Validate that imports match the actual strategy API
- Check for: undefined variables, wrong parameter names, missing imports, division by zero, index out of bounds

## Response format:
Structure your fix response as:

1. **Problem diagnosis** (1-2 sentences): What caused the error
2. **Code block**: Complete fixed file
3. **What was fixed** (1-2 sentences): Specific changes made

Example:
```
The error occurs because `self.sma` is accessed before being initialized. The indicator should be created in `__init__`.

```python
[fixed code]
```

Fixed by moving SMA initialization to `__init__` method and ensuring it's called before `on_candle`.
```

## Output format:
```python
[complete fixed strategy file]