# ============================================================
# AGREGAT Server — Multi-Stage Production Dockerfile
# ============================================================
# P1-7: Multi-stage build reduces attack surface by excluding
# build tools (gcc, etc.) from the final runtime image.
# ============================================================

# ─── Stage 1: Build ─────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies into a virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ─── Stage 2: Runtime ───────────────────────────────────────
FROM python:3.12-slim AS runtime

# P1-7: Security labels
LABEL maintainer="AGREGAT Team" \
      security.multi-stage="true" \
      security.non-root="true" \
      security.read-only-root="recommended"

# Create non-root user with no home directory and no shell
RUN groupadd -r agregat && \
    useradd -r -g agregat -d /app -s /usr/sbin/nologin -M agregat

# P1-7: Install only runtime dependencies (no gcc, no build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
COPY --chown=agregat:agregat . .

# P1-7: Remove sensitive/unnecessary files from final image
RUN rm -rf .git .gitignore .env.example .env tests/ docs/ notebooks/ bench/ \
    && find /app -name "*.pyc" -delete \
    && find /app -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# P1-7: Set restrictive file permissions
RUN chmod -R o-rwx /app && \
    chmod -R g-w /app

# Switch to non-root user
USER agregat

# P1-7: Environment hardening
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONSAFEPATH=1

# Health check (uses curl instead of httpx to avoid Python overhead)
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Expose port
EXPOSE 8000

# P1-7: Run with uvicorn (production hardened settings)
# --no-access-log: Disable access logs (use structured logging instead)
# --limit-concurrency: Prevent resource exhaustion
# --timeout-keep-alive: Close idle connections
# --timeout-graceful-shutdown: Graceful shutdown timeout
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "2", \
     "--log-level", "info", \
     "--no-access-log", \
     "--limit-concurrency", "1000", \
     "--timeout-keep-alive", "30", \
     "--timeout-graceful-shutdown", "10"]