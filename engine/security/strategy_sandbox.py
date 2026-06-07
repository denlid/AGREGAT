"""
AGREGAT Engine — Strategy Sandbox & Security Validator

C-01 FIX: RestrictedPython sandbox for user strategy execution.
H-01 FIX: AST-based static analysis before execution.
M-05 FIX: Timeout-based execution guard.

This module provides three layers of defense:
1. Static analysis (AST validator) — catches dangerous patterns BEFORE execution
2. RestrictedPython sandbox — limits what code CAN do at runtime
3. Timeout guard — prevents infinite loops / DoS

Security model:
- Strategies can ONLY access the trading API (c, strategy, indicators)
- NO file system access, NO network, NO subprocess, NO os/sys
- NO dynamic code execution (exec/eval/compile)
- NO reflection attacks (__import__, getattr on dangerous objects)
"""

from __future__ import annotations

import ast
import re
import threading
import time
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("StrategyTester.engine.security.sandbox")


# ===================================================================
# H-01: AST-Based Static Security Validator
# ===================================================================

# Категорії заборонених модулів
DANGEROUS_IMPORTS_CRITICAL = frozenset({
    # System access
    'os', 'sys', 'posix', 'nt', 'ntpath', 'posixpath',
    # Process execution
    'subprocess', 'multiprocessing', 'threading', '_thread',
    # Native code / memory
    'ctypes', 'cffi', 'swig', 'cython',
    # Network
    'socket', 'ssl', 'http', 'httplib', 'urllib', 'urllib2',
    'requests', 'aiohttp', 'httpx', 'ftplib', 'smtplib', 'imaplib',
    'poplib', 'telnetlib', 'xmlrpc', 'jsonrpc',
    # Serialization (code execution risk)
    'pickle', 'cPickle', 'shelve', 'marshal', 'dill', 'cloudpickle',
    # Dynamic code
    'importlib', 'builtins', '__builtins__', 'code', 'codeop',
    'compileall', 'py_compile',
    # Introspection
    'inspect', 'traceback', 'pdb', 'bdb',
    # Database (unauthorized access)
    'sqlite3', 'psycopg2', 'pymysql', 'pymongo', 'redis',
    # Cryptography (key extraction)
    'cryptography', 'Crypto', 'nacl',
    # GUI (escape sandbox)
    'tkinter', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'wx',
})

DANGEROUS_IMPORTS_MODERATE = frozenset({
    # Potentially dangerous but sometimes needed
    'pathlib', 'glob', 'fnmatch', 'shutil', 'tempfile',
    'zipfile', 'tarfile', 'gzip', 'bz2', 'lzma',
    'hashlib', 'hmac', 'secrets', 'random',
    'datetime', 'time', 'calendar',
    'logging', 'warnings',
    'copy', 'weakref',
    'collections', 'itertools', 'functools',
})

# Функції які НЕМОЖЛИВО використовувати
DANGEROUS_FUNCTIONS = frozenset({
    'exec', 'eval', 'compile', '__import__',
    'open', 'input', 'breakpoint',
    'globals', 'locals', 'vars', 'dir',
    'getattr', 'setattr', 'delattr', 'hasattr',
    'reload',
})

# Атрибути які блокують reflection атаки
DANGEROUS_ATTRIBUTES = frozenset({
    '__class__', '__bases__', '__mro__', '__subclasses__',
    '__globals__', '__code__', '__closure__', '__dict__',
    '__module__', '__qualname__', '__self__',
    'gi_frame', 'gi_code', 'cr_frame', 'cr_code',
    'f_globals', 'f_locals', 'f_builtins', 'f_code',
    '__reduce__', '__reduce_ex__', '__getstate__', '__setstate__',
})

# Regex патерни для обходу AST (динамічні атаки)
DANGEROUS_PATTERNS = [
    # Dynamic import
    (r'__import__\s*\(', 'dynamic_import'),
    # Reflection
    (r'getattr\s*\(\s*[^)]*__\w+__', 'reflection_attack'),
    (r'setattr\s*\(\s*[^)]*__\w+__', 'reflection_attack'),
    # Class hierarchy traversal
    (r'\.__class__\.__', 'class_traversal'),
    (r'\.__bases__\.__', 'class_traversal'),
    (r'\.__mro__\.__', 'class_traversal'),
    (r'\.__subclasses__\s*\(', 'subclass_traversal'),
    # Frame/globals access
    (r'\.__globals__\s*[\[\(]', 'globals_access'),
    (r'\.__code__\s*\.', 'code_access'),
    # Bytecode manipulation
    (r'bytearray\s*\(', 'bytecode_manipulation'),
    (r'memoryview\s*\(', 'memory_access'),
    # Lambda with dangerous operations
    (r'lambda\s*[^:]*:\s*(exec|eval|compile|__import__)\s*\(', 'lambda_injection'),
    # String formatting for code injection
    (r'f["\'].*\{.*(exec|eval|compile|__import__)', 'fstring_injection'),
    # Encoded payloads
    (r'base64\.(b64decode|decodebytes)\s*\(', 'encoded_payload'),
    (r'bytes\.fromhex\s*\(', 'hex_payload'),
    # Process spawning via os module
    (r'os\.(system|popen|spawn|exec|fork)\s*\(', 'process_spawn'),
    (r'subprocess\.(run|call|Popen|check_output)\s*\(', 'process_spawn'),
]


class SecurityViolation:
    """Represents a single security violation found in strategy code."""
    
    def __init__(self, line: int, severity: str, category: str, message: str):
        self.line = line
        self.severity = severity  # 'critical', 'high', 'medium', 'low'
        self.category = category
        self.message = message
    
    def __str__(self) -> str:
        return f"L{self.line} [{self.severity.upper()}] {self.category}: {self.message}"


class StrategyASTValidator(ast.NodeVisitor):
    """
    AST-based security validator for strategy code.
    
    Walks the AST and flags any dangerous operations.
    This is the FIRST line of defense — catches issues BEFORE execution.
    """
    
    def __init__(self, allowed_imports: Optional[set] = None):
        self.violations: List[SecurityViolation] = []
        self.allowed_imports = allowed_imports or {
            'numpy', 'np', 'math', 'json', 'typing',
            'engine', 'engine.api', 'engine.core', 'engine.core.indicators',
        }
    
    def _add_violation(self, node: ast.AST, severity: str, category: str, message: str):
        self.violations.append(SecurityViolation(
            line=getattr(node, 'lineno', 0),
            severity=severity,
            category=category,
            message=message,
        ))
    
    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            module_base = alias.name.split('.')[0]
            if module_base in DANGEROUS_IMPORTS_CRITICAL:
                self._add_violation(node, 'critical', 'forbidden_import',
                    f"Forbidden import: '{alias.name}' — system access not allowed")
            elif module_base not in self.allowed_imports and module_base in DANGEROUS_IMPORTS_MODERATE:
                self._add_violation(node, 'medium', 'restricted_import',
                    f"Restricted import: '{alias.name}' — not in allowed list")
        self.generic_visit(node)
    
    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            module_base = node.module.split('.')[0]
            if module_base in DANGEROUS_IMPORTS_CRITICAL:
                self._add_violation(node, 'critical', 'forbidden_import',
                    f"Forbidden import from: '{node.module}' — system access not allowed")
            elif module_base not in self.allowed_imports and module_base in DANGEROUS_IMPORTS_MODERATE:
                self._add_violation(node, 'medium', 'restricted_import',
                    f"Restricted import from: '{node.module}' — not in allowed list")
        self.generic_visit(node)
    
    def visit_Call(self, node: ast.Call):
        # Check direct function calls (exec(), eval(), etc.)
        if isinstance(node.func, ast.Name):
            if node.func.id in DANGEROUS_FUNCTIONS:
                self._add_violation(node, 'critical', 'forbidden_function',
                    f"Forbidden function: '{node.func.id}()' — code execution not allowed")
        
        # Check method calls (os.system(), subprocess.run(), etc.)
        if isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                full_call = f"{node.func.value.id}.{node.func.attr}()"
                if node.func.value.id in DANGEROUS_IMPORTS_CRITICAL:
                    self._add_violation(node, 'critical', 'forbidden_method',
                        f"Forbidden method call: '{full_call}'")
        
        self.generic_visit(node)
    
    def visit_Attribute(self, node: ast.Attribute):
        # Check dangerous attribute access (__class__, __globals__, etc.)
        if node.attr in DANGEROUS_ATTRIBUTES:
            self._add_violation(node, 'critical', 'forbidden_attribute',
                f"Forbidden attribute access: '{node.attr}' — reflection not allowed")
        self.generic_visit(node)
    
    def visit_Name(self, node: ast.Name):
        # Check access to dangerous builtins
        if node.id in ('__builtins__', '__import__', 'exec', 'eval', 'compile'):
            if isinstance(node.ctx, ast.Load):
                self._add_violation(node, 'critical', 'forbidden_name',
                    f"Forbidden name access: '{node.id}'")
        self.generic_visit(node)
    
    def visit_Exec(self, node):
        self._add_violation(node, 'critical', 'exec_statement',
            "exec statement is forbidden")
        self.generic_visit(node)


def validate_strategy_code(code: str, allowed_imports: Optional[set] = None) -> Tuple[bool, List[str]]:
    """
    Validate strategy code for security violations.
    
    Returns:
        (is_safe, list_of_violation_messages)
    
    This performs TWO checks:
    1. AST analysis — catches imports, function calls, attribute access
    2. Regex analysis — catches dynamic/encoded attacks that AST might miss
    """
    violations: List[SecurityViolation] = []
    
    # === CHECK 1: AST Analysis ===
    validator = StrategyASTValidator(allowed_imports=allowed_imports)
    try:
        tree = ast.parse(code, filename='<strategy>')
        validator.visit(tree)
        violations.extend(validator.violations)
    except SyntaxError as e:
        return False, [f"Syntax error at line {e.lineno}: {e.msg}"]
    except (ValueError, TypeError) as e:
        return False, [f"Parse error: {e}"]
    
    # === CHECK 2: Regex Analysis (catches AST evasion) ===
    for pattern, category in DANGEROUS_PATTERNS:
        matches = list(re.finditer(pattern, code))
        for match in matches:
            line_num = code[:match.start()].count('\n') + 1
            violations.append(SecurityViolation(
                line=line_num,
                severity='critical',
                category=category,
                message=f"Detected dangerous pattern: {category} at position {match.start()}",
            ))
    
    # === CHECK 3: Line length and complexity limits ===
    for i, line in enumerate(code.split('\n'), 1):
        if len(line) > 1000:
            violations.append(SecurityViolation(
                line=i, severity='medium', category='complexity',
                message=f"Line too long ({len(line)} chars) — possible obfuscation",
            ))
    
    # Count total lines
    total_lines = code.count('\n') + 1
    if total_lines > 5000:
        violations.append(SecurityViolation(
            line=0, severity='medium', category='complexity',
            message=f"Strategy too large ({total_lines} lines) — max 5000 lines",
        ))
    
    # === Format results ===
    if violations:
        # Sort by severity (critical first)
        severity_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
        violations.sort(key=lambda v: (severity_order.get(v.severity, 9), v.line))
        
        messages = [str(v) for v in violations]
        return False, messages
    
    return True, []


# ===================================================================
# C-01: RestrictedPython Sandbox
# ===================================================================

def create_sandbox_globals(ctx: Any, strategy_instance: Any) -> Dict[str, Any]:
    """
    Create a restricted globals dict for strategy execution.
    
    ONLY includes what strategies actually need:
    - Trading context (c)
    - Strategy instance (strategy)
    - numpy (as np)
    - math module
    - Safe builtins
    
    Everything else is BLOCKED.
    """
    # Safe builtins — minimal subset
    safe_builtins = {
        # Type constructors
        'int': int, 'float': float, 'str': str, 'bool': bool,
        'list': list, 'dict': dict, 'tuple': tuple, 'set': set,
        'frozenset': frozenset,
        # Basic operations
        'len': len, 'range': range, 'enumerate': enumerate,
        'zip': zip, 'map': map, 'filter': filter,
        'sorted': sorted, 'reversed': reversed,
        'min': min, 'max': max, 'sum': sum, 'abs': abs,
        'round': round, 'pow': pow, 'divmod': divmod,
        'any': any, 'all': all,
        # Type checking
        'isinstance': isinstance, 'issubclass': issubclass,
        'type': type, 'id': id,
        # Iterators
        'iter': iter, 'next': next,
        # Printing (redirected to strategy log)
        'print': getattr(ctx, 'log', print) if ctx else print,
        # Exceptions
        'Exception': Exception, 'ValueError': ValueError,
        'TypeError': TypeError, 'KeyError': KeyError,
        'IndexError': IndexError, 'RuntimeError': RuntimeError,
        'StopIteration': StopIteration,
        'True': True, 'False': False, 'None': None,
        # Ellipsis and NotImplemented
        'Ellipsis': Ellipsis, 'NotImplemented': NotImplemented,
    }
    
    # Build restricted globals
    restricted = {
        '__builtins__': safe_builtins,
        '__name__': '<strategy>',
        '__doc__': None,
    }
    
    # Add trading API
    if ctx is not None:
        restricted['c'] = ctx
    if strategy_instance is not None:
        restricted['strategy'] = strategy_instance
    
    # Add numpy (restricted)
    try:
        import numpy as np
        restricted['np'] = np
        restricted['numpy'] = np
    except ImportError:
        pass
    
    # Add math
    import math
    restricted['math'] = math
    
    # Add indicators (from engine)
    try:
        from engine.core import indicators
        for name in dir(indicators):
            if not name.startswith('_'):
                restricted[name] = getattr(indicators, name)
    except ImportError:
        pass
    
    return restricted


class SandboxExecutionError(Exception):
    """Raised when sandboxed execution fails."""
    pass


class SandboxTimeoutError(SandboxExecutionError):
    """Raised when strategy execution exceeds time limit."""
    pass


class SandboxSecurityError(SandboxExecutionError):
    """Raised when strategy attempts a forbidden operation."""
    pass


def execute_in_sandbox(
    code: str,
    ctx: Any,
    strategy_instance: Any,
    timeout_ms: int = 5000,
    validate_first: bool = True,
) -> Any:
    """
    Execute strategy code in a restricted sandbox.
    
    Args:
        code: The strategy code to execute
        ctx: The trading context (passed as 'c')
        strategy_instance: The strategy instance (passed as 'strategy')
        timeout_ms: Maximum execution time in milliseconds
        validate_first: Run AST validation before execution
    
    Returns:
        The result of execution (usually None for strategies)
    
    Raises:
        SandboxSecurityError: If code contains forbidden operations
        SandboxTimeoutError: If execution exceeds timeout
        SandboxExecutionError: For other execution errors
    """
    # === Step 1: Static validation (H-01) ===
    if validate_first:
        is_safe, violations = validate_strategy_code(code)
        if not is_safe:
            error_msg = "Strategy security validation failed:\n" + "\n".join(
                f"  • {v}" for v in violations[:10]
            )
            if len(violations) > 10:
                error_msg += f"\n  ... and {len(violations) - 10} more violations"
            logger.error("Sandbox: validation failed — %d violations", len(violations))
            raise SandboxSecurityError(error_msg)
    
    # === Step 2: Create restricted environment ===
    restricted_globals = create_sandbox_globals(ctx, strategy_instance)
    
    # === Step 3: Execute with timeout (M-05) ===
    result = [None]
    error = [None]
    
    def _execute():
        try:
            exec(compile(code, '<strategy>', 'exec'), restricted_globals)
            result[0] = restricted_globals.get('_result')
        except Exception as e:
            error[0] = e
    
    thread = threading.Thread(target=_execute, daemon=True)
    start_time = time.monotonic()
    thread.start()
    thread.join(timeout=timeout_ms / 1000.0)
    
    elapsed_ms = (time.monotonic() - start_time) * 1000
    
    if thread.is_alive():
        logger.error("Sandbox: timeout after %.0fms (limit: %dms)", elapsed_ms, timeout_ms)
        raise SandboxTimeoutError(
            f"Strategy execution exceeded {timeout_ms}ms timeout "
            f"(ran for {elapsed_ms:.0f}ms). "
            "Check for infinite loops or heavy computations."
        )
    
    if error[0] is not None:
        # Check if it's a security-related error
        err_str = str(error[0]).lower()
        if any(kw in err_str for kw in ['forbidden', 'not allowed', 'restricted', 'blocked']):
            raise SandboxSecurityError(f"Sandbox violation: {error[0]}")
        raise SandboxExecutionError(f"Strategy execution error: {error[0]}")
    
    logger.debug("Sandbox: execution completed in %.0fms", elapsed_ms)
    return result[0]


# ===================================================================
# M-05: Strategy Execution Guard (standalone timeout wrapper)
# ===================================================================

class StrategyExecutionGuard:
    """
    Wrapper for strategy callbacks with timeout and error handling.
    
    Use this to wrap on_candle, on_15m_close, etc. callbacks.
    """
    
    def __init__(
        self,
        timeout_ms: int = 5000,
        max_consecutive_errors: int = 10,
        error_cooldown_seconds: float = 1.0,
    ):
        self.timeout_ms = timeout_ms
        self.max_consecutive_errors = max_consecutive_errors
        self.error_cooldown = error_cooldown_seconds
        self._consecutive_errors = 0
        self._last_error_time = 0.0
        self._total_errors = 0
        self._total_executions = 0
        self._total_time_ms = 0.0
    
    def execute(
        self,
        callback: Callable,
        *args,
        **kwargs,
    ) -> Any:
        """Execute a strategy callback with timeout and error tracking."""
        self._total_executions += 1
        
        # Check error rate
        if self._consecutive_errors >= self.max_consecutive_errors:
            elapsed = time.monotonic() - self._last_error_time
            if elapsed < self.error_cooldown:
                raise SandboxExecutionError(
                    f"Strategy has {self._consecutive_errors} consecutive errors. "
                    f"Cooldown active ({self.error_cooldown - elapsed:.1f}s remaining)."
                )
            else:
                # Reset after cooldown
                self._consecutive_errors = 0
        
        # Execute with timeout
        result = [None]
        error = [None]
        
        def _target():
            try:
                result[0] = callback(*args, **kwargs)
            except Exception as e:
                error[0] = e
        
        thread = threading.Thread(target=_target, daemon=True)
        start = time.monotonic()
        thread.start()
        thread.join(timeout=self.timeout_ms / 1000.0)
        
        elapsed_ms = (time.monotonic() - start) * 1000
        self._total_time_ms += elapsed_ms
        
        if thread.is_alive():
            self._consecutive_errors += 1
            self._last_error_time = time.monotonic()
            self._total_errors += 1
            raise SandboxTimeoutError(
                f"Strategy callback timed out after {self.timeout_ms}ms "
                f"(error #{self._consecutive_errors})"
            )
        
        if error[0] is not None:
            self._consecutive_errors += 1
            self._last_error_time = time.monotonic()
            self._total_errors += 1
            raise error[0]
        
        # Success — reset consecutive counter
        self._consecutive_errors = 0
        return result[0]
    
    @property
    def stats(self) -> Dict[str, Any]:
        """Get execution statistics."""
        avg_time = (
            self._total_time_ms / self._total_executions
            if self._total_executions > 0 else 0
        )
        error_rate = (
            self._total_errors / self._total_executions
            if self._total_executions > 0 else 0
        )
        return {
            'total_executions': self._total_executions,
            'total_errors': self._total_errors,
            'error_rate': f"{error_rate:.1%}",
            'avg_time_ms': f"{avg_time:.1f}",
            'consecutive_errors': self._consecutive_errors,
        }


# ===================================================================
# Public API
# ===================================================================

__all__ = [
    'validate_strategy_code',
    'execute_in_sandbox',
    'StrategyExecutionGuard',
    'SandboxExecutionError',
    'SandboxTimeoutError',
    'SandboxSecurityError',
    'SecurityViolation',
    'StrategyASTValidator',
]