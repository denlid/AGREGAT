#!/usr/bin/env bash
# ============================================================
# AGREGAT Server — Dependency Security Audit (P3-2)
# ============================================================
# Free automated vulnerability scanning using pip-audit.
# Run before every production deployment.
# ============================================================

set -euo pipefail

echo "============================================================"
echo "AGREGAT — Dependency Security Audit"
echo "============================================================"
echo ""

# ─── Step 1: Install pip-audit ──────────────────────────────
echo "[1/4] Installing pip-audit..."
pip install --quiet pip-audit

# ─── Step 2: Run vulnerability scan ─────────────────────────
echo "[2/4] Scanning dependencies for known vulnerabilities..."
echo ""

# --strict: exit with error if vulnerabilities found
# --format=json: machine-readable output
# --no-deps: only scan direct dependencies (faster)
# --requirement: scan from requirements.txt
pip-audit \
    --requirement requirements.txt \
    --format=columns \
    --no-deps \
    --disable-pip \
    2>&1 || true

echo ""

# ─── Step 3: Full scan (including transitive deps) ─────────
echo "[3/4] Full scan (including transitive dependencies)..."
echo ""

pip-audit \
    --requirement requirements.txt \
    --format=columns \
    --disable-pip \
    2>&1 || true

echo ""

# ─── Step 4: Summary ───────────────────────────────────────
echo "[4/4] Audit complete."
echo ""
echo "============================================================"
echo "If vulnerabilities were found:"
echo "  1. Update the affected package in requirements.txt"
echo "  2. Run: pip install -r requirements.txt --upgrade"
echo "  3. Re-run this audit to confirm fixes"
echo ""
echo "For critical vulnerabilities, consider:"
echo "  - Pinning to a specific safe version"
echo "  - Finding an alternative package"
echo "  - Applying a vendor patch"
echo "============================================================"