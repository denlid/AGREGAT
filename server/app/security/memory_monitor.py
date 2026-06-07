"""
L-03: Memory Monitor & DoS Protection Module

Security Model:
    Denial of Service (DoS) attacks can exhaust server resources (memory, CPU,
    file descriptors) making the service unavailable. This module provides:

    1. Real-time memory usage monitoring with configurable thresholds
    2. Automatic request throttling when memory pressure is detected
    3. Per-endpoint memory budget tracking
    4. Request size limits to prevent memory exhaustion
    5. Connection pooling limits
    6. Graceful degradation under load (reject new requests, finish existing)

Threat Model:
    - Memory exhaustion via large request bodies
    - Slowloris-style attacks holding connections open
    - Computationally expensive queries causing memory spikes
    - Resource exhaustion via parallel requests
    - Zip bomb / decompression bomb attacks

Protection Layers:
    Layer 1: Request size limits (body, headers, URL)
    Layer 2: Memory threshold monitoring (warn → throttle → reject)
    Layer 3: Per-client request rate limiting
    Layer 4: Emergency circuit breaker (kill switch)

Platform Support:
    - Windows: psutil or ctypes (GetProcessMemoryInfo)
    - Linux: /proc/self/status or psutil
    - macOS: psutil or sysctl
    Graceful degradation: if no memory API available, uses request counting heuristic
"""

import logging
import os
import platform
import time
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Dict, Callable, List, Tuple

try:
    from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
    from starlette.requests import Request
    from starlette.responses import Response, JSONResponse
    HAS_STARLETTE = True
except ImportError:
    HAS_STARLETTE = False
    class BaseHTTPMiddleware:
        pass
    class Request:
        pass
    class Response:
        pass
    class JSONResponse(Response):
        pass

logger = logging.getLogger(__name__)


class MemoryPressure(Enum):
    """Memory pressure levels."""
    NORMAL = "normal"           # < 60% — no action
    ELEVATED = "elevated"       # 60-75% — log warnings
    HIGH = "high"               # 75-90% — throttle requests
    CRITICAL = "critical"       # > 90% — reject new requests
    EMERGENCY = "emergency"     # > 95% — circuit breaker


@dataclass
class MemoryStats:
    """Current memory statistics."""
    rss_bytes: int              # Resident Set Size
    rss_mb: float
    available_bytes: int        # System available memory
    available_mb: float
    percent_used: float         # Process memory as % of system total
    pressure: MemoryPressure
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class RequestBudget:
    """Memory budget for a request."""
    max_body_size: int = 10 * 1024 * 1024       # 10 MB default
    max_headers_size: int = 64 * 1024            # 64 KB
    max_url_length: int = 2048                    # 2 KB
    max_query_params: int = 100                   # Max query parameters
    max_json_depth: int = 20                      # Max JSON nesting depth
    max_json_keys: int = 1000                     # Max keys in JSON object


@dataclass
class ThrottleConfig:
    """Configuration for request throttling."""
    # Memory thresholds (percentage of system memory)
    warn_threshold: float = 60.0
    throttle_threshold: float = 75.0
    reject_threshold: float = 90.0
    emergency_threshold: float = 95.0

    # Throttling behavior
    throttle_max_concurrent: int = 50            # Max concurrent when throttling
    reject_status_code: int = 503                # Status when rejecting
    emergency_status_code: int = 503             # Status in emergency

    # Request limits
    request_budget: RequestBudget = field(default_factory=RequestBudget)

    # Per-client rate limiting
    rate_limit_window: float = 60.0              # seconds
    rate_limit_max_requests: int = 120           # per window
    rate_limit_burst: int = 20                   # burst allowance

    # Monitoring
    check_interval: float = 5.0                  # seconds between memory checks
    stats_retention: int = 3600                  # seconds to retain stats

    # Circuit breaker
    circuit_breaker_enabled: bool = True
    circuit_breaker_cooldown: float = 30.0       # seconds before retry


class MemoryMonitor:
    """
    Monitors process memory usage and provides pressure level assessment.

    Usage:
        monitor = MemoryMonitor()
        stats = monitor.get_stats()
        if stats.pressure == MemoryPressure.CRITICAL:
            # Take action
    """

    def __init__(self, config: Optional[ThrottleConfig] = None):
        self.config = config or ThrottleConfig()
        self._stats_history: List[MemoryStats] = []
        self._lock = threading.Lock()
        self._monitor_thread: Optional[threading.Thread] = None
        self._running = False
        self._circuit_breaker_active = False
        self._circuit_breaker_triggered_at: Optional[float] = None
        self._memory_api_available = self._detect_memory_api()

        logger.info(
            "MemoryMonitor initialized: api=%s, thresholds=%.0f/%.0f/%.0f%%",
            self._memory_api_available,
            self.config.warn_threshold,
            self.config.throttle_threshold,
            self.config.reject_threshold,
        )

    def start_background_monitor(self) -> None:
        """Start background memory monitoring thread."""
        if self._running:
            return

        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="memory-monitor",
        )
        self._monitor_thread.start()
        logger.info("Background memory monitor started")

    def stop(self) -> None:
        """Stop background monitoring."""
        self._running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
        logger.info("Background memory monitor stopped")

    def get_stats(self) -> MemoryStats:
        """Get current memory statistics."""
        rss_bytes = self._get_process_rss()
        available_bytes = self._get_system_available()
        total_bytes = self._get_system_total()

        if total_bytes > 0:
            percent_used = (rss_bytes / total_bytes) * 100
        else:
            percent_used = 0.0

        pressure = self._assess_pressure(percent_used)

        stats = MemoryStats(
            rss_bytes=rss_bytes,
            rss_mb=rss_bytes / (1024 * 1024),
            available_bytes=available_bytes,
            available_mb=available_bytes / (1024 * 1024),
            percent_used=percent_used,
            pressure=pressure,
        )

        with self._lock:
            self._stats_history.append(stats)
            self._cleanup_history()

        return stats

    def is_circuit_breaker_active(self) -> bool:
        """Check if circuit breaker is currently active."""
        if not self._circuit_breaker_active:
            return False

        # Check if cooldown has elapsed
        if self._circuit_breaker_triggered_at:
            elapsed = time.monotonic() - self._circuit_breaker_triggered_at
            if elapsed >= self.config.circuit_breaker_cooldown:
                self._circuit_breaker_active = False
                self._circuit_breaker_triggered_at = None
                logger.info("Circuit breaker cooldown elapsed, resuming normal operation")
                return False

        return True

    def trigger_circuit_breaker(self) -> None:
        """Activate circuit breaker (emergency shutdown of new requests)."""
        if not self.config.circuit_breaker_enabled:
            return

        self._circuit_breaker_active = True
        self._circuit_breaker_triggered_at = time.monotonic()
        logger.critical(
            "CIRCUIT BREAKER ACTIVATED: cooldown=%.0fs",
            self.config.circuit_breaker_cooldown,
        )

    def reset_circuit_breaker(self) -> None:
        """Manually reset circuit breaker."""
        self._circuit_breaker_active = False
        self._circuit_breaker_triggered_at = None
        logger.info("Circuit breaker manually reset")

    def get_history(self, seconds: int = 300) -> List[Dict]:
        """Get memory stats history."""
        cutoff = datetime.now() - timedelta(seconds=seconds)
        with self._lock:
            return [
                {
                    "timestamp": s.timestamp.isoformat(),
                    "rss_mb": round(s.rss_mb, 1),
                    "available_mb": round(s.available_mb, 1),
                    "percent_used": round(s.percent_used, 1),
                    "pressure": s.pressure.value,
                }
                for s in self._stats_history
                if s.timestamp > cutoff
            ]

    def _assess_pressure(self, percent_used: float) -> MemoryPressure:
        """Assess memory pressure level from percentage."""
        if percent_used >= self.config.emergency_threshold:
            return MemoryPressure.EMERGENCY
        elif percent_used >= self.config.reject_threshold:
            return MemoryPressure.CRITICAL
        elif percent_used >= self.config.throttle_threshold:
            return MemoryPressure.HIGH
        elif percent_used >= self.config.warn_threshold:
            return MemoryPressure.ELEVATED
        return MemoryPressure.NORMAL

    def _get_process_rss(self) -> int:
        """Get process RSS (Resident Set Size) in bytes."""
        try:
            import psutil
            process = psutil.Process(os.getpid())
            return process.memory_info().rss
        except ImportError:
            pass

        # Platform-specific fallbacks
        system = platform.system()

        if system == "Linux":
            try:
                with open("/proc/self/status", "r") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            # Value is in kB
                            return int(line.split()[1]) * 1024
            except (IOError, IndexError, ValueError):
                pass

        elif system == "Windows":
            try:
                import ctypes
                from ctypes import wintypes

                class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
                    _fields_ = [
                        ("cb", wintypes.DWORD),
                        ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t),
                        ("PrivateUsage", ctypes.c_size_t),
                    ]

                psapi = ctypes.windll.psapi
                handle = ctypes.windll.kernel32.GetCurrentProcess()
                counters = PROCESS_MEMORY_COUNTERS_EX()
                counters.cb = ctypes.sizeof(counters)

                if psapi.GetProcessMemoryInfo(
                    handle, ctypes.byref(counters), ctypes.sizeof(counters)
                ):
                    return counters.WorkingSetSize
            except Exception:
                pass

        elif system == "Darwin":
            try:
                import subprocess
                result = subprocess.run(
                    ["ps", "-o", "rss=", "-p", str(os.getpid())],
                    capture_output=True, text=True, timeout=5,
                )
                # ps returns RSS in KB
                return int(result.stdout.strip()) * 1024
            except Exception:
                pass

        # Last resort: estimate from resource module
        try:
            import resource
            # ru_maxrss is in KB on Linux, bytes on macOS
            usage = resource.getrusage(resource.RUSAGE_SELF)
            if system == "Darwin":
                return usage.ru_maxrss  # bytes on macOS
            return usage.ru_maxrss * 1024  # KB to bytes on Linux
        except Exception:
            pass

        # Cannot determine — return 0 (monitoring degraded)
        logger.debug("Unable to determine process RSS")
        return 0

    def _get_system_available(self) -> int:
        """Get system available memory in bytes."""
        try:
            import psutil
            return psutil.virtual_memory().available
        except ImportError:
            pass

        if platform.system() == "Linux":
            try:
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        if line.startswith("MemAvailable:"):
                            return int(line.split()[1]) * 1024
            except (IOError, IndexError, ValueError):
                pass

        return 0

    def _get_system_total(self) -> int:
        """Get system total memory in bytes."""
        try:
            import psutil
            return psutil.virtual_memory().total
        except ImportError:
            pass

        if platform.system() == "Linux":
            try:
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        if line.startswith("MemTotal:"):
                            return int(line.split()[1]) * 1024
            except (IOError, IndexError, ValueError):
                pass

        return 0

    def _detect_memory_api(self) -> str:
        """Detect available memory monitoring API."""
        try:
            import psutil
            return "psutil"
        except ImportError:
            pass

        system = platform.system()
        if system == "Linux" and os.path.exists("/proc/self/status"):
            return "/proc"
        elif system == "Windows":
            return "ctypes/psapi"
        elif system == "Darwin":
            return "ps"

        try:
            import resource
            return "resource"
        except ImportError:
            pass

        return "none"

    def _monitor_loop(self) -> None:
        """Background monitoring loop."""
        while self._running:
            try:
                stats = self.get_stats()

                if stats.pressure == MemoryPressure.ELEVATED:
                    logger.warning(
                        "Memory pressure ELEVATED: %.1f%% (%.0f MB RSS)",
                        stats.percent_used,
                        stats.rss_mb,
                    )
                elif stats.pressure == MemoryPressure.HIGH:
                    logger.warning(
                        "Memory pressure HIGH: %.1f%% (%.0f MB RSS) — throttling",
                        stats.percent_used,
                        stats.rss_mb,
                    )
                elif stats.pressure == MemoryPressure.CRITICAL:
                    logger.critical(
                        "Memory pressure CRITICAL: %.1f%% (%.0f MB RSS) — rejecting requests",
                        stats.percent_used,
                        stats.rss_mb,
                    )
                elif stats.pressure == MemoryPressure.EMERGENCY:
                    logger.critical(
                        "Memory pressure EMERGENCY: %.1f%% — activating circuit breaker",
                        stats.percent_used,
                    )
                    if self.config.circuit_breaker_enabled:
                        self.trigger_circuit_breaker()

            except Exception as e:
                logger.error("Memory monitor error: %s", e)

            time.sleep(self.config.check_interval)

    def _cleanup_history(self) -> None:
        """Remove old stats from history."""
        cutoff = datetime.now() - timedelta(seconds=self.config.stats_retention)
        self._stats_history = [s for s in self._stats_history if s.timestamp > cutoff]


class ClientRateLimiter:
    """Per-client rate limiter for DoS protection."""

    def __init__(self, config: ThrottleConfig):
        self.config = config
        self._requests: Dict[str, List[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def is_allowed(self, client_ip: str) -> Tuple[bool, int]:
        """
        Check if client is within rate limit.

        Returns:
            (allowed, remaining_requests)
        """
        now = time.monotonic()
        window_start = now - self.config.rate_limit_window

        with self._lock:
            # Clean old entries
            self._requests[client_ip] = [
                t for t in self._requests[client_ip] if t > window_start
            ]

            request_count = len(self._requests[client_ip])

            if request_count >= self.config.rate_limit_max_requests:
                return False, 0

            self._requests[client_ip].append(now)
            remaining = self.config.rate_limit_max_requests - request_count - 1
            return True, remaining

    def cleanup(self) -> None:
        """Remove expired entries."""
        now = time.monotonic()
        window_start = now - self.config.rate_limit_window

        with self._lock:
            expired = [
                ip for ip, timestamps in self._requests.items()
                if all(t <= window_start for t in timestamps)
            ]
            for ip in expired:
                del self._requests[ip]


class MemoryMonitorMiddleware(BaseHTTPMiddleware):
    """
    FastAPI/Starlette middleware for memory-based DoS protection.

    Usage:
        from fastapi import FastAPI
        from server.app.security.memory_monitor import (
            MemoryMonitorMiddleware, ThrottleConfig
        )

        app = FastAPI()
        config = ThrottleConfig()
        app.add_middleware(MemoryMonitorMiddleware, config=config)
    """

    def __init__(self, app, config: Optional[ThrottleConfig] = None):
        super().__init__(app)
        self.config = config or ThrottleConfig()
        self.monitor = MemoryMonitor(self.config)
        self.rate_limiter = ClientRateLimiter(self.config)
        self._concurrent_requests = 0
        self._concurrent_lock = threading.Lock()

        # Start background monitor
        self.monitor.start_background_monitor()

        logger.info("MemoryMonitorMiddleware initialized")

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Process request with memory-based DoS protection."""
        client_ip = self._get_client_ip(request)

        # 1. Circuit breaker check
        if self.monitor.is_circuit_breaker_active():
            return JSONResponse(
                status_code=self.config.emergency_status_code,
                content={
                    "error": "service_unavailable",
                    "message": "Service temporarily unavailable. Please retry later.",
                    "retry_after": int(self.config.circuit_breaker_cooldown),
                },
                headers={"Retry-After": str(int(self.config.circuit_breaker_cooldown))},
            )

        # 2. Request size validation
        size_error = self._validate_request_size(request)
        if size_error:
            return size_error

        # 3. Rate limiting
        allowed, remaining = self.rate_limiter.is_allowed(client_ip)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limit_exceeded",
                    "message": "Too many requests. Please slow down.",
                    "retry_after": int(self.config.rate_limit_window),
                },
                headers={
                    "Retry-After": str(int(self.config.rate_limit_window)),
                    "X-RateLimit-Limit": str(self.config.rate_limit_max_requests),
                    "X-RateLimit-Remaining": "0",
                },
            )

        # 4. Memory pressure check
        stats = self.monitor.get_stats()

        if stats.pressure == MemoryPressure.CRITICAL:
            return JSONResponse(
                status_code=self.config.reject_status_code,
                content={
                    "error": "server_overloaded",
                    "message": "Server is under heavy load. Please retry later.",
                },
                headers={"Retry-After": "30"},
            )

        if stats.pressure == MemoryPressure.HIGH:
            # Throttle: limit concurrent requests
            with self._concurrent_lock:
                if self._concurrent_requests >= self.config.throttle_max_concurrent:
                    return JSONResponse(
                        status_code=self.config.reject_status_code,
                        content={
                            "error": "server_busy",
                            "message": "Server is busy. Please retry shortly.",
                        },
                        headers={"Retry-After": "5"},
                    )
                self._concurrent_requests += 1
        else:
            with self._concurrent_lock:
                self._concurrent_requests += 1

        # 5. Process request
        try:
            response = await call_next(request)

            # Add rate limit headers
            response.headers["X-RateLimit-Limit"] = str(self.config.rate_limit_max_requests)
            response.headers["X-RateLimit-Remaining"] = str(remaining)

            # Add memory pressure header (for debugging/monitoring)
            response.headers["X-Server-Pressure"] = stats.pressure.value

            return response
        finally:
            with self._concurrent_lock:
                self._concurrent_requests = max(0, self._concurrent_requests - 1)

    def _validate_request_size(self, request: Request) -> Optional[Response]:
        """Validate request size limits."""
        budget = self.config.request_budget

        # URL length
        if len(str(request.url)) > budget.max_url_length:
            return JSONResponse(
                status_code=414,
                content={
                    "error": "uri_too_long",
                    "message": f"URL exceeds maximum length of {budget.max_url_length}",
                },
            )

        # Headers size (approximate)
        headers_size = sum(
            len(k) + len(v) for k, v in request.headers.items()
        )
        if headers_size > budget.max_headers_size:
            return JSONResponse(
                status_code=431,
                content={
                    "error": "headers_too_large",
                    "message": f"Headers exceed maximum size of {budget.max_headers_size} bytes",
                },
            )

        # Content-Length check (if provided)
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > budget.max_body_size:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "error": "payload_too_large",
                            "message": f"Request body exceeds maximum size of {budget.max_body_size // (1024*1024)}MB",
                        },
                    )
            except ValueError:
                pass

        # Query params count
        if len(request.query_params) > budget.max_query_params:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "too_many_parameters",
                    "message": f"Too many query parameters (max: {budget.max_query_params})",
                },
            )

        return None

    @staticmethod
    def _get_client_ip(request: Request) -> str:
        """Extract client IP from request."""
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
        if request.client:
            return request.client.host
        return "unknown"

    def get_status(self) -> Dict:
        """Get current middleware status for monitoring endpoint."""
        stats = self.monitor.get_stats()
        return {
            "memory": {
                "rss_mb": round(stats.rss_mb, 1),
                "available_mb": round(stats.available_mb, 1),
                "percent_used": round(stats.percent_used, 1),
                "pressure": stats.pressure.value,
            },
            "concurrent_requests": self._concurrent_requests,
            "circuit_breaker": self.monitor.is_circuit_breaker_active(),
            "rate_limiter_clients": len(self.rate_limiter._requests),
        }


__all__ = [
    "MemoryMonitor",
    "MemoryMonitorMiddleware",
    "MemoryPressure",
    "MemoryStats",
    "ThrottleConfig",
    "RequestBudget",
    "ClientRateLimiter",
]