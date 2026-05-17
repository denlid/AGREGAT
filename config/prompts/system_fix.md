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

## Output format:
```python
[complete fixed strategy file]