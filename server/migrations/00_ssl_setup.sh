#!/usr/bin/env bash
# ============================================================
# M-05: PostgreSQL SSL Certificate Generation
# ============================================================
# Generates self-signed SSL certificates for PostgreSQL.
# Run ONCE before first production deployment.
# For production with real domain, use Let's Encrypt instead.
# ============================================================

set -euo pipefail

DATA_DIR="${1:-./pg_ssl}"
CERT_FILE="$DATA_DIR/server.crt"
KEY_FILE="$DATA_DIR/server.key"

echo "============================================================"
echo "AGREGAT — PostgreSQL SSL Certificate Setup"
echo "============================================================"

# Create directory
mkdir -p "$DATA_DIR"

if [ -f "$CERT_FILE" ] && [ -f "$KEY_FILE" ]; then
    echo "[SKIP] SSL certificates already exist at $DATA_DIR"
    echo "       To regenerate, delete: rm $DATA_DIR/server.{crt,key}"
    exit 0
fi

echo "[1/3] Generating private key (RSA 4096)..."
openssl genrsa -out "$KEY_FILE" 4096 2>/dev/null
chmod 600 "$KEY_FILE"  # PostgreSQL requires 0600 permissions

echo "[2/3] Generating self-signed certificate (valid 10 years)..."
openssl req -new -x509 \
    -key "$KEY_FILE" \
    -out "$CERT_FILE" \
    -days 3650 \
    -subj "/C=UA/O=AGREGAT/CN=db.agregat.internal" \
    -addext "subjectAltName=DNS:db,DNS:localhost,IP:127.0.0.1" \
    2>/dev/null

echo "[3/3] Setting permissions..."
chmod 600 "$KEY_FILE"
chmod 644 "$CERT_FILE"

echo ""
echo "============================================================"
echo "SSL certificates generated successfully!"
echo "============================================================"
echo "  Certificate: $CERT_FILE"
echo "  Private Key: $KEY_FILE"
echo ""
echo "Add to docker-compose.prod.yml db.volumes:"
echo "  - $DATA_DIR/server.crt:/var/lib/postgresql/data/server.crt:ro"
echo "  - $DATA_DIR/server.key:/var/lib/postgresql/data/server.key:ro"
echo ""
echo "Add to DATABASE_URL in .env:"
echo "  ?sslmode=require"
echo "============================================================"